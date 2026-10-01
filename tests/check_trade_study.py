"""check_trade_study.py -- direct STK check of the trade_study helper (no API cost).

    python tests\\check_trade_study.py

STK open, no scenario of your own. Builds scenario Bench_trade (3 days),
searches Walker designs over a Europe box and checks the physics:
more satellites never make the worst revisit worse at equal planes,
every candidate is evaluated, and a Pareto front comes out.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Helpers"))
import stk_helpers as stk  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "eo"      # eo | comms
s = stk.StkSession.attach()
if s.scenario_name and s.scenario_name != "Bench_trade":
    sys.exit("Close your scenario first (%s is open)." % s.scenario_name)
if s.scenario_name is None:
    s.ensure_scenario("Bench_trade")
s.set_time_period("1 Jul 2026 00:00:00.000", "4 Jul 2026 00:00:00.000")
s.set_units()

rgt = stk.StkSession.repeat_ground_track_altitudes(500, 700, 3)
print("analytical repeat-ground-track SSO altitudes:", [(r["altitude_km"], "%d/%d" % (r["revs"], r["days"])) for r in rgt])
if MODE == "comms":
    cands = stk.StkSession.candidate_grid(altitudes_km=[1200], total_sats=[12, 18, 24],
                                          planes=[3, 6], inclinations_deg=[53, 70], phase_f=[1])
else:
    cands = stk.StkSession.candidate_grid(altitudes_km=[550, 650], total_sats=[4, 8, 12],
                                          planes=[2, 4], half_angles_deg=[30], phase_f=[0, 1])
print("%d candidates" % len(cands))
state = os.path.join(ROOT, "tests", "results", "trade_check_state_%s.json" % MODE)
if os.path.exists(state):
    os.remove(state)
t0 = time.time()
while True:
    out = s.trade_study("Europe", cands, box=(35, 70, -10, 30), resolution_deg=3.0,
                        budget_s=140, state_file=state, mission=MODE, min_elevation_deg=10)
    print("... %d evaluated, %d remaining" % (out["evaluated"], out["remaining"]))
    if out["done"]:
        break
print("\ntook %.1f min" % ((time.time() - t0) / 60))
print("%-9s %6s %7s %7s %7s %7s %6s %8s %8s %8s %6s" % ("walker", "alt", "inc", "dRAAN", "dM", "phase",
      "half", "cover%", "worst h", "mean h", "cost"))
for r in out["results"]:
    o = r.get("orbit", {})
    print("%-9s %6.0f %7.3f %7.1f %7.1f %7.1f %6.0f %8s %8s %8s %6s %s" % (
          o.get("walker"), o.get("altitude_km", 0), o.get("inclination_deg", 0), o.get("raan_spacing_deg", 0),
          o.get("in_plane_spacing_deg", 0), o.get("phase_offset_deg", 0), o.get("sensor_half_angle_deg") or 0,
          r.get("coverage_pct"), r.get("worst_revisit_h"), r.get("mean_revisit_h"), r.get("cost"),
          ("  worst-point service %s%%" % r.get("worst_point_time_pct")) if MODE == "comms" else "",
          ) + (" " + r["error"] if "error" in r else ""))
print("\nPareto front (cost -> score; eo: worst revisit h, comms: 100 - worst-point service %):")
for r in out["pareto"]:
    print("   cost %-5s %s at %.0f km i=%.2f -> score %.3f" % (r["cost"], r["orbit"]["walker"],
          r["orbit"]["altitude_km"], r["orbit"]["inclination_deg"], r["score"]))
errors = [r for r in out["results"] if "error" in r]
print("\nCHECKS:")
print("  all candidates evaluated:", "OK" if out["evaluated"] == len(cands) and not errors else "FAIL")
print("  pareto front not empty:  ", "OK" if out["pareto"] else "FAIL")
# physics check: the BEST design with more satellites is never worse than
# the best with fewer (individual designs may be -- phasing matters)
best = {}
for r in out["results"]:
    if r.get("score") is not None:
        n = r["candidate"]["total_sats"]
        best[n] = min(best.get(n, 1e9), r["score"])
ns = sorted(best)
mono = all(best[b] <= best[a] + 0.05 for a, b in zip(ns, ns[1:]))
print("  best design improves with more sats:", "OK" if mono else "CHECK " + str(best))
