"""
compare_access_to_stk.py -- check our access numbers against STK's own report.

Everything we report about access comes from compute_access(), which reads
ComputedAccessIntervalTimes off the COM object and sums the intervals
ourselves. That is our arithmetic on STK's data. This script asks STK to
produce its OWN access report for the same pairs, parses it, and compares.

A mismatch means one of:
  * we are summing intervals wrongly (double-counting, missing the last one)
  * the constraint set differs between what we compute and what STK reports
  * time-unit or epoch handling differs

Run it with the scenario ALREADY BUILT and open -- it does not create
anything, it only reports on what is there.

    python compare_access_to_stk.py            # sample of 6 pairs
    python compare_access_to_stk.py --all      # every satellite/station pair
"""
import csv
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

SAMPLE_PAIRS = 6


def children_of_type(session, type_name):
    """Object paths whose OWN class is type_name, in the open scenario.

    Matching on "/Satellite/" anywhere in the path is wrong: it also matches
    */Satellite/EO1/Sensor/Pushbroom, so sensors get compared as if they were
    satellites and STK declines to produce a satellite Access report for
    them. The class is the second-to-last path element.
    """
    try:
        result = session.root.ExecuteCommand("AllInstanceNames /")
        names = str(result.Item(0)).split()
    except Exception:
        return []
    matched = []
    for name in names:
        parts = [p for p in name.split("/") if p and p != "*"]
        if len(parts) >= 2 and parts[-2].lower() == type_name.lower():
            matched.append(name)
    return matched


def parse_stk_access_report(path):
    """Return (interval_count, total_seconds) from an exported access report.

    STK writes a header block before the table and a summary after it, so we
    look for the row that names the columns and read until the table ends.
    """
    if not os.path.isfile(path):
        return None, None
    with open(path, "r", errors="replace") as handle:
        rows = list(csv.reader(handle))

    header_index = None
    for index, row in enumerate(rows):
        lowered = [c.strip().lower() for c in row]
        if any(c.startswith("duration") for c in lowered) and len(row) >= 3:
            header_index = index
            break
    if header_index is None:
        return None, None

    header = [c.strip().lower() for c in rows[header_index]]
    duration_col = next(i for i, c in enumerate(header) if c.startswith("duration"))

    count, total = 0, 0.0
    for row in rows[header_index + 1:]:
        if len(row) <= duration_col:
            continue
        cell = row[duration_col].strip()
        if not cell:
            continue
        try:
            total += float(cell)
            count += 1
        except ValueError:
            continue          # summary lines and blanks
    return count, total


def main():
    show_all = "--all" in sys.argv
    session = stk.StkSession.attach()
    scenario = session.scenario_name
    if scenario is None:
        print("No scenario open. Build one first, then re-run.")
        return 2
    print("Comparing access in scenario: %s\n" % scenario)

    satellites = children_of_type(session, "Satellite")
    facilities = children_of_type(session, "Facility")
    if not satellites or not facilities:
        print("Need at least one satellite and one facility; found %d and %d."
              % (len(satellites), len(facilities)))
        return 2

    pairs = [(s, f) for f in facilities for s in satellites]
    if not show_all:
        # spread the sample across stations -- taking the first N would only
        # ever exercise one station's geometry
        step = max(1, len(pairs) // SAMPLE_PAIRS)
        pairs = pairs[::step][:SAMPLE_PAIRS]
        print("(sampling %d of %d pairs -- pass --all for every pair)\n"
              % (len(pairs), len(satellites) * len(facilities)))

    out_dir = session.output_dir()
    print("%-14s %-14s %8s %12s %8s %12s %10s"
          % ("satellite", "station", "ours n", "ours secs", "STK n", "STK secs", "diff"))
    print("-" * 84)

    worst = 0.0
    mismatches = 0
    for sat_path, gs_path in pairs:
        sat_name = sat_path.rsplit("/", 1)[-1]
        gs_name = gs_path.rsplit("/", 1)[-1]

        try:
            ours_n, ours_s, _ = session.compute_access(sat_path, gs_path)
        except stk.StkError as exc:
            print("%-14s %-14s  our access failed: %s" % (sat_name, gs_name, exc))
            continue

        report = os.path.join(out_dir, "stkaccess_%s_%s.csv" % (sat_name, gs_name))
        ok = session.cmd(
            'ReportCreate %s Type Export Style "Access" AccessObject %s File "%s"'
            % (sat_path, gs_path, report), fatal=False)
        stk_n, stk_s = parse_stk_access_report(report) if ok else (None, None)

        if stk_n is None:
            print("%-14s %-14s %8d %12.1f %8s %12s %10s"
                  % (sat_name, gs_name, ours_n, ours_s, "-", "-", "no report"))
            continue

        diff = ours_s - stk_s
        worst = max(worst, abs(diff))
        flag = ""
        if ours_n != stk_n or abs(diff) > 1.0:
            flag = "  <-- MISMATCH"
            mismatches += 1
        print("%-14s %-14s %8d %12.1f %8d %12.1f %10.2f%s"
              % (sat_name, gs_name, ours_n, ours_s, stk_n, stk_s, diff, flag))

    print("-" * 84)
    if mismatches:
        print("%d pair(s) disagree; largest difference %.2f s." % (mismatches, worst))
        print("Our numbers come from summing ComputedAccessIntervalTimes; STK's")
        print("come from its own Access report. Investigate before trusting either.")
    else:
        print("All sampled pairs agree (largest difference %.3f s)." % worst)
    return 1 if mismatches else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
