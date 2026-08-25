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

import re
from pathlib import Path

import streamlit as st
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
        f'<div class="custom-sidebar-header">{emblem_html("/ STK Agent", variant="pass")}</div>',
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

with st.expander("Your STK version (recommended — avoids attach errors)", expanded=False):
    st.caption(
        "The generated script needs to know your exact installed STK COM "
        "ProgID to attach reliably — guessing from a generic fallback list "
        "can fail with a confusing 'Invalid class string' error if your "
        "version isn't in it. Find yours by running this in PowerShell:"
    )
    st.code(
        'Get-ChildItem Registry::HKEY_CLASSES_ROOT | '
        'Where-Object { $_.PSChildName -like "*STK*Application*" } | '
        "Select-Object PSChildName",
        language="powershell",
    )
    st.caption(
        "Look for an entry like `STK.Application`, `STK11_x64.Application`, "
        "or `STK12.Application` and paste it below (leave blank to fall "
        "back to a generic guess-list instead)."
    )
    stk_progid = st.text_input(
        "Your STK ProgID",
        placeholder="e.g. STK11_x64.Application",
        label_visibility="collapsed",
    )

# ── Step 2: composed prompt to copy into claude.ai ──────────────────────────
st.markdown("### 2. Copy this prompt into claude.ai")
if user_request.strip():
    full_prompt = _build_prompt(user_request.strip(), stk_progid)
    st.code(full_prompt, language="markdown")
    st.caption(
        "Click the copy icon in the top-right of the box above, paste it "
        "into a new chat at claude.ai, and send it."
    )
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
    st.markdown("### Generated Script")
    st.code(script, language="python")
    st.download_button(
        "⬇  Download script (.py)",
        script,
        file_name="stk_generated_scenario.py",
        mime="text/x-python",
    )
    st.caption(
        "Save this next to **stk_helpers.py** (sidebar download). Review "
        "it before running — check central-body assignments, "
        "gravity-file paths and object names against your own scenario "
        "before executing against a live STK session."
    )

# ── References ────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "STK automation via stk_helpers.py (pywin32 / STK Connect)  ·  "
    "Generation via a manual claude.ai copy/paste round trip — no API key required  ·  "
    "Conventions derived from a real cislunar (BGM3/QASCOM) mission-analysis engagement."
)