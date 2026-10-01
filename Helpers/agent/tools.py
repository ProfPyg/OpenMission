"""tools.py -- what the model can do, and what each tool actually does.

The model never talks to STK directly: it writes a short Python step that
calls the verified helpers, and run_stk_step executes it in STK through
stk_link. Numbers meant for charts go STK -> file -> chart; they never pass
through the model, so a 10,000-point orbit costs the same as a 10-point one.
"""

from __future__ import annotations

import ast
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HELPERS_PATH = os.path.join(os.path.dirname(HERE), "stk_helpers.py")
MAX_OUTPUT_CHARS = 8000

TOOLS = [
    {
        "name": "run_stk_step",
        "description": (
            "Run one short Python step inside the user's STK, in the mission's "
            "scenario. Pre-defined names: stk (helpers module), session "
            "(attached StkSession), root, result (dict, returned to you), "
            "save_data(name, data_result_or_dict) (stores a dataset for charts). "
            "Each step is a FRESH process: Python variables do not survive between "
            "steps, STK objects do -- refer to them by path (e.g. '*/Satellite/Sat1'). "
            "print() what you need to see. Keep steps small: one logical action each."),
        "input_schema": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string",
                            "description": "One line shown to the user in the process log."},
                "code": {"type": "string"},
            },
            "required": ["purpose", "code"],
        },
    },
    {
        "name": "read_helper_doc",
        "description": (
            "Full signature and measured notes of one helper (e.g. 'set_constraint', "
            "'finite_burn', 'fom_values'). The index in your instructions only has "
            "one-line summaries; read the full doc before using a helper for the "
            "first time in a mission."),
        "input_schema": {"type": "object",
                         "properties": {"name": {"type": "string"}},
                         "required": ["name"]},
    },
    {
        "name": "get_scenario_tree",
        "description": "List every object currently in the mission's scenario.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "request_confirmation",
        "description": (
            "Present the plan and STOP until the user answers Yes/No. Required "
            "before the first run_stk_step of a mission and before any change the "
            "user did not ask for."),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "Short markdown for an engineer (10-15 lines): Goal / What I'll build / How I'll find the answer / You'll get / Assumptions. No function names, code or internals."},
                "estimated_steps": {"type": "integer"},
                "concerns": {"type": "string",
                             "description": "2-3 lines: inputs that look wrong or risky, and the suggested fix."},
            },
            "required": ["plan", "estimated_steps"],
        },
    },
    {
        "name": "make_chart",
        "description": (
            "Draw a chart in the app from a dataset saved with save_data. kinds: "
            "'line' (x vs one or more y), 'scatter', 'bar', 'timeline' (needs "
            "start/stop columns, optional label column), 'elements' (x = time, "
            "y = several columns, one small panel each -- use for mean orbital "
            "elements a/e/i/RAAN/omega over time), 'ground_track' (lat/lon "
            "columns in deg, in the CENTRAL BODY's fixed frame), 'map_heat' "
            "(lat/lon/value columns, e.g. FOM per grid point). For ground_track "
            "and map_heat ALWAYS set body (Earth, Moon, Mars...): only Earth gets "
            "coastlines; other bodies get a lat/lon grid. Add targets to mark "
            "sites. No 3D views: the user has STK's own 3D window."),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "kind": {"type": "string",
                         "enum": ["line", "scatter", "bar", "timeline", "elements",
                                  "ground_track", "map_heat"]},
                "body": {"type": "string", "description": "Central body for maps: Earth, Moon, Mars..."},
                "targets": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}},
                    "required": ["name", "lat", "lon"]},
                    "description": "Sites to mark on a map, e.g. a crater or ground station."},
                "title": {"type": "string", "description": "Short, max ~40 characters, e.g. 'Revisit over Greece' -- details go in axis titles."},
                "x": {"type": "string"}, "y": {"type": "array", "items": {"type": "string"}},
                "lat": {"type": "string"}, "lon": {"type": "string"},
                "value": {"type": "string"}, "z": {"type": "string"},
                "start": {"type": "string"}, "stop": {"type": "string"},
                "label": {"type": "string"},
                "x_title": {"type": "string"}, "y_title": {"type": "string"},
            },
            "required": ["dataset", "kind", "title"],
        },
    },
    {
        "name": "report_findings",
        "description": (
            "Record the mission's results for the Results tab. Call once at the "
            "end (and again if results change). Every metric must come from STK. "
            "Keep it to the 4-10 numbers that answer the user's questions; group "
            "them by the question they answer."),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "The answer in at most 3 sentences / 60 words, plain words; wrap the 1-3 most important phrases in **bold**. Supporting numbers go in metrics, caveats in issues -- not here."},
                "metrics": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"group": {"type": "string",
                                             "description": "The plain question this metric answers, e.g. 'How often do we see the region?', 'How long are we in contact?', 'How strong is the link?'. Metrics sharing a question share the same text."},
                                   "name": {"type": "string", "description": "Short label, e.g. 'Greece' or 'Hermes · 12 sats'"},
                                   "value": {"type": "string", "description": "Number only, e.g. '6.8' or '31–37'"},
                                   "unit": {"type": "string"},
                                   "note": {"type": "string",
                                            "description": "WHERE IN STK the number came from + statistic, short: 'Revisit Time FOM, worst gap, avg of 54 points' or 'STK Maneuver Summary'. A textbook value may appear only as a comparison, never as the result."}},
                    "required": ["name", "value", "note"]}},
                "issues": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"what": {"type": "string"}, "why": {"type": "string"},
                                   "status": {"type": "string",
                                              "enum": ["fixed", "needs_user", "info"]}},
                    "required": ["what", "status"]}},
            },
            "required": ["summary"],
        },
    },
]

