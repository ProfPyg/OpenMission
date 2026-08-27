"""
probe_capabilities.py -- find out what ELSE this STK will accept.

The helper module wraps what we have tested. Everything else is reachable in
principle through session.cmd(), but our hit rate on guessed Connect syntax
has been about 50% -- SetState, targeted pointing, report styles and graph
creation were all wrong first time. So before claiming a capability is
"missing" or "available", test it.

For each capability this tries the plausible Connect forms and reports which
were accepted, so the answer is measured rather than assumed.

Throwaway scenario, discarded at the end. STK open, no scenario loaded.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

START, STOP = "1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


def try_all(session, label, commands):
    """Run each command, report which are accepted. Returns the first winner."""
    winner = None
    for description, command in commands:
        ok = session.cmd(command, fatal=False)
        print("    %-46s %s" % (description, "ACCEPTED" if ok else "rejected"))
        if ok and winner is None:
            winner = command
    print("  -> %s: %s" % (label, "works" if winner else "NO WORKING FORM FOUND"))
    return winner


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("CapabilityProbe")
    session.set_time_period(START, STOP)
    sat = session.new_satellite("ProbeSat", sma_km=stk.altitude_to_sma(550.0),
                                inc_deg=97.6, raan_deg=0.0)
    gs = session.new_facility("ProbeGS", 38.5333, 23.6333, 0.2)
    results = {}

    # ---- 1. access constraints beyond min elevation --------------------
    rule("1. ACCESS CONSTRAINTS (currently only ElevationAngle Min is wrapped)")
    for name, command in [
        ("range max 2000 km",      'SetConstraint %s Range Max 2000' % gs),
        ("altitude min",           'SetConstraint %s Altitude Min 100' % sat),
        ("sun elevation min",      'SetConstraint %s SunElevationAngle Min -6' % gs),
        ("lighting: direct sun",   'SetConstraint %s Lighting DirectSun' % sat),
        ("ground track lighting",  'SetConstraint %s LightingSunElevAngle Min 5' % gs),
        ("elevation min (known)",  'SetConstraint %s ElevationAngle Min 5' % gs),
    ]:
        ok = session.cmd(command, fatal=False)
        print("    %-28s %s" % (name, "ACCEPTED" if ok else "rejected"))
        results["constraint:" + name] = ok

    # ---- 2. area targets ------------------------------------------------
    rule("2. AREA TARGETS (region of interest)")
    session.cmd("New / */AreaTarget ProbeROI", fatal=False)
    at = "*/AreaTarget/ProbeROI"
    results["areatarget"] = bool(try_all(session, "area target boundary", [
        ("AreaType Pattern + points",
         'AreaTarget %s AreaType Pattern 38 23 39 23 39 24 38 24' % at),
        ("Ellipse form",
         'AreaTarget %s AreaType Ellipse 38.5 23.6 200 100 0' % at),
        ("SetAreaType Pattern",
         'SetAreaType %s Pattern 38 23 39 23 39 24 38 24' % at),
    ]))

    # ---- 3. coverage definition + figure of merit ------------------------
    rule("3. COVERAGE DEFINITION AND FIGURE OF MERIT (revisit statistics)")
    session.cmd("New / */CoverageDefinition ProbeCov", fatal=False)
    cov = "*/CoverageDefinition/ProbeCov"
    results["coverage"] = bool(try_all(session, "coverage grid + assets", [
        ("Cover grid latitude bounds",
         'Cover %s Grid Bounds Latitude 30 45' % cov),
        ("Cover grid resolution",
         'Cover %s Grid Resolution LatLon 2.0' % cov),
        ("assign asset",
         'Cover %s Asset Add %s' % (cov, sat)),
        ("compute accesses",
         'Cover %s Access Compute' % cov),
    ]))
    session.cmd("New / %s/FigureOfMerit ProbeFOM" % cov, fatal=False)
    fom = "%s/FigureOfMerit/ProbeFOM" % cov
    results["fom"] = bool(try_all(session, "figure of merit", [
        ("FOM revisit time", 'FOM %s Define RevisitTime' % fom),
        ("FOM definition set", 'SetFOM %s RevisitTime' % fom),
    ]))

    # ---- 4. chains -------------------------------------------------------
    rule("4. CHAINS (satellite -> relay -> ground as one object)")
    session.cmd("New / */Chain ProbeChain", fatal=False)
    chain = "*/Chain/ProbeChain"
    results["chain"] = bool(try_all(session, "chain members", [
        ("Chain Add objects", 'Chain %s Add %s' % (chain, sat)),
        ("ChainAdd", 'ChainAdd %s %s' % (chain, gs)),
        ("Chain Compute", 'Chain %s Compute' % chain),
    ]))

    # ---- 5. satellite attitude ------------------------------------------
    rule("5. SATELLITE ATTITUDE PROFILES (bus pointing, not sensor pointing)")
    results["attitude"] = bool(try_all(session, "attitude profile", [
        ("SetAttitude Profile Nadir", 'SetAttitude %s Profile NadirAlignmentWithECIVelocityConstraint' % sat),
        ("SetAttitude Profile Nadir alt", 'SetAttitude %s Profile Nadir(CentralBody)' % sat),
        ("Attitude Profile", 'Attitude %s Profile NadirAlignedWithECIVelocity' % sat),
    ]))

    # ---- 6. things we believe already work -------------------------------
    rule("6. ALREADY AVAILABLE VIA export_report -- confirming")
    for style in ("Eclipse Times", "Beta Angle", "Lighting Times", "Access"):
        try:
            path = session.export_report(sat, style, time_step_sec=600)
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            print("    %-22s %s (%d bytes)" % (style, "works", size))
            results["report:" + style] = size > 0
        except Exception as exc:
            print("    %-22s failed: %s" % (style, str(exc).splitlines()[0][:60]))
            results["report:" + style] = False

    # ---- summary ---------------------------------------------------------
    rule("SUMMARY -- what is actually reachable on this STK")
    for key in sorted(results):
        print("  %-38s %s" % (key, "yes" if results[key] else "NO"))
    print("\nAnything marked NO needs real work, not just a wrapper.")
    print("Anything marked yes needs only a tested helper around it.")

    session.close_scenario()
    print("\n(probe scenario closed without saving)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
