"""
pages/pass_prediction.py — Satellite Pass Prediction for OpenMission.

TLE-based pass prediction using Skyfield SGP4 propagation.
Single satellite, single ground location, configurable time window.

References:
  SGP4:     Vallado et al. (2006) AIAA 2006-6753
  Accuracy: Vallado & Cefola (2012)
  Skyfield: Rhodes (2019) ascl:1907.024
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st
from styles import _CSS, _BG_SVG, _WATERMARK_HTML, emblem_html

from skyfield.api import load, wgs84
from skyfield.sgp4lib import EarthSatellite

from access import _detect_windows

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Pass Prediction — OpenMission",
    page_icon="\U0001f6f0",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Design system — injected from shared styles.py
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(_CSS,            unsafe_allow_html=True)
st.markdown(_BG_SVG,         unsafe_allow_html=True)
st.markdown(_WATERMARK_HTML, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level Skyfield timescale (created once)
# ─────────────────────────────────────────────────────────────────────────────
_ts = load.timescale()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_STATIONS: dict[str, dict] = {
    "Helmos Observatory, NOA": {"lat": 37.9856, "lon": 22.1983},
    "UoA GS – Psachna":       {"lat": 38.5697, "lon": 23.6486},
}
_DURATION_OPTIONS = [1, 2, 3, 7, 14]
_STEP_S           = 10.0   # propagation step [s] — 10 s for accurate peak elevation

# Colour palette for per-pass elevation curves
_PASS_COLORS = [
    "#4a9eff", "#4caf50", "#ffa726", "#e8001c", "#ab47bc",
    "#26c6da", "#66bb6a", "#ffca28", "#ef5350", "#7e57c2",
]

# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────
if "pp_results" not in st.session_state:
    st.session_state["pp_results"] = None

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_tle(text: str) -> tuple[str, str, str] | None:
    """
    Parse 2-line or 3-line TLE from raw text.
    Returns (name, line1, line2) or None if unrecognised.
    """
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) >= 3 and lines[1].startswith("1 ") and lines[2].startswith("2 "):
        return lines[0], lines[1], lines[2]
    if len(lines) >= 2 and lines[0].startswith("1 ") and lines[1].startswith("2 "):
        return "SATELLITE", lines[0], lines[1]
    return None


def _tle_epoch(line1: str) -> datetime:
    """
    Parse TLE epoch from Line 1 characters 18-32.
    Format: YYddd.dddddddd  (2-digit year + day-of-year with fraction).
    Returns a naive UTC datetime.
    """
    epoch_str   = line1[18:32]
    year_2digit = int(epoch_str[0:2])
    year        = 2000 + year_2digit if year_2digit < 57 else 1900 + year_2digit
    day_of_year = float(epoch_str[2:])
    return datetime(year, 1, 1) + timedelta(days=day_of_year - 1)


def _quality_label(max_el: float) -> str:
    if max_el > 45:
        return "Overhead"
    if max_el >= 20:
        return "Good"
    if max_el >= 10:
        return "Low"
    return "Very Low"


def _compute_passes(
    line1: str, line2: str, sat_name: str,
    lat: float, lon: float, min_el: float,
    start_dt: datetime, stop_dt: datetime,
) -> list[dict]:
    """
    Run SGP4 pass prediction via Skyfield.

    Returns a list of pass dicts, each containing:
        n, start, end, dur_min, max_el, az_max_el,
        t_slice, alt_slice, az_slice, dist_slice
    """
    satellite = EarthSatellite(line1, line2, sat_name, _ts)
    location  = wgs84.latlon(lat, lon)

    n_steps  = int((stop_dt - start_dt).total_seconds() / _STEP_S) + 1
    t_sec    = np.arange(n_steps, dtype=float) * _STEP_S

    # Build naive-UTC times for the window list and aware datetimes for Skyfield
    start_utc = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=timezone.utc)
    times     = [start_utc + timedelta(seconds=float(s)) for s in t_sec]
    sf_times  = _ts.from_datetimes(times)

    # Topocentric position at every step
    topo             = (satellite - location).at(sf_times)
    alt, az, dist    = topo.altaz()
    alt_arr          = alt.degrees          # shape (n_steps,)
    az_arr           = az.degrees
    dist_arr         = dist.km

    windows = _detect_windows(alt_arr >= min_el, times, elev=alt_arr)

    passes = []
    for i, w in enumerate(windows):
        # Recover array indices from timestamps (exact, no search needed)
        s_idx = round((w['s'] - start_utc).total_seconds() / _STEP_S)
        e_idx = round((w['e'] - start_utc).total_seconds() / _STEP_S)
        s_idx = max(0, min(int(s_idx), n_steps - 1))
        e_idx = max(s_idx, min(int(e_idx), n_steps - 1))

        seg_alt = alt_arr[s_idx:e_idx + 1]
        max_i   = s_idx + int(np.argmax(seg_alt))

        passes.append({
            "n":          i + 1,
            "start":      w['s'],
            "end":        w['e'],
            "dur_min":    (w['e'] - w['s']).total_seconds() / 60.0,
            "max_el":     float(w['max_el']),
            "az_max_el":  float(az_arr[max_i]),
            "t_slice":    times[s_idx:e_idx + 1],
            "alt_slice":  alt_arr[s_idx:e_idx + 1],
            "az_slice":   az_arr[s_idx:e_idx + 1],
            "dist_slice": dist_arr[s_idx:e_idx + 1],
        })

    return passes


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        f'<div class="custom-sidebar-header">{emblem_html("/ Pass Prediction", variant="pass")}</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    # ── 1. TLE Input ──────────────────────────────────────────────────────────
    st.markdown("#### TLE Input")
    uploaded = st.file_uploader(
        "Upload TLE file (.txt)",
        type=["txt"],
        help="Download TLE from celestrak.org or space-track.org",
        label_visibility="collapsed",
    )

    parsed = None
    if uploaded is not None:
        tle_text = uploaded.read().decode("utf-8", errors="replace")
        parsed   = _parse_tle(tle_text)
        if parsed is None:
            st.error("TLE format not recognised. Ensure Line 1 starts with '1 ' and Line 2 with '2 '.")

    tle_ok = parsed is not None

    if tle_ok:
        sat_name, line1, line2 = parsed
        tle_ep = _tle_epoch(line1)
        st.success(f"Loaded: **{sat_name}**")
        st.caption(f"TLE epoch: **{tle_ep.strftime('%Y-%m-%d %H:%M')} UTC**")
    else:
        sat_name = line1 = line2 = ""
        tle_ep   = None

    st.divider()

    # ── 2. Ground Location ────────────────────────────────────────────────────
    st.markdown("#### Ground Location")
    loc_mode = st.radio(
        "Location source",
        ["Preset station", "Custom coordinates"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if loc_mode == "Preset station":
        preset = st.selectbox("Station", list(_DEFAULT_STATIONS.keys()))
        gs_lat = _DEFAULT_STATIONS[preset]["lat"]
        gs_lon = _DEFAULT_STATIONS[preset]["lon"]
        st.caption(f"Lat **{gs_lat:.4f}°N**  ·  Lon **{gs_lon:.4f}°E**")
    else:
        _lc1, _lc2 = st.columns(2)
        gs_lat = _lc1.number_input("Lat (°N)",  -90.0,  90.0,  37.9856, format="%.4f")
        gs_lon = _lc2.number_input("Lon (°E)", -180.0, 180.0,  23.7275, format="%.4f")

    min_el = st.number_input(
        "Min elevation (°)", min_value=0, max_value=90, value=5, step=1,
        help="Minimum elevation above the horizon for a pass to count.",
    )
    st.divider()

    # ── 3. Time Window (only shown once a TLE is loaded) ─────────────────────
    if tle_ok and tle_ep is not None:
        st.markdown("#### Time Window")
        start_dt = tle_ep.replace(tzinfo=timezone.utc)
        st.info(f"Simulation starts at TLE epoch: **{tle_ep.strftime('%Y-%m-%d %H:%M')} UTC**")

        dur_days = st.selectbox(
            "Predict passes for next:",
            options=_DURATION_OPTIONS,
            index=2,
            format_func=lambda d: f"{d} day{'s' if d > 1 else ''}",
        )
        stop_dt = start_dt + timedelta(days=dur_days)
        n_steps = int(dur_days * 86400 / _STEP_S) + 1
        st.caption(
            f"Stop **{stop_dt.strftime('%Y-%m-%d %H:%M')} UTC**  ·  "
            f"**{n_steps:,}** steps"
        )

        # TLE age: how old is the TLE right now (both naive UTC)
        age_days = (datetime.utcnow() - tle_ep).days
        if age_days < 3:
            st.success(
                f"TLE age: **{age_days} days** — "
                f"position error < ~1 km → timing error < ~10 s"
            )
        elif age_days < 7:
            st.info(
                f"TLE age: **{age_days} days** — "
                f"position error ~1–5 km → timing error < ~1 min"
            )
        elif age_days < 14:
            st.warning(
                f"TLE age: **{age_days} days** — "
                f"position error ~5–20 km → timing error < ~5 min"
            )
        else:
            st.error(
                f"TLE age: **{age_days} days** — "
                f"position error likely > 20 km — update TLE recommended"
            )
        st.caption(
            "Position errors from Vallado & Cefola (2012). Timing errors derived from "
            "LEO orbital velocity ~7.6 km/s. Actual accuracy depends on satellite altitude "
            "and atmospheric drag environment."
        )
    else:
        # No TLE yet — provide safe fallback values so the rest of the sidebar renders
        start_dt   = datetime.utcnow().replace(tzinfo=timezone.utc)
        stop_dt    = start_dt + timedelta(days=3)
        dur_days = 3
        n_steps  = int(3 * 86400 / _STEP_S) + 1

    st.divider()

    # ── Run ───────────────────────────────────────────────────────────────────
    run_btn = st.button(
        "▶  Predict Passes",
        type="primary",
        use_container_width=True,
        disabled=not tle_ok,
    )

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## Pass Prediction")
st.markdown(
    "Predict satellite passes over a ground location using **SGP4** (Skyfield). "
    "Paste a current TLE for best accuracy."
)

# ── Trigger computation ───────────────────────────────────────────────────────
if run_btn and tle_ok:
    loc_label = (
        preset if loc_mode == "Preset station"
        else f"{gs_lat:.4f}°N, {gs_lon:.4f}°E"
    )
    with st.spinner(
        f"Computing passes for **{sat_name}** over **{loc_label}** "
        f"({n_steps:,} steps) …"
    ):
        try:
            passes = _compute_passes(
                line1, line2, sat_name,
                gs_lat, gs_lon, float(min_el),
                start_dt, stop_dt,
            )
            st.session_state["pp_results"] = {
                "passes":    passes,
                "sat_name":  sat_name,
                "gs_lat":    gs_lat,
                "gs_lon":    gs_lon,
                "loc_label": loc_label,
                "min_el":    float(min_el),
                "start_dt":  start_dt,
                "stop_dt":   stop_dt,
                "dur_days":  dur_days,
            }
        except Exception as exc:
            st.error(f"Computation failed: {exc}")
            st.exception(exc)

res = st.session_state["pp_results"]

if res is None:
    st.info("Configure a TLE and ground location in the sidebar, then press **▶ Predict Passes**.")
    st.stop()

passes    = res["passes"]
sat_name  = res["sat_name"]
loc_label = res["loc_label"]

if not passes:
    st.warning(
        f"No passes found for **{sat_name}** over **{loc_label}** "
        f"above **{res['min_el']:.0f}°** elevation "
        f"in the **{res['dur_days']}-day** window starting "
        f"**{res['start_dt'].strftime('%Y-%m-%d %H:%M')} UTC**."
    )
    st.stop()

# ── Summary metrics ───────────────────────────────────────────────────────────
st.markdown(f"### {sat_name}  —  {len(passes)} pass{'es' if len(passes) != 1 else ''}")
_next     = passes[0]
_mean_dur = sum(p["dur_min"] for p in passes) / len(passes)
_max_of_max = max(p["max_el"] for p in passes)

m = st.columns(4)
m[0].metric("Total Passes",        len(passes))
m[1].metric("Next Pass",           _next["start"].strftime("%Y-%m-%d %H:%M UTC"))
m[2].metric("Next Max Elevation",  f"{_next['max_el']:.1f}°")
m[3].metric("Mean Duration",       f"{_mean_dur:.1f} min")
st.divider()

# ── Pass table ────────────────────────────────────────────────────────────────
st.markdown("### Pass Schedule")

_quality_colors = {
    "Overhead": "#4caf50",
    "Good":     "#4a9eff",
    "Low":      "#ffa726",
    "Very Low": "#6b7a9e",
}

table_rows = [
    {
        "Pass #":              p["n"],
        "Start (UTC)":         p["start"].strftime("%Y-%m-%d %H:%M:%S"),
        "End (UTC)":           p["end"].strftime("%H:%M:%S"),
        "Duration (min)":      round(p["dur_min"], 1),
        "Max El (°)":          round(p["max_el"], 1),
        "Az at Max El (°)":    round(p["az_max_el"], 1),
        "Quality":             _quality_label(p["max_el"]),
    }
    for p in passes
]
df_passes = pd.DataFrame(table_rows)

# Colour the Quality column via pandas Styler
def _colour_quality(val: str) -> str:
    c = _quality_colors.get(val, "#e0e6f5")
    return f"color: {c}; font-weight: bold;"

try:
    styled_df = df_passes.style.map(_colour_quality, subset=["Quality"])
except AttributeError:
    # pandas < 2.1 fallback
    styled_df = df_passes.style.applymap(_colour_quality, subset=["Quality"])  # type: ignore[attr-defined]

st.dataframe(styled_df, use_container_width=True, hide_index=True)

# CSV download
_csv = io.StringIO()
df_passes.to_csv(_csv, index=False)
st.download_button(
    "⬇  Download passes as CSV",
    _csv.getvalue(),
    file_name=f"{sat_name.replace(' ', '_')}_passes.csv",
    mime="text/csv",
)
st.divider()

# ── Elevation profile plot ────────────────────────────────────────────────────
import plotly.graph_objects as go

st.markdown("### Elevation Profile")

fig = go.Figure()

for p in passes:
    color      = _PASS_COLORS[(p["n"] - 1) % len(_PASS_COLORS)]
    t_labels   = [t.strftime("%Y-%m-%d %H:%M:%S") for t in p["t_slice"]]
    hover_text = [
        f"El: {a:.1f}°<br>Az: {az:.1f}°<br>Range: {d:.0f} km"
        for a, az, d in zip(p["alt_slice"], p["az_slice"], p["dist_slice"])
    ]
    fig.add_trace(go.Scatter(
        x          = t_labels,
        y          = p["alt_slice"].tolist(),
        mode       = "lines",
        name       = f"Pass {p['n']}  {p['start'].strftime('%m-%d %H:%M')}  ({p['max_el']:.1f}°)",
        line       = dict(color=color, width=2),
        hovertext  = hover_text,
        hoverinfo  = "text+x",
    ))

fig.add_hline(
    y=res["min_el"], line_dash="dash",
    line_color="#888", line_width=1,
    annotation_text=f"Min elevation ({res['min_el']:.0f}°)",
    annotation_position="bottom right",
)

fig.update_layout(
    title      = dict(
        text=f"Elevation Profile — {sat_name}  ·  {loc_label}",
        font=dict(color="#e0e6f5"),
    ),
    xaxis      = dict(
        title="Time (UTC)", color="#4a5c80",
        gridcolor="#1e2847", showgrid=True,
        tickangle=-30,
    ),
    yaxis      = dict(
        title="Elevation (°)", color="#4a5c80",
        gridcolor="#1e2847", showgrid=True,
        rangemode="tozero",
    ),
    paper_bgcolor = "#0b0f1e",
    plot_bgcolor  = "#111827",
    legend        = dict(
        bgcolor="#111827", bordercolor="#1e2847",
        font=dict(color="#e0e6f5", size=11),
    ),
    margin = dict(l=60, r=20, t=60, b=80),
)

st.plotly_chart(fig, use_container_width=True)

# ── References ────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "SGP4 propagation: Vallado et al. (2006) AIAA 2006-6753  ·  "
    "TLE accuracy: Vallado & Cefola (2012)  ·  "
    "Skyfield: Rhodes (2019) ascl:1907.024"
)
