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

import io
import json
import sys
import re
import zipfile
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
_HELPERS_DIR = Path(__file__).resolve().parent.parent / "Helpers"
_HELPER_PATH = Path(__file__).resolve().parent.parent / "Helpers" / "stk_helpers.py"


@st.cache_data(show_spinner=False)
def _read_text(path_str: str, _mtime: float) -> str:
    """Read a file, cached on its modification time.

    The mtime is part of the cache key, so editing the prompt takes effect on
    the next rerun without restarting Streamlit. A plain cache meant prompt
    edits were silently ignored until a restart -- and a stale prompt looks
    exactly like a model that ignored your instructions.
    """
    return Path(path_str).read_text(encoding="utf-8")


@st.cache_data(show_spinner=False)
def _api_reference(helper_path: str, _mtime: float) -> str:
    """The API section, generated from stk_helpers.py rather than typed.

    A hand-maintained list of the helper's functions drifted from the code,
    and each drift became a bug in a generated script -- most sharply when
    new_facility was documented without central_body and a lunar landing site
    was built on Earth. Generating it means nobody writes the description, so
    nobody can get it wrong.

    The module is parsed, not imported: this app runs on Linux where pywin32
    does not exist.
    """
    sys.path.insert(0, str(_HELPERS_DIR))
    from api_reference import generate
    return generate(helper_path)


def _load_prompt() -> str:
    """Read the system prompt and splice in the generated API reference."""
    try:
        text = _read_text(str(_PROMPT_PATH), _PROMPT_PATH.stat().st_mtime).strip()
    except OSError as exc:
        st.error(
            f"Could not read the system prompt at `{_PROMPT_PATH}`: {exc}. "
            "The agent cannot compose a prompt without it."
        )
        return ""

    if "{{HELPER_API}}" in text:
        helper_path = _HELPERS_DIR / "stk_helpers.py"
        try:
            reference = _api_reference(str(helper_path), helper_path.stat().st_mtime)
        except Exception as exc:            # noqa: BLE001 - surface, do not hide
            st.error(
                f"Could not generate the API reference from `{helper_path}`: "
                f"{exc}. The prompt would describe no helpers at all, so it is "
                "not safe to send."
            )
            return ""
        text = text.replace("{{HELPER_API}}", reference)
    return text


def _load_helper_module() -> str:
    """Read stk_helpers.py so the user can download it beside the script.

    Also mtime-keyed: a bundle downloaded after the helper is patched must
    contain the patched helper, not a cached copy.
    """
    try:
        return _read_text(str(_HELPER_PATH), _HELPER_PATH.stat().st_mtime)
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


_RUN_INSTRUCTIONS = """\
HOW TO RUN THIS SCENARIO
========================

1. Unzip this folder anywhere. Keep the two .py files TOGETHER -- the script
   imports stk_helpers from its own directory and will not start without it.

2. Install pywin32 (once per machine):

       pip install -r requirements-stk.txt

3. Open STK and load (or leave empty) the scenario you want to build into.

4. Run the script:

       python {script_name}

WHAT IS IN HERE
---------------
  {script_name}
      Your generated scenario. Every mission number is a named constant at
      the top -- edit those to tweak the scenario, no STK knowledge needed.

  stk_helpers.py
      The STK interface layer. Handles the COM details and is verified
      against a real STK install by its own test suite. Do not edit unless
      you know why; the script depends on it.

  requirements-stk.txt
      The one Python dependency (pywin32). STK itself must be installed and
      licensed separately.

TROUBLESHOOTING
---------------
  "No module named stk_helpers"
      The two .py files are not in the same folder.

  "Could not attach to a running STK instance"
      STK is not open, or its ProgID is unusual -- the error lists every one
      tried; pass yours as StkSession.attach(progids=["..."]).

  "No module named win32com"
      Step 2 was skipped.
"""


def _safe_script_name(name: str) -> str:
    """Turn user input into a safe .py filename.

    Path separators are flattened to underscores rather than split on: a
    user typing "Walker 6/3/2" means all of it as a name, and Path().stem
    would silently keep only "2". Flattening also removes any traversal
    ("../../x" cannot escape), which matters because this name goes into a
    zip entry.
    """
    raw = re.sub(r"[\\/]+", "_", str(name or "").strip())
    # drop a trailing extension, whatever it is, so "report.txt" does not
    # become "report.txt.py"
    head, _dot, tail = raw.rpartition(".")
    if head and tail.isalnum() and len(tail) <= 4:
        raw = head
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    stem = re.sub(r"_{2,}", "_", stem) or "stk_scenario"
    return f"{stem}.py"


