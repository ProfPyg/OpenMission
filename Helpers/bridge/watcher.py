"""watcher.py -- the bridge between the agent and STK.

Run once, on the Windows machine, in the project venv, with STK open:

    cd Helpers\bridge
    python watcher.py

It watches jobs/ for Python files, runs each one against STK in a separate
process (runner.py), writes the outcome to results/<job>.json, and moves the
job to done/. The agent writes jobs and reads results through the shared
folder -- nobody pastes anything.

Rails, because this runs code the agent wrote on your machine:

  * only files in jobs/ are ever executed
  * every job runs inside the 'AgentSandbox' scenario; if you have any other
    scenario open, jobs refuse to run rather than touch your work
  * each job has a hard timeout (default 180 s) and is killed if it overruns
  * everything is appended to log.jsonl -- what ran, when, and what happened
  * create a file named STOP in this folder and the watcher exits

A heartbeat file is rewritten every few seconds so the agent can tell the
watcher is alive.
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = os.path.join(HERE, "jobs")
RESULTS = os.path.join(HERE, "results")
DONE = os.path.join(HERE, "done")
LOG = os.path.join(HERE, "log.jsonl")
STOP = os.path.join(HERE, "STOP")
HEARTBEAT = os.path.join(HERE, "heartbeat.json")
RUNNER = os.path.join(HERE, "runner.py")

POLL_SECONDS = 1.0
JOB_TIMEOUT = 180


def log(entry):
    entry["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def heartbeat(state, job=None):
    with open(HEARTBEAT, "w", encoding="utf-8") as handle:
        json.dump({"state": state, "job": job, "pid": os.getpid(),
                   "at": time.time(),
                   "at_text": time.strftime("%Y-%m-%d %H:%M:%S")}, handle)


PING_JOB = os.path.join(HERE, "_ping_job.py")
PING_RESULT = os.path.join(HERE, "_ping_result.json")
PING_TIMEOUT = 20


def wait_until_stk_answers():
    """After a timeout, STK is usually still busy.

    Killing the runner stops only Python -- STK carries on with the call it
    was given (a link-margin access constraint once froze it for 10+
    minutes). Every job sent meanwhile would time out too, so hold the queue
    and ping until STK responds.
    """
    attempt = 0
    while True:
        attempt += 1
        heartbeat("stk-busy", "ping %d" % attempt)
        print("[busy] STK not answering -- ping %d" % attempt, flush=True)
        try:
            subprocess.run([sys.executable, RUNNER, PING_JOB, PING_RESULT],
                           timeout=PING_TIMEOUT, cwd=HERE)
            with open(PING_RESULT, "r", encoding="utf-8") as handle:
                if json.load(handle).get("status") == "ok":
                    print("[busy] STK answers again", flush=True)
                    log({"event": "stk recovered", "pings": attempt})
                    return
        except Exception:
            pass
        if os.path.exists(STOP):
            return
        time.sleep(PING_TIMEOUT)


def run_job(name):
    job_path = os.path.join(JOBS, name)
    stem = os.path.splitext(name)[0]
    result_path = os.path.join(RESULTS, stem + ".json")
    heartbeat("running", name)
    print("[run ] %s" % name, flush=True)
    started = time.time()
    try:
        subprocess.run([sys.executable, RUNNER, job_path, result_path],
                       timeout=JOB_TIMEOUT, cwd=HERE)
    except subprocess.TimeoutExpired:
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump({"job": name, "status": "timeout",
                       "error": "killed after %d s" % JOB_TIMEOUT,
                       "stdout": "", "result": {}}, handle, indent=2)

    status = "unknown"
    try:
        with open(result_path, "r", encoding="utf-8") as handle:
            status = json.load(handle).get("status", "unknown")
    except Exception:
        status = "no-result"
    seconds = round(time.time() - started, 1)
    print("[done] %s -> %s (%.1f s)" % (name, status, seconds), flush=True)
    log({"job": name, "status": status, "seconds": seconds})

    if status == "timeout":
        wait_until_stk_answers()

    destination = os.path.join(DONE, name)
    if os.path.exists(destination):
        destination = os.path.join(DONE, "%s_%d.py" % (stem, int(time.time())))
    os.replace(job_path, destination)


def main():
    for folder in (JOBS, RESULTS, DONE):
        os.makedirs(folder, exist_ok=True)
    if os.path.exists(STOP):
        os.remove(STOP)
    print("STK bridge watching %s" % JOBS)
    print("Create a file named STOP in %s to stop it.\n" % HERE, flush=True)
    log({"event": "watcher started", "python": sys.executable})

    while True:
        if os.path.exists(STOP):
            print("STOP file found -- exiting.")
            log({"event": "watcher stopped"})
            heartbeat("stopped")
            return 0
        pending = sorted(n for n in os.listdir(JOBS) if n.endswith(".py"))
        if pending:
            run_job(pending[0])
        else:
            heartbeat("idle")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log({"event": "watcher interrupted"})
        heartbeat("stopped")
