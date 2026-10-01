"""
pages/stk_agent.py — PRONOIA · Foresee your mission.

The live mission agent: it talks to the user's RUNNING STK on this computer,
plans with the user, and builds only after the plan is approved (engine in
Helpers/agent). Needs Windows, STK open and an Anthropic API key. The older
copy-paste page is pages/stk_agent_legacy.py.
"""
from __future__ import annotations

import html
import os
import re
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Helpers"))

import pronoia_style as ps                                    # noqa: E402
from agent import charts, missions                            # noqa: E402
from agent.loop import (Agent, MODELS, DEFAULT_MODEL, agent_state,  # noqa: E402
                        estimate_usd, restore_agent)
from agent.stk_link import StkLink, module_summary             # noqa: E402
from agent.tools import ToolContext                            # noqa: E402

st.set_page_config(page_title="Pronoia", page_icon=str(ROOT / "assets" / "pronoia_icon.png"),
                   layout="wide", initial_sidebar_state="collapsed")

S = st.session_state
for key, value in {"probe": None, "mission": None, "agent": None, "allow_open": False,
                   "motion": True, "model": DEFAULT_MODEL,
                   "api_key": os.environ.get("ANTHROPIC_API_KEY", "")}.items():
    S.setdefault(key, value)

WORK_DIR = str(ROOT / "missions" / "_steps")
PERIOD = ["1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000"]
EXAMPLES = {
    "Walker constellation coverage": "A Walker 12/3/1 constellation at 700 km, 55° inclination, with 30° nadir cameras. Revisit over Greece for one day.",
    "GEO relay link budget": "A GEO relay at 25°E linking a 550 km SSO satellite (X-band, 8.2 GHz) to a 3.7 m station in Athens. Link margin per pass for one day.",
    "LEO → GEO transfer": "Plan a Hohmann transfer from a 300 km circular orbit to GEO with Astrogator. Report both burns, total Δv and fuel for a 1,000 kg spacecraft with Isp 320 s.",
    "Deorbit plan": "A 150 kg satellite at 500 km with 20 N thruster (Isp 220 s). Plan a deorbit burn to a 150 km perigee and report fuel and time to reentry.",
}

