"""
styles.py — Shared design-system constants for OpenMission.

Import in all three Streamlit pages:
    from styles import _CSS, _BG_SVG, _WATERMARK_HTML, emblem_html
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Master CSS — applies to every page
# ─────────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&display=swap');

/* ── Global font & base size ── */
html, body, [class*="css"] {
    font-family: 'Space Mono', monospace !important;
    font-size: 15px !important;
}

/* ── App background — deep space grid ── */
[data-testid="stApp"] {
    background-color: #0b0f1e;
    background-image:
        linear-gradient(rgba(26, 36, 66, 0.8) 1px, transparent 1px),
        linear-gradient(90deg, rgba(26, 36, 66, 0.8) 1px, transparent 1px);
    background-size: 32px 32px;
}

/* ── Hide Streamlit chrome ── */
[data-testid="stHeader"]  { background: transparent !important; border-bottom: none !important; box-shadow: none !important; }
[data-testid="stToolbar"] { display: none !important; }
footer                    { display: none !important; }
#MainMenu                 { display: none !important; }
.stDeployButton           { display: none !important; }

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: rgba(9, 13, 28, 0.97) !important;
    border-right: 1.5px solid #1e2847;
    min-width: 420px !important;
    max-width: 420px !important;
}
section[data-testid="stSidebar"] > div { background: transparent !important; }

/* ── Hide sidebar collapse arrow ── */
[data-testid="collapsedControl"] { display: none !important; }

/* ── Reorder sidebar children so emblem sits above nav ── */
[data-testid="stSidebarNav"] {
    order: 2 !important;
}
[data-testid="stSidebarContent"] {
    display: flex !important;
    flex-direction: column !important;
}
[data-testid="stSidebarContent"] > div:has([data-testid="stSidebarNav"]) {
    order: 2 !important;
}
[data-testid="stSidebarContent"] > div:not(:has([data-testid="stSidebarNav"])) {
    order: 1 !important;
}

/* ── Sidebar Branding Typography Overrides ── */
section[data-testid="stSidebar"] .om-brand-container {
    display: flex !important;
    align-items: center !important;
    gap: 16px !important;
    padding: 12px 0 14px 0 !important;
}
section[data-testid="stSidebar"] .om-brand-title {
    font-family: 'Space Mono', monospace !important;
    font-size: 22px !important;
    font-weight: 700 !important;
    line-height: 1.1 !important;
    color: #e0e6f5 !important;
    letter-spacing: -0.01em !important;
    white-space: nowrap !important;
}
section[data-testid="stSidebar"] .om-brand-title span {
    color: #e8001c !important;
}
section[data-testid="stSidebar"] .om-brand-subtitle {
    font-family: 'Space Mono', monospace !important;
    font-size: 10.5px !important;
    letter-spacing: 0.04em !important;
    text-transform: uppercase !important;
    color: #4a5c80 !important;
    margin-top: 4px !important;
    font-weight: 700 !important;
    white-space: nowrap !important;
}
section[data-testid="stSidebar"] .om-brand-page-subtitle {
    font-family: 'Space Mono', monospace !important;
    font-size: 12px !important;
    color: #e8001c !important;
    letter-spacing: 0.04em !important;
    margin-top: 5px !important;
    font-weight: 700 !important;
    white-space: nowrap !important;
}

/* ── Section labels (#### headings inside sidebar) ── */
section[data-testid="stSidebar"] h4,
section[data-testid="stSidebar"] .stMarkdown h4,
section[data-testid="stSidebar"] .stMarkdown h3 {
    font-size: 13px !important;
    letter-spacing: 0.2em !important;
    text-transform: uppercase !important;
    color: #4a9eff !important;
    margin: 14px 0 5px !important;
    font-weight: 700 !important;
}

/* ── Custom div labels with letter-spacing (inline style divs) ── */
div[style*="letter-spacing"] {
    font-size: 11px !important;
}

/* ── Page navigation pills ── */
[data-testid="stSidebarNav"] { padding: 0 0 6px 0; }
[data-testid="stSidebarNavItems"] { padding: 0 !important; gap: 3px !important; display: flex; flex-direction: column; }
[data-testid="stSidebarNavLink"] {
    display: flex !important;
    align-items: center !important;
    padding: 12px 14px !important;
    border-radius: 4px !important;
    border-left: 3px solid #e8001c !important;
    background: transparent !important;
    text-decoration: none !important;
    font-family: 'Space Mono', monospace !important;
    font-size: 13px !important;
    font-weight: 700 !important;
    letter-spacing: 0.15em !important;
    text-transform: uppercase !important;
    color: #e0e6f5 !important;
    transition: background 0.15s;
}
[data-testid="stSidebarNavLink"]:hover {
    background: rgba(232, 0, 28, 0.1) !important;
}
[data-testid="stSidebarNavLink"][aria-current="page"] {
    background: #e8001c !important;
    color: #ffffff !important;
    border-left-color: transparent !important;
}
/* Hide default auto-generated page-name spans */
[data-testid="stSidebarNavLink"] span { display: none !important; }
/* Inject custom labels — ordered: app → deorbit → pass_prediction.
   Icons live in ::before (animated SVG, injected below); labels in ::after. */
[data-testid="stSidebarNavItems"] > li:nth-child(1) [data-testid="stSidebarNavLink"]::after { content: "COVERAGE"; }
[data-testid="stSidebarNavItems"] > li:nth-child(2) [data-testid="stSidebarNavLink"]::after { content: "LIFETIME"; }
[data-testid="stSidebarNavItems"] > li:nth-child(3) [data-testid="stSidebarNavLink"]::after { content: "PASS PREDICTION"; }

/* ── Primary button — mission red ── */
[data-testid="baseButton-primary"],
[data-testid="baseButton-primaryFormSubmit"] {
    background-color: #e8001c !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 6px !important;
    font-family: 'Space Mono', monospace !important;
    font-weight: 700 !important;
    letter-spacing: 0.06em !important;
    transition: background 0.15s;
}
[data-testid="baseButton-primary"]:hover,
[data-testid="baseButton-primaryFormSubmit"]:hover { background-color: #c0001a !important; }

/* ── Secondary button ── */
[data-testid="baseButton-secondary"] {
    border: 1.5px solid #1e2847 !important;
    border-radius: 6px !important;
    background: #111827 !important;
    color: #e0e6f5 !important;
    font-family: 'Space Mono', monospace !important;
}

/* ── Metric cards ── */
[data-testid="stMetric"] {
    background: #111827 !important;
    border: 1.5px solid #1e2847 !important;
    border-top: 3px solid #4a9eff !important;
    padding: 12px 14px 10px !important;
    border-radius: 4px !important;
}
[data-testid="stMetricLabel"] > div {
    font-size: 11px !important;
    letter-spacing: 0.18em !important;
    text-transform: uppercase !important;
    color: #4a9eff !important;
    font-weight: 700 !important;
}
[data-testid="stMetricValue"] > div {
    font-size: 20px !important;
    font-weight: 700 !important;
    color: #e0e6f5 !important;
}

/* ── Number / text inputs ── */
[data-testid="stNumberInput"] input,
[data-testid="stTextInput"]   input {
    border: 1.5px solid #1e2847 !important;
    border-radius: 4px !important;
    background: #111827 !important;
    color: #e0e6f5 !important;
    font-family: 'Space Mono', monospace !important;
}
[data-testid="stNumberInput"] input:focus,
[data-testid="stTextInput"]   input:focus {
    border-color: #4a9eff !important;
    box-shadow: 0 0 0 2px rgba(74,158,255,0.18) !important;
    outline: none !important;
}
[data-testid="stNumberInput"] button        { min-width: 18px !important; width: 18px !important; padding: 0 !important; }
[data-testid="stNumberInput"] button svg    { width: 10px !important; height: 10px !important; }

/* ── Textarea ── */
[data-testid="stTextArea"] textarea {
    border: 1.5px solid #1e2847 !important;
    border-radius: 4px !important;
    background: #111827 !important;
    color: #e0e6f5 !important;
    font-family: 'Space Mono', monospace !important;
    font-size: 12px !important;
}

/* ── Selectbox ── */
[data-testid="stSelectbox"] > div > div {
    border: 1.5px solid #1e2847 !important;
    border-radius: 4px !important;
    background: #111827 !important;
}
[data-testid="stSelectbox"] > div > div:focus-within { border-color: #4a9eff !important; }

/* ── Radio ── */
[data-testid="stRadio"] label { font-size: 13px !important; }

/* ── Tabs ── */
[data-baseweb="tab-list"] {
    border-bottom: 1.5px solid #1e2847 !important;
    background: transparent !important;
    gap: 0 !important;
}
[data-baseweb="tab"] {
    font-size: 13px !important;
    letter-spacing: 0.05em !important;
    color: #4a5c80 !important;
    padding: 9px 18px !important;
    background: transparent !important;
    border-bottom: 2px solid transparent !important;
}
[data-baseweb="tab"][aria-selected="true"] {
    color: #4a9eff !important;
    border-bottom: 2px solid #4a9eff !important;
    background: transparent !important;
}
[data-baseweb="tab-highlight"] { display: none !important; }
[data-baseweb="tab-border"]    { display: none !important; }

/* ── Dividers ── */
hr { border: none !important; border-top: 1px solid #1e2847 !important; margin: 10px 0 !important; }

/* ── Captions ── */
.stCaption, .stCaption p,
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
    color: #6b8cba !important;
    opacity: 0.75 !important;
    font-size: 12px !important;
}

/* ── Alerts ── */
[data-testid="stAlertContainer"],
[data-testid="stAlert"] { border-radius: 4px !important; border: 1.5px solid #1e2847 !important; }

/* ── Dataframe / data-editor ── */
[data-testid="stDataFrame"] iframe         { border: 1.5px solid #1e2847 !important; }
[data-testid="stDataEditorContainer"]       { border: 1.5px solid #1e2847 !important; border-radius: 4px !important; background: #111827 !important; }
[data-testid="stDataEditorContainer"] canvas { background: #111827 !important; }
.dvn-scroller .gdg-header    { background: #0f1829 !important; }
.dvn-scroller .gdg-row-odd   { background: #111827 !important; }
.dvn-scroller .gdg-row-even  { background: #0d1525 !important; }

/* ── Toggle ── */
[data-testid="stToggle"] span[data-testid="stWidgetLabel"] { font-size: 13px !important; letter-spacing: 0.04em !important; }

/* ── Animations & micro-interactions ── */

@keyframes om-orbit-drift {
    to { stroke-dashoffset: -16; }
}
.om-orbit {
    animation: om-orbit-drift 3s linear infinite;
}

@keyframes om-sat-pulse {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.55; }
}
.om-sat {
    animation: om-sat-pulse 2.2s ease-in-out infinite;
}

[data-testid="stMetric"] {
    transition: transform 0.18s ease, box-shadow 0.18s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-3px);
    box-shadow: 0 6px 18px rgba(232, 0, 28, 0.12);
}

.stButton button[kind="primary"] {
    transition: box-shadow 0.2s ease, transform 0.15s ease;
}
.stButton button[kind="primary"]:hover {
    box-shadow: 0 0 16px rgba(232, 0, 28, 0.45);
    transform: translateY(-1px);
}

.stNumberInput input, .stTextInput input,
.stSelectbox > div, .stDateInput input {
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

@keyframes om-fade-in {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
}
[data-testid="stMetric"],
.stPlotlyChart,
.stDataFrame {
    animation: om-fade-in 0.4s ease-out;
}

[data-testid="stSidebarNav"] a {
    transition: background 0.18s ease, padding-left 0.18s ease;
}
[data-testid="stSidebarNav"] a:hover {
    padding-left: 6px;
}

/* ── Per-page emblem variants ── */

/* "decay" — satellite spirals into Earth */
@keyframes om-decay {
    0%   { transform: translate(0, 0) scale(1); opacity: 1; }
    70%  { transform: translate(-14px, 16px) scale(0.55); opacity: 1; }
    85%  { transform: translate(-17px, 19px) scale(0.3); opacity: 0.6; }
    92%  { transform: translate(-18px, 20px) scale(0.15); opacity: 0; }
    100% { transform: translate(0, 0) scale(1); opacity: 0; }
}
.om-sat-shape {
    transform-origin: 46px 11px;
}
.om-sat-decay {
    animation: om-decay 6s ease-in infinite;
    transform-origin: 46px 11px;
}
@keyframes om-burnup {
    0%, 84%, 100% { opacity: 0; r: 1; }
    88%           { opacity: 0.9; }
    92%           { opacity: 0; }
}
.om-burnup {
    animation: om-burnup 6s ease-in infinite;
}

/* "pass" — satellite sweeps and pings the ground */
@keyframes om-pass-sweep {
    0%   { transform: translateX(-20px); }
    100% { transform: translateX(20px); }
}
.om-sat-pass {
    animation: om-pass-sweep 5s ease-in-out infinite alternate;
}
@keyframes om-ping {
    0%, 35%  { opacity: 0; transform: scale(0.3); }
    45%      { opacity: 0.8; transform: scale(0.6); }
    60%      { opacity: 0;   transform: scale(1.1); }
    100%     { opacity: 0; }
}
.om-signal {
    animation: om-ping 5s ease-out infinite;
    transform-origin: 46px 11px;
}

/* Ambient globe rotation (background + watermark meridians) */
@keyframes om-globe-spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}
.om-globe-spin {
    animation: om-globe-spin 90s linear infinite;
    transform-box: fill-box;
    transform-origin: center;
}

/* ── Ambient background animations ── */

/* 1. Orbital motion (emblem + background globe) — pure CSS, no SMIL.
   The satellite is carried by a rotating group nested inside a
   vertically-squashed frame, so it traces the real orbit ellipse.
   A counter-rotating inner group cancels the squash so the satellite
   keeps its shape. Two synced copies are drawn — one below the Earth
   disc (occluded = "behind" the planet) and one above — and swapped
   at the orbit extremes. One lap = 10 s; front half = 0–50%. */
@keyframes om-orbit-run {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}
.om-orbiter {
    animation: om-orbit-run 10s linear infinite;
    transform-box: fill-box;
    transform-origin: center;
}
@keyframes om-orbit-counter {
    from { transform: rotate(0deg); }
    to   { transform: rotate(-360deg); }
}
.om-counter {
    animation: om-orbit-counter 10s linear infinite;
    transform-box: fill-box;
    transform-origin: center;
}
@keyframes om-vis-front {
    0%, 49%  { opacity: 1; }
    50%, 99% { opacity: 0; }
    100%     { opacity: 1; }
}
@keyframes om-vis-back {
    0%, 49%  { opacity: 0; }
    50%, 99% { opacity: 1; }
    100%     { opacity: 0; }
}
.om-orb-front { animation: om-vis-front 10s linear infinite; }
.om-orb-back  { animation: om-vis-back  10s linear infinite; }

/* 2. Live ground track — the track draws itself on the globe under
   the satellite during the front pass (this IS the beacon trace),
   then fades while the satellite is behind Earth. A sub-satellite
   dot rides along it, radially aligned with the satellite. */
@keyframes om-trk-draw {
    0%   { stroke-dashoffset: 100; opacity: 0.9; }
    50%  { stroke-dashoffset: 0;   opacity: 0.9; }
    64%  { stroke-dashoffset: 0;   opacity: 0; }
    100% { stroke-dashoffset: 0;   opacity: 0; }
}
.om-trk {
    stroke-dasharray: 100 100;
    animation: om-trk-draw 10s linear infinite;
}
@keyframes om-gnd-run {
    0%        { transform: rotate(0deg); }
    50%, 100% { transform: rotate(180deg); }
}
.om-gnd-runner {
    animation: om-gnd-run 10s linear infinite;
    transform-box: fill-box;
    transform-origin: center;
}
@keyframes om-gnd-counter-run {
    0%        { transform: rotate(0deg); }
    50%, 100% { transform: rotate(-180deg); }
}
.om-gnd-counter {
    animation: om-gnd-counter-run 10s linear infinite;
    transform-box: fill-box;
    transform-origin: center;
}

/* 3. Shooting-star streaks — visible only ~6% of each cycle */
@keyframes om-streak-fly {
    0%, 92%  { opacity: 0; transform: translate(-10vw, -5vh); }
    94%      { opacity: 0.5; }
    100%     { opacity: 0; transform: translate(110vw, 55vh); }
}
.om-streak-1 { animation: om-streak-fly 16s linear infinite; }
.om-streak-2 { animation: om-streak-fly 23s linear infinite 8s; }

/* 4. Beacon ping + downlink — fire at 20–33% of the lap, exactly
   when the satellite crosses the sub-satellite point at 25%.
   All animations share the same 10 s clock so they stay in phase. */
@keyframes om-beacon-ping {
    0%, 20%  { opacity: 0; transform: scale(0.4); }
    25%      { opacity: 0.7; transform: scale(1); }
    33%      { opacity: 0; transform: scale(2.2); }
    100%     { opacity: 0; }
}
.om-beacon {
    animation: om-beacon-ping 10s ease-out infinite;
    transform-box: fill-box;
    transform-origin: center;
}
.om-beacon-2 {
    animation: om-beacon-ping 10s ease-out infinite 0.4s;
    transform-box: fill-box;
    transform-origin: center;
}
.om-beacon-3 {
    animation: om-beacon-ping 10s ease-out infinite 0.8s;
    transform-box: fill-box;
    transform-origin: center;
}
@keyframes om-downlink {
    0%, 19%   { opacity: 0; }
    24%, 31%  { opacity: 0.65; }
    36%, 100% { opacity: 0; }
}
.om-downlink { animation: om-downlink 10s linear infinite; }
</style>
"""

