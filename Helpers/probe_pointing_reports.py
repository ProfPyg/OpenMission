"""
probe_pointing_reports.py -- answer three questions by asking STK, not guessing.

  1. What are a satellite's ACTUAL orbital elements after new_satellite?
     (The Orbit Wizard shows its own defaults, not the current state, so it
     cannot answer this.)
  2. Which AzEl elevation gives true NADIR pointing? Every access we compute
     is satellite-to-facility, which ignores sensor pointing entirely -- so a
     sensor aimed at deep space has been indistinguishable from a correct one.
     Here we compute SENSOR-to-facility access, which does depend on it.
  3. How are STK's own reports and graphs created on this build?

Throwaway scenario, discarded at the end. Requires STK open, no scenario.
"""
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

START, STOP = "1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"


def rule(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


def elements(session, path):
    """Read back real classical elements via the data provider."""
    for group in ("J2000", "ICRF", "Inertial"):
        try:
            provider = session.obj(path).DataProviders.Item("Classical Elements")
            g = provider.Group.Item(group) if hasattr(provider, "Group") else provider
            r = g.Exec(START, START, 60.0)
            if r.DataSets.Count == 0:
                continue
            out = {}
            for name in ("Semi-major Axis", "Eccentricity", "Inclination",
                         "RAAN", "Arg of Perigee", "True Anomaly"):
                try:
                    out[name] = float(r.DataSets.GetDataSetByName(name).GetValues()[0])
                except Exception:
                    pass
            if out:
                return group, out
        except Exception:
            continue
    return None, {}


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("PointingReportProbe")
    session.set_time_period(START, STOP)

    try:
        # ---- 1. what did we actually build? ----------------------------
        rule("1. ACTUAL ORBITAL ELEMENTS (what the Orbit Wizard cannot show you)")
        sat = session.new_satellite("ProbeSat", sma_km=stk.altitude_to_sma(550.0),
                                    inc_deg=97.593, raan_deg=257.5)
        group, els = elements(session, sat)
        print("  read from the '%s' group:" % group)
        for k, v in els.items():
            print("    %-18s %12.4f" % (k, v))
        print("  asked for: sma 6928.137 km, inc 97.593 deg, raan 257.5 deg")

        # ---- 2. which elevation is nadir? ------------------------------
        rule("2. NADIR CONVENTION -- measured by sensor-to-ground access")
        gs = session.new_facility("ProbeGS", 0.0, 0.0, 0.0)
        sat_count, sat_secs, _ = session.compute_access(sat, gs)
        print("  satellite -> facility (pointing-independent): %d passes, %.0f s"
              % (sat_count, sat_secs))
        print("  a correctly-pointed nadir sensor should be in the same ballpark;")
        print("  an anti-nadir sensor should be ZERO.\n")

        for elev in (-90.0, 90.0):
            name = "Cam%s" % ("Neg90" if elev < 0 else "Pos90")
            cam = session.add_sensor_conic(sat, name, 45.0)
            session.cmd("Point %s Fixed AzEl 0.0 %.1f Rotate" % (cam, elev),
                        label="probe pointing", fatal=False)
            count, secs, _ = session.compute_access(cam, gs)
            verdict = "NADIR (sees ground)" if count else "anti-nadir (sees space)"
            print("    elevation %+6.1f deg -> %3d passes, %8.0f s   %s"
                  % (elev, count, secs, verdict))

        # ---- 3. reports and graphs -------------------------------------
        rule("3. REPORTS AND GRAPHS -- which Connect forms work?")
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stk_output")
        os.makedirs(out_dir, exist_ok=True)
        report_file = os.path.join(out_dir, "probe_report.csv")

        forms = [
            ("report window (Display)",
             'ReportCreate %s Type Display Style "Classical Elements"' % sat),
            ("report to file (Export)",
             'ReportCreate %s Type Export Style "Classical Elements" File "%s"'
             % (sat, report_file)),
            ("report with time step",
             'ReportCreate %s Type Export Style "LLA Position" File "%s" '
             'TimeStep 300' % (sat, report_file.replace(".csv", "_lla.csv"))),
            ("graph window (GraphCreate)",
             'GraphCreate %s Type Display Style "Classical Elements"' % sat),
            ("graph window (ReportCreate Graph)",
             'ReportCreate %s Type Graph Style "Classical Elements"' % sat),
            ("access report window",
             'ReportCreate %s Type Display Style "Access" AccessObject %s' % (sat, gs)),
        ]
        for label, command in forms:
            ok = session.cmd(command, fatal=False)
            print("  %-28s %s" % (label, "ACCEPTED" if ok else "rejected"))
        for produced in ("probe_report.csv", "probe_report_lla.csv"):
            p = os.path.join(out_dir, produced)
            print("  file %-22s %s" % (produced,
                  ("written, %d bytes" % os.path.getsize(p)) if os.path.isfile(p) else "not created"))

        # what report styles exist at all?
        try:
            result = session.root.ExecuteCommand('ReportStyles */Satellite/ProbeSat')
            styles = [str(result.Item(i)) for i in range(min(result.Count, 25))]
            print("\n  available report styles (first %d):" % len(styles))
            for s in styles:
                print("    %s" % s)
        except Exception as exc:
            print("\n  ReportStyles query failed: %s" % session._com_reason(exc)
                  if hasattr(session, "_com_reason") else exc)

    finally:
        session.close_scenario()
        print("\n(probe scenario closed without saving)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