def _build_bundle(script: str, script_name: str) -> bytes:
    """Zip the script together with everything needed to run it.

    Shipping the script alone is the single most common way this goes wrong:
    it imports stk_helpers from its own directory, so a user who downloads
    only the script gets ImportError before STK is ever contacted. Bundling
    removes the failure instead of documenting it.
    """
    helper_source = _load_helper_module()
    requirements = ""
    req_path = Path(__file__).resolve().parent.parent / "Helpers" / "requirements-stk.txt"
    try:
        requirements = req_path.read_text(encoding="utf-8")
    except OSError:
        requirements = "pywin32>=306\n"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(script_name, script)
        if helper_source:
            archive.writestr("stk_helpers.py", helper_source)
        archive.writestr("requirements-stk.txt", requirements)
        archive.writestr("README.txt", _RUN_INSTRUCTIONS.format(script_name=script_name))
    return buffer.getvalue()


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
        st.caption(
            "The generated script imports this module — download it into the "
            "same folder. Running it needs **pywin32** on that machine "
            "(`pip install pywin32`) plus a licensed STK install; the app "
            "itself needs neither."
        )
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
    "Describe an STK scenario in plain English. This page composes a full "
    "prompt for you to paste into a **claude.ai** chat you already have — no "
    "API key, no extra billing — and pulls the runnable Python script out of "
    "the reply. Nothing here touches STK or your machine; the script you "
    "download does that when you run it."
)
st.markdown(
    "**To run what you get, you need:** the generated script and "
    "**stk_helpers.py** in the *same folder* — the script imports it. The "
    "download button gives you a zip containing both, so normally there is "
    "nothing to think about. The sidebar has stk_helpers.py on its own if you "
    "want to update an existing folder. You also need **pywin32** "
    "(`pip install pywin32`) and a licensed STK on that machine."
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
    st.caption("Describe a scenario above to generate the prompt.")

# ── Step 3: paste the reply back ────────────────────────────────────────────
st.markdown("### 3. Paste the reply back for a ready-to-run bundle")
st.markdown(
    "You could copy the code out of claude.ai yourself — pasting it here "
    "instead gets you a **zip that already contains stk_helpers.py**, the "
    "pywin32 requirement and a README with the run steps, under a filename "
    "you choose. That is the difference between a file that runs and a file "
    "that raises `ImportError`."
)
pasted_reply = st.text_area(
    "Paste the reply here",
    height=200,
    label_visibility="collapsed",
    placeholder="Paste the whole reply, or just the code block — either works.",
)

# Extract as soon as there is something to extract; a separate button was one
# more click that could only ever be pressed in one order.
if pasted_reply.strip():
    st.session_state["stk_agent_script"] = _extract_code(pasted_reply.strip())

script = st.session_state["stk_agent_script"]

if script:
    st.divider()
    st.markdown("### Generated script")

    name_col, dl_col = st.columns([1, 1])
    with name_col:
        chosen_name = st.text_input(
            "File name",
            value=st.session_state.get("stk_agent_name", "stk_scenario.py"),
            help="Name the script whatever suits the study — the bundle is "
                 "named to match.",
        )
    script_name = _safe_script_name(chosen_name)
    st.session_state["stk_agent_name"] = script_name

    with dl_col:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        st.download_button(
            "\u2b07  Download bundle (.zip)",
            _build_bundle(script, script_name),
            file_name=f"{Path(script_name).stem}.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True,
        )

    st.caption(
        f"The zip contains **{script_name}**, **stk_helpers.py** (which the "
        "script imports — they must stay in the same folder), the pywin32 "
        "requirement, and a README with the run steps. Open STK, then run "
        f"`python {script_name}`."
    )

    with st.expander("View the script", expanded=False):
        st.code(script, language="python")
    with st.expander("Just the script, without the bundle", expanded=False):
        st.caption(
            "Only useful if you already have stk_helpers.py in the folder you "
            "are saving into — the script will not run without it."
        )
        st.download_button(
            "Download script only (.py)",
            script,
            file_name=script_name,
            mime="text/x-python",
        )

# ── References ────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "STK automation via stk_helpers.py (pywin32 / STK Connect)  ·  "
    "Generation via a manual claude.ai copy/paste round trip — no API key required  ·  "
    "Conventions derived from a real cislunar (BGM3/QASCOM) mission-analysis engagement."
)