# ─────────────────────────────────────────────────────────────────────────────
# Animated sidebar-nav icons
#
# Streamlit's auto multipage nav (stSidebarNavLink) reruns the whole page on
# click, so JS animation state can't persist. Instead each icon is an inline
# SVG injected as a ::before background-image, and the motion is driven purely
# by CSS on :hover (instant feedback on all three) and [aria-current="page"]
# (a one-shot play that fires whenever a page becomes active — i.e. on press).
#
# Two colour variants per icon:
#   • default — #4a9eff structure + #e8001c accent, for the dark sidebar
#   • active  — solid white, legible on the red active-pill background
# The whole ::before box is transformed (flip / spin / ping); the SVG itself
# is static, which is why the motion fits the meaning of each page.
# ─────────────────────────────────────────────────────────────────────────────
from urllib.parse import quote as _quote

# ── Coverage: tilted red orbit ring around a blue globe ──────────────────────
def _svg_coverage(struct: str, accent: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<circle cx="12" cy="12" r="6.4" fill="none" stroke="{struct}" stroke-width="1.3"/>'
        f'<ellipse cx="12" cy="12" rx="2.6" ry="6.4" fill="none" stroke="{struct}" stroke-width="0.8" opacity="0.75"/>'
        f'<line x1="5.6" y1="12" x2="18.4" y2="12" stroke="{struct}" stroke-width="0.8" opacity="0.75"/>'
        f'<ellipse cx="12" cy="12" rx="10" ry="3.6" fill="none" stroke="{accent}" stroke-width="1.5" transform="rotate(-25 12 12)"/>'
        f'<circle cx="20.4" cy="8.1" r="1.3" fill="{accent}"/>'
        f'</svg>'
    )

