"""runner.py -- execute ONE bridge job against STK, in its own process.

Started by watcher.py, never by hand. Running each job in a separate process
is what lets the watcher enforce a real timeout (a hung COM call cannot be
interrupted from a thread) and keeps one crashing job from taking the
watcher down with it.

The job is plain Python. It runs with these names already defined:

    stk        the stk_helpers module
    session    an attached StkSession, already inside the sandbox scenario
    root       session.root, the STK object-model root
    result     a dict -- put anything structured here; it is saved as JSON

Everything printed goes into the result file too.
"""

import contextlib
import io
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
HELPERS = os.path.dirname(HERE)
sys.path.insert(0, HELPERS)

SANDBOX = "AgentSandbox"


class _Tee(io.StringIO):
    """A StringIO that also appends every write to a file, flushed at once."""

    def __init__(self, path):
        super().__init__()
        self._handle = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, text):
        self._handle.write(text)
        self._handle.flush()
        return super().write(text)


def main(job_path, result_path):
    started = time.time()
    record = {"job": os.path.basename(job_path), "status": "error",
              "stdout": "", "error": None, "result": {}, "seconds": None}
    # Progress goes to disk line by line, not only at the end: a job killed
    # at the timeout otherwise leaves no trace of how far it got.
    partial_path = os.path.splitext(result_path)[0] + ".partial.txt"
    buffer = _Tee(partial_path)
    try:
        with open(job_path, "r", encoding="utf-8") as handle:
            source = handle.read()

        import stk_helpers as stk

        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            session = stk.StkSession.attach()
            current = session.scenario_name
            # Never touch the user's own work. A job only ever runs inside
            # the sandbox scenario; any other open scenario stops it cold.
            if current is not None and current != SANDBOX:
                raise RuntimeError(
                    "scenario '%s' is open -- the bridge only runs inside "
                    "'%s'. Close your scenario to let jobs run." % (current, SANDBOX))
            if current is None:
                session.ensure_scenario(SANDBOX)
                session.set_time_period("1 Jul 2026 00:00:00.000",
                                        "2 Jul 2026 00:00:00.000")
            else:
                # Each job is a fresh process, so the session knows nothing
                # about a sandbox that already exists. Read its period back
                # from STK instead of assuming it.
                scenario = session.root.CurrentScenario
                session._start = str(scenario.StartTime)
                session._stop = str(scenario.StopTime)
            session.set_units()

            namespace = {"stk": stk, "session": session, "root": session.root,
                         "result": {}, "__name__": "__bridge_job__",
                         "__file__": job_path}
            exec(compile(source, job_path, "exec"), namespace)
            record["result"] = namespace.get("result", {})
        record["status"] = "ok"
    except Exception:
        record["error"] = traceback.format_exc()
    finally:
        record["stdout"] = buffer.getvalue()
        record["seconds"] = round(time.time() - started, 3)
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, default=repr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
