"""loop.py -- the model <-> tools loop, with token metering.

One Agent per mission. ``run(on_event)`` calls the model, executes the tools
it asks for, feeds results back, and returns when the model finishes its
turn or a tool needs the user (plan confirmation, scenario choice).

Metering uses the usage block of EVERY response, so costs are measured, not
estimated. Prices are per million tokens (platform.claude.com pricing, Sep
2026); cache writes 1.25x input, cache reads 0.1x input.
"""

from __future__ import annotations

import copy
import time

from .tools import TOOLS

MODELS = {
    "claude-sonnet-5-5": {"label": "Sonnet 5.5 (default)", "in": 2.0, "out": 10.0},
    "claude-opus-5-5": {"label": "Opus 5.5 (hard missions)", "in": 4.0, "out": 20.0},
    "claude-haiku-4-5-20251001": {"label": "Haiku 4.5 (cheapest)", "in": 1.0, "out": 5.0},
}
DEFAULT_MODEL = "claude-sonnet-5-5"


class Meter:
    def __init__(self):
        self.calls = []

    def add(self, model, usage):
        price = MODELS.get(model, MODELS[DEFAULT_MODEL])
        fresh = getattr(usage, "input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cost = (fresh * price["in"] + write * price["in"] * 1.25
                + read * price["in"] * 0.1 + out * price["out"]) / 1e6
        row = {"model": model, "input": fresh, "cache_write": write,
               "cache_read": read, "output": out, "usd": round(cost, 5),
               "at": time.time()}
        self.calls.append(row)
        return row

    @property
    def total_usd(self):
        return sum(c["usd"] for c in self.calls)

    @property
    def total_tokens(self):
        return sum(c["input"] + c["cache_write"] + c["cache_read"] + c["output"]
                   for c in self.calls)

    def per_step_usd(self):
        """Average cost of a model call so far -- for the plan estimate."""
        return self.total_usd / len(self.calls) if self.calls else None


def estimate_usd(model, steps, context_tokens=20000):
    """Pre-run guess: each step ~ cached context + 2k fresh in + 1k out."""
    price = MODELS.get(model, MODELS[DEFAULT_MODEL])
    per_step = (context_tokens * price["in"] * 0.1 + 2000 * price["in"]
                + 1000 * price["out"]) / 1e6
    return per_step * steps


def _blocks_to_dicts(content):
    out = []
    for block in content:
        data = block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block)
        out.append(data)
    return out


