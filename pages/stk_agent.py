"""
pages/stk_agent.py — STK Scenario-Builder Agent for OpenMission.

Describe a desired STK scenario in plain English; get back a ready-to-run
win32com Python automation script. This page does NOT call the Anthropic
API and does NOT talk to STK itself (Hugging Face's servers have no path to
a user's local Windows/STK machine, and API usage is billed separately from
a claude.ai subscription). Instead it composes the full prompt (system
instructions + your request) for you to paste into any claude.ai chat you
already have access to, then extracts the script from whatever you paste
back. Manual copy/paste, zero extra billing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from styles import _CSS, _BG_SVG, _WATERMARK_HTML, emblem_html

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="STK Agent — OpenMission",
    page_icon="\U0001f916",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(_CSS,            unsafe_allow_html=True)
st.markdown(_BG_SVG,         unsafe_allow_html=True)
st.markdown(_WATERMARK_HTML, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# System prompt
#
# The prompt lives in prompts/stk_agent.md, NOT in this file. Keeping it as a
# versioned text file means it can be edited, diffed and reviewed without
# touching application code, and two versions can be compared before one is
# committed. It also keeps this page about the UI.
#
# The prompt points the model at Helpers/stk_helpers.py rather than describing
# STK's COM API directly: API knowledge belongs in code that can be executed
# and tested (see Helpers/verify_stk_helpers.py), not in prose the model has
# to be trusted to reproduce.
# ─────────────────────────────────────────────────────────────────────────────
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "stk_agent.md"
_HELPER_PATH = Path(__file__).resolve().parent.parent / "Helpers" / "stk_helpers.py"


@st.cache_data(show_spinner=False)
def _load_prompt() -> str:
    """Read the system prompt from disk.

    Cached so the file is not re-read on every Streamlit rerun; clear the
    cache (or restart) after editing the prompt.
    """
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        st.error(
            f"Could not read the system prompt at `{_PROMPT_PATH}`: {exc}. "
            "The agent cannot compose a prompt without it."
        )
        return ""


@st.cache_data(show_spinner=False)
def _load_helper_module() -> str:
    """Read stk_helpers.py so the user can download it beside the script."""
    try:
        return _HELPER_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


_SYSTEM_PROMPT = _load_prompt()

# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────
if "stk_agent_script" not in st.session_state:
    st.session_state["stk_agent_script"] = None

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Known STK COM ProgIDs, newest first. "Automatic" is the default and the
# right answer for almost everyone: stk_helpers tries these in order and
# reports every one it tried if none resolve, so naming a version is only
# needed on an install where the generic ProgID is missing.
_KNOWN_PROGIDS = (
    "Automatic — try the usual ProgIDs (recommended)",
    "STK.Application",
    "STK12.Application",
    "STK11.Application",
    "STK11_x64.Application",
    "STK_11",
    "Other — type it in",
)


def _copy_button(text: str, label: str = "Copy prompt for Claude") -> None:
    """A single copy-to-clipboard button styled like the app's primary button.

    Streamlit has no native clipboard control, so this is a small HTML
    component. It runs inside an iframe where navigator.clipboard is often
    blocked, hence the execCommand fallback on a hidden textarea.
    """
    payload = json.dumps(text)
    components.html(
        f"""
        <style>
          .om-copy {{
              background: #e8001c; color: #ffffff; border: none;
              border-radius: 6px; padding: 11px 20px;
              font-family: 'Space Mono', ui-monospace, monospace;
              font-weight: 700; font-size: 13px; letter-spacing: 0.06em;
              cursor: pointer; width: 100%;
              transition: background 0.15s, box-shadow 0.2s, transform 0.15s;
          }}
          .om-copy:hover {{
              background: #c0001a;
              box-shadow: 0 0 16px rgba(232, 0, 28, 0.45);
              transform: translateY(-1px);
          }}
          .om-copy.done {{ background: #1e2847; color: #4a9eff; }}
          body {{ margin: 0; background: transparent; }}
        </style>
        <button class="om-copy" id="omCopy">{label}</button>
        <script>
          const payload = {payload};
          const btn = document.getElementById("omCopy");
          btn.addEventListener("click", async () => {{
              try {{
                  await navigator.clipboard.writeText(payload);
              }} catch (err) {{
                  const ta = document.createElement("textarea");
                  ta.value = payload;
                  ta.style.position = "fixed";
                  ta.style.opacity = "0";
                  document.body.appendChild(ta);
                  ta.select();
                  document.execCommand("copy");
                  document.body.removeChild(ta);
              }}
              const original = btn.textContent;
              btn.textContent = "Copied \u2014 paste into claude.ai";
              btn.classList.add("done");
              setTimeout(() => {{
                  btn.textContent = original;
                  btn.classList.remove("done");
              }}, 2200);
          }});
        </script>
        """,
        height=54,
    )


def _extract_code(text: str) -> str:
    """Pull the first fenced code block out of a pasted reply, if any."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def _build_prompt(user_request: str, stk_progid: str = "") -> str:
    prompt = _SYSTEM_PROMPT
    if stk_progid.strip():
        prompt += (
            f"\n\n---\n\nThe user has confirmed their exact, installed STK "
            f"COM ProgID is: `{stk_progid.strip()}` (found via their own "
            f"Windows Registry / STK About dialog — this is verified, not a "
            f"guess). Pass it explicitly as the first entry so it is tried "
            f"before the defaults:\n\n"
            f"    session = stk.StkSession.attach(progids=[\"{stk_progid.strip()}\", \"STK.Application\"])\n"
        )
    return f"{prompt}\n\n---\n\nUser request:\n{user_request}"


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        f'<div class="custom-sidebar-header">{emblem_html("/ STK Agent", variant="agent")}</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    with st.expander("What this agent knows", expanded=False):
        st.caption(
            "The agent does not talk to STK's COM API directly. It writes "
            "scripts against **stk_helpers.py**, a helper module whose every "
            "call is verified against a real STK install by its own smoke "
            "test (Helpers/verify_stk_helpers.py). That is what keeps the "
            "generated scripts working: API details live in code that can be "
            "run and tested, not in prompt text."
        )
        st.caption(
            "Encoded conventions: central-body handling for mixed Earth/Moon "
            "constellations, HPOP vs J2Perturbation setup, the GL0420A lunar "
            "gravity file's valid degree/order range (48-420), Walker T/P/F "
            "geometry, Transmitter/Receiver RF mounting, conic and "
            "rectangular sensor FOV cones, attach-to-running-STK, and "
            "LVLH/ECI/J2000/ICRF frame naming."
        )

    st.divider()
    _helper_src = _load_helper_module()
    if _helper_src:
        st.caption("The generated script imports this module — download it "
                   "into the same folder.")
        st.download_button(
            "\u2b07  stk_helpers.py",
            _helper_src,
            file_name="stk_helpers.py",
            mime="text/x-python",
            use_container_width=True,
        )
    else:
        st.warning("Helpers/stk_helpers.py not found — generated scripts will "
                   "not run without it.")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## STK Scenario-Builder Agent")
st.markdown(
    "Describe the STK scenario you want in plain English. This page builds "
    "a complete prompt for you to paste into a **claude.ai** chat you "
    "already have access to — no separate API key or billing needed. Paste "
    "Claude's reply back below and this page pulls out the runnable Python "
    "script. Nothing here connects to STK or your local machine directly."
)
st.info(
    "The generated script imports **stk_helpers.py** — download it from the "
    "sidebar and keep it in the same folder as the script. The helper module "
    "handles the STK COM details and is verified against a real STK install.",
    icon="\u2139\ufe0f",
)

# ── Step 1: describe the scenario ───────────────────────────────────────────
st.markdown("### 1. Describe the scenario")
user_request = st.text_area(
    "Describe the scenario",
    height=140,
    label_visibility="collapsed",
    placeholder="e.g. Create a Moon-centered scenario with one orbiter satellite on "
                "HPOP with the GL0420A gravity file at degree/order 48, plus 5 GNSS "
                "satellites on J2Perturbation around Earth.",
)

with st.expander("STK version (optional)", expanded=False):
    st.caption(
        "Leave this on **Automatic** unless attaching fails. The helper "
        "module tries the common ProgIDs in order and, if none work, tells "
        "you exactly which ones it tried."
    )
    progid_choice = st.selectbox(
        "STK COM ProgID",
        _KNOWN_PROGIDS,
        index=0,
        label_visibility="collapsed",
    )
    if progid_choice.startswith("Other"):
        stk_progid = st.text_input(
            "Your STK ProgID",
            placeholder="e.g. STK13.Application",
            label_visibility="collapsed",
        )
        st.caption("Find yours by running this in PowerShell:")
        st.code(
            "Get-ChildItem Registry::HKEY_CLASSES_ROOT | "
            'Where-Object { $_.PSChildName -like "*STK*Application*" } | '
            "Select-Object PSChildName",
            language="powershell",
        )
    elif progid_choice.startswith("Automatic"):
        stk_progid = ""
    else:
        stk_progid = progid_choice

# ── Step 2: composed prompt to copy into claude.ai ──────────────────────────
st.markdown("### 2. Copy this prompt into claude.ai")
if user_request.strip():
    full_prompt = _build_prompt(user_request.strip(), stk_progid)
    copy_col, _ = st.columns([1, 2])
    with copy_col:
        _copy_button(full_prompt)
    st.caption(
        "Paste it into a new chat at claude.ai and send. Then bring the "
        "reply back to step 3."
    )
    with st.expander("View the full prompt", expanded=False):
        st.code(full_prompt, language="markdown")
else:
    st.info("Describe a scenario above to generate the prompt.")

# ── Step 3: paste the reply back ────────────────────────────────────────────
st.markdown("### 3. Paste Claude's reply back here")
pasted_reply = st.text_area(
    "Paste Claude's reply back here",
    height=200,
    label_visibility="collapsed",
    placeholder="Paste the full reply from claude.ai here, code block and all …",
)

extract_col, _ = st.columns([1, 3])
extract_btn = extract_col.button(
    "Extract Script",
    type="primary",
    use_container_width=True,
    disabled=not pasted_reply.strip(),
)

if extract_btn:
    st.session_state["stk_agent_script"] = _extract_code(pasted_reply.strip())

script = st.session_state["stk_agent_script"]

if script:
    st.divider()
    st.markdown("### Generated script")
    dl_col, _ = st.columns([1, 2])
    with dl_col:
        st.download_button(
            "\u2b07  Download script (.py)",
            script,
            file_name="stk_generated_scenario.py",
            mime="text/x-python",
            type="primary",
            use_container_width=True,
        )
    st.caption(
        "Save it beside **stk_helpers.py** (sidebar download), open STK, then "
        "run it. Review it first — check central-body assignments, "
        "gravity-file paths and object names against your own scenario "
        "before running against a live session."
    )
    with st.expander("View the script", expanded=False):
        st.code(script, language="python")

# ── References ────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "STK automation via stk_helpers.py (pywin32 / STK Connect)  ·  "
    "Generation via a manual claude.ai copy/paste round trip — no API key required  ·  "
    "Conventions derived from a real cislunar (BGM3/QASCOM) mission-analysis engagement."
)