# ── Lifetime: hourglass with red sand (matches the uploaded icon) ────────────
def _svg_lifetime(struct: str, accent: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<rect x="4.5" y="2.4" width="15" height="2.4" rx="1.2" fill="{struct}"/>'
        f'<rect x="4.5" y="19.2" width="15" height="2.4" rx="1.2" fill="{struct}"/>'
        f'<path d="M6.6 4.8 C6.6 9 10.6 11 12 12 C13.4 13 17.4 15 17.4 19.2 '
        f'M17.4 4.8 C17.4 9 13.4 11 12 12 C10.6 13 6.6 15 6.6 19.2" '
        f'fill="none" stroke="{struct}" stroke-width="1.5" stroke-linecap="round"/>'
        f'<path d="M8 6.1 C9.6 8.1 14.4 8.1 16 6.1 C15 8.9 13 10.6 12 11.4 '
        f'C11 10.6 9 8.9 8 6.1 Z" fill="{accent}"/>'
        f'<circle cx="12" cy="12.7" r="0.55" fill="{accent}"/>'
        f'<circle cx="12" cy="14.3" r="0.55" fill="{accent}"/>'
        f'<circle cx="12" cy="15.9" r="0.55" fill="{accent}"/>'
        f'<path d="M8.2 18.5 C9.6 16 14.4 16 15.8 18.5 Z" fill="{accent}"/>'
        f'</svg>'
    )

