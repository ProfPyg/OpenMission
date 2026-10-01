"""run_benchmark.py -- run the standard missions headless and score them.

    python tests/run_benchmark.py                 # all missions
    python tests/run_benchmark.py deorbit lunar_shackleton

Needs STK open with NO scenario of your own (each mission gets a fresh
scenario, closed afterwards) and ANTHROPIC_API_KEY set. The plan is approved
automatically; questions are answered with "use your recommended defaults".
Writes tests/results/<timestamp>.json with scores, tokens and cost.
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "Helpers"))

import anthropic                                   # noqa: E402
from api_reference import generate                 # noqa: E402
from agent.loop import Agent, DEFAULT_MODEL       # noqa: E402
from agent.stk_link import StkLink, module_summary  # noqa: E402
from agent.tools import ToolContext                # noqa: E402

PERIOD = ["1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"]
WORK = os.path.join(ROOT, "missions", "_steps")


def system_prompt(probe, scenario):
    text = open(os.path.join(ROOT, "prompts", "stk_agent_live.md"), encoding="utf-8").read()
    have, missing = module_summary(probe.get("licenses"))
    for k, v in {"{{STK_VERSION}}": probe.get("version", "STK"), "{{SCENARIO}}": scenario,
                 "{{PERIOD}}": " to ".join(PERIOD), "{{LICENSED}}": ", ".join(have) or "unknown",
                 "{{UNLICENSED}}": ", ".join(missing) or "none", "{{PROFILE}}": "(none)",
                 "{{HELPER_API}}": generate(os.path.join(ROOT, "Helpers", "stk_helpers.py"))}.items():
        text = text.replace(k, v)
    return text


def first_number(text):
    m = re.search(r"-?\d[\d,]*\.?\d*", str(text))
    return float(m.group(0).replace(",", "")) if m else None


def score(mission, findings):
    results = []
    metrics = (findings or {}).get("metrics") or []
    for chk in mission.get("checks", []):
        words = [w.lower() for w in chk["match"]]
        hit = next((m for m in metrics if all(w in " ".join(str(m.get(k, "")) for k in
                    ("name", "group", "note", "unit")).lower() for w in words)), None)
        val = first_number(hit["value"]) if hit else None
        if val is None:
            ok = False
        elif "expect" in chk:
            ok = abs(val - chk["expect"]) <= chk["tol"]
        else:
            ok = val >= chk.get("min", float("-inf"))
        results.append({"check": chk, "metric": hit, "value": val, "ok": ok})
    if mission.get("expect_issue"):
        issues = " ".join(i.get("what", "") + " " + i.get("why", "")
                          for i in (findings or {}).get("issues") or []).lower()
        results.append({"check": {"issue": mission["expect_issue"]},
                        "ok": mission["expect_issue"].lower() in issues})
    return results


def run_one(mission, client, link, probe, model):
    scenario = "Bench_" + mission["name"][:20]
    data_dir = os.path.join(ROOT, "missions", "_bench", mission["name"], "data")
    os.makedirs(data_dir, exist_ok=True)
    # close the previous benchmark scenario only -- never anything else
    link.run("sc = root.CurrentScenario\n"
             "if sc is not None and str(sc.InstanceName).startswith('Bench_'):\n"
             "    root.CloseScenario()", scenario, allow_open=True)
    ctx = ToolContext(link, scenario, data_dir, allow_open=False, period=PERIOD)
    agent = Agent(client, system_prompt(probe, scenario), ctx, model=model)
    agent.add_user(mission["prompt"])
    log = []
    started = time.time()
    for _ in range(4):
        agent.run(lambda k, d: log.append(k) if k in ("error",) else None)
        if not agent.waiting_for_user:
            break
        agent.ctx.pending_confirmation = None
        agent.add_user("Yes, proceed with the plan." if "request_confirmation" in json.dumps(agent.messages[-1])
                       else "Use your recommended defaults and proceed.")
    checks = score(mission, ctx.findings)
    return {"name": mission["name"], "passed": all(c["ok"] for c in checks), "checks": checks,
            "findings": ctx.findings, "usd": round(agent.meter.total_usd, 4),
            "tokens": agent.meter.total_tokens, "calls": len(agent.meter.calls),
            "minutes": round((time.time() - started) / 60, 1), "errors": log.count("error")}


def main(names):
    spec = json.load(open(os.path.join(ROOT, "tests", "benchmark.json"), encoding="utf-8"))
    chosen = [m for m in spec["missions"] if not names or m["name"] in names]
    link = StkLink(WORK)
    probe = link.probe()
    if probe.get("status") != "ok":
        sys.exit("STK not reachable: %s" % probe.get("error"))
    if probe.get("scenario") and not str(probe["scenario"]).startswith("Bench_"):
        sys.exit("Scenario '%s' is open. Save and close it first: the benchmark opens and "
                 "closes its own scenarios." % probe["scenario"])
    client = anthropic.Anthropic()
    model = os.environ.get("PRONOIA_MODEL", DEFAULT_MODEL)
    out = []
    for m in chosen:
        print("== %s ..." % m["name"], flush=True)
        r = run_one(m, client, link, probe, model)
        out.append(r)
        print("   %s  $%.3f  %d calls  %.1f min" % ("PASS" if r["passed"] else "FAIL",
              r["usd"], r["calls"], r["minutes"]), flush=True)
        for c in r["checks"]:
            print("     %s %s -> %s" % ("ok " if c["ok"] else "BAD", c["check"], c.get("value")), flush=True)
    os.makedirs(os.path.join(ROOT, "tests", "results"), exist_ok=True)
    path = os.path.join(ROOT, "tests", "results", time.strftime("%Y%m%d_%H%M%S") + ".json")
    json.dump({"model": model, "results": out}, open(path, "w", encoding="utf-8"), indent=1, default=str)
    print("\n%d/%d passed · $%.2f total · saved %s" % (sum(r["passed"] for r in out), len(out),
          sum(r["usd"] for r in out), path))


if __name__ == "__main__":
    main(sys.argv[1:])
