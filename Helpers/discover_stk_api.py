"""
discover_stk_api.py -- ask STK what it actually supports, instead of guessing.

This is a DIAGNOSTIC, not part of the shipped helper module. It creates a
throwaway scenario, probes the API in several ways, prints a report, and
closes the scenario without saving.

    python discover_stk_api.py

It answers four questions:

  1. What are the REAL enum names in this STK's type library? (dumps every
     name matching the ones we need, plus a full list to a text file)
  2. Which SetState Connect argument order, coordinate frame and unit
     convention does this build accept? (tries a matrix of permutations)
  3. Does the object-model orbit route work once the right enum names are
     known?
  4. How can a receiver's frequency be set on this build, given that
     AutoSelectFrequency appears to be read-only?

Send the printed report back and the helper module gets patched against
facts rather than another guess.

Requires: STK open, NO scenario loaded.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stk_helpers as stk  # noqa: E402


SCENARIO = "STKApiDiscovery"
START = "1 Jul 2026 00:00:00.000"
STOP = "1 Jul 2026 06:00:00.000"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stk_output")

# Enum families the helper module needs. We print every name containing any
# of these fragments, so the real spelling shows up whatever it is.
ENUM_KEYWORDS = [
    "OrbitState", "SizeShape", "AscNode", "Location", "Propagator",
    "STKObject", "CoordinateSystem", "ClassicalSizeShape", "Classical",
]


def rule(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------------------
# 1. enums
# ---------------------------------------------------------------------------
def dump_enums(session):
    rule("1. ENUM NAMES IN THIS STK'S TYPE LIBRARY")
    enums = session._load_enums()
    print("total enum values read: %d" % len(enums))

    os.makedirs(OUT_DIR, exist_ok=True)
    full_path = os.path.join(OUT_DIR, "stk_enum_names.txt")
    with open(full_path, "w") as handle:
        for name in sorted(enums):
            handle.write("%s = %s\n" % (name, enums[name]))
    print("full list written to: %s" % full_path)

    for keyword in ENUM_KEYWORDS:
        matches = sorted(n for n in enums if keyword.lower() in n.lower())
        print("\n  names containing %-18s (%d)" % ("'%s'" % keyword, len(matches)))
        for name in matches[:40]:
            print("      %-46s = %s" % (name, enums[name]))
        if len(matches) > 40:
            print("      ... %d more (see the file)" % (len(matches) - 40))
    return enums


# ---------------------------------------------------------------------------
# 2. Connect SetState matrix
# ---------------------------------------------------------------------------
def probe_setstate(session):
    rule("2. WHICH 'SetState Classical' FORM DOES THIS BUILD ACCEPT?")

    sma_km = 6878.137
    sma_m = sma_km * 1000.0
    elements = "%s 0.0 97.4 0.0 0.0 0.0"          # sma placeholder + e i argp raan ta

    forms = []
    for coord in ("J2000", "ICRF", "Fixed"):
        for interval, interval_text in (
                ("UseScenarioInterval", "UseScenarioInterval"),
                ("TimePeriod", 'TimePeriod "%s" "%s"' % (START, STOP))):
            for unit_label, sma in (("km", sma_km), ("m", sma_m)):
                forms.append((
                    "%s / %s / SMA in %s" % (coord, interval, unit_label),
                    'SetState {path} Classical TwoBody %s 60 %s "%s" %s'
                    % (interval_text, coord, START, elements % ("%.6f" % sma))))

    # also the order with epoch before the coordinate system
    forms.append((
        "J2000 / TimePeriod / epoch before coord / km",
        'SetState {path} Classical TwoBody TimePeriod "%s" "%s" 60 "%s" J2000 %s'
        % (START, STOP, START, elements % ("%.6f" % sma_km))))

    accepted = []
    for index, (label, template) in enumerate(forms):
        name = "ProbeSat%d" % index
        session.cmd("New / */Satellite %s" % name, fatal=False)
        path = "*/Satellite/%s" % name
        command = template.format(path=path)
        try:
            session.root.ExecuteCommand(command)
        except Exception as exc:
            print("  reject  %-46s %s" % (label, session._com_reason(exc)))
            continue
        has_ephem = _has_ephemeris(session, path)
        print("  ACCEPT  %-46s ephemeris=%s" % (label, has_ephem))
        accepted.append((label, command, has_ephem))

    if not accepted:
        print("\n  NOTHING accepted. Printing one full command for reference:")
        print("    " + forms[0][1].format(path="*/Satellite/ProbeSat0"))
    else:
        print("\n  First accepted command in full:")
        print("    " + accepted[0][1])
    return accepted


def _has_ephemeris(session, path):
    """True if the satellite actually has computed ephemeris."""
    try:
        provider = session.obj(path).DataProviders.Item("Cartesian Position")
        group = provider.Group.Item("ICRF") if hasattr(provider, "Group") else provider
        return group.Exec(START, STOP, 3600.0).DataSets.Count > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 3. object-model orbit route, using whatever the enums are really called
# ---------------------------------------------------------------------------
def probe_object_model(session, enums):
    rule("3. OBJECT-MODEL ORBIT ROUTE")

    def find(*fragments):
        """First enum name containing all fragments (case-insensitive)."""
        for name in sorted(enums):
            low = name.lower()
            if all(f.lower() in low for f in fragments):
                return name
        return None

    candidates = {
        "classical orbit state": find("orbitstate", "classical") or find("classical"),
        "semimajor axis size/shape": find("sizeshape", "semimajor"),
        "RAAN asc node": find("ascnode", "raan"),
        "true anomaly location": find("location", "trueanomaly"),
        "J2 propagator": find("propagator", "j2"),
        "satellite object type": find("esatellite"),
    }
    for label, name in candidates.items():
        print("  %-28s -> %s" % (label, name or "NOT FOUND"))

    session.cmd("New / */Satellite OmSat", fatal=False)
    path = "*/Satellite/OmSat"
    try:
        satellite = session.obj(path)
        prop_enum = candidates["J2 propagator"]
        if prop_enum:
            satellite.SetPropagatorType(enums[prop_enum])
            print("  SetPropagatorType(%s) OK" % prop_enum)
        prop = satellite.Propagator
        representation = prop.InitialState.Representation
        print("  Representation type: %s" % type(representation))

        classical_enum = candidates["classical orbit state"]
        if not classical_enum:
            print("  no classical enum found -- cannot ConvertTo")
            return
        orbit = representation.ConvertTo(enums[classical_enum])
        print("  ConvertTo(%s) OK -> %s" % (classical_enum, type(orbit)))

        orbit.SizeShapeType = enums[candidates["semimajor axis size/shape"]]
        orbit.SizeShape.SemiMajorAxis = 6878.137
        orbit.SizeShape.Eccentricity = 0.0
        orbit.Orientation.Inclination = 97.4
        orbit.Orientation.ArgOfPerigee = 0.0
        orbit.Orientation.AscNodeType = enums[candidates["RAAN asc node"]]
        orbit.Orientation.AscNode.Value = 90.0
        orbit.LocationType = enums[candidates["true anomaly location"]]
        orbit.Location.Value = 0.0
        representation.Assign(orbit)
        prop.Propagate()
        print("  Assign + Propagate OK, ephemeris=%s" % _has_ephemeris(session, path))
    except Exception as exc:
        print("  object-model route failed: %s" % exc)
        traceback.print_exc(limit=2)


# ---------------------------------------------------------------------------
# 4. receiver frequency
# ---------------------------------------------------------------------------
def probe_receiver(session):
    rule("4. HOW TO SET A RECEIVER FREQUENCY ON THIS BUILD")

    session.cmd("New / */Facility ProbeGS", fatal=False)
    session.cmd("New / */Facility/ProbeGS/Receiver ProbeRX", fatal=False)
    path = "*/Facility/ProbeGS/Receiver/ProbeRX"

    try:
        receiver = session.obj(path)
    except Exception as exc:
        print("  could not resolve receiver: %s" % exc)
        return

    for model_name in ("Simple Receiver Model", "Complex Receiver Model"):
        try:
            receiver.SetModel(model_name)
            print("  SetModel(%r) OK" % model_name)
        except Exception as exc:
            print("  SetModel(%r) failed: %s" % (model_name, exc))
            continue

        model = receiver.Model
        for attribute in ("AutoSelectFrequency", "AutoScaleBandwidth"):
            try:
                current = getattr(model, attribute)
                print("      %s reads as %r" % (attribute, current))
            except Exception as exc:
                print("      %s not readable: %s" % (attribute, exc))
            for value in (False, True):
                try:
                    setattr(model, attribute, value)
                    print("      %s = %r accepted" % (attribute, value))
                    break
                except Exception as exc:
                    print("      %s = %r rejected: %s"
                          % (attribute, value, session._com_reason(exc)))

        try:
            model.Frequency = 0.437
            print("      Frequency = 0.437 accepted (model: %s)" % model_name)
        except Exception as exc:
            print("      Frequency assignment rejected: %s" % session._com_reason(exc))

    for command in ('Define %s Frequency 0.437' % path,
                    'Define %s AutoSelectFrequency Off' % path,
                    'Receiver %s Frequency 0.437' % path):
        ok = session.cmd(command, fatal=False)
        print("  Connect %-52s -> %s" % (command.split(" ", 2)[-1][:52],
                                         "ACCEPTED" if ok else "rejected"))


# ---------------------------------------------------------------------------
def main():
    session = stk.StkSession.attach()
    print("connected via ProgID: %s" % session.progid)

    if session.scenario_name is not None:
        print("\nREFUSING TO RUN: scenario '%s' is open. Close it first."
              % session.scenario_name)
        return 2

    session.ensure_scenario(SCENARIO)
    session.set_time_period(START, STOP)

    try:
        enums = dump_enums(session)
        probe_setstate(session)
        probe_object_model(session, enums)
        probe_receiver(session)
    finally:
        session.close_scenario()
        print("\n(discovery scenario closed without saving)")

    rule("SEND BACK: everything printed above, plus stk_output/stk_enum_names.txt "
         "if the enum section looks empty")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)