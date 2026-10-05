"""stk_link.py -- the app's only door into STK.

Each step runs in a child process (step_runner.py) so a hung COM call can be
killed. Killing Python does NOT stop STK: after a timeout STK usually keeps
computing (a link-margin access constraint once froze it for 10+ minutes),
so the link then pings until STK answers before accepting another step.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import atexit
import weakref

_LIVE = weakref.WeakSet()


@atexit.register
def _kill_all():
    """Pronoia closing: never leave a step process behind."""
    for link in list(_LIVE):
        link.cancel()

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "step_runner.py")

#: Modules the agent must know about before planning. Product codes are the
#: ones STK 11.2's licensing report uses (measured, job 0128).
MODULE_NAMES = {
    "ASTG": "Astrogator (manoeuvres)",
    "COV": "Coverage",
    "Comm": "Communications (link budgets)",
    "AdvHPOP": "HPOP (high-precision propagation)",
    "CAT": "Conjunction Analysis",
    "CHAINS": "Chains",
    "CON": "Connect",
    "AMM": "Aviator",
}


class StkLink:
    def __init__(self, work_dir, python=None, step_timeout=180, ping_timeout=20):
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self.python = python or sys.executable
        self.step_timeout = step_timeout
        self.ping_timeout = ping_timeout
        self.cancelled = False
        self._proc = None
        _LIVE.add(self)

    def cancel(self):
        """Stop button / app closing: kill the running step at once."""
        self.cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # -- low level ------------------------------------------------------------
    def _spawn(self, policy, code, timeout):
        tag = uuid.uuid4().hex[:8]
        policy_path = os.path.join(self.work_dir, "policy_%s.json" % tag)
        code_path = os.path.join(self.work_dir, "step_%s.py" % tag)
        result_path = os.path.join(self.work_dir, "result_%s.json" % tag)
        with open(policy_path, "w", encoding="utf-8") as handle:
            json.dump(policy, handle)
        with open(code_path, "w", encoding="utf-8") as handle:
            handle.write(code or "")
        started = time.time()
        if self.cancelled:
            return {"status": "stopped", "seconds": 0, "error": "stopped by the user",
                    "stdout": "", "datasets": {}}
        self._proc = subprocess.Popen(
            [self.python, RUNNER, policy_path, code_path, result_path], cwd=HERE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while self._proc.poll() is None:
                if self.cancelled:
                    self._proc.kill()
                    self._proc.wait()
                    return {"status": "stopped", "seconds": round(time.time() - started, 1),
                            "error": "stopped by the user", "stdout": "", "datasets": {}}
                if time.time() - started > timeout:
                    self._proc.kill()
                    self._proc.wait()
                    return {"status": "timeout", "seconds": round(time.time() - started, 1),
                            "error": "step exceeded %d s and was stopped" % timeout,
                            "stdout": "", "datasets": {}}
                time.sleep(0.2)
        finally:
            self._proc = None
        try:
            with open(result_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except OSError:
            return {"status": "error", "error": "the step process died without a result",
                    "stdout": "", "datasets": {}}
        finally:
            for path in (policy_path, code_path, result_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # -- public ---------------------------------------------------------------
    def probe(self):
        """{"status": "ok"|"no_stk"|..., "version", "scenario", "licenses"}."""
        record = self._spawn({"mode": "probe"}, "", self.ping_timeout * 2)
        out = {"status": record.get("status"), "error": record.get("error")}
        out.update(record.get("result") or {})
        return out

    def wait_until_free(self, on_wait=None, give_up_s=1800):
        """Ping until STK answers. Returns True when free, False on give-up."""
        waited = 0.0
        attempt = 0
        while waited < give_up_s:
            if self.cancelled:
                return False
            attempt += 1
            if on_wait:
                on_wait(attempt, waited)
            record = self._spawn({"mode": "probe"}, "", self.ping_timeout)
            if record.get("status") == "ok":
                return True
            time.sleep(self.ping_timeout)
            waited += self.ping_timeout * 2
        return False

    def run(self, code, scenario, allow_open=False, period=None, data_dir=None,
            timeout=None, on_wait=None):
        policy = {"mode": "run", "scenario": scenario, "allow_open": bool(allow_open),
                  "period": period, "data_dir": data_dir}
        record = self._spawn(policy, code, timeout or self.step_timeout)
        if record.get("status") == "timeout":
            record["stk_recovered"] = self.wait_until_free(on_wait)
        return record


def module_summary(licenses):
    """Readable licensed/unlicensed lists for the prompt and the UI."""
    have, missing = [], []
    for product, desc, ok in licenses or []:
        label = MODULE_NAMES.get(product)
        if label:
            (have if ok else missing).append(label)
    return have, missing