st.markdown(ps.CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Engine plumbing
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def _api_reference(mtime: float) -> str:
    from api_reference import generate
    return generate(str(ROOT / "Helpers" / "stk_helpers.py"))


def _system_prompt(mission, probe) -> str:
    text = (ROOT / "prompts" / "stk_agent_live.md").read_text(encoding="utf-8")
    have, missing = module_summary(probe.get("licenses"))
    helper = ROOT / "Helpers" / "stk_helpers.py"
    for key, value in {
        "{{STK_VERSION}}": probe.get("version", "STK"),
        "{{SCENARIO}}": mission["scenario"],
        "{{PERIOD}}": " to ".join(PERIOD),
        "{{LICENSED}}": ", ".join(have) or "unknown",
        "{{UNLICENSED}}": ", ".join(missing) or "none",
        "{{PROFILE}}": mission.get("profile") or "(none)",
        "{{HELPER_API}}": _api_reference(helper.stat().st_mtime),
    }.items():
        text = text.replace(key, value)
    if mission.get("reference"):
        text += "\n\n## Reference material from the user\n\n" + mission["reference"]
    return text


def _make_agent(mission, state=None):
    import anthropic
    data_dir = str(Path(missions.mission_dir(mission["id"])) / "data")
    ctx = ToolContext(StkLink(WORK_DIR), mission["scenario"], data_dir,
                      allow_open=mission.get("allow_open", False), period=PERIOD)
    agent = Agent(anthropic.Anthropic(api_key=S.api_key), _system_prompt(mission, S.probe),
                  ctx, model=(state or {}).get("model", S.model))
    return restore_agent(agent, state) if state else agent


def _save(mission=None, agent=None):
    mission, agent = mission or S.get("mission"), agent or S.get("agent")
    if mission and agent:
        state = {k: v for k, v in mission.items() if k != "now"}
        state.update(agent_state(agent))
        missions.save(mission["id"], state)


def _worker(mission, agent):
    """Runs in a background thread, so clicking around the page (settings,
    tabs, background toggle) never interrupts the agent mid-step. It only
    touches plain data; the page redraws from that data."""
    def on_event(kind, data):
        log = mission["log"]
        if kind == "text":
            log.append({"kind": "text", "text": data})
        elif kind == "tool_start":
            inp = data.get("input") or {}
            purpose = inp.get("purpose") or data["name"].replace("_", " ")
            log.append({"kind": "step", "tool": data["name"], "status": "running",
                        "purpose": purpose, "code": inp.get("code")})
            n = sum(1 for e in log if e["kind"] == "step" and e.get("tool") == "run_stk_step")
            mission["now"] = "Step %d · %s…" % (n, purpose)
        elif kind == "tool_end":
            last = [e for e in log if e["kind"] == "step"][-1]
            last["status"] = data["record"].get("status")
            last["output"] = data["text"]
            mission["now"] = "Thinking…"
        elif kind == "wait":
            mission["now"] = "STK is busy — waiting (%.0f s)…" % data["waited_s"]
        elif kind == "error":
            log.append({"kind": "error", "text": data})
    mission["now"] = "Thinking…"
    try:
        agent.run(on_event)
    except Exception as exc:                  # noqa: BLE001
        mission["log"].append({"kind": "error", "text": "Agent stopped: %s" % exc})
    if agent.ctx.findings and not agent.waiting_for_user:
        mission["finished"] = time.time()
    mission["now"] = None
    _save(mission, agent)


def _launch():
    import threading
    t = threading.Thread(target=_worker, args=(S.mission, S.agent), daemon=True)
    t.start()
    S.worker = t


def _busy():
    t = S.get("worker")
    return bool(t and t.is_alive())


def _open_mission(mid):
    state = missions.load(mid)
    S.mission = {k: state.get(k) for k in ("id", "title", "scenario", "profile", "reference",
                                           "log", "allow_open", "started", "finished")}
    S.mission["log"] = S.mission["log"] or []
    S.agent = _make_agent(S.mission, state)


def _stk_name(text):
    """A name STK accepts: letters, digits, underscores; no spaces."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", (text or "").strip()).strip("_")
    return ("S_" + name if name[:1].isdigit() else name)[:40]


def _start(prompt, profile, reference, allow_open, scenario, title=None):
    words = re.sub(r"\s+", " ", prompt).strip().split(" ")
    title = (title or "").strip() or (" ".join(words[:5]).rstrip(".,;:") + ("…" if len(words) > 5 else ""))
    mid = missions.new_id(title)
    mission = dict(id=mid, title=title, scenario=scenario, profile=profile,
                   reference=reference, allow_open=allow_open, started=time.time(),
                   finished=None, log=[{"kind": "user", "text": prompt}])
    agent = _make_agent(mission)
    agent.add_user(prompt)
    # assign together: a click that reruns the page mid-way must never leave a
    # mission without its agent
    S.mission, S.agent = mission, agent
    _save(mission, agent)
    _launch()


if S.probe is None:
    _w = st.empty()
    _w.markdown(ps.loader("Looking for STK…", big=True), unsafe_allow_html=True)
    S.probe = StkLink(WORK_DIR).probe()
    _w.empty()
probe = S.probe or {}
stk_ok = probe.get("status") == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Top bar
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(ps.background(0.7 if S.mission is None else 0.21,
                          motion=S.motion), unsafe_allow_html=True)
bar = st.columns([2.2, 1, 1, 1, 2.2], vertical_alignment="center")
_NAV_LOGO = (ROOT / "assets" / "pronoia_logo_nav.svg").read_text(encoding="utf-8")
with bar[0].container(key="logo"):
    st.markdown('<div class="pr-logo-svg">%s</div>' % _NAV_LOGO, unsafe_allow_html=True)
    if st.button("Home", key="home"):          # invisible, laid over the logo
        if S.get("mission") and not _busy():
            _save()
        S.mission = S.agent = None
        st.rerun()
with bar[1].popover("Missions"):
    listed = missions.list_missions()
    if not listed:
        st.caption("No missions yet.")
    for mid, title, usd in listed[:20]:
        if st.button(title, key="pm_" + mid, use_container_width=True):
            _open_mission(mid); st.rerun()
with bar[2].popover("Guide"):
    st.markdown("**How it works**\n\n1. Open STK on this computer.\n2. Describe the mission.\n"
                "3. The agent asks what it needs and shows a plan with a cost estimate.\n"
                "4. Say yes — it builds the scenario in your STK step by step.\n"
                "5. Read the conclusion, numbers and charts; ask follow-ups.\n\n"
                "The agent never closes or overwrites your work without asking.")
    st.markdown('<div class="pr-dlg-small">Planet and Moon textures: '
                '<a href="https://www.solarsystemscope.com/textures/" target="_blank">Solar System Scope</a>, '
                'CC BY 4.0, based on NASA data.</div>', unsafe_allow_html=True)
with bar[3].popover("Settings"):
    st.text_input("Anthropic API key", key="api_key", type="password",
                              help="Kept in memory only. Or set ANTHROPIC_API_KEY.")
    st.selectbox("Model", list(MODELS), key="model", format_func=lambda k: MODELS[k]["label"])
    st.toggle("Moving background", key="motion")     # bound by key: one click = one change
    if st.button("Re-check STK"):
        S.probe = StkLink(WORK_DIR).probe(); st.rerun()
WATCH_S = 30        # STK / licence watchdog period


@st.fragment(run_every=WATCH_S)
def _stk_watchdog():
    """Re-checks STK and its licences every WATCH_S seconds, so the status
    pill is always true: STK closed, a licence lost (e.g. a licence server
    dropping) or a module becoming available shows up without a reload.
    Skipped while the agent is running a step -- STK is busy then, and the
    step itself reports any failure."""
    if not _busy():
        before = S.probe or {}
        now = StkLink(WORK_DIR).probe()
        S.probe = now
        lost = [m for m in module_summary(before.get("licenses"))[0]
                if m not in module_summary(now.get("licenses"))[0]]
        if before.get("status") == "ok" and now.get("status") != "ok":
            S.stk_alert = "STK is no longer reachable — reopen it; nothing will run until it is back."
        elif lost:
            S.stk_alert = "Licence change: %s no longer available." % ", ".join(lost)
        elif now.get("status") == "ok":
            S.stk_alert = None
    pr = S.probe or {}
    if pr.get("status") == "ok":
        have, _ = module_summary(pr.get("licenses"))
        where = ("scenario " + S.mission["scenario"]) if S.get("mission") else "%d modules" % len(have)
        st.markdown('<div class="pr-status"><i></i>%s linked · %s</div>'
                    % (html.escape(pr.get("version", "STK").replace("STK v", "STK ")), html.escape(where)),
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="pr-status off"><i></i>STK not found — open STK</div>',
                    unsafe_allow_html=True)
    if S.get("stk_alert"):
        st.markdown('<div class="pr-status off" style="margin-top:4px">%s</div>'
                    % html.escape(S.stk_alert), unsafe_allow_html=True)


with bar[4]:
    _stk_watchdog()


# ─────────────────────────────────────────────────────────────────────────────
# Landing
# ─────────────────────────────────────────────────────────────────────────────
@st.dialog("A scenario is already open")
def _scenario_dialog(prompt, profile, reference, open_now):
    st.markdown('<div class="pr-dlg">STK has <b>%s</b> open. Pronoia never closes your work '
                'without asking.</div>' % html.escape(open_now), unsafe_allow_html=True)
    if st.button("Build inside '%s'" % open_now, type="primary", use_container_width=True):
        _start(prompt, profile, reference, True, open_now, S.get("scen_name") or open_now); st.rerun()
    if st.button("I'll save and close it in STK first", use_container_width=True):
        st.rerun()          # closes the dialog; Start checks STK again
    st.markdown('<div class="pr-dlg-small">Close it in STK, then press Start again.</div>',
                unsafe_allow_html=True)


if S.mission is None:
    st.markdown('<div class="pr-eyebrow">Foresee your mission</div>'
                '<div class="pr-h1">Describe the mission.<br><em>We build it in your STK.</em></div>'
                '<div class="pr-sub">One satellite<span>—</span>multi-orbit constellations'
                '<span>—</span>transfers<span>—</span>link budgets</div>', unsafe_allow_html=True)
    _, mid_col, _ = st.columns([1, 2.2, 1])
    with mid_col:
        with st.container(key="promptbox"):
            prompt = st.text_area("Mission", key="prompt_text", height=110, label_visibility="collapsed",
                                  placeholder="e.g. A 12-satellite Walker at 700 km plus an SSO imaging pair "
                                              "and a GEO relay. Revisit over Greece and contact windows with Athens.")
            row = st.columns([1.5, 1.4, 1.5, 1.25], vertical_alignment="center")
            row[0].text_input("Scenario name", key="scen_name", label_visibility="collapsed",
                              placeholder="Scenario name")
            with row[1].popover("\\+ Spacecraft"):
                st.text_area("Spacecraft profile", key="profile_text", height=140,
                             placeholder="mass, size, Cd, antennas, propulsion, attitude…")
            with row[2].popover("\\+ References"):
                st.text_area("Reference material", key="reference_text", height=140,
                             placeholder="TLEs, datasheets, ground-station lists…")
            go = row[3].button("Start mission →", type="primary", use_container_width=True)
        def _fill_example():
            picked = S.get("ex_pick")
            if picked:
                S.prompt_text = EXAMPLES[picked]

        with st.container(key="examples"):
            st.markdown('<div class="pr-exlabel">Things you can build</div>', unsafe_allow_html=True)
            st.pills("Examples", list(EXAMPLES), key="ex_pick", label_visibility="collapsed",
                     on_change=_fill_example)
        if go and not S.get("starting"):
            if not prompt.strip():
                st.warning("Describe the mission first.")
            elif not S.api_key:
                st.warning("Add your Anthropic API key in Settings (top of the page).")
            elif not stk_ok:
                st.warning("STK is not reachable. Open STK, then Settings › Re-check STK.")
            else:
                S.starting = True                          # a second click is ignored
                wait = st.empty()
                wait.markdown(ps.loader("Checking STK…"), unsafe_allow_html=True)
                S.probe = StkLink(WORK_DIR).probe()        # fresh: the user may have closed a scenario
                wait.empty()
                S.starting = False
                wanted = S.get("scen_name", "")
                scenario = _stk_name(wanted) or "Pronoia_" + time.strftime("%m%d_%H%M")
                open_now = S.probe.get("scenario") if S.probe.get("status") == "ok" else None
                profile, reference = S.get("profile_text", ""), S.get("reference_text", "")
                if open_now:
                    try:
                        _scenario_dialog(prompt, profile, reference, open_now)
                    except st.errors.StreamlitAPIException:
                        pass                               # already open (repeated click)
                else:
                    _start(prompt, profile, reference, False, scenario, wanted); st.rerun()
    st.markdown('<div class="pr-foot">NOTHING RUNS IN STK UNTIL YOU APPROVE THE PLAN</div>',
                unsafe_allow_html=True)
    st.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Mission view
# ─────────────────────────────────────────────────────────────────────────────
mission, agent = S.mission, S.agent
if agent is None:                     # half-started (interrupted): rebuild from disk or go home
    try:
        _open_mission(mission["id"]); mission, agent = S.mission, S.agent
    except Exception:                 # noqa: BLE001
        S.mission = None
        st.rerun()
st.markdown('<div class="pr-rule"></div>', unsafe_allow_html=True)
rail, chat, out = st.columns([0.32, 2.3, 3.1], gap="medium")

with rail:                       # missions, folded: + and one short tag per mission
    with st.container(key="rail"):
        if st.button("\\+", key="rail_new", help="New mission"):
            if not _busy():
                _save()
            S.mission = S.agent = None; st.rerun()
        for mid, title, usd in missions.list_missions()[:12]:
            tag = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", title)[:2]).upper() or "·"
            on = mid == mission["id"]
            if st.button(tag, key="ml_" + mid, help=title, type="primary" if on else "secondary") \
                    and not on:
                _save(); _open_mission(mid); st.rerun()

steps = [e for e in mission["log"] if e["kind"] == "step" and e.get("tool") == "run_stk_step"]
retried = sum(1 for e in steps if e.get("status") not in ("ok", "running"))
if _busy():
    state_txt = "<b>Working</b>"
elif agent.waiting_for_user or (agent.messages and agent.messages[-1]["role"] == "assistant"
                                and not agent.ctx.findings):
    state_txt = "<b>Waiting for you</b>"
else:
    state_txt = "<b>Completed</b>"
minutes = ((mission.get("finished") or time.time()) - (mission.get("started") or time.time())) / 60

with chat:
    st.markdown('<div class="pr-title">%s</div><div class="pr-meta">%s · scenario %s · %d steps%s · %s</div>'
                % (html.escape(mission["title"]), state_txt, html.escape(mission["scenario"]), len(steps),
                   (" · %.0f min" % minutes) if mission.get("started") else "",
                   MODELS.get(agent.model, {}).get("label", agent.model).split(" (")[0]),
                unsafe_allow_html=True)
    st.markdown('<div class="pr-q" style="margin-top:18px">Chat</div>', unsafe_allow_html=True)
    if True:
        shown = [e for i, e in enumerate(mission["log"]) if not (i == 0 and e["kind"] == "user")]
        for e in shown:            # the first prompt is not repeated here: the plan speaks for it
            if e["kind"] == "user":
                with st.container(key="u%d" % id(e)):
                    st.markdown(e["text"])
            elif e["kind"] == "text":
                st.markdown(e["text"])
            elif e["kind"] == "error":
                st.warning(e["text"])
        if _busy():
            @st.fragment(run_every=1.5)
            def _progress():
                if not _busy():
                    st.rerun()
                first = not any(e["kind"] in ("text", "user") for e in shown)
                st.markdown(ps.loader(html.escape(mission.get("now") or "Working…"), big=first),
                            unsafe_allow_html=True)
            _progress()

        conf = agent.ctx.pending_confirmation
        if conf and agent.waiting_for_user and not _busy():
            steps_est = int(conf.get("estimated_steps") or 10)
            with st.container(border=True):
                st.markdown('<div class="pr-q">The plan</div>', unsafe_allow_html=True)
                st.markdown(conf.get("plan", ""))
                if conf.get("concerns"):
                    st.markdown('<div class="pr-q" style="color:var(--clay)">Worth checking</div>',
                                unsafe_allow_html=True)
                    st.markdown(conf["concerns"])
                st.caption("About %d steps · **Build it** runs this plan exactly as written, including "
                           "the assumptions above. **Change the plan** lets you say what to adjust; "
                           "the agent answers with a revised plan before anything runs." % steps_est)
                c1, c2, _ = st.columns([1, 1, 3])
                if c1.button("Build it", type="primary", use_container_width=True):
                    agent.ctx.pending_confirmation = None
                    agent.add_user("Yes, proceed with the plan.")
                    mission["log"].append({"kind": "user", "text": "Approved — build it."})
                    _launch(); st.rerun()
                if c2.button("Change the plan", use_container_width=True):
                    agent.ctx.pending_confirmation = None
                    S.await_changes = True; st.rerun()

        text = st.chat_input("Answer, ask for changes, or ask a follow-up…", disabled=_busy())
        if text and not _busy():
            agent.ctx.pending_confirmation = None
            prefix = "The user wants changes to the plan: " if S.pop("await_changes", False) else ""
            agent.add_user(prefix + text)
            mission["log"].append({"kind": "user", "text": text})
            mission["finished"] = None
            _launch(); st.rerun()

    # ── Process log (one small drop-down, under the chat) ───────────────────
    label = "Process log · %d steps%s" % (len(steps), (" · %d retried" % retried) if retried else "")
    with st.expander(label):
        tag = {"ok": ("DONE", "var(--olive)"), "error": ("RETRIED", "var(--clay)"),
               "timeout": ("TIMEOUT", "#d9826b"), "running": ("…", "var(--dim)"),
               "scenario_conflict": ("ASKED", "var(--clay)")}
        lines = []
        for i, e in enumerate(steps, 1):
            t, color = tag.get(e.get("status"), (str(e.get("status")).upper(), "var(--dim)"))
            lines.append('<div class="pr-logline"><span style="color:%s">%s</span>%d · %s</div>'
                         % (color, t, i, html.escape(e.get("purpose") or "")))
        st.markdown("".join(lines) or "No steps yet.", unsafe_allow_html=True)
        if steps:
            k = st.selectbox("Inspect a step", range(1, len(steps) + 1),
                             format_func=lambda n: "%d · %s" % (n, steps[n - 1].get("purpose")))
            e = steps[k - 1]
            if e.get("code"):
                st.code(e["code"], language="python")
            if e.get("output"):
                st.text(e["output"][:6000])

with out:
    tab_res, tab_vis = st.tabs(["Results", "Visuals"])

    def _rich(text):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text or ""))

    with tab_res:
        f = agent.ctx.findings
        if not f:
            st.markdown('<div class="pr-concl" style="color:var(--dim)">Results appear here when the '
                        'agent finishes the build.</div>', unsafe_allow_html=True)
        else:
            notes = "".join('<div><span class="%s">%s</span>%s%s</div>'
                            % (i.get("status", "info"),
                               {"fixed": "FIXED", "needs_user": "NEEDS YOU"}.get(i.get("status"), "NOTE"),
                               html.escape(i["what"]),
                               (" — " + html.escape(i["why"])) if i.get("why") else "")
                            for i in f.get("issues") or [])
            groups = {}
            for m in f.get("metrics") or []:
                groups.setdefault(m.get("group") or "Key numbers", []).append(m)
            blocks = []
            for q, rows in groups.items():
                body = "".join('<div class="pr-r"><div class="l">%s<small>%s</small></div>'
                               '<div class="v%s">%s<u>%s</u></div></div>'
                               % (html.escape(m["name"]), html.escape(m.get("note") or ""),
                                  " long" if len(str(m["value"])) > 14 else "",
                                  html.escape(str(m["value"])), html.escape(m.get("unit") or ""))
                               for m in rows)
                blocks.append('<div class="pr-q">%s</div><div class="pr-read">%s</div>' % (html.escape(q), body))
            st.markdown('<div class="pr-q">Conclusion</div><div class="pr-concl">%s</div>%s%s'
                        % (_rich(f.get("summary")), "".join(blocks),
                           ('<div class="pr-q pr-notes-h">Notes</div><div class="pr-notes">%s</div>' % notes)
                           if notes else ""),
                        unsafe_allow_html=True)

    # ── Visuals ─────────────────────────────────────────────────────────────
    with tab_vis:
        specs = [c for c in agent.ctx.charts if c.get("kind") != "orbit_3d"]   # 3D dropped: STK has it
        if not specs:
            st.markdown('<div class="pr-concl" style="color:var(--dim)">Charts appear here — ask for one '
                        'in the chat at any time.</div>', unsafe_allow_html=True)
        else:
            titles = [s["title"] for s in specs]
            pick = st.segmented_control("Chart", titles, default=titles[0], label_visibility="collapsed")
            spec = specs[titles.index(pick or titles[0])]
            try:
                st.plotly_chart(charts.build(spec, agent.ctx.dataset(spec["dataset"])),
                                use_container_width=True)
            except Exception as exc:          # noqa: BLE001
                st.caption("Chart '%s' could not be drawn: %s" % (spec.get("title"), exc))
