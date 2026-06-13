"""
app.py — OpenMission  Streamlit front-end.

Sidebar  → constellation / orbit / simulation / payload / GS / ROI inputs
Run      → calls access.computeAccess()
Results  → propagation info bar · ROI metric cards · per-GS tabs
"""

from __future__ import annotations
from datetime import datetime, timedelta, date, time as dtime
import numpy as np
import pandas as pd
import streamlit as st

from propagator import (Re, mu, J2,
                        sso_inclination, ltan2raan, raan2ltan, orbPeriod,
                        repeat_cycle)
from access import computeAccess
from geo_data import listCountries, buildROIGrid, load_shapefile
from styles import _CSS, _BG_SVG, _WATERMARK_HTML, emblem_html, _USER_GUIDE_LINK

# ─────────────────────────────────────────────────────────────────────────────
# Page config  (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="OpenMission",
    page_icon="assets/emblem.png",   # OpenMission emblem
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants & defaults
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_GS = pd.DataFrame({
    "Name":       ["Helmos Observatory, NOA", "UoA GS – Psachna"],
    "Latitude":   [37.9856, 38.5697],
    "Longitude":  [22.1983, 23.6486],
    "Alt (m)":    [2340,    80],
    "Min El (°)": [30.0,    5.0],
})
_SAMPLE_OPTIONS   = ["30 s", "60 s", "120 s"]
_SAMPLE_SECS      = [30, 60, 120]

# ─────────────────────────────────────────────────────────────────────────────
# Session-state initialisation
# ─────────────────────────────────────────────────────────────────────────────
_SS: dict = {
    "results":         None,
    # RAAN / LTAN live-link state
    "_ltan_wgt":       10.5,
    "_raan_wgt":       320.68,
    "_prev_ltan":      10.5,
    "_prev_raan":      320.68,
    "_prev_epoch":     datetime(2026, 3, 2, 8, 23, 0),
    # GS table
    "gs_df":           _DEFAULT_GS.copy(),
    # Simulation duration number-input
    "_dur_days_input": 7,
}
for _k, _v in _SS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_td(td: timedelta | None) -> str:
    """Format a timedelta as hh h mm m ss s, or 'N/A'."""
    if td is None:
        return "N/A"
    total = int(td.total_seconds())
    h, r  = divmod(abs(total), 3600)
    m, s  = divmod(r, 60)
    return f"{h:02d}h {m:02d}m {s:02d}s"


def _raan_drift_deg_day(alt_km: float, inc_deg: float, e: float = 0.001) -> float:
    """J2 secular RAAN drift rate [deg/day] — shown in the info bar."""
    a = Re + alt_km
    p = a * (1.0 - e**2)
    n = np.sqrt(mu / a**3)
    k = 1.5 * n * J2 * (Re / p)**2
    return float(np.degrees(-k * np.cos(np.radians(inc_deg))) * 86400.0)