# ── Pass Prediction: tilted parabolic dish on A-frame, red signal arcs ───────
def _svg_pass(struct: str, accent: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        # A-frame mount legs (clean strokes) + baseplate
        f'<g stroke="{struct}" stroke-width="1.9" stroke-linecap="round" fill="none">'
        f'<line x1="6.3" y1="19.4" x2="9.2" y2="14.8"/>'
        f'<line x1="12.1" y1="19.4" x2="9.2" y2="14.8"/>'
        f'</g>'
        f'<rect x="3.4" y="19.2" width="12.4" height="2.3" rx="0.7" fill="{struct}"/>'
        # parabolic dish: half-disc, flat rim aimed up-right, convex back lower-left
        f'<path d="M5.0 7.0 A6 6 0 0 0 13.4 15.4 Z" fill="{struct}"/>'
        # feed boom + sub-reflector sitting in front of the dish, on the aim axis
        f'<line x1="9.2" y1="11.2" x2="11.0" y2="9.0" stroke="{struct}" stroke-width="1.1"/>'
        f'<circle cx="11.0" cy="9.0" r="1.5" fill="{struct}"/>'
        f'<circle cx="11.0" cy="9.0" r="0.85" fill="{accent}"/>'
        # two concentric signal arcs radiating upper-right
        f'<path d="M13.6 7.8 A4.4 4.4 0 0 1 17.1 11.4" fill="none" '
        f'stroke="{accent}" stroke-width="1.7" stroke-linecap="round"/>'
        f'<path d="M15.3 5.4 A7.4 7.4 0 0 1 19.7 11.6" fill="none" '
        f'stroke="{accent}" stroke-width="1.7" stroke-linecap="round"/>'
        f'</svg>'
    )

