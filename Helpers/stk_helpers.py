"""
stk_helpers.py -- a small, version-independent wrapper around STK's COM API.

WHY THIS EXISTS
---------------
Scripts that talk to STK through ``win32com.client`` usually fail for one of
two reasons, neither of which has anything to do with the mission being
modelled:

1. ``win32com.client.constants`` is EMPTY when you attach to a running STK
   with ``GetActiveObject``. pywin32 only populates that module after makepy
   has generated wrappers for the exact type library on that machine. So
   ``constants.eSatellite`` / ``constants.eOrbitStateClassical`` raise a bare
   ``AttributeError`` naming the constant -- a confusing error that looks
   like a bug in the script.
2. ProgIDs are version-specific. ``STK12.Application`` does not exist on an
   STK 11 box and vice versa.

This module removes both failure modes:

* Structure, orbit state, geometry and constraints go through STK **Connect**
  commands -- plain text commands, identical across STK 11 / 12 / newer Ansys
  releases, requiring no enums at all.
* Object-model calls are used only where they take strings and numbers
  (``SetModel("Simple Transmitter Model")``, ``.Frequency``, ``.Eirp``,
  ``SetEmbeddedModel(...)``), which need no enums either.
* ProgIDs are tried in order until one resolves.

DESIGN RULES (keep these if you extend the module)
--------------------------------------------------
* Never import or reference ``win32com.client.constants``. If an enum
  value is genuinely needed, read it at runtime with ``session.enum()``,
  which walks the running STK's own type library.
* Never pass a numeric enum literal. If a call needs an enum, find the
  Connect equivalent instead.
* Every helper returns the object PATH it created (e.g.
  ``"*/Satellite/EOSat1"``). Paths are the currency of this module -- pass
  them to the next helper.
* Helpers are ADDITIVE. ``unique_path`` guarantees a new object is never
  created on top of an existing one.

QUICK START
-----------
    import stk_helpers as stk

    s = stk.StkSession.attach()
    s.ensure_scenario("MyScenario")
    s.set_time_period("1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000")

    sat = s.new_satellite("Sat1", sma_km=6878.137, inc_deg=97.4, raan_deg=0.0)
    s.add_sensor_rectangular(sat, "Cam", 1.11, 0.83)
    s.point_nadir(sat + "/Sensor/Cam")
    s.add_transmitter(sat, "TX", freq_ghz=0.437, eirp_dbw=0.0)

    gs = s.new_facility("Svalbard", 78.229772, 15.407786, 0.458)
    s.add_receiver(gs, "RX", freq_ghz=0.437)
    s.set_min_elevation(gs, 5.0)

    for name, count, secs in s.access_summary([sat], gs):
        print(name, count, secs)

VERIFYING THIS MODULE
---------------------
Run ``verify_stk_helpers.py`` against a real STK install after ANY change
here. It exercises every helper in a throwaway scenario and prints PASS/FAIL
per feature, so a broken helper is caught before it reaches a user.
"""

from __future__ import annotations

import os
import sys

try:
    import win32com.client
    import pywintypes
    import pythoncom
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "stk_helpers requires pywin32 (Windows only). Install it with:\n"
        "    pip install pywin32\n"
        "Original error: %s" % _exc
    )


__all__ = ["StkSession", "StkError", "DEFAULT_PROGIDS", "EARTH_RADIUS_KM",
           "MOON_RADIUS_KM", "altitude_to_sma", "walker_delta_elements"]


# ---------------------------------------------------------------------------
# Constants that are ours, not STK's
# ---------------------------------------------------------------------------

#: ProgIDs tried in order by :meth:`StkSession.attach`. The version-agnostic
#: "STK.Application" resolves to whatever STK is registered as default, so it
#: is the most portable; the pinned ones cover installs where the generic
#: entry is missing. Callers can override via the ``progids`` argument.
DEFAULT_PROGIDS = (
    "STK.Application",
    "STK_11",
    "STK12.Application",
    "STK11.Application",
    "STK11_x64.Application",
)

EARTH_RADIUS_KM = 6378.137
MOON_RADIUS_KM = 1737.4

#: Units this module pins on the STK root. Connect commands are interpreted
#: in the CURRENT unit preferences, so every session pins them explicitly --
#: otherwise a user whose STK is set to metres silently gets orbits 1000x
#: too small.
_UNIT_PREFERENCES = (
    ("DateFormat", "UTCG"),
    ("Distance", "km"),
    ("Angle", "deg"),
    ("Time", "sec"),
)