# ─────────────────────────────────────────────────────────────────────────────
# Design system — injected from shared styles.py
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(_CSS,            unsafe_allow_html=True)
st.markdown(_BG_SVG,         unsafe_allow_html=True)
st.markdown(_WATERMARK_HTML, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(emblem_html("/ SSO Coverage Analysis", variant="orbit"), unsafe_allow_html=True)
    st.markdown(_USER_GUIDE_LINK, unsafe_allow_html=True)
    st.divider()

    # ── 1. Constellation ─────────────────────────────────────────────────────
    st.markdown("#### Constellation")
    col_a, col_b = st.columns(2)
    num_planes     = col_a.number_input("Planes",    min_value=1, max_value=30, value=3)
    sats_per_plane = col_b.number_input("Sats/plane",min_value=1, max_value=30, value=3)
    total_sats     = num_planes * sats_per_plane
    st.caption(f"Total satellites: **{total_sats}**")
    st.divider()

    # ── 2. Orbital Elements ──────────────────────────────────────────────────
    st.markdown("#### Orbital Elements")

    altitude     = st.number_input("Altitude (km)", 200, 2000, 500, step=10)
    eccentricity = 0.001
    sma          = Re + altitude
    period_min   = orbPeriod(altitude)
    sso_inc      = sso_inclination(altitude)
    drift        = _raan_drift_deg_day(altitude, sso_inc)

    st.caption(
        f"SMA **{sma:.1f} km** · Period **{period_min:.2f} min** · "
        f"J2 drift **{drift:+.4f} °/day** · e = {eccentricity}"
    )

    auto_sso = st.toggle("Auto SSO inclination", value=True)
    if auto_sso:
        inclination = sso_inc
        st.caption(f"Inclination: **{inclination:.4f}°**  (J2-derived SSO)")
    else:
        inclination = st.number_input(
            "Inclination (°)", 0.0, 180.0,
            value=float(f"{sso_inc:.4f}"), step=0.001, format="%.4f"
        )

    # Epoch — needed here for RAAN ↔ LTAN conversion
    _ecol1, _ecol2 = st.columns(2)
    start_date = _ecol1.date_input("Epoch date (UTC)", value=date(2026, 3, 2))
    start_time = _ecol2.time_input("Epoch time (UTC)", value=dtime(8, 23, 0), step=60)
    epoch = datetime(start_date.year, start_date.month, start_date.day,
                     start_time.hour, start_time.minute, start_time.second)

    # Recompute RAAN when epoch changes (LTAN is kept as-is)
    if epoch != st.session_state["_prev_epoch"]:
        st.session_state["_raan_wgt"]    = round(ltan2raan(epoch, st.session_state["_ltan_wgt"]), 4)
        st.session_state["_prev_raan"]   = st.session_state["_raan_wgt"]
        st.session_state["_prev_epoch"]  = epoch

    # LTAN ↔ RAAN live-link.
    # Streamlit ≥1.28 forbids writing a widget's session-state key after it is
    # rendered in the same run.  Sync must therefore happen BEFORE the widgets
    # are instantiated so that the computed value is already in session_state
    # when the widget reads it.
    _cl = st.session_state["_ltan_wgt"]
    _cr = st.session_state["_raan_wgt"]
    _ltan_chg = abs(_cl - st.session_state["_prev_ltan"]) > 4e-3
    _raan_chg = abs(_cr - st.session_state["_prev_raan"]) > 4e-3

    if _ltan_chg and not _raan_chg:
        _nr = round(ltan2raan(epoch, _cl), 4)
        st.session_state["_raan_wgt"]  = _nr
        st.session_state["_prev_ltan"] = _cl
        st.session_state["_prev_raan"] = _nr
    elif _raan_chg and not _ltan_chg:
        _nl = round(raan2ltan(epoch, _cr), 4)
        st.session_state["_ltan_wgt"]  = _nl
        st.session_state["_prev_raan"] = _cr
        st.session_state["_prev_ltan"] = _nl
    else:
        st.session_state["_prev_ltan"] = _cl
        st.session_state["_prev_raan"] = _cr

    ltan_val = st.number_input(
        "LTAN (decimal hours)", 0.0, 24.0,
        key="_ltan_wgt", step=0.01, format="%.2f",
        help="Local Time of Ascending Node"
    )
    raan_val = st.number_input(
        "RAAN₀ (°)", 0.0, 360.0,
        key="_raan_wgt", step=0.01, format="%.4f",
        help="Right Ascension of first orbital plane's ascending node"
    )

    st.divider()

    # ── 3. Simulation ────────────────────────────────────────────────────────
    st.markdown("#### Simulation")

    exact_days, _, _  = repeat_cycle(period_min, max_days=365)
    approx_days, _, _ = repeat_cycle(period_min, max_days=30)
    st.caption(f"Exact repeat cycle: **{exact_days} days**")
    st.caption(f"Approximated repeat cycle: **{approx_days} days**")

    use_repeat = st.checkbox("Use repeat cycle as simulation duration", value=False)
    if use_repeat:
        st.session_state["_dur_days_input"] = approx_days if exact_days > 30 else exact_days

    dur_days = st.number_input(
        "Duration (days)", min_value=1, max_value=365, step=1,
        key="_dur_days_input",
    )
    step_idx   = st.selectbox("Sample step", _SAMPLE_OPTIONS,   index=1)

    sample_sec = _SAMPLE_SECS[_SAMPLE_OPTIONS.index(step_idx)]
    stop_dt    = epoch + timedelta(days=dur_days)
    n_steps    = int(dur_days * 86400 / sample_sec) + 1
    st.caption(
        f"Stop: **{stop_dt.strftime('%Y-%m-%d %H:%M')}** UTC · "
        f"**{n_steps:,}** steps"
    )
    st.divider()

    # ── 4. Payload constraint ────────────────────────────────────────────────
    st.markdown("#### Payload Constraint")
    off_nadir  = st.slider("Max off-nadir angle (°)", 0, 90, 45, step=1)
    min_el_roi = 90.0 - off_nadir
    st.caption(
        f"Equivalent min elevation for ROI: **{min_el_roi:.0f}°**  "
        f"(= 90° − {off_nadir}°)"
    )
    st.divider()

    # ── 5. Ground stations ───────────────────────────────────────────────────
    st.markdown("#### Ground Stations")

    # One expander per existing station — caption-only display + Remove button
    for _gs_idx, _gs_row in st.session_state["gs_df"].iterrows():
        with st.expander(_gs_row["Name"] or f"Station {_gs_idx + 1}", expanded=False):
            st.caption(f"Lat: **{_gs_row['Latitude']:.4f}°N** · "
                       f"Lon: **{_gs_row['Longitude']:.4f}°E** · "
                       f"Alt: **{int(_gs_row['Alt (m)'])} m** · "
                       f"Min El: **{_gs_row['Min El (°)']:.1f}°**")
            if st.button("🗑 Remove", key=f"del_gs_{_gs_idx}"):
                st.session_state["gs_df"] = (
                    st.session_state["gs_df"]
                    .drop(index=_gs_idx)
                    .reset_index(drop=True)
                )
                st.rerun()

    # Add ground station expander
    with st.expander("➕ Add ground station", expanded=False):
        _new_name   = st.text_input("Name", key="new_gs_name")
        _nc1, _nc2  = st.columns(2)
        _new_lat    = _nc1.number_input("Latitude (°N)",   -90.0,  90.0,  37.9856, format="%.4f", key="new_gs_lat")
        _new_lon    = _nc2.number_input("Longitude (°E)", -180.0, 180.0,  22.1983, format="%.4f", key="new_gs_lon")
        _new_alt    = _nc1.number_input("Altitude (m)",       0,   9000,       0,               key="new_gs_alt")
        _new_el     = _nc2.number_input("Min elevation (°)", 0.0,  90.0,     0.0, format="%.1f", key="new_gs_el")
        if st.button("Add", key="btn_add_gs"):
            if _new_name.strip():
                _new_row = pd.DataFrame([{
                    "Name":       _new_name.strip(),
                    "Latitude":   _new_lat,
                    "Longitude":  _new_lon,
                    "Alt (m)":    _new_alt,
                    "Min El (°)": _new_el,
                }])
                st.session_state["gs_df"] = pd.concat(
                    [st.session_state["gs_df"], _new_row], ignore_index=True
                )
                st.rerun()

    gs_edited = st.session_state["gs_df"]
    st.divider()

    # ── 6. ROI ───────────────────────────────────────────────────────────────
    st.markdown("#### Region of Interest")

    # Optional custom shapefile — overrides the built-in polygons when provided
    uploaded_files = st.file_uploader(
        "Upload custom boundary shapefile",
        type=["shp", "dbf", "shx"],
        accept_multiple_files=True,
        help="Select all three files together: .shp, .dbf and .shx"
    )

    shp_geom = None
    shp_label = None

    if uploaded_files:
        # Check all three required files are present
        exts = {f.name.split('.')[-1].lower() for f in uploaded_files}
        if {'shp', 'dbf', 'shx'}.issubset(exts):
            import tempfile, os
            tmp_dir = tempfile.mkdtemp()
            for f in uploaded_files:
                with open(os.path.join(tmp_dir, f.name), 'wb') as out:
                    out.write(f.read())
            shp_file = [f for f in uploaded_files if f.name.endswith('.shp')][0]
            shp_path = os.path.join(tmp_dir, shp_file.name)
            try:
                shp_geom  = load_shapefile(shp_path)
                shp_label = shp_file.name
                st.success(f"Loaded: **{shp_label}**")
            except Exception as e:
                st.error(f"Shapefile error: {e}")
        else:
            missing = {'shp', 'dbf', 'shx'} - exts
            st.warning(f"Missing files: {', '.join('.' + e for e in missing)}")

    # Single-point mode — bypasses buildROIGrid entirely
    single_point = st.toggle(
        "Single point",
        value=False,
        help="Enter one lat/lon directly. Skips the polygon grid — useful for "
             "validating against a known reference point.",
        disabled=shp_geom is not None,
    )

    if single_point and shp_geom is None:
        _sp_col1, _sp_col2 = st.columns(2)
        sp_lat = _sp_col1.number_input("Latitude (°N)",  -90.0,  90.0, 37.9856, step=0.0001, format="%.4f")
        sp_lon = _sp_col2.number_input("Longitude (°E)", -180.0, 180.0, 23.7275, step=0.0001, format="%.4f")
        lat_roi  = np.array([sp_lat])
        lon_roi  = np.array([sp_lon])
        n_roi    = 1
        country  = ""          # not used in single-point mode
        st.caption(f"Single ROI point: **({sp_lat:.4f}°N, {sp_lon:.4f}°E)**")
    else:
        # Country selector (used when no shapefile is provided)
        countries  = listCountries()
        default_ci = countries.index("Greece") if "Greece" in countries else 0
        country    = st.selectbox(
            "Country (built-in outline)",
            countries,
            index=default_ci,
            disabled=shp_geom is not None,
            help="Ignored when a custom shapefile is loaded above.",
        )
        col_r, col_c = st.columns(2)
        roi_rows   = col_r.number_input("Rows", 2, 30, 5)
        roi_cols   = col_c.number_input("Cols", 2, 50, 10)

        try:
            lat_roi, lon_roi = buildROIGrid(country, roi_rows, roi_cols, geom=shp_geom)
            n_roi = len(lat_roi)
        except Exception as _e:
            st.error(f"ROI error: {_e}")
            lat_roi, lon_roi, n_roi = np.array([]), np.array([]), 0

        if n_roi > 0:
            roi_src = shp_label if shp_geom is not None else country
            st.caption(f"Interior grid points: **{n_roi}**  ({roi_src})")
        else:
            st.warning("No interior points — increase rows/cols.")

    st.divider()

    # ── Run button ───────────────────────────────────────────────────────────
    run_ok     = n_roi > 0
    run_btn    = st.button(
        "▶  Run Analysis",
        type="primary",
        use_container_width=True,
        disabled=not run_ok,
    )

# ─────────────────────────────────────────────────────────────────────────────
# RUN LOGIC  (outside sidebar context so spinner appears in main area)
# ─────────────────────────────────────────────────────────────────────────────
if run_btn and run_ok:
    # Build satellite list (Walker-delta-like: equal RAAN spacing, equal M0 spacing)
    sats = []
    for p in range(num_planes):
        raan_p = (raan_val + p * 360.0 / num_planes) % 360.0
        for s in range(sats_per_plane):
            sats.append({
                "a":        sma,
                "e":        eccentricity,
                "i_deg":    inclination,
                "RAAN_deg": raan_p,
                "argp_deg": 0.0,
                "M0_deg":   (s * 360.0 / sats_per_plane) % 360.0,
            })

    # Build GS list — skip rows that are incomplete (new/blank rows from dynamic editor)
    gs_list = []
    for _, row in gs_edited.dropna(subset=["Name", "Latitude", "Longitude"]).iterrows():
        name = str(row["Name"]).strip()
        if not name:
            continue
        gs_list.append({
            "name":          name,
            "lat":           float(row["Latitude"]),
            "lon":           float(row["Longitude"]),
            "min_elevation": float(row["Min El (°)"]) if pd.notna(row["Min El (°)"]) else 5.0,
        })

    roi_list = [
        {"lat": float(la), "lon": float(lo), "min_elevation": min_el_roi}
        for la, lo in zip(lat_roi, lon_roi)
    ]

    with st.spinner(
        f"Propagating {total_sats} satellites · "
        f"{n_roi} ROI points · {n_steps:,} steps …"
    ):
        try:
            res = computeAccess(sats, gs_list, roi_list, epoch, stop_dt, float(sample_sec))
            res["_cfg"] = {
                "total_sats":  total_sats,  "sma":      sma,
                "e":           eccentricity,"inc":       inclination,
                "raan0":       raan_val,    "ltan":      ltan_val,
                "off_nadir":   off_nadir,   "n_steps":   n_steps,
                "country":     (shp_label if shp_geom is not None
                               else (f"Single point ({sp_lat:.4f}°N, {sp_lon:.4f}°E)"
                                     if single_point else country)),
                "n_roi":       n_roi,
                "dur_label":   f"{dur_days} days", "sample_sec":sample_sec,
                "epoch":       epoch,       "stop":      stop_dt,
            }
            st.session_state["results"] = res
        except Exception as exc:
            st.error(f"Analysis failed: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────────────────────────────────────
res = st.session_state["results"]

if res is None:
    # ── Welcome / no-results screen ──────────────────────────────────────────
    st.markdown("## OpenMission — SSO Constellation Access Analyser")
    st.markdown(
        "Configure your constellation in the sidebar, then press "
        "**▶ Run Analysis**."
    )
    st.info(
        "**Quick-start defaults:** 3 planes × 3 satellites, 500 km SSO, "
        "LTAN 10.5 h, Greece ROI (5 × 10 grid), Helmos + Psachna ground "
        "stations, 7-day simulation at 60 s steps."
    )
    st.stop()

# ── Results ──────────────────────────────────────────────────────────────────
cfg = res["_cfg"]

# 1. Propagation info bar
st.markdown("### Propagation Summary")
c = st.columns(8)
c[0].metric("Satellites",    cfg["total_sats"])
c[1].metric("SMA",           f"{cfg['sma']:.1f} km")
c[2].metric("e",             f"{cfg['e']}")
c[3].metric("Inclination",   f"{cfg['inc']:.4f}°")
c[4].metric("RAAN₀",    f"{cfg['raan0']:.4f}°")
c[5].metric("LTAN",          f"{cfg['ltan']:.2f} h")
c[6].metric("Off-nadir",     f"{cfg['off_nadir']}°")
c[7].metric("Steps",         f"{cfg['n_steps']:,}")

st.caption(
    f"Epoch {cfg['epoch'].strftime('%Y-%m-%d %H:%M')} UTC  "
    f"→  {cfg['stop'].strftime('%Y-%m-%d %H:%M')} UTC  "
    f"({cfg['dur_label']}, {cfg['sample_sec']} s step)"
)
st.divider()

# 2. ROI metric cards
st.markdown(f"### ROI Metrics — {cfg['country']}")
r = st.columns(4)
r[0].metric(
    "Coverage",
    f"{res['roi_coverage']:.1f} %",
    help="Fraction of ROI grid points with at least one satellite pass",
)
r[1].metric(
    "Mean Access Ratio",
    f"{res['roi_mean_access_ratio']:.2f} %",
    help="Average fraction of simulation time a satellite is overhead",
)
r[2].metric(
    "Mean Revisit",
    _fmt_td(res["roi_mean_revisit"]),
    help="Average gap between consecutive passes, averaged over all ROI points",
)
r[3].metric(
    "Max Revisit",
    _fmt_td(res["roi_max_revisit"]),
    help="Worst-case gap between passes over all ROI points",
)

# ROI per-point table + CSV export
st.markdown("### ROI Per-Point Breakdown")
_roi_rows = []
for _i, _pt in enumerate(res["roi_results"], start=1):
    _mr = _pt["mean_revisit"].total_seconds() / 3600.0 if _pt["mean_revisit"] else None
    _xr = _pt["max_revisit"].total_seconds()  / 3600.0 if _pt["max_revisit"]  else None
    _roi_rows.append({
        "Point #":           _i,
        "Latitude (°N)":     round(_pt["lat"], 6),
        "Longitude (°E)":    round(_pt["lon"], 6),
        "Passes":            len(_pt["merged_windows"]),
        "Mean Revisit (h)":  round(_mr, 4) if _mr is not None else "—",
        "Max Revisit (h)":   round(_xr, 4) if _xr is not None else "—",
        "Access Ratio (%)":  round(_pt["access_ratio"], 4),
        "Max Elevation (°)": _pt["overall_max_el"] if _pt["overall_max_el"] is not None else "—",
    })
_roi_df = pd.DataFrame(_roi_rows)

st.dataframe(_roi_df, use_container_width=True, hide_index=True)
st.download_button(
    label="Download ROI metrics CSV",
    data=_roi_df.to_csv(index=False).encode("utf-8"),
    file_name="roi_metrics.csv",
    mime="text/csv",
)
st.divider()

# 3. Ground station results — one tab per GS
gs_results = res["gs_results"]
if not gs_results:
    st.info("No ground stations were configured.")
    st.stop()

st.markdown("### Ground Station Results")
tabs = st.tabs([r["name"] for r in gs_results])

for idx, (tab, gsr) in enumerate(zip(tabs, gs_results)):
    with tab:
        merged    = gsr["merged_windows"]
        n_passes  = len(merged)

        if n_passes == 0:
            st.warning("No access windows during the simulation period.")
            continue

        dur_min = [(w['e'] - w['s']).total_seconds() / 60.0 for w in merged]
        avg_dur = sum(dur_min) / len(dur_min)

        # Summary metrics row
        g = st.columns(5)
        g[0].metric("Passes",       n_passes)
        g[1].metric("Access ratio", f"{gsr['access_ratio']:.2f} %")
        g[2].metric("Avg duration", f"{avg_dur:.1f} min")
        g[3].metric("Mean revisit", _fmt_td(gsr["mean_revisit"]))
        g[4].metric("Max revisit",  _fmt_td(gsr["max_revisit"]))

        # Pass table
        rows = [
            {
                "Start (UTC)":      w['s'].strftime("%Y-%m-%d %H:%M:%S"),
                "End (UTC)":        w['e'].strftime("%H:%M:%S"),
                "Duration (min)":   round(d, 2),
                "Max Elevation (°)": round(w['max_el'], 1),
            }
            for w, d in zip(merged, dur_min)
        ]
        gs_df = pd.DataFrame(rows)
        st.dataframe(gs_df, use_container_width=True, hide_index=True)
        st.download_button(
            label="Download pass list CSV",
            data=gs_df.to_csv(index=False).encode("utf-8"),
            file_name=f"passes_{gsr['name'].replace(' ', '_')}.csv",
            mime="text/csv",
            key=f"dl_gs_{idx}_{gsr['name']}",
        )