def _uri(svg: str) -> str:
    """Inline SVG → CSS-safe data URI (only # needs hard encoding in url())."""
    return "data:image/svg+xml," + _quote(svg, safe="")

# Default (dark-sidebar) and active (white-on-red) variants
_ICON_STRUCT, _ICON_ACCENT, _ICON_ACTIVE = "#4a9eff", "#e8001c", "#ffffff"
_icons = {
    1: (_svg_coverage, "om-icon-spin"),
    2: (_svg_lifetime, "om-icon-flip"),
    3: (_svg_pass,     "om-icon-ping"),
}

_nav_icon_rules = []
for _n, (_fn, _anim) in _icons.items():
    _uri_default = _uri(_fn(_ICON_STRUCT, _ICON_ACCENT))
    _uri_active  = _uri(_fn(_ICON_ACTIVE, _ICON_ACTIVE))
    _sel = f'[data-testid="stSidebarNavItems"] > li:nth-child({_n}) [data-testid="stSidebarNavLink"]'
    _nav_icon_rules.append(f"""
{_sel}::before {{
    content: "";
    flex: 0 0 auto;
    width: 26px; height: 26px;
    margin-right: 12px;
    background-image: url("{_uri_default}");
    background-size: contain;
    background-repeat: no-repeat;
    background-position: center;
    transform-origin: center;
}}
{_sel}[aria-current="page"]::before {{
    background-image: url("{_uri_active}");
    animation: {_anim} 0.65s ease-in-out;
}}
{_sel}:hover::before {{
    animation: {_anim} 0.9s ease-in-out infinite;
}}""")

_NAV_ICON_CSS = """
<style>
@keyframes om-icon-flip {
    0%   { transform: rotateX(0deg); }
    100% { transform: rotateX(360deg); }
}
@keyframes om-icon-spin {
    0%   { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}
@keyframes om-icon-ping {
    0%, 100% { transform: scale(1); }
    45%      { transform: scale(1.18); }
}
""" + "\n".join(_nav_icon_rules) + "\n</style>\n"

_CSS = _CSS + _NAV_ICON_CSS