class Agent:
    def __init__(self, client, system_prompt, ctx, model=DEFAULT_MODEL,
                 max_calls_per_turn=40, max_tokens=8000):
        self.client, self.system_prompt, self.ctx = client, system_prompt, ctx
        self.model = model
        self.max_calls_per_turn = max_calls_per_turn
        self.max_tokens = max_tokens
        self.messages = []
        self.meter = Meter()
        self.stop_requested = False

    # -- conversation -----------------------------------------------------------
    _pending_tool_id = None
    _pending_results = ()

    @property
    def waiting_for_user(self):
        return self._pending_tool_id is not None

    def add_user(self, text):
        """The user's message. If a tool paused for them, it becomes that
        tool's result (the API requires every tool_use to get one)."""
        self._repair()
        if self.messages and self.messages[-1]["role"] == "user" and not self._pending_tool_id:
            # the repair just added tool_results: put the user's words in the same turn
            self.messages[-1]["content"].append({"type": "text", "text": text})
            return
        if self._pending_tool_id:
            content = list(self._pending_results) + [
                {"type": "tool_result", "tool_use_id": self._pending_tool_id,
                 "content": "User answered: " + text}]
            self._pending_tool_id, self._pending_results = None, ()
        else:
            content = [{"type": "text", "text": text}]
        self.messages.append({"role": "user", "content": content})

    def _repair(self):
        """Make every tool_use have its tool_result in the very next message.

        A run interrupted mid-tool (Stop, page reload, a click that reran the
        page) leaves a tool_use without a result, and the API then rejects the
        WHOLE conversation -- 'continue' failed with a 400. Scan the full
        history, not just the end: the user may already have typed after it.
        """
        msgs = self.messages
        i = 0
        while i < len(msgs):
            m = msgs[i]
            ids = [b["id"] for b in m["content"] if m["role"] == "assistant"
                   and b.get("type") == "tool_use"]
            if ids:
                if i == len(msgs) - 1:
                    if self._pending_tool_id:      # a pause tool: answered by add_user
                        break
                    msgs.append({"role": "user", "content": []})
                nxt = msgs[i + 1]
                have = {b.get("tool_use_id") for b in nxt["content"] if b.get("type") == "tool_result"}
                missing = [{"type": "tool_result", "tool_use_id": t, "is_error": True,
                            "content": "Interrupted before a result came back. Check the "
                                       "scenario before assuming it ran."}
                           for t in ids if t not in have]
                if missing:
                    results = [b for b in nxt["content"] if b.get("type") == "tool_result"]
                    others = [b for b in nxt["content"] if b.get("type") != "tool_result"]
                    nxt["content"] = results + missing + others
            i += 1
        if msgs and msgs[-1]["role"] == "user" and not msgs[-1]["content"]:
            msgs.pop()

    def _payload_messages(self):
        """Messages with ONE cache breakpoint on the newest block."""
        messages = copy.deepcopy(self.messages)
        for m in messages:
            for b in m["content"]:
                b.pop("cache_control", None)
        if messages:
            messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
        return messages

    def run(self, on_event):
        """Advance until the model ends its turn or a tool needs the user.

        on_event(kind, data) kinds: "text", "tool_start", "tool_end", "usage",
        "wait" (STK busy), "pause" (needs user), "done", "error".
        """
        self.stop_requested = False
        self._repair()
        for _ in range(self.max_calls_per_turn):
            if self.stop_requested:
                on_event("error", "Stopped by the user.")
                return "stopped"
            try:
                response = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens,
                    system=[{"type": "text", "text": self.system_prompt,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=TOOLS, messages=self._payload_messages())
            except Exception as exc:          # surface API errors as-is
                on_event("error", "API error: %s" % exc)
                return "error"
            on_event("usage", self.meter.add(self.model, response.usage))
            content = _blocks_to_dicts(response.content)
            self.messages.append({"role": "assistant", "content": content})
            for block in content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    on_event("text", block["text"])

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if not tool_uses:
                on_event("done", response.stop_reason)
                return "done"

            results = []
            pause_id = None
            for use in tool_uses:
                if pause_id:
                    results.append({"type": "tool_result", "tool_use_id": use["id"],
                                    "content": "Not run: waiting for the user first.",
                                    "is_error": True})
                    continue
                on_event("tool_start", use)
                text, record, pause = self.ctx.handle(
                    use["name"], use.get("input") or {},
                    on_wait=lambda n, s: on_event("wait", {"attempt": n, "waited_s": s}))
                on_event("tool_end", {"use": use, "record": record, "text": text})
                if pause:
                    pause_id = use["id"]
                    continue
                failed = record.get("status") not in ("ok", "waiting")
                results.append({"type": "tool_result", "tool_use_id": use["id"],
                                "content": text, "is_error": failed})
                if record.get("status") == "no_stk":
                    # STK closed or crashed: stop here, without another model
                    # call. The app tells the user and resumes once STK is back.
                    done = {r["tool_use_id"] for r in results}
                    for other in tool_uses:
                        if other["id"] not in done:
                            results.append({"type": "tool_result", "tool_use_id": other["id"],
                                            "content": "Not run: STK closed.", "is_error": True})
                    self.messages.append({"role": "user", "content": results})
                    on_event("stk_lost", use)
                    return "stk_lost"
            if pause_id:
                # Results of the other tools are sent together with the user's
                # answer (see add_user) -- keep them ready.
                self._pending_results = results
                self._pending_tool_id = pause_id
                on_event("pause", pause_id)
                return "pause"
            self.messages.append({"role": "user", "content": results})
        on_event("error", "Reached %d model calls in one turn; paused so costs "
                          "cannot run away. Say 'continue' to go on."
                 % self.max_calls_per_turn)
        return "limit"


def agent_state(agent):
    """Everything needed to resume a mission later."""
    return {"model": agent.model, "messages": agent.messages,
            "calls": agent.meter.calls,
            "pending_tool_id": agent._pending_tool_id,
            "pending_results": list(agent._pending_results),
            "pending_confirmation": agent.ctx.pending_confirmation,
            "charts": agent.ctx.charts, "findings": agent.ctx.findings,
            "usd": agent.meter.total_usd}


def restore_agent(agent, state):
    agent.model = state.get("model", agent.model)
    agent.messages = state.get("messages", [])
    agent.meter.calls = state.get("calls", [])
    agent._pending_tool_id = state.get("pending_tool_id")
    agent._pending_results = tuple(state.get("pending_results") or ())
    agent.ctx.pending_confirmation = state.get("pending_confirmation")
    agent.ctx.charts = state.get("charts", [])
    agent.ctx.findings = state.get("findings")
    return agent