_TREE_CODE = r'''
def walk(obj, depth):
    for i in range(obj.Children.Count):
        child = obj.Children.Item(i)
        print("%s%s  [%s]" % ("  " * depth, child.InstanceName, child.ClassName))
        walk(child, depth + 1)
sc = root.CurrentScenario
print("Scenario %s  %s -> %s" % (sc.InstanceName, sc.StartTime, sc.StopTime))
walk(sc, 1)
'''


def helper_doc(name):
    """Signature + docstring of a helper, parsed (not imported: no COM here)."""
    with open(HELPERS_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    matches = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            matches.append(node)
    if not matches:
        close = sorted({n.name for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
                        and (name.lower() in n.name.lower() or n.name.lower() in name.lower())})
        return "No helper named %r. Similar: %s" % (name, ", ".join(close[:12]) or "none")
    node = matches[0]
    if isinstance(node, ast.FunctionDef):
        sig = "%s(%s)" % (node.name, ast.unparse(node.args))
    else:
        sig = "class %s" % node.name
    return "%s\n\n%s" % (sig, ast.get_docstring(node) or "(no docstring)")


def _clip(text, limit=MAX_OUTPUT_CHARS):
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return "%s\n... [%d characters cut] ...\n%s" % (head, len(text) - limit, tail)


def format_step_result(record):
    """What the model reads after a step: status, output, error -- compact."""
    parts = ["status: %s  (%.1f s)" % (record.get("status"), record.get("seconds") or 0)]
    if record.get("stdout"):
        parts.append("output:\n" + _clip(record["stdout"]))
    if record.get("result"):
        parts.append("result: " + _clip(json.dumps(record["result"], default=str), 3000))
    if record.get("datasets"):
        parts.append("datasets saved: " + json.dumps(record["datasets"]))
    if record.get("unapplied_constraints"):
        parts.append("WARNING constraints that did not apply: %s"
                     % record["unapplied_constraints"])
    if record.get("error"):
        parts.append("error:\n" + _clip(record["error"], 4000))
    if record.get("status") == "timeout":
        parts.append("STK recovered afterwards: %s. Do NOT repeat the same "
                     "operation; find a cheaper way or ask the user."
                     % record.get("stk_recovered"))
    return "\n".join(parts)


class ToolContext:
    """Everything a tool needs; one per mission."""

    def __init__(self, link, scenario, data_dir, allow_open=False, period=None):
        self.link, self.scenario, self.data_dir = link, scenario, data_dir
        self.allow_open, self.period = allow_open, period
        self.charts, self.findings = [], None
        self.pending_confirmation = None
        self.scenario_conflict = None

    def _run(self, code, on_wait=None):
        return self.link.run(code, self.scenario, allow_open=self.allow_open,
                             period=self.period, data_dir=self.data_dir, on_wait=on_wait)

    def dataset(self, name):
        path = os.path.join(self.data_dir, name + ".json")
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def handle(self, name, args, on_wait=None):
        """Return (text for the model, record for the log, pause?)."""
        if name == "run_stk_step":
            record = self._run(args.get("code", ""), on_wait)
            if record.get("status") == "scenario_conflict":
                self.scenario_conflict = record.get("result", {}).get("open_scenario")
                return (format_step_result(record) +
                        "\nThe app is asking the user now; wait for their answer.",
                        record, True)
            return format_step_result(record), record, False
        if name == "get_scenario_tree":
            record = self._run(_TREE_CODE)
            return format_step_result(record), record, False
        if name == "read_helper_doc":
            text = helper_doc(args.get("name", ""))
            return text, {"status": "ok"}, False
        if name == "request_confirmation":
            self.pending_confirmation = args
            return ("Plan shown to the user; waiting for Yes/No.",
                    {"status": "waiting"}, True)
        if name == "make_chart":
            try:
                data = self.dataset(args["dataset"])
            except OSError:
                return ("No dataset %r. Save it first with save_data() inside a step."
                        % args.get("dataset"), {"status": "error"}, False)
            cols = data.get("columns", {})
            wanted = [args.get(k) for k in ("x", "lat", "lon", "value", "z", "start",
                                            "stop", "label") if args.get(k)]
            wanted += list(args.get("y") or [])
            missing = [c for c in wanted if c not in cols]
            if missing:
                return ("Columns %s not in dataset %r; it has %s"
                        % (missing, args["dataset"], list(cols)), {"status": "error"}, False)
            self.charts.append(args)
            return "Chart '%s' added." % args["title"], {"status": "ok"}, False
        if name == "report_findings":
            self.findings = args
            return "Findings recorded.", {"status": "ok"}, False
        return "Unknown tool %r" % name, {"status": "error"}, False
