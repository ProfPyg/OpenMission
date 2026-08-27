"""
probe_object_model.py -- ask STK's objects what they can actually do.

Guessing Connect syntax has a poor hit rate: area targets, coverage
definitions, chains and attitude profiles all rejected every form tried.
That does not mean STK cannot do them -- they are core features -- it means
the syntax is not what was guessed.

Rather than guess again, this interrogates the COM type library. Every STK
object exposes its interface, so we can list the methods and properties that
actually exist on this installation, then build wrappers against real names.

    python probe_object_model.py                 # the four unknown capabilities
    python probe_object_model.py "*/Satellite/X" # anything you like

Throwaway scenario, discarded at the end.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

try:
    import pythoncom
except ImportError:
    pythoncom = None

START, STOP = "1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"
INTERESTING = ("add", "set", "compute", "define", "grid", "bounds", "asset",
               "point", "profile", "area", "type", "objects", "resolution")


def members(dispatch, depth=0):
    """Method and property names on a COM object, from its type info."""
    names = set()
    try:
        info = dispatch._oleobj_.GetTypeInfo()
    except Exception:
        return names
    try:
        attr = info.GetTypeAttr()
        for i in range(attr.cFuncs):
            desc = info.GetFuncDesc(i)
            names.add(info.GetNames(desc.memid)[0])
        for i in range(attr.cVars):
            desc = info.GetVarDesc(i)
            names.add(info.GetNames(desc.memid)[0])
    except Exception:
        pass
    return names


def show(label, dispatch, filter_interesting=True):
    names = members(dispatch)
    if not names:
        print("  %-26s (no type info)" % label)
        return names
    shown = sorted(n for n in names
                   if not filter_interesting
                   or any(k in n.lower() for k in INTERESTING))
    print("  %-26s %d members; relevant:" % (label, len(names)))
    line = "      "
    for name in shown:
        if len(line) + len(name) > 74:
            print(line)
            line = "      "
        line += name + "  "
    if line.strip():
        print(line)
    return names


def descend(session, path, label, children):
    """Print the object's members, then those of named sub-objects."""
    print("\n" + "-" * 74)
    print("%s   (%s)" % (label, path))
    print("-" * 74)
    try:
        obj = session.obj(path)
    except stk.StkError as exc:
        print("  could not resolve: %s" % exc)
        return
    show("<object>", obj)
    for child in children:
        current, name = obj, child
        try:
            for part in child.split("."):
                current = getattr(current, part)
            show(name, current)
        except Exception as exc:
            print("  %-26s not available (%s)" % (name, str(exc)[:40]))


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("ObjectModelProbe")
    session.set_time_period(START, STOP)

    explicit = [a for a in sys.argv[1:] if not a.startswith("-")]
    if explicit:
        for path in explicit:
            descend(session, path, path, [])
        session.close_scenario()
        return 0

    sat = session.new_satellite("ProbeSat", sma_km=stk.altitude_to_sma(550.0),
                                inc_deg=97.6, raan_deg=0.0)
    gs = session.new_facility("ProbeGS", 38.5333, 23.6333, 0.2)

    print("\nListing the members that exist on THIS STK. Wrappers get built")
    print("against these names instead of guessed Connect commands.")

    # 1. area target
    session.cmd("New / */AreaTarget ProbeROI", fatal=False)
    descend(session, "*/AreaTarget/ProbeROI", "1. AREA TARGET",
            ["AreaTypeData", "CommonTasks", "AccessConstraints"])

    # 2. coverage definition + figure of merit
    session.cmd("New / */CoverageDefinition ProbeCov", fatal=False)
    descend(session, "*/CoverageDefinition/ProbeCov", "2. COVERAGE DEFINITION",
            ["Grid", "Grid.BoundsType", "AssetList", "Advanced", "Interval"])
    session.cmd("New / */CoverageDefinition/ProbeCov/FigureOfMerit ProbeFOM", fatal=False)
    descend(session, "*/CoverageDefinition/ProbeCov/FigureOfMerit/ProbeFOM",
            "3. FIGURE OF MERIT", ["Definition", "DefinitionType"])

    # 3. chain
    session.cmd("New / */Chain ProbeChain", fatal=False)
    descend(session, "*/Chain/ProbeChain", "4. CHAIN", ["Objects", "Intervals"])

    # 4. satellite attitude
    descend(session, sat, "5. SATELLITE (attitude)",
            ["Attitude", "Attitude.Basic", "Attitude.Basic.Profile"])

    # 5. the facility's constraint collection, since constraints DO work
    descend(session, gs, "6. FACILITY (constraints)", ["AccessConstraints"])

    session.close_scenario()
    print("\n(probe scenario closed without saving)")
    print("\nSend this output back -- wrappers will be written against these")
    print("real member names, then verified by the smoke test.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