# ─────────────────────────────────────────────────────────────────────────────
# Background Earth SVG — faint grid watermark, right side
# ─────────────────────────────────────────────────────────────────────────────
_BG_SVG = """
<div style="position:fixed;right:-60px;top:50%;
            transform:translateY(-50%);z-index:0;
            pointer-events:none;">
  <svg viewBox="0 0 560 560" width="560" height="560"
       xmlns="http://www.w3.org/2000/svg">
    <g opacity="0.35">
      <g class="om-orb-back">
        <g transform="rotate(-15 280 220)">
          <g transform="translate(280 220) scale(1 0.357)">
            <g class="om-orbiter">
              <circle cx="0" cy="0" r="308" fill="#000" opacity="0"/>
              <g transform="translate(280 0)">
                <g class="om-counter">
                  <g transform="scale(1 2.8)">
                    <rect x="-6" y="-4.5" width="12" height="9" rx="1.8" fill="#e8001c" opacity="0.7"/>
                    <rect x="-19.5" y="-3" width="10.5" height="6" fill="#e8001c" opacity="0.55"/>
                    <rect x="9" y="-3" width="10.5" height="6" fill="#e8001c" opacity="0.55"/>
                  </g>
                </g>
              </g>
            </g>
          </g>
        </g>
      </g>
    </g>
    <circle cx="280" cy="280" r="240" fill="#0b0f1e" fill-opacity="0.85"/>
    <g opacity="0.08">
      <circle cx="280" cy="280" r="240"
              fill="none" stroke="#4a9eff" stroke-width="3"/>
      <ellipse cx="280" cy="280" rx="240" ry="80"
               fill="none" stroke="#4a9eff" stroke-width="1.5"/>
      <ellipse cx="280" cy="280" rx="240" ry="150"
               fill="none" stroke="#4a9eff" stroke-width="1.5"/>
      <g class="om-globe-spin">
        <ellipse cx="280" cy="280" rx="90"  ry="240"
                 fill="none" stroke="#4a9eff" stroke-width="1.5"/>
        <ellipse cx="280" cy="280" rx="180" ry="240"
                 fill="none" stroke="#4a9eff" stroke-width="1.5"/>
      </g>
      <line x1="40" y1="280" x2="520" y2="280"
            stroke="#4a9eff" stroke-width="2"/>
    </g>
    <ellipse cx="280" cy="220" rx="280" ry="100"
             fill="none" stroke="#e8001c" stroke-width="3" opacity="0.1"
             stroke-dasharray="15,8" transform="rotate(-15 280 220)"/>
    <g opacity="0.35">
      <g class="om-orb-front">
        <g transform="rotate(-15 280 220)">
          <g transform="translate(280 220) scale(1 0.357)">
            <g class="om-orbiter">
              <circle cx="0" cy="0" r="308" fill="#000" opacity="0"/>
              <g transform="translate(280 0)">
                <g class="om-counter">
                  <g transform="scale(1 2.8)">
                    <rect x="-6" y="-4.5" width="12" height="9" rx="1.8" fill="#e8001c" opacity="0.7"/>
                    <rect x="-19.5" y="-3" width="10.5" height="6" fill="#e8001c" opacity="0.55"/>
                    <rect x="9" y="-3" width="10.5" height="6" fill="#e8001c" opacity="0.55"/>
                  </g>
                </g>
              </g>
            </g>
          </g>
        </g>
      </g>
    </g>
    <g opacity="0.3">
      <line class="om-downlink" x1="306" y1="317" x2="367" y2="403"
            stroke="#e8001c" stroke-width="1.2" stroke-dasharray="4,3" opacity="0"/>
      <circle class="om-beacon" cx="367" cy="403" r="3"
              fill="none" stroke="#e8001c" stroke-width="1" opacity="0"/>
      <circle class="om-beacon-2" cx="367" cy="403" r="3"
              fill="none" stroke="#e8001c" stroke-width="0.8" opacity="0"/>
    </g>
    <g opacity="0.1">
      <line class="om-streak om-streak-1" x1="0" y1="0" x2="60" y2="20"
            stroke="#e8001c" stroke-width="1" opacity="0"/>
      <line class="om-streak om-streak-2" x1="0" y1="0" x2="50" y2="30"
            stroke="#4a9eff" stroke-width="0.8" opacity="0"/>
    </g>
  </svg>
</div>
"""

# ─────────────────────────────────────────────────────────────────────────────
# Watermark — small 36 px emblem fixed bottom-right
# ─────────────────────────────────────────────────────────────────────────────
_WATERMARK_HTML = """
<div style="position:fixed;bottom:16px;right:16px;z-index:999;
            opacity:0.5;pointer-events:none;">
  <svg viewBox="0 0 56 56" width="36" height="36"
       xmlns="http://www.w3.org/2000/svg">
    <circle cx="28" cy="30" r="19"
            fill="#1a4a6e" stroke="#4a9eff" stroke-width="2"/>
    <circle cx="28" cy="30" r="19" fill="#0e3254" opacity="0.6"/>
    <circle cx="22" cy="23" r="6" fill="#c8e6f8" opacity="0.18"/>
    <ellipse cx="28" cy="30" rx="19" ry="6.5"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="19" ry="12"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <g class="om-globe-spin">
      <ellipse cx="28" cy="30" rx="7.5" ry="19"
               fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
      <ellipse cx="28" cy="30" rx="14" ry="19"
               fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    </g>
    <line x1="9" y1="30" x2="47" y2="30"
          stroke="#4a9eff" stroke-width="0.9" opacity="0.55"/>
    <ellipse cx="28" cy="20" rx="22" ry="8"
             fill="none" stroke="#e8001c" stroke-width="2.2"
             stroke-dasharray="5,3" transform="rotate(-15 28 20)"/>
    <line x1="42.5" y1="11" x2="43.5" y2="11"
          stroke="#e8001c" stroke-width="1.2"/>
    <line x1="49.5" y1="11" x2="52.5" y2="11"
          stroke="#e8001c" stroke-width="1.2"/>
    <rect x="38.5" y="9.5" width="5"   height="3" fill="#e8001c"/>
    <rect x="52.5" y="9.5" width="4.5" height="3" fill="#e8001c"/>
    <circle cx="46" cy="11" r="3.5"
            fill="#e8001c" stroke="#c0001a" stroke-width="1.2"/>
    <path d="M 43 14 Q 36 20 32 26"
          fill="none" stroke="#e8001c" stroke-width="1"
          stroke-dasharray="2,2" opacity="0.85"/>
    <path d="M 45 15 Q 40 23 36 28"
          fill="none" stroke="#e8001c" stroke-width="1"
          stroke-dasharray="2,2" opacity="0.85"/>
  </svg>
</div>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Emblem — sidebar header with 82 px SVG, 22 px brand text, page subtitle
# ─────────────────────────────────────────────────────────────────────────────
def _collapse_html(html: str) -> str:
    """Collapse leading whitespace so Streamlit markdown doesn't
    interpret 4-space-indented lines as code blocks, and drop blank
    lines (a blank line terminates an HTML block in markdown and can
    make the SVG render as raw text)."""
    return "\n".join(
        line.strip() for line in html.split("\n") if line.strip()
    )


def emblem_html(subtitle: str = "", variant: str = "orbit") -> str:
    """
    Return the sidebar emblem HTML (normal document flow).

    variant:
        "orbit" — satellite flies the full orbit (passes behind Earth);
                  a live ground track draws on during the front pass
        "decay" — satellite spirals into Earth with burn-up flash
        "pass"  — same orbital motion, with echoing ground-station pings
    """
    subtitle_line = (
        f'<div class="om-brand-page-subtitle">'
        f'{subtitle}</div>'
    ) if subtitle else ""

    # ── "decay" keeps its dedicated spiral narrative ──
    if variant == "decay":
        return _collapse_html(f"""
