"""check_trade_study.py -- direct STK check of the trade_study helper (no API cost).

    python tests\\check_trade_study.py

STK open, no scenario of your own. Builds scenario Bench_trade (2 days),
searches 9 Walker designs over a Europe box and checks the physics:
more satellites never make the worst revisit worse at equal planes,
every candidate is evaluated, and a Pareto front comes out.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Helpers"))
import stk_helpers as stk  # noqa: E402

s = stk.StkSession.attach()
if s.scenario_name and s.scenario_name != "Bench_trade":
    sys.exit("Close your scenario first (%s is open)." % s.scenario_name)
if s.scenario_name is None:
    s.ensure_scenario("Bench_trade")
s.set_time_period("1 Jul 2026 00:00:00.000", "3 Jul 2026 00:00:00.000")
s.set_units()

cands = stk.StkSession.candidate_grid(altitudes_km=[550], total_sats=[4, 6, 8, 12],
                                      planes=[2, 4, 6], half_angles_deg=[30])
print("%d candidates" % len(cands))
state = os.path.join(ROOT, "tests", "results", "trade_check_state.json")
if os.path.exists(state):
    os.remove(state)
t0 = time.time()
while True:
    out = s.trade_study("Europe", cands, box=(35, 70, -10, 30), resolution_deg=3.0,
                        budget_s=140, state_file=state)
    print("... %d evaluated, %d remaining" % (out["evaluated"], out["remaining"]))
    if out["done"]:
        break
print("\ntook %.1f min" % ((time.time() - t0) / 60))
print("%-36s %8s %10s %8s" % ("candidate (sats/planes/f)", "cover%", "worst h", "cost"))
for r in out["results"]:
    c = r["candidate"]
    print("%-36s %8s %10s %8s %s" % ("%d sats / %d planes / f=%d" % (c["total_sats"], c["planes"], c["phase_f"]),
          r.get("coverage_pct"), r.get("worst_revisit_h"), r.get("cost"), r.get("error", "")))
print("\nPareto front (cost -> worst revisit):")
for r in out["pareto"]:
    print("   cost %-5s %d sats/%d planes -> %.2f h" % (r["cost"], r["candidate"]["total_sats"],
          r["candidate"]["planes"], r["worst_revisit_h"]))
errors = [r for r in out["results"] if "error" in r]
print("\nCHECKS:")
print("  all candidates evaluated:", "OK" if out["evaluated"] == len(cands) and not errors else "FAIL")
print("  pareto front not empty:  ", "OK" if out["pareto"] else "FAIL")
by = {}
for r in out["results"]:
    if r.get("worst_revisit_h") is not None:
        by.setdefault(r["candidate"]["planes"], []).append((r["candidate"]["total_sats"], r["worst_revisit_h"]))
mono = all(all(b[1] <= a[1] + 0.05 for a, b in zip(sorted(v), sorted(v)[1:])) for v in by.values())
print("  more sats never worse (same planes):", "OK" if mono else "CHECK " + str(by))
