"""step_runner.py -- run ONE agent step against STK, in its own process.

Started by stk_link.py. A separate process is the only way to enforce a real
timeout on a hung COM call, and it keeps a crashing step away from the app.

    python step_runner.py <policy.json> <code.py> <result.json>

policy.json:
    {"mode": "probe"}
        attach, report version, open scenario and licensed modules
    {"mode": "run", "scenario": "Name", "allow_open": false,
     "period": ["1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"],
     "data_dir": "..."}
        run the code inside scenario "Name". If no scenario is open it is
        created. If ANOTHER scenario is open the step is refused with status
        "scenario_conflict" unless allow_open is true: the user's own work
        is never touched without their explicit choice.

The step's code runs with: stk, session, root, result (dict), and
save_data(name, data) which stores a dataset for charts WITHOUT sending the
numbers through the model.
"""

import contextlib
import io
import json
import os
import re
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
HELPERS = os.path.dirname(HERE)
sys.path.insert(0, HELPERS)


def _licenses(root):
    """[(product, description, licensed)] from STK's licensing report.

    Only product names leave this function -- the report also holds host IDs
    and file paths, which are nobody's business but the user's.
    """
    text = str(root.GetLicensingReport())
    out = []
    for block in re.findall(r"<LicenseStatus (.*?)</LicenseStatus>", text, re.S):
        prod = re.search(r'Product = "([^"]+)"', block)
        status = re.search(r'Status = "([^"]+)"', block)
        desc = re.search(r'Name = "productDescription" >\s*(.*?)\s*<', block)
        if prod:
            out.append((prod.group(1), desc.group(1) if desc else prod.group(1),
                        bool(status and status.group(1) == "1")))
    return out


def _version(root):
    try:
        return str(root.ExecuteCommand("GetSTKVersion /").Item(0)).strip()
    except Exception:
        return "unknown"


def _to_jsonable(data):
    """DataResult / dict / list -> {"columns": {...}} for the chart store."""
    cols = getattr(data, "columns", None)
    if isinstance(cols, dict):
        payload = {"columns": cols, "message": getattr(data, "message", ""),
                   "intervals": getattr(data, "intervals", None)}
    elif isinstance(data, dict):
        payload = {"columns": data}
    else:
        raise TypeError("save_data wants a DataResult or {column: [values]}")
    for name, values in payload["columns"].items():
        payload["columns"][name] = list(values)
    return payload


def main(policy_path, code_path, result_path):
    started = time.time()
    record = {"status": "error", "stdout": "", "error": None, "result": {},
              "datasets": {}, "seconds": None}
    buffer = io.StringIO()
    try:
        with open(policy_path, "r", encoding="utf-8") as handle:
            policy = json.load(handle)
        import stk_helpers as stk
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            try:
                session = stk.StkSession.attach()
            except Exception as exc:
                record["status"] = "no_stk"
                record["error"] = str(exc)
                return
            root = session.root
            if policy.get("mode") == "probe":
                record["result"] = {"version": _version(root),
                                    "scenario": session.scenario_name,
                                    "licenses": _licenses(root)}
                record["status"] = "ok"
                return

            wanted = policy["scenario"]
            current = session.scenario_name
            if current is not None and current != wanted and not policy.get("allow_open"):
                record["status"] = "scenario_conflict"
                record["error"] = ("Scenario '%s' is open; this mission works in '%s'. "
                                   "The user must choose: work in '%s', or save and "
                                   "close it." % (current, wanted, current))
                record["result"] = {"open_scenario": current}
                return
            if current is None:
                session.ensure_scenario(wanted)
                period = policy.get("period")
                if period:
                    session.set_time_period(period[0], period[1])
            else:
                scenario = root.CurrentScenario
                session._start = str(scenario.StartTime)
                session._stop = str(scenario.StopTime)
            session.set_units()

            data_dir = policy.get("data_dir") or HERE
            os.makedirs(data_dir, exist_ok=True)
            # files the agent exports (CSV reports...) go to the mission's own
            # outputs folder, not beside this runner
            session._default_out = os.path.join(os.path.dirname(data_dir), "outputs")

            def save_data(name, data):
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))[:60]
                payload = _to_jsonable(data)
                with open(os.path.join(data_dir, safe + ".json"), "w",
                          encoding="utf-8") as handle:
                    json.dump(payload, handle, default=str)
                rows = max((len(v) for v in payload["columns"].values()), default=0)
                record["datasets"][safe] = {"columns": list(payload["columns"]),
                                            "rows": rows}
                print("[data] saved '%s': %d rows, columns %s"
                      % (safe, rows, list(payload["columns"])))
                return safe

            with open(code_path, "r", encoding="utf-8") as handle:
                source = handle.read()
            namespace = {"stk": stk, "session": session, "root": root,
                         "result": {}, "save_data": save_data,
                         "__name__": "__agent_step__", "__file__": code_path}
            exec(compile(source, "<step>", "exec"), namespace)
            record["result"] = namespace.get("result", {})
            record["unapplied_constraints"] = list(
                getattr(session, "unapplied_constraints", []))
            record["status"] = "ok"
    except Exception:
        record["error"] = traceback.format_exc()
        # STK died mid-step (closed or crashed): COM reports the server gone.
        # Same status as "could not attach", so the run stops at once.
        if any(k in record["error"] for k in (
                "-2147023174", "-2147023170", "-2147417848",
                "RPC server is unavailable", "remote procedure call failed",
                "object invoked has disconnected")):
            record["status"] = "no_stk"
    finally:
        record["stdout"] = buffer.getvalue()
        record["seconds"] = round(time.time() - started, 3)
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, default=repr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
