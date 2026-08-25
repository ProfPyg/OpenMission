"""
probe_report_styles.py -- find the report and graph style names STK accepts.

The first probe showed that ReportCreate itself works (the Access style
opened a window, LLA Position exported a file) but "Classical Elements" was
rejected. So the command syntax is fine and the STYLE NAMES are the unknown.

STK stores styles as files on disk, so rather than guess we enumerate them
and then try the ones that look useful. Graph styles are stored the same way,
which should also settle how a graph window is opened.

Throwaway scenario, discarded at the end. Requires STK open, no scenario.
"""
import os, sys, collections, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

START, STOP = "1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stk_output")


def rule(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


def find_styles(home):
    """Collect style files by extension, grouped by their parent folder."""
    found = collections.defaultdict(list)
    for root, _dirs, files in os.walk(home):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in (".rst", ".gst", ".sty"):
                # the folder name is usually the object type the style suits
                found[(os.path.basename(root), ext)].append(os.path.splitext(name)[0])
    return found


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("ReportStyleProbe")
    session.set_time_period(START, STOP)
    os.makedirs(OUT, exist_ok=True)

    try:
        home = session.directory("STKHome")
        rule("1. STYLE FILES ON DISK  (STKHome: %s)" % home)
        styles = find_styles(home) if home else {}
        satellite_styles = []
        for (folder, ext), names in sorted(styles.items()):
            if folder.lower() in ("satellite", "vehicle", "sensor", "facility", "place"):
                print("\n  %s  (%s)  %d styles" % (folder, ext, len(names)))
                for n in sorted(names)[:30]:
                    print("      %s" % n)
                if folder.lower() == "satellite":
                    satellite_styles.extend((n, ext) for n in names)
        if not styles:
            print("  no style files found under STKHome")
        else:
            print("\n  style folders seen: %s"
                  % ", ".join(sorted({f for f, _ in styles})[:20]))

        # ---- 2. try the satellite styles -------------------------------
        rule("2. WHICH SATELLITE STYLES DOES ReportCreate ACCEPT?")
        sat = session.new_satellite("StyleSat", sma_km=stk.altitude_to_sma(550.0),
                                    inc_deg=97.593, raan_deg=0.0)
        candidates = [n for n, _e in satellite_styles] or [
            "Classical Orbit Elements", "Classical Elements", "LLA Position",
            "Cartesian Position", "AER", "Access", "Beta Angle", "Lighting"]
        seen, tried = set(), 0
        for name in candidates:
            if name in seen or tried >= 14:
                continue
            seen.add(name)
            tried += 1
            path = os.path.join(OUT, "style_%s.csv" % name.replace(" ", "_")[:32])
            ok = session.cmd('ReportCreate %s Type Export Style "%s" File "%s" TimeStep 600'
                             % (sat, name, path), fatal=False)
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            print("  %-38s %-9s %s" % (name, "ACCEPTED" if ok else "rejected",
                                       ("%d bytes" % size) if size else ""))

        # ---- 3. graph windows ------------------------------------------
        rule("3. HOW IS A GRAPH WINDOW OPENED?")
        graph_names = [n for n, e in satellite_styles if e == ".gst"][:3]
        print("  graph-style files found: %s" % (graph_names or "none"))
        attempts = [
            'ReportCreate %s Type Display Style "%s"',
            'ReportCreate %s Type Graph Style "%s"',
            'GraphCreate %s Type Display Style "%s"',
            'Graph %s Style "%s"',
        ]
        probe_style = graph_names[0] if graph_names else "LLA Position"
        for template in attempts:
            command = template % (sat, probe_style)
            ok = session.cmd(command, fatal=False)
            print("  %-58s %s" % (command.split(" Style")[0][:58],
                                  "ACCEPTED" if ok else "rejected"))

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