<div class="om-brand-container">
  <svg viewBox="0 0 56 56" width="82" height="82"
       xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;">
    <circle cx="28" cy="30" r="19"
            fill="#1a4a6e" stroke="#4a9eff" stroke-width="2"/>
    <circle cx="28" cy="30" r="19" fill="#0e3254" opacity="0.6"/>
    <circle cx="22" cy="23" r="6" fill="#c8e6f8" opacity="0.18"/>
    <ellipse cx="28" cy="30" rx="19" ry="6.5"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="19" ry="12"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="7.5" ry="19"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="14" ry="19"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <line x1="9" y1="30" x2="47" y2="30"
          stroke="#4a9eff" stroke-width="0.9" opacity="0.55"/>
    <g class="om-sat-shape om-sat-decay">
      <rect x="44" y="9" width="4" height="4" rx="0.8"
            fill="#e8001c" stroke="#c0001a" stroke-width="0.6"/>
      <rect x="38.5" y="10" width="4.5" height="2" rx="0.4"
            fill="#e8001c" opacity="0.85"/>
      <rect x="49" y="10" width="4.5" height="2" rx="0.4"
            fill="#e8001c" opacity="0.85"/>
    </g>
    <circle class="om-burnup" cx="28" cy="31" r="2"
            fill="#ffa726" opacity="0"/>
  </svg>
  <div>
    <div class="om-brand-title">Open<span>Mission</span></div>
    <div class="om-brand-subtitle">SSO Earth Observation Analysis</div>
    {subtitle_line}
  </div>
</div>
""")

    # ── "orbit" / "pass" — satellite flies the real elliptical orbit ──
    # Two synced satellite copies: "back" is drawn before the (filled)
    # globe so it is occluded while passing behind Earth; "front" is
    # drawn on top. Visibility swaps at the orbit extremes (0% / 50%).
    def _sat_copy(side: str) -> str:
        return f"""
<g class="om-orb-{side}">
<g transform="rotate(-15 28 20)">
<g transform="translate(28 20) scale(1 0.3636)">
<g class="om-orbiter">
<circle cx="0" cy="0" r="32" fill="#000" opacity="0"/>
<g transform="translate(22 0)">
<g class="om-counter">
<g transform="scale(1 2.75)">
<g class="om-sat">
<rect x="-2" y="-2" width="4" height="4" rx="0.8"
      fill="#e8001c" stroke="#c0001a" stroke-width="0.6"/>
<rect x="-7.5" y="-1" width="4.5" height="2" rx="0.4"
      fill="#e8001c" opacity="0.85"/>
<rect x="3" y="-1" width="4.5" height="2" rx="0.4"
      fill="#e8001c" opacity="0.85"/>
</g>
</g>
</g>
</g>
</g>
</g>
</g>
</g>"""

    # Orbit track (dashed ellipse + ticks) only for the "orbit" variant
    if variant == "orbit":
        _orbit_track = """
    <ellipse class="om-orbit" cx="28" cy="20" rx="22" ry="8"
             fill="none" stroke="#e8001c" stroke-width="2.2"
             stroke-dasharray="5,3" transform="rotate(-15 28 20)"/>
    <line x1="42.5" y1="11" x2="43.5" y2="11"
          stroke="#e8001c" stroke-width="1.2"/>
    <line x1="49.5" y1="11" x2="52.5" y2="11"
          stroke="#e8001c" stroke-width="1.2"/>"""
    else:
        _orbit_track = ""

    # "pass" gets an extra echoing ping ring at the ground point
    _ping_extra = ("""
<circle class="om-beacon-3" cx="28" cy="31.5" r="1.5"
        fill="none" stroke="#e8001c" stroke-width="0.6" opacity="0"/>"""
                   if variant == "pass" else "")

    # Live ground track: the trace draws itself on the globe under the
    # satellite during the front pass (= the beacon), with a moving
    # sub-satellite dot, and ping rings firing at closest approach.
    # Only on the "pass" variant — "orbit" keeps just the orbit track.
    _ground = f"""