class StkError(RuntimeError):
    """Raised for any STK interaction failure, with actionable context."""


def altitude_to_sma(altitude_km: float, body_radius_km: float = EARTH_RADIUS_KM) -> float:
    """Circular-orbit semi-major axis for a given altitude above a body."""
    return body_radius_km + float(altitude_km)


def walker_delta_elements(total_sats: int, num_planes: int, phase_f: int,
                          raan0_deg: float = 0.0, true_anom0_deg: float = 0.0):
    """Yield ``(index, plane, raan_deg, true_anomaly_deg)`` for a Walker Delta.

    Standard Walker ``T/P/F`` notation:

    * ``T`` = total satellites, ``P`` = planes, ``S = T/P`` sats per plane
    * RAAN spacing between planes        = 360 / P
    * in-plane spacing                   = 360 / S
    * phase step per plane increment     = F * 360 / T

    Example -- ``walker_delta_elements(4, 4, 1)`` gives four planes 90 deg
    apart in RAAN, each satellite offset 90 deg in true anomaly from the
    previous plane.
    """
    if total_sats <= 0 or num_planes <= 0:
        raise ValueError("total_sats and num_planes must be positive")
    if total_sats % num_planes != 0:
        raise ValueError(
            "Walker constellation requires total_sats (%d) to be divisible by "
            "num_planes (%d)" % (total_sats, num_planes))

    sats_per_plane = total_sats // num_planes
    raan_step = 360.0 / num_planes
    in_plane_step = 360.0 / sats_per_plane
    phase_step = float(phase_f) * 360.0 / total_sats

    for index in range(total_sats):
        plane = index // sats_per_plane
        slot = index % sats_per_plane
        raan = (raan0_deg + plane * raan_step) % 360.0
        true_anom = (true_anom0_deg + slot * in_plane_step + plane * phase_step) % 360.0
        yield index, plane, raan, true_anom


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class StkSession(object):
    """A connected STK session. Construct with :meth:`attach` or :meth:`launch`."""

    def __init__(self, app):
        self.app = app
        try:
            self.root = app.Personality2  # object-model root, all STK versions
        except Exception as exc:
            raise StkError("Connected to STK but could not obtain the "
                           "Personality2 root object: %s" % exc)
        self.progid = None
        self.set_units()

    # -- connection ---------------------------------------------------------
    @classmethod
    def attach(cls, progids=None):
        """Attach to an ALREADY-RUNNING STK instance.

        Preferred over :meth:`launch`: launching creates a second, invisible
        STK the user cannot see. Raises :class:`StkError` listing every
        ProgID tried if none resolve.
        """
        progids = tuple(progids) if progids else DEFAULT_PROGIDS
        attempts = []
        for progid in progids:
            try:
                app = win32com.client.GetActiveObject(progid)
            except Exception as exc:
                attempts.append("    %-24s -> %s" % (progid, exc))
                continue
            session = cls(app)
            session.progid = progid
            return session
        raise StkError(
            "Could not attach to a running STK instance.\n"
            "  ProgIDs attempted:\n%s\n"
            "  Fix: open STK and re-run, or pass progids=[...] with the ProgID\n"
            "  registered on this machine. Find it in PowerShell:\n"
            "    Get-ChildItem Registry::HKEY_CLASSES_ROOT | "
            "Where-Object { $_.PSChildName -like \"*STK*Application*\" } | "
            "Select-Object PSChildName" % "\n".join(attempts))

    @classmethod
    def launch(cls, progids=None, visible=True):
        """Start a NEW STK instance. Only use when explicitly asked to."""
        progids = tuple(progids) if progids else DEFAULT_PROGIDS
        attempts = []
        for progid in progids:
            try:
                app = win32com.client.Dispatch(progid)
                app.Visible = bool(visible)
                app.UserControl = True
            except Exception as exc:
                attempts.append("    %-24s -> %s" % (progid, exc))
                continue
            session = cls(app)
            session.progid = progid
            return session
        raise StkError("Could not launch STK.\n  ProgIDs attempted:\n%s"
                       % "\n".join(attempts))

    # -- low-level ----------------------------------------------------------
    # -- enums, read from the running STK's own type library ----------------
    def _load_enums(self):
        """Build ``{enum_name: value}`` by reading STK's type library.

        This is the reliable replacement for ``win32com.client.constants``.
        That module is only populated by makepy; here we walk the type
        library the running STK application already exposes through
        ``GetTypeInfo``, so the values come from THIS machine's STK, whatever
        version it is. Nothing is hardcoded and no code generation is needed.
        """
        enums = {}
        for source in (self.root, self.app):
            try:
                type_info = source._oleobj_.GetTypeInfo()
                typelib, _index = type_info.GetContainingTypeLib()
            except Exception:
                continue
            for i in range(typelib.GetTypeInfoCount()):
                try:
                    if typelib.GetTypeInfoType(i) != pythoncom.TKIND_ENUM:
                        continue
                    enum_info = typelib.GetTypeInfo(i)
                    attributes = enum_info.GetTypeAttr()
                    for v in range(attributes.cVars):
                        var = enum_info.GetVarDesc(v)
                        name = enum_info.GetNames(var.memid)[0]
                        enums[name] = var.value
                except Exception:
                    continue
        return enums

    def enum(self, name):
        """Return an STK enum value by name, e.g. ``enum("eSatellite")``.

        Raises :class:`StkError` naming the enum if this STK's type library
        does not define it -- far clearer than the bare ``AttributeError``
        that ``win32com.client.constants`` produces.
        """
        if getattr(self, "_enums", None) is None:
            self._enums = self._load_enums()
        try:
            return self._enums[name]
        except KeyError:
            raise StkError(
                "Enum '%s' is not defined in this STK installation's type "
                "library (%d enum values were read). Either the name is wrong "
                "or this STK version calls it something else." % (name, len(self._enums)))

    def has_enums(self):
        """True if enum values could be read from the type library."""
        if getattr(self, "_enums", None) is None:
            self._enums = self._load_enums()
        return bool(self._enums)

    @staticmethod
    def _com_reason(exc):
        """Pull the human-readable reason out of a pywin32 com_error."""
        try:
            excepinfo = exc.excepinfo
            if excepinfo and len(excepinfo) > 2 and excepinfo[2]:
                return str(excepinfo[2])
        except Exception:
            pass
        return str(exc)

    def cmd(self, command, label="", fatal=True):
        """Execute one Connect command.

        Connect is STK's text command interface -- the same commands you can
        type into STK's own command window. Text commands are stable across
        STK versions, which is why this module prefers them to enum-taking
        object-model calls.

        Returns True on success. With ``fatal=False`` a failure prints a
        warning and returns False instead of raising.

        The error message keeps the reason on the FIRST line, so a caller
        that only shows one line of an exception still shows something
        useful.
        """
        try:
            self.root.ExecuteCommand(command)
            return True
        except pywintypes.com_error as exc:
            message = ("STK rejected a Connect command%s: %s\n    command: %s"
                       % ((" [%s]" % label) if label else "",
                          self._com_reason(exc), command))
            if fatal:
                raise StkError(message)
            sys.stderr.write("[warn] %s\n" % message)
            return False

    def obj(self, path):
        """Return the object-model interface for an STK path, or raise."""
        try:
            return self.root.GetObjectFromPath(path)
        except Exception as exc:
            raise StkError("No STK object at path '%s': %s" % (path, exc))

    def exists(self, path):
        """True if an object exists at ``path``."""
        try:
            self.root.GetObjectFromPath(path)
            return True
        except Exception:
            return False

    @staticmethod
    def _require_path(path, who):
        """Guard against a None path silently producing 'New / None/Sensor'.

        A helper whose parent failed returns None; without this the next
        helper builds a nonsense command and reports a confusing second
        error that hides the real one.
        """
        if not path or not isinstance(path, str):
            raise StkError(
                "%s was given an invalid parent path (%r). This usually means "
                "the helper that should have created the parent object failed "
                "earlier -- fix that first error, not this one." % (who, path))
        return path

    def unique_path(self, base_path):
        """Return ``base_path``, or ``base_path_2``, ``_3``... if taken.

        This is what makes every helper additive: running a script twice adds
        a second set of objects instead of failing or overwriting the first.
        """
        path, suffix = base_path, 1
        while self.exists(path):
            suffix += 1
            path = "%s_%d" % (base_path, suffix)
        return path

    # -- scenario -----------------------------------------------------------
    def set_units(self):
        """Pin the unit preferences Connect commands are interpreted in."""
        for dimension, unit in _UNIT_PREFERENCES:
            try:
                self.root.UnitPreferences.Item(dimension).SetCurrentUnit(unit)
            except Exception as exc:
                sys.stderr.write("[warn] could not set unit preference %s=%s: %s\n"
                                 % (dimension, unit, exc))

    @property
    def scenario_name(self):
        """Name of the open scenario, or None if no scenario is open."""
        try:
            return self.root.CurrentScenario.InstanceName
        except Exception:
            return None

    def ensure_scenario(self, name, reuse_open=True):
        """Return the name of a usable scenario, creating one if needed.

        With ``reuse_open=True`` (default) an already-open scenario is used
        whatever its name -- helpers name objects uniquely, so adding to a
        user's open scenario is safe. With ``reuse_open=False`` an open
        scenario whose name differs raises, which is the right choice for a
        script that must not touch the wrong scenario.
        """
        open_name = self.scenario_name
        if open_name is not None:
            if reuse_open or open_name == name:
                self.set_units()
                return open_name
            raise StkError(
                "Scenario '%s' is open but this script targets '%s'. Close it, "
                "or call ensure_scenario(..., reuse_open=True)." % (open_name, name))
        try:
            self.root.NewScenario(str(name))
        except pywintypes.com_error as exc:
            raise StkError("Failed to create scenario '%s': %s" % (name, exc))
        self.set_units()
        return self.scenario_name

    def set_time_period(self, start, stop, rewind=True):
        """Set the scenario analysis period. Times are UTCG strings."""
        self.cmd('SetAnalysisTimePeriod * "%s" "%s"' % (start, stop),
                 label="scenario analysis period")
        if rewind:
            self.rewind()
        self._start, self._stop = start, stop

    def rewind(self):
        try:
            self.root.Rewind()
        except Exception:
            pass

    def save_scenario(self):
        self.cmd("Save / *", label="save scenario", fatal=False)

    def close_scenario(self):
        """Close the open scenario WITHOUT saving. Used by the smoke test."""
        self.cmd("Unload / *", label="close scenario", fatal=False)

    # -- satellites ---------------------------------------------------------
    def new_satellite(self, name, sma_km, inc_deg, raan_deg,
                      ecc=0.0, arg_perigee_deg=0.0, true_anomaly_deg=0.0,
                      propagator="J2Perturbation", start=None, stop=None,
                      step_sec=60.0, coord_system="J2000", epoch=None,
                      central_body=None):
        """Create a satellite and set its classical orbit in one step.

        The Connect ``SetState Classical`` command sets the propagator, the
        elements AND propagates the ephemeris, so no separate Propagate()
        call is needed -- downstream access/AER/export work immediately.

        ``propagator`` is a Connect propagator name: "J2Perturbation" (fast,
        good for reference/GNSS constellations, needs no gravity file),
        "TwoBody", "HPOP" (high precision -- see :meth:`set_hpop_gravity`),
        "J4Perturbation".

        ``central_body`` selects the body the satellite orbits. Each
        satellite carries its own central body in STK, so one scenario can
        mix Earth and Moon orbiters. Leave as None for the scenario's body.

        ``sma_km`` is the SEMI-MAJOR AXIS, not altitude -- use
        :func:`altitude_to_sma` to convert.

        Returns the satellite path, e.g. ``"*/Satellite/Sat1"``.
        """
        start = start or getattr(self, "_start", None)
        stop = stop or getattr(self, "_stop", None)
        if not start or not stop:
            raise StkError("new_satellite needs a time period. Call "
                           "set_time_period() first, or pass start=/stop=.")
        epoch = epoch or start

        path = self.unique_path("*/Satellite/%s" % name)
        actual = path.rsplit("/", 1)[-1]

        if central_body:
            # Connect's New command takes an optional CentralBody keyword.
            # Fall back to a plain New (scenario central body) if the
            # installed STK build rejects it, so the script still runs.
            created = self.cmd("New / */Satellite %s CentralBody %s" % (actual, central_body),
                               label="create satellite on %s" % central_body, fatal=False)
            if not created:
                self.cmd("New / */Satellite %s" % actual, label="create satellite")
                sys.stderr.write(
                    "[warn] central body '%s' not applied to %s; satellite uses the "
                    "scenario central body.\n" % (central_body, actual))
        else:
            self.cmd("New / */Satellite %s" % actual, label="create satellite")

        self._set_classical_state(
            path, propagator=propagator, start=start, stop=stop,
            step_sec=step_sec, coord_system=coord_system, epoch=epoch,
            sma_km=sma_km, ecc=ecc, inc_deg=inc_deg,
            arg_perigee_deg=arg_perigee_deg, raan_deg=raan_deg,
            true_anomaly_deg=true_anomaly_deg)
        return path

    def new_walker(self, prefix, total_sats, num_planes, phase_f,
                   sma_km, inc_deg, **kwargs):
        """Create a Walker Delta constellation. Returns a list of paths.

        Extra keyword arguments pass through to :meth:`new_satellite`
        (propagator, ecc, coord_system, central_body, ...).
        """
        paths = []
        for index, _plane, raan, true_anom in walker_delta_elements(
                total_sats, num_planes, phase_f,
                raan0_deg=kwargs.pop("raan0_deg", 0.0),
                true_anom0_deg=kwargs.pop("true_anom0_deg", 0.0)):
            paths.append(self.new_satellite(
                "%s%d" % (prefix, index + 1), sma_km=sma_km, inc_deg=inc_deg,
                raan_deg=raan, true_anomaly_deg=true_anom, **kwargs))
        return paths

    #: Verified against a real STK 11 install by discover_stk_api.py.
    #: Findings that drove this list, and that are easy to get wrong:
    #:   * ``UseScenarioInterval`` works; the ``TimePeriod <start> <stop>``
    #:     form was rejected in every combination tried.
    #:   * SetState wants the semi-major axis in METRES, and ignores the
    #:     Distance unit preference -- even though SetPosition honours it.
    #:   * J2000 and ICRF are both accepted; Fixed is not.
    #: The forms are still probed in order and the winner cached, so a build
    #: that differs degrades gracefully instead of failing outright.
    _CLASSICAL_FORMS = (
        ("UseScenarioInterval/metres",
         'SetState {path} Classical {prop} UseScenarioInterval {step:g} '
         '{coord} "{epoch}" {sma_m:.6f} {ecc:.12f} {inc:.9f} {argp:.9f} '
         '{raan:.9f} {ta:.9f}'),
        ("UseScenarioInterval/km",
         'SetState {path} Classical {prop} UseScenarioInterval {step:g} '
         '{coord} "{epoch}" {sma_km:.9f} {ecc:.12f} {inc:.9f} {argp:.9f} '
         '{raan:.9f} {ta:.9f}'),
        ("TimePeriod/metres",
         'SetState {path} Classical {prop} TimePeriod "{start}" "{stop}" {step:g} '
         '{coord} "{epoch}" {sma_m:.6f} {ecc:.12f} {inc:.9f} {argp:.9f} '
         '{raan:.9f} {ta:.9f}'),
    )

    def _set_classical_state(self, path, propagator, start, stop, step_sec,
                             coord_system, epoch, sma_km, ecc, inc_deg,
                             arg_perigee_deg, raan_deg, true_anomaly_deg):
        """Set classical elements and propagate, then VERIFY the result.

        Verification matters more than it looks. A SetState that STK accepts
        but interprets in the wrong distance unit produces a scenario that
        looks fine and is wrong by a factor of 1000. So after each accepted
        form the orbit radius is read back and compared with what was asked
        for; a mismatch is treated as a rejection and the next form is tried.
        """
        fields = dict(path=path, prop=propagator, start=start, stop=stop,
                      step=float(step_sec), coord=coord_system, epoch=epoch,
                      sma_km=float(sma_km), sma_m=float(sma_km) * 1000.0,
                      ecc=float(ecc), inc=float(inc_deg),
                      argp=float(arg_perigee_deg), raan=float(raan_deg),
                      ta=float(true_anomaly_deg))

        cached = getattr(self, "_classical_form", None)
        forms = self._CLASSICAL_FORMS
        if cached is not None:
            forms = tuple(f for f in forms if f[0] == cached) or forms

        attempts = []
        for name, template in forms:
            try:
                self.root.ExecuteCommand(template.format(**fields))
            except pywintypes.com_error as exc:
                attempts.append("    [%s] rejected: %s" % (name, self._com_reason(exc)))
                continue

            radius = self.orbit_radius_km(path)
            if radius is None:
                attempts.append("    [%s] accepted but produced no ephemeris" % name)
                continue
            # Circular-ish sanity band: radius must be within 20% of the
            # requested semi-major axis. A unit error is off by ~1000x.
            if abs(radius - float(sma_km)) > 0.2 * float(sma_km):
                attempts.append(
                    "    [%s] accepted but orbit radius read back as %.1f km, "
                    "expected ~%.1f km (unit mismatch)" % (name, radius, sma_km))
                continue

            self._classical_form = name
            return True

        raise StkError(
            "Could not set a correct classical orbit state on %s.\n"
            "  Attempts:\n%s\n"
            "  The satellite object exists but has no usable ephemeris, so "
            "access and reports would be empty or wrong." % (path, "\n".join(attempts)))

    def orbit_radius_km(self, sat_path, when=None):
        """Orbit radius in km at ``when`` (default: ephemeris start), or None.

        Used to verify that a satellite really has ephemeris AND that it came
        out at the intended scale.
        """
        when = when or getattr(self, "_start", None)
        if not when:
            return None
        try:
            provider = self.obj(sat_path).DataProviders.Item("Cartesian Position")
            group = provider.Group.Item("ICRF") if hasattr(provider, "Group") else provider
            result = group.Exec(when, when, 60.0)
            datasets = result.DataSets
            if datasets.Count == 0:
                return None
            x = float(datasets.GetDataSetByName("x").GetValues()[0])
            y = float(datasets.GetDataSetByName("y").GetValues()[0])
            z = float(datasets.GetDataSetByName("z").GetValues()[0])
        except Exception:
            return None
        return (x * x + y * y + z * z) ** 0.5

    def set_hpop_gravity(self, sat_path, grv_file, max_degree, max_order):
        """Configure the HPOP central-body gravity field.

        Only valid on a satellite whose propagator is HPOP. All properties
        here are strings/numbers -- no enums involved.

        Watch the valid degree/order range of the gravity file: the lunar
        ``GL0420A.grv`` accepts 48 to 420, and STK raises a com_error outside
        that range. Higher degree = slower propagation.
        """
        try:
            gravity = self.obj(sat_path).Propagator.ForceModel.CentralBodyGravity
            gravity.File = str(grv_file)
            gravity.MaxDegree = int(max_degree)
            gravity.MaxOrder = int(max_order)
        except pywintypes.com_error as exc:
            raise StkError(
                "Failed to set HPOP gravity on %s (file=%s degree=%s order=%s). "
                "Check that the propagator is HPOP, the .grv path exists, and the "
                "degree/order are inside the file's valid range: %s"
                % (sat_path, grv_file, max_degree, max_order, exc))
        self.propagate(sat_path)

    def propagate(self, sat_path):
        """Re-run the propagator. Needed after changing the force model."""
        try:
            self.obj(sat_path).Propagator.Propagate()
        except pywintypes.com_error as exc:
            raise StkError("Propagation failed for %s: %s" % (sat_path, exc))

    def set_epoch(self, sat_path, epoch):
        """Re-anchor an existing orbit to a new epoch, keeping its shape."""
        self.cmd('SetState %s Epoch "%s"' % (sat_path, epoch),
                 label="re-epoch %s" % sat_path, fatal=False)
        self.propagate(sat_path)

    # -- sensors (visual FOV cones) -----------------------------------------
    def _new_sensor(self, parent_path, name, who):
        self._require_path(parent_path, who)
        path = self.unique_path("%s/Sensor/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Sensor %s" % (parent_path, actual), label="create sensor")
        return path

    def add_sensor_conic(self, parent_path, name, half_angle_deg,
                         angular_resolution_deg=0.1):
        """Add a simple conic sensor -- a visible FOV cone in the 3D window.

        Uses the object-model ``CommonTasks.SetPatternSimpleConic`` (which
        takes plain numbers, no enums). The Connect ``Define ... SimpleConic``
        form is kept as a fallback because it was REJECTED on STK 11 in
        testing even though ``Define ... Rectangular`` was accepted.

        Keep this conceptually separate from the transmitter/receiver RF
        chain: a sensor draws geometry, a transmitter carries a link budget.
        An object can have both.
        """
        path = self._new_sensor(parent_path, name, "add_sensor_conic")
        try:
            self.obj(path).CommonTasks.SetPatternSimpleConic(
                float(half_angle_deg), float(angular_resolution_deg))
            return path
        except Exception as exc:
            first = self._com_reason(exc) if isinstance(exc, pywintypes.com_error) else exc
        if not self.cmd("Define %s SimpleConic %.6f %.6f"
                        % (path, float(half_angle_deg), float(angular_resolution_deg)),
                        label="conic sensor pattern", fatal=False):
            raise StkError("Could not set a conic pattern on %s. Object model "
                           "route said: %s" % (path, first))
        return path

    def add_sensor_rectangular(self, parent_path, name,
                               half_angle_across_deg, half_angle_along_deg):
        """Add a rectangular sensor. Both arguments are HALF-angles.

        For a camera quoted as a full FOV (e.g. 2.22 deg across-track by
        1.66 deg along-track), pass half of each: 1.11 and 0.83.
        """
        path = self._new_sensor(parent_path, name, "add_sensor_rectangular")
        try:
            self.obj(path).CommonTasks.SetPatternRectangular(
                float(half_angle_across_deg), float(half_angle_along_deg))
            return path
        except Exception as exc:
            first = self._com_reason(exc) if isinstance(exc, pywintypes.com_error) else exc
        if not self.cmd("Define %s Rectangular %.6f %.6f"
                        % (path, float(half_angle_across_deg), float(half_angle_along_deg)),
                        label="rectangular sensor pattern", fatal=False):
            raise StkError("Could not set a rectangular pattern on %s. Object "
                           "model route said: %s" % (path, first))
        return path

    def point_nadir(self, sensor_path):
        """Point a sensor along nadir (elevation -90 deg in the body frame)."""
        self.cmd("Point %s Fixed AzEl 0.0 -90.0 Rotate" % sensor_path,
                 label="nadir pointing", fatal=False)

    def point_az_el(self, sensor_path, az_deg, el_deg, about_boresight="Rotate"):
        """Fixed az/el pointing. ``about_boresight`` is "Rotate" or "Hold"."""
        self.cmd("Point %s Fixed AzEl %.6f %.6f %s"
                 % (sensor_path, float(az_deg), float(el_deg), about_boresight),
                 label="az/el pointing", fatal=False)

    def point_at_target(self, sensor_path, target_path):
        """Slew a sensor to track another object."""
        self.cmd("Point %s Targeted Track %s" % (sensor_path, target_path),
                 label="targeted pointing", fatal=False)

    # -- RF chain -----------------------------------------------------------
    @staticmethod
    def _try_set_antenna(model, antenna_model, path):
        """Select an embedded antenna pattern, if this model has an antenna.

        The SIMPLE transmitter/receiver models have no ``AntennaControl`` at
        all -- they are characterised by EIRP alone, which is exactly why
        they are simple. Asking for a pattern there is not an error, it is a
        no-op, so this reports and moves on rather than failing the helper.

        Where AntennaControl does exist, note that pattern selection is a
        METHOD -- ``SetEmbeddedModel(name)`` -- not an ``EmbeddedModel = name``
        assignment.
        """
        try:
            control = model.AntennaControl
        except Exception:
            sys.stderr.write(
                "[info] %s uses a Simple model, which has no antenna object; "
                "the '%s' pattern was not applied. Use a Complex model if you "
                "need a real antenna pattern.\n" % (path, antenna_model))
            return False
        try:
            control.SetEmbeddedModel(antenna_model)
            return True
        except Exception as exc:
            sys.stderr.write("[warn] could not select antenna model '%s' on %s: %s\n"
                             % (antenna_model, path, exc))
            return False

    def add_transmitter(self, parent_path, name, freq_ghz, eirp_dbw,
                        data_rate_kbps=None, antenna_model="Isotropic"):
        """Mount a Simple Transmitter Model on a satellite or facility.

        Two STK gotchas are handled here so callers cannot get them wrong:

        * the EIRP property is ``.Eirp`` (camelCase) -- ``.EIRP`` raises
        * antenna pattern selection is a METHOD, ``SetEmbeddedModel(name)``,
          NOT an ``EmbeddedModel = name`` assignment

        ``antenna_model`` is a built-in embedded model name: "Isotropic",
        "Gaussian", "Parabolic", "External Antenna Pattern".
        """
        self._require_path(parent_path, "add_transmitter")
        path = self.unique_path("%s/Transmitter/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Transmitter %s" % (parent_path, actual),
                 label="create transmitter")
        try:
            transmitter = self.obj(path)
            transmitter.SetModel("Simple Transmitter Model")
            model = transmitter.Model
            model.Frequency = float(freq_ghz)   # GHz
            model.Eirp = float(eirp_dbw)        # dBW -- '.Eirp', not '.EIRP'
            if data_rate_kbps is not None:
                try:
                    model.DataRate = float(data_rate_kbps)   # kbps
                except Exception:
                    sys.stderr.write("[warn] data rate not settable on this "
                                     "transmitter model; skipped.\n")
            if antenna_model:
                self._try_set_antenna(model, antenna_model, path)
        except pywintypes.com_error as exc:
            raise StkError("Failed to configure transmitter %s: %s" % (path, exc))
        return path

    def add_receiver(self, parent_path, name, freq_ghz=None,
                     antenna_model="Isotropic", auto_select_frequency=True):
        """Mount a Simple Receiver Model on a satellite or facility.

        NOTE ON FREQUENCY (verified against STK 11 by discover_stk_api.py):
        a receiver's ``Frequency`` is READ-ONLY and there is no
        ``AutoSelectFrequency`` property on either the Simple or Complex
        receiver model; the Connect ``Define ... Frequency`` forms are
        rejected too. STK derives the receive frequency from the transmitter
        on the other end of the link, which is the correct behaviour -- you
        set the frequency on the TRANSMITTER and the receiver follows.

        ``freq_ghz`` is therefore accepted for symmetry with
        :meth:`add_transmitter` and for forward compatibility, but is only
        applied if this build actually allows it. It is never an error.
        """
        self._require_path(parent_path, "add_receiver")
        path = self.unique_path("%s/Receiver/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Receiver %s" % (parent_path, actual), label="create receiver")
        try:
            receiver = self.obj(path)
            receiver.SetModel("Simple Receiver Model")
            model = receiver.Model
            if freq_ghz is not None and not auto_select_frequency:
                try:
                    model.AutoSelectFrequency = False
                    model.Frequency = float(freq_ghz)
                except Exception:
                    sys.stderr.write(
                        "[info] receiver frequency is read-only on this STK build; "
                        "STK will derive it from the linked transmitter.\n")
            if antenna_model:
                self._try_set_antenna(model, antenna_model, path)
        except pywintypes.com_error as exc:
            raise StkError("Failed to configure receiver %s: %s" % (path, exc))
        return path

    # -- ground assets ------------------------------------------------------
    def new_facility(self, name, lat_deg, lon_deg, alt_km=0.0, central_body=None):
        """Create a ground facility at a geodetic position. Altitude in km."""
        path = self.unique_path("*/Facility/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        if central_body:
            created = self.cmd("New / */Facility %s CentralBody %s" % (actual, central_body),
                               label="create facility on %s" % central_body, fatal=False)
            if not created:
                self.cmd("New / */Facility %s" % actual, label="create facility")
        else:
            self.cmd("New / */Facility %s" % actual, label="create facility")
        self.cmd("SetPosition %s Geodetic %.9f %.9f %.9f"
                 % (path, float(lat_deg), float(lon_deg), float(alt_km)),
                 label="facility position")
        return path

    def new_planet(self, name):
        """Add a planet/moon as an ephemeris reference object (e.g. Earth in
        a Moon-centred scenario, for Earth-pointing geometry or AER)."""
        path = self.unique_path("*/Planet/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Planet %s" % actual, label="create planet")
        return path

    def set_min_elevation(self, path, min_elev_deg):
        """Apply a minimum elevation-angle access constraint."""
        self.cmd("SetConstraint %s ElevationAngle Min %.6f" % (path, float(min_elev_deg)),
                 label="min elevation constraint", fatal=False)

    # -- analysis -----------------------------------------------------------
    def compute_access(self, from_path, to_path):
        """Compute access and return ``(count, total_seconds, intervals)``.

        ``intervals`` is a list of ``(start_utcg, stop_utcg)`` string pairs.
        """
        try:
            access = self.obj(from_path).GetAccessToObject(self.obj(to_path))
            access.ComputeAccess()
            computed = access.ComputedAccessIntervalTimes
            intervals, total = [], 0.0
            for i in range(computed.Count):
                start, stop = computed.GetInterval(i)
                intervals.append((str(start), str(stop)))
                # ConvertDate returns a STRING; subtracting them raises
                # "unsupported operand type(s) for -: 'str' and 'str'".
                total += (float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", stop))
                          - float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", start)))
            return len(intervals), total, intervals
        except pywintypes.com_error as exc:
            raise StkError("Access computation %s -> %s failed: %s"
                           % (from_path, to_path, exc))

    def access_summary(self, from_paths, to_path):
        """Return ``[(name, count, total_seconds), ...]`` for several objects.

        A failure on one pair is reported and recorded as ``(name, None,
        None)`` rather than aborting the whole summary.
        """
        rows = []
        for path in from_paths:
            name = path.rsplit("/", 1)[-1]
            try:
                count, total, _ = self.compute_access(path, to_path)
                rows.append((name, count, total))
            except StkError as exc:
                sys.stderr.write("[warn] %s\n" % exc)
                rows.append((name, None, None))
        return rows

    def write_access_csv(self, rows, to_name, out_dir=None, filename="access_summary.csv"):
        """Write an access summary to CSV in a script-relative directory."""
        out_dir = out_dir or os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0] or __file__)), "stk_output")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, filename)
        with open(out_file, "w") as handle:
            handle.write("Object,Target,NumPasses,TotalDuration_s\n")
            for name, count, total in rows:
                if count is None:
                    handle.write("%s,%s,ERROR,ERROR\n" % (name, to_name))
                else:
                    handle.write("%s,%s,%d,%.3f\n" % (name, to_name, count, total))
        return out_file