<g transform="rotate(-15 28 20)">
<path class="om-trk" pathLength="100" d="M 41 27 A 13 4.5 0 0 1 15 27"
      fill="none" stroke="#e8001c" stroke-width="1"
      stroke-linecap="round" opacity="0"/>
<g class="om-orb-front">
<g transform="translate(28 27) scale(1 0.346)">
<g class="om-gnd-runner">
<circle cx="0" cy="0" r="17" fill="#000" opacity="0"/>
<g transform="translate(13 0)">
<g class="om-gnd-counter">
<g transform="scale(1 2.889)">
<circle cx="0" cy="0" r="1.1" fill="#e8001c"/>
</g>
</g>
</g>
</g>
</g>
</g>
<circle class="om-beacon" cx="28" cy="31.5" r="1.5"
        fill="none" stroke="#e8001c" stroke-width="0.7" opacity="0"/>
<circle class="om-beacon-2" cx="28" cy="31.5" r="1.5"
        fill="none" stroke="#e8001c" stroke-width="0.6" opacity="0"/>{_ping_extra}
</g>""" if variant == "pass" else ""

    return _collapse_html(f"""
<div class="om-brand-container">
  <svg viewBox="0 0 56 56" width="82" height="82"
       xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;">
{_sat_copy("back")}
    <circle cx="28" cy="30" r="19"
            fill="#1a4a6e" stroke="#4a9eff" stroke-width="2"/>
    <circle cx="28" cy="30" r="19" fill="#0e3254" opacity="0.6"/>
    <circle cx="22" cy="23" r="6" fill="#c8e6f8" opacity="0.18"/>
    <ellipse cx="28" cy="30" rx="19" ry="6.5"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="19" ry="12"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="7.5" ry="19"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <ellipse cx="28" cy="30" rx="14" ry="19"
             fill="none" stroke="#ffffff" stroke-width="0.8" opacity="0.25"/>
    <line x1="9" y1="30" x2="47" y2="30"
          stroke="#4a9eff" stroke-width="0.9" opacity="0.55"/>
{_orbit_track}
{_ground}
{_sat_copy("front")}
  </svg>
  <div>
    <div class="om-brand-title">Open<span>Mission</span></div>
    <div class="om-brand-subtitle">SSO Earth Observation Analysis</div>
    {subtitle_line}
  </div>
</div>
""")

# ─────────────────────────────────────────────────────────────────────────────
# User Guide link — fixed top-right, links to the PDF served from static/.
# Requires enableStaticServing=true in .streamlit/config.toml and the PDF at
# static/OpenMission_User_Guide.pdf. Streamlit serves static/ under app/static/.
# Render in app.py with: st.markdown(_USER_GUIDE_LINK, unsafe_allow_html=True)
# ─────────────────────────────────────────────────────────────────────────────
_USER_GUIDE_URL = "https://github.com/ProfPyg/OpenMission/blob/main/OpenMission_User_Guide.pdf"

# Sidebar User Guide link — themed SVG book icon (blue structure + red accent),
# slim outline pill that fills red on hover. Rendered inside st.sidebar so it
# lives below the emblem and away from the app header (which intercepts clicks).
_USER_GUIDE_LINK = f"""
<a href="{_USER_GUIDE_URL}" target="_blank" rel="noopener" class="om-guide-link">
  <svg viewBox="0 0 24 24" width="15" height="15" fill="none"
       xmlns="http://www.w3.org/2000/svg" class="om-guide-book">
    <path d="M3 4.2 C3 3.5 3.5 3.2 4.2 3.2 L11 4.4 L11 20.4 L4.2 19.2
             C3.5 19.1 3 18.7 3 18 Z" fill="#4a9eff"/>
    <path d="M21 4.2 C21 3.5 20.5 3.2 19.8 3.2 L13 4.4 L13 20.4 L19.8 19.2
             C20.5 19.1 21 18.7 21 18 Z" fill="#4a9eff" opacity="0.78"/>
    <line x1="12" y1="4.4" x2="12" y2="20.4" stroke="#e8001c" stroke-width="1.6"
          stroke-linecap="round"/>
  </svg>
  <span>User Guide</span>
</a>
<style>
.om-guide-link {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    width: 100%;
    box-sizing: border-box;
    margin: 2px 0 4px 0;
    padding: 6px 12px;
    font-family: 'Space Mono', monospace;
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: #4a9eff;
    text-decoration: none;
    border: 1px solid rgba(74, 158, 255, 0.55);
    border-radius: 7px;
    background: transparent;
    transition: all 0.2s ease;
}}
.om-guide-link .om-guide-book {{ transition: transform 0.45s ease; }}
.om-guide-link:hover {{
    color: #ffffff;
    background: #e8001c;
    border-color: #e8001c;
}}
/* interactive book: flips on hover, and recolors to white to read on red */
.om-guide-link:hover .om-guide-book {{ transform: rotateY(180deg); }}
.om-guide-link:hover .om-guide-book path {{ fill: #ffffff; }}
.om-guide-link:hover .om-guide-book line {{ stroke: #ffffff; }}
</style>
"""



