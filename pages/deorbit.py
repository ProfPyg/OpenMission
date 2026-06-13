"""
pages/deorbit.py — Orbital Lifetime Estimator for OpenMission.

Atmospheric density: NRLMSISE-00 via nrlmsise00 Python package.
Drag model:  Vallado (2013) §9.5 / King-Hele (1964).
Integration: RK4 with adaptive step (1 × T_orb, 0.1 × T_orb below 200 km).
Solar inputs: ECSS-E-ST-10-04C Rev.1 (2020) Tables G-1 to G-3.
Compliance:   IADC Space Debris Mitigation Guidelines Rev.4 (2024) — 25-year rule.
"""

from __future__ import annotations
from datetime import datetime, timedelta
import numpy as np
import streamlit as st
from styles import _CSS, _BG_SVG, _WATERMARK_HTML, emblem_html

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Lifetime Estimator — OpenMission",
    page_icon="\U0001f6f0",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Design system (imported from styles.py)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(_CSS,            unsafe_allow_html=True)
st.markdown(_BG_SVG,         unsafe_allow_html=True)
st.markdown(_WATERMARK_HTML, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Physical constants
# ─────────────────────────────────────────────────────────────────────────────
_Re     = 6378.137          # km — WGS-84 equatorial radius
_mu     = 398600.4418       # km³/s²
_mu_SI  = _mu * 1e9         # m³/s²
_g0     = 9.80665           # m/s²
_DEORBIT_ALT_KM = 80.0      # deorbit threshold, fixed (DRAMA OSCAR convention)
_MAX_YEARS      = 100        # integration ceiling

# ECSS-E-ST-10-04C Rev.1 (2020) Tables G-1 to G-3
_SOLAR = {
    "Solar Min":  {"F107": 65,  "F107a": 65,  "Ap": 0},
    "Solar Mean": {"F107": 140, "F107a": 140, "Ap": 15},
    "Solar Max":  {"F107": 250, "F107a": 250, "Ap": 45},
}
_SOLAR_COLORS = {
    "Solar Min":  "#4a9eff",
    "Solar Mean": "#ffa726",
    "Solar Max":  "#e8001c",
}

# ─────────────────────────────────────────────────────────────────────────────
# Atmosphere model
# ─────────────────────────────────────────────────────────────────────────────

def _exp_atm_density(alt_km: float) -> float:
    """
    Simple exponential atmosphere fallback [kg/m³].
    Used when nrlmsise00 is unavailable or raises an exception.
    Reference: Vallado (2013) Appendix C, Table C-1.
    """
    # Piecewise exponential model (scale height varies with altitude)
    _layers = [
        (0,    86,   1.225,   8.44),
        (86,   110,  5.66e-6, 7.71),
        (110,  150,  6.00e-8, 7.00),
        (150,  200,  2.07e-9, 7.17),
        (200,  300,  1.98e-10,10.5),
        (300,  500,  5.21e-12,21.0),
        (500,  700,  6.80e-14,29.0),
        (700, 2000,  4.60e-15,40.0),
    ]
    for lo, hi, rho0, H in _layers:
        if alt_km <= hi:
            return rho0 * np.exp(-(alt_km - lo) / H)
    return 4.60e-15 * np.exp(-(alt_km - 700) / 40.0)


def _build_rho_interp(f107: float, f107a: float, ap: float,
                       epoch: datetime):
    """
    Pre-compute NRLMSISE-00 density on a fine altitude grid and return a
    fast numpy interpolator.  Pre-computing avoids ~100k individual calls
    inside the RK4 loop (reduces runtime from minutes to seconds).

    Ref: Picone et al. (2002). NRLMSISE-00. J. Geophys. Res., 107(A12).
    """
    from scipy.interpolate import interp1d
    alts = np.concatenate([
        np.linspace(80,  200,  150),
        np.linspace(200, 500,  200),
        np.linspace(500, 2000, 200),
    ])
    rhos = np.empty(len(alts))

    try:
        from nrlmsise00 import msise_flat
        for i, alt in enumerate(alts):
            try:
                res = msise_flat(epoch, float(alt),
                                 lat=0, lon=0,
                                 f107=f107, f107a=f107a, ap=ap)
                rho = float(res[5]) * 1000.0  # g/cm³ → kg/m³
                rhos[i] = rho if np.isfinite(rho) and rho > 0 else _exp_atm_density(alt)
            except Exception:
                rhos[i] = _exp_atm_density(alt)
    except ImportError:
        for i, alt in enumerate(alts):
            rhos[i] = _exp_atm_density(alt)

    return interp1d(alts, rhos, kind="linear",
                    bounds_error=False,
                    fill_value=(rhos[0], rhos[-1]))


# ─────────────────────────────────────────────────────────────────────────────
# Dynamics
# ─────────────────────────────────────────────────────────────────────────────

def _drag_da_dt(a_km: float, rho: float,
                Cd: float, A_m2: float, m_kg: float) -> float:
    """
    da/dt from atmospheric drag [km/s], circular-orbit approximation.

    Derived from dE/dt = F_drag · v_circ, E = -μ/(2a):
        -μ/(2a²) · da/dt = -(½ Cd A/m ρ v²) · v_circ
        da/dt = -Cd · (A/m) · ρ · √(μ · a)            [m/s]

    All quantities in SI: μ [m³/s²], a [m], ρ [kg/m³].
    Ref: Vallado (2013) §9.5; King-Hele (1987) §2.
    """
    a_m    = a_km * 1e3
    da_m_s = -Cd * (A_m2 / m_kg) * rho * np.sqrt(_mu_SI * a_m)  # m/s
    return da_m_s / 1e3


def _thrust_da_dt(a_km: float, T_N: float, m_kg: float) -> float:
    """
    da/dt from continuous retrograde thrust [km/s].

    da/dt = -2a·T / (m·v_c)   where v_c = √(μ/a)

    Ref: Gauss VOP for tangential perturbation, circular orbit.
    """
    a_m  = a_km * 1e3
    v_ms = np.sqrt(_mu_SI / a_m)       # m/s
    da_ms = -2.0 * a_m * T_N / (m_kg * v_ms)   # m/s
    return da_ms / 1e3


# ─────────────────────────────────────────────────────────────────────────────
# RK4 integrator
# ─────────────────────────────────────────────────────────────────────────────

def _integrate(a0_km: float,
               Cd: float, A_m2: float, m0_kg: float,
               strategy: str,
               T_N: float, Isp_s: float, prop0_kg: float,
               rho_fn,
               epoch: datetime,
               dv_override: float = 0.0,
               a_transfer_m: float = 0.0,
               ) -> dict:
    """
    RK4 orbital lifetime integration — all state variables in SI (m, kg, s).

    Drag:   da/dt = -Cd * (A/m) * rho * sqrt(mu * a)          [m/s]
    Thrust: da/dt = -2 * a * T / (m * v_circ)                 [m/s, retrograde]
    Mass:   dm/dt = -T / (Isp * g0)                            [kg/s]

    Returns a dict with:
        t_days, alt_km  — downsampled trajectory arrays
        lifetime_days   — total time to deorbit [days]
        dv_used_m_s     — ΔV applied (propulsion, 0 if none)
        prop_consumed_kg— propellant mass consumed [kg]
        deorbit_date    — datetime of predicted deorbit
        exceeded_max    — True if lifetime > _MAX_YEARS
    """
    # ── SI constants ───────────────────────────────────────────────────────────
    Re_m      = _Re * 1e3               # m
    deorbit_m = _DEORBIT_ALT_KM * 1e3  # m (80 000 m)
    dry_mass  = m0_kg - prop0_kg        # kg

    # ── State (all SI) ────────────────────────────────────────────────────────
    a           = a0_km * 1e3           # m  — updated by burn before loop
    m           = m0_kg                 # kg — updated by burn before loop
    t           = 0.0                   # s
    prop_rem    = prop0_kg              # kg
    prop_burned = 0.0                   # kg
    dv_used     = 0.0                   # m/s

    # Record pre-burn altitude for plot and debug output
    h0_km = (a - Re_m) / 1e3           # km — always h0 at this point

    # ── Two-burn circularization (Hohmann disposal) ────────────────────────────
    # STEP 1  satellite is at h0 with wet mass
    # STEP 2  two impulses lower perigee to target and circularize there:
    #         prop_consumed = prop0_kg (already computed in sidebar)
    #         a  ← r2 (target circular orbit) — orbit drops instantaneously
    #         m  ← m_wet − prop_consumed
    # STEP 3  integration loop starts from the circular r2, natural drag only
    if strategy == "Two-burn circularization" and prop0_kg > 0 and dry_mass > 0:
        dv = dv_override if dv_override > 0 else Isp_s * _g0 * np.log(m0_kg / dry_mass)
        # a_transfer_m = r2 (target circular) → already circularized, drag decays from r2
        a           = max(a_transfer_m, Re_m + deorbit_m)
        m           = m0_kg - prop0_kg
        prop_rem    = 0.0
        prop_burned = prop0_kg
        dv_used     = dv

    use_continuous = (strategy == "Continuous thrust" and T_N > 0 and prop0_kg > 0)


    max_t_s = _MAX_YEARS * 365.25 * 86400.0

    # Plot includes h0 as first point; for the disposal burn a second point at h1
    # (both at t=0) so the impulsive drop is visible on the altitude chart
    t_list   = [0.0]
    alt_list = [h0_km]
    if strategy == "Two-burn circularization" and prop0_kg > 0:
        t_list.append(0.0)
        alt_list.append((a - Re_m) / 1e3)

    while (a - Re_m) > deorbit_m and t < max_t_s:
        # Orbital period — _mu in km³/s² so convert a to km first
        T_orb = 2.0 * np.pi * np.sqrt((a / 1e3)**3 / _mu)   # s
        h     = T_orb if (a - Re_m) >= 200e3 else 0.1 * T_orb
        h     = min(h, max_t_s - t)
        if h <= 0:
            break

        if use_continuous and prop_rem > 0.0:
            # ── Continuous retrograde thrust + drag: 2-D state [a (m), m (kg)] ──
            dm_rate = -T_N / (Isp_s * _g0)          # kg/s  (negative)
            # Clip step so we never over-burn the remaining propellant
            h_eff   = min(h, -prop_rem / dm_rate)

            def _f(a_, m_):
                alt_km  = max(_DEORBIT_ALT_KM, (a_ - Re_m) / 1e3)
                rho_    = max(float(rho_fn(alt_km)), 1e-30)
                v_c     = np.sqrt(_mu_SI / a_)
                da_drag = -Cd * (A_m2 / m_) * rho_ * np.sqrt(_mu_SI * a_)  # m/s
                da_thr  = -2.0 * a_ * T_N / (m_ * v_c)                     # m/s
                return da_drag + da_thr, dm_rate

            k1a, k1m = _f(a,               m              )
            k2a, k2m = _f(a+k1a*h_eff/2,  m+k1m*h_eff/2 )
            k3a, k3m = _f(a+k2a*h_eff/2,  m+k2m*h_eff/2 )
            k4a, k4m = _f(a+k3a*h_eff,    m+k3m*h_eff   )

            a_new = a + h_eff * (k1a + 2*k2a + 2*k3a + k4a) / 6.0
            m_new = m + h_eff * (k1m + 2*k2m + 2*k3m + k4m) / 6.0
            m_new = max(m_new, dry_mass)     # cannot drop below dry mass

            dm_step     = max(0.0, m - m_new)
            prop_rem    = max(0.0, prop_rem - dm_step)
            prop_burned += dm_step
            dv_used     += (T_N / m) * h_eff   # ΔV ≈ (T/m) × dt [m/s]

            a  = max(a_new, Re_m + deorbit_m / 2.0)
            m  = m_new
            t += h_eff

        else:
            # ── Natural drag only: 1-D state [a (m)] ────────────────────────
            def _g(a_):
                alt_km = max(_DEORBIT_ALT_KM, (a_ - Re_m) / 1e3)
                rho_   = max(float(rho_fn(alt_km)), 1e-30)
                return -Cd * (A_m2 / m) * rho_ * np.sqrt(_mu_SI * a_)  # m/s

            k1 = _g(a)
            k2 = _g(a + k1*h/2)
            k3 = _g(a + k2*h/2)
            k4 = _g(a + k3*h)
            a += h * (k1 + 2*k2 + 2*k3 + k4) / 6.0
            t += h

        t_list.append(t / 86400.0)
        alt_list.append(max(0.0, (a - Re_m) / 1e3))   # km

    t_arr   = np.array(t_list)
    alt_arr = np.array(alt_list)

    # Downsample to ≤ 2000 points for plotting
    if len(t_arr) > 2000:
        idx     = np.round(np.linspace(0, len(t_arr) - 1, 2000)).astype(int)
        t_arr   = t_arr[idx]
        alt_arr = alt_arr[idx]

    lifetime_days = t / 86400.0
    return {
        "t_days":           t_arr,
        "alt_km":           alt_arr,
        "lifetime_days":    lifetime_days,
        "dv_used_m_s":      dv_used,
        "prop_consumed_kg": prop_burned,
        "deorbit_date":     epoch + timedelta(days=lifetime_days),
        "exceeded_max":     t >= max_t_s,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Session-state defaults
# ─────────────────────────────────────────────────────────────────────────────
_DEORBIT_SS: dict = {
    "do_dry_mass":   5.5,
    "do_area":       0.03,
    "do_cd":         2.2,
    "do_altitude":   500,
    "do_propulsion": False,
    "do_thrust_mN":  1.0,
    "do_isp":        200.0,
    "do_prop_mass":  0.5,
    "do_strategy":   "Two-burn circularization",
    "do_solar":      "Solar Mean",
    "do_results":    None,
}
for _k, _v in _DEORBIT_SS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(emblem_html("/ Lifetime Estimator", variant="decay"), unsafe_allow_html=True)
    st.divider()

    # ── 1. Satellite Parameters ───────────────────────────────────────────────
    st.markdown("#### Satellite Parameters")
    dry_mass = st.number_input("Dry mass (kg)", min_value=0.1, max_value=10000.0,
                               step=0.1, format="%.2f", key="do_dry_mass")
    area = st.number_input("Cross-sectional area (m²)", min_value=0.001, max_value=100.0,
                           step=0.001, format="%.4f", key="do_area")
    cd   = st.slider("Drag coefficient Cd", min_value=1.5, max_value=3.0,
                     step=0.05, key="do_cd")
    # Peek at propulsion state so the wet-mass chip stays accurate on every run
    _prop_peek = (st.session_state.get("do_prop_mass", 0.5)
                  if st.session_state.get("do_propulsion", False) else 0.0)
    wet_mass = dry_mass + _prop_peek
    st.caption(f"Wet mass: **{wet_mass:.2f} kg**")
    beta = wet_mass / (cd * area)
    st.caption(f"Ballistic coefficient β = **{beta:.2f} kg/m²**")
    st.divider()

    # ── 2. Initial Orbit ──────────────────────────────────────────────────────
    st.markdown("#### Initial Orbit")
    altitude = st.number_input("Altitude (km)", min_value=150, max_value=500,
                               step=10, key="do_altitude")
    if altitude == 500:
        st.warning(
            "At 500 km the NRLMSISE-00 density is at the lower boundary of "
            "reliable accuracy. Results above ~500 km should be treated as "
            "indicative only — use DRAMA OSCAR for high-altitude missions.",
            icon="⚠️",
        )
    # Note: orbital inclination is intentionally not an input here. The lifetime
    # estimator integrates a scalar da/dt and samples NRLMSISE-00 at the equator
    # (phi=0), so inclination does not enter the calculation — exposing it would
    # imply a dependence that does not exist.
    st.caption("Eccentricity: **0.001** (fixed — circular orbit)")
    st.caption(f"Deorbit threshold: **{_DEORBIT_ALT_KM:.0f} km** (DRAMA OSCAR convention)")
    st.divider()

    # ── 3. Propulsion ─────────────────────────────────────────────────────────
    st.markdown("#### Propulsion")
    use_propulsion = st.toggle("Enable propulsion",
                               key="do_propulsion")
    if use_propulsion:
        prop_mass = st.number_input("Propellant mass (kg)", min_value=0.001, max_value=500.0,
                                    step=0.01, format="%.3f", key="do_prop_mass")
        thrust_mN = st.number_input("Thrust T (mN)", min_value=0.01, max_value=1000.0,
                                    step=0.1, format="%.2f", key="do_thrust_mN")
        isp = st.number_input("Specific impulse Isp (s)", min_value=1.0, max_value=10000.0,
                              step=1.0, format="%.0f", key="do_isp")
        _strategy_opts = ["Two-burn circularization", "Continuous thrust"]
        _strategy_saved = st.session_state["do_strategy"]
        _strategy_idx   = _strategy_opts.index(_strategy_saved)                           if _strategy_saved in _strategy_opts else 0
        strategy = st.selectbox(
            "Deorbit strategy",
            _strategy_opts,
            index=_strategy_idx,
            key="do_strategy",
        )
        prop_kg    = prop_mass
        T_N        = thrust_mN * 1e-3   # mN → N
        Isp_s      = isp
        _dv_single = 0.0
        _a_trans_m = 0.0

        if strategy == "Two-burn circularization":
            target_alt = st.number_input(
                "Target altitude (km)",
                min_value=100,
                max_value=max(101, altitude - 1),
                value=max(100, altitude - 50),
                step=10,
                help="Target circular altitude after the burn sequence",
            )
            _r1_m      = (_Re + altitude)   * 1e3
            _r2_m      = (_Re + target_alt) * 1e3
            _a_trans_m = (_r1_m + _r2_m) / 2.0
            _v1_ms     = np.sqrt(_mu_SI / _r1_m)
            _v_ap_ms   = np.sqrt(_mu_SI * (2.0 / _r1_m - 1.0 / _a_trans_m))
            _dv1       = _v1_ms - _v_ap_ms

            _v_pe_ms        = np.sqrt(_mu_SI * (2.0 / _r2_m - 1.0 / _a_trans_m))
            _v2_ms          = np.sqrt(_mu_SI / _r2_m)
            _dv2            = _v2_ms - _v_pe_ms
            _dv_total       = _dv1 + abs(_dv2)
            _m_wet          = dry_mass + prop_kg
            _m_after_burn1  = _m_wet        * np.exp(-_dv1        / (Isp_s * _g0))
            _m_after_burn2  = _m_after_burn1 * np.exp(-abs(_dv2)  / (Isp_s * _g0))
            _prop_burned    = _m_wet - _m_after_burn2
            burn_dur        = (_prop_burned * Isp_s * _g0 / T_N) if T_N > 0 else 0.0
            _dv_single      = _dv_total
            _a_trans_m      = _r2_m
            st.caption(f"ΔV burn 1 (perigee drop): **{_dv1:.1f} m/s**")
            st.caption(f"ΔV burn 2 (circularization): **{abs(_dv2):.1f} m/s**")
            st.caption(f"Total ΔV: **{_dv_total:.1f} m/s**")
            st.caption(f"Total propellant: **{_prop_burned:.4f} kg**")
            st.caption(f"Combined burn duration: **{burn_dur:.0f} s**")
            if _prop_burned > prop_kg:
                st.error("Insufficient propellant for this target altitude.")
            if burn_dur > 86400:
                st.warning("Combined burn duration exceeds 24 h — consider higher thrust.")
            prop_for_integrate = min(_prop_burned, prop_kg)

        elif strategy == "Continuous thrust":
            _dv_total = Isp_s * _g0 * np.log((dry_mass + prop_kg) / dry_mass) if dry_mass > 0 else 0.0
            st.caption(
                f"Total ΔV available: **{_dv_total:.2f} m/s** "
                f"({dry_mass + prop_kg:.2f} kg → {dry_mass:.2f} kg)"
            )
            prop_for_integrate = prop_kg

        else:
            prop_for_integrate = 0.0

    else:
        thrust_mN = prop_mass = isp = None
        strategy  = "Natural decay"
        T_N = prop_kg = Isp_s = 0.0
        prop_for_integrate = 0.0
        _dv_single = 0.0
        _a_trans_m = 0.0
    st.divider()

    # ── 4. Solar Activity ─────────────────────────────────────────────────────
    st.markdown("#### Solar Activity")
    st.caption("ECSS-E-ST-10-04C Rev.1, 15 June 2020")
    solar_choice = st.radio(
        "Solar activity scenario",
        options=["Solar Min", "Solar Mean", "Solar Max", "All three"],
        index=["Solar Min", "Solar Mean", "Solar Max", "All three"]
              .index(st.session_state.get("do_solar", "Solar Mean")),
        key="do_solar", label_visibility="collapsed",
    )
    if solar_choice != "All three":
        _sv = _SOLAR[solar_choice]
        st.caption(f"F10.7 = **{_sv['F107']}**  ·  F10.7a = **{_sv['F107a']}**  ·  Ap = **{_sv['Ap']}**")
    else:
        st.caption("Min: F10.7=65, Ap=0  ·  Mean: F10.7=140, Ap=15  ·  Max: F10.7=250, Ap=45")
    st.divider()

    # ── Run ───────────────────────────────────────────────────────────────────
    run_btn = st.button("▶  Compute Lifetime", type="primary", use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## Orbital Lifetime Estimator")
st.markdown(
    "Atmospheric drag modelled with **NRLMSISE-00** · "
    "RK4 integration · ECSS-E-ST-10-04C Rev.1 solar inputs"
)

# ── Trigger computation ───────────────────────────────────────────────────────
if run_btn:
    epoch = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    scenarios = (
        list(_SOLAR.keys()) if solar_choice == "All three" else [solar_choice]
    )

    all_results = {}
    progress    = st.progress(0, text="Building density tables…")

    for s_idx, scenario in enumerate(scenarios):
        sv   = _SOLAR[scenario]
        prog_base = s_idx / len(scenarios)

        progress.progress(prog_base, text=f"NRLMSISE-00 density table — {scenario}…")
        rho_fn = _build_rho_interp(sv["F107"], sv["F107a"], sv["Ap"], epoch)

        progress.progress(prog_base + 0.25 / len(scenarios),
                          text=f"Integrating orbit — {scenario}…")

        result = _integrate(
            a0_km    = _Re + altitude,
            Cd       = cd,
            A_m2     = area,
            m0_kg    = wet_mass,             # wet mass = dry + all propellant
            strategy = strategy,
            T_N      = T_N,
            Isp_s    = Isp_s,
            prop0_kg = prop_for_integrate,   # burned portion (two-burn) or total (continuous)
            rho_fn       = rho_fn,
            epoch        = epoch,
            dv_override  = _dv_single if strategy == "Two-burn circularization" else 0.0,
            a_transfer_m = _a_trans_m if strategy == "Two-burn circularization" else 0.0,
        )
        all_results[scenario] = result

    progress.progress(1.0, text="Done.")
    progress.empty()

    st.session_state["do_results"] = {
        "scenarios":         all_results,
        "solar_choice":      solar_choice,
        "epoch":             epoch,
        "altitude":          altitude,
        "dry_mass":          dry_mass,
        "wet_mass":          wet_mass,
        "cd":                cd,
        "area":              area,
        "strategy":          strategy,
        "T_N":               T_N,
        "Isp_s":             Isp_s,
        "prop_kg":           prop_kg,
        "prop_for_integrate": prop_for_integrate,
    }

# ── Display results ───────────────────────────────────────────────────────────
stored = st.session_state.get("do_results")

if stored is None:
    st.info(
        "Configure parameters in the sidebar, then press **▶ Compute Lifetime**."
    )
    st.markdown("---")
    c = st.columns(4)
    c[0].metric("Dry mass",      f"{dry_mass:.2f} kg")
    c[1].metric("Wet mass",      f"{wet_mass:.2f} kg")
    c[2].metric("Area",          f"{area:.4f} m²")
    c[3].metric("Cd",            f"{cd:.2f}")
    c2 = st.columns(4)
    c2[0].metric("β",            f"{beta:.1f} kg/m²")
    c2[1].metric("Altitude",     f"{altitude} km")
    c2[2].metric("Solar",        solar_choice)
    c2[3].metric("Propulsion",   "ON" if use_propulsion else "OFF")
    st.stop()

# ── Plot ─────────────────────────────────────────────────────────────────────
import plotly.graph_objects as go

fig = go.Figure()
scenarios_res = stored["scenarios"]

for scenario, res in scenarios_res.items():
    color = _SOLAR_COLORS[scenario]
    name  = f"{scenario} — {res['lifetime_days']:.0f} d"
    if res["exceeded_max"]:
        name += f"  (>{_MAX_YEARS} yr, integration capped)"
    fig.add_trace(go.Scatter(
        x=res["t_days"], y=res["alt_km"],
        mode="lines", name=name,
        line=dict(color=color, width=2),
        hovertemplate="Day %{x:.1f}<br>Alt %{y:.1f} km<extra></extra>",
    ))

fig.add_hline(y=_DEORBIT_ALT_KM, line_dash="dash",
              line_color="#888", line_width=1,
              annotation_text=f"Deorbit threshold ({_DEORBIT_ALT_KM:.0f} km)",
              annotation_position="bottom right")
fig.add_hline(y=200, line_dash="dot", line_color="#444", line_width=1,
              annotation_text="200 km (step-size boundary)",
              annotation_position="top right")

fig.update_layout(
    title=dict(text="Altitude Decay vs Time", font=dict(color="#e0e6f5")),
    xaxis=dict(title="Days", color="#4a5c80", gridcolor="#1e2847", showgrid=True),
    yaxis=dict(title="Altitude (km)", color="#4a5c80", gridcolor="#1e2847", showgrid=True),
    paper_bgcolor="#0b0f1e",
    plot_bgcolor="#111827",
    legend=dict(bgcolor="#111827", bordercolor="#1e2847",
                font=dict(color="#e0e6f5", size=12)),
    margin=dict(l=60, r=20, t=60, b=60),
)
st.plotly_chart(fig, use_container_width=True)

# ── Per-scenario result cards ─────────────────────────────────────────────────
st.markdown("### Results")

for scenario, res in scenarios_res.items():
    color  = _SOLAR_COLORS[scenario]
    lt_d   = res["lifetime_days"]
    lt_y   = lt_d / 365.25

    # Derive display values before opening expander
    _instant = (lt_d == 0.0)                       # burn alone dropped below threshold
    _subday  = (lt_d > 0.0 and lt_d < 1.0)        # deorbits in less than one day
    _lt_days_str = ("< 1 day" if _subday else
                    "0 days"  if _instant else
                    f"{lt_d:.0f} days")
    _lt_yrs_str  = f"{lt_y:.4f} years" if lt_y < 0.01 else f"{lt_y:.2f} years"
    _deorbit_dt  = (res["deorbit_date"] if not _instant
                    else res["deorbit_date"].replace(
                        day=min(res["deorbit_date"].day + 1, 28)))  # epoch + ~1 day

    with st.expander(f"**{scenario}** — {_lt_days_str}  ({_lt_yrs_str})", expanded=True):

        # Instant deorbit notice
        if _instant:
            st.info(
                f"Deorbit from burn — altitude dropped below {_DEORBIT_ALT_KM:.0f} km "
                "during the burn itself."
            )

        # 5-year compliance badge
        compliant = (lt_y <= 5.0)
        if compliant:
            st.markdown(
                f'<div style="background:#1a3a1a;border:1.5px solid #4caf50;border-radius:4px;'
                f'padding:8px 14px;font-family:\'Space Mono\',monospace;color:#4caf50;font-size:13px;">'
                f'✓ Compliant — deorbits in <b>{_lt_yrs_str}</b></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="background:#3a1a1a;border:1.5px solid #e8001c;border-radius:4px;'
                f'padding:8px 14px;font-family:\'Space Mono\',monospace;color:#e8001c;font-size:13px;">'
                f'✗ Non-compliant — deorbits in <b>{_lt_yrs_str}</b></div>',
                unsafe_allow_html=True,
            )

        st.markdown("")
        cols = st.columns(4)
        cols[0].metric("Lifetime",     _lt_days_str)
        cols[1].metric("Lifetime",     _lt_yrs_str)
        cols[2].metric("Deorbit date", _deorbit_dt.strftime("%Y-%m-%d"))

        if res["exceeded_max"]:
            cols[3].metric("Status", f">{_MAX_YEARS} yr cap")
        elif _instant:
            cols[3].metric("Status", "Immediate deorbit")
        elif _subday:
            cols[3].metric("Status", "< 1 day")
        else:
            cols[3].metric("Status", "Completed")

        if stored["strategy"] != "Natural decay":
            cols2 = st.columns(2)
            if res["dv_used_m_s"] > 0:
                cols2[0].metric("ΔV applied",    f"{res['dv_used_m_s']:.2f} m/s")
                cols2[1].metric("Prop consumed", f"{res['prop_consumed_kg']:.4f} kg")

st.markdown("---")
st.caption(
    "**5-year post-mission LEO disposal requirement:**  "
    "ESA Space Debris Mitigation Requirements, ESSB-ST-U-007 Issue 1 (November 2023) — "
    "mandatory for all new ESA procurements. "
    "ESA Zero Debris approach: https://esoc.esa.int/new-space-debris-mitigation-policy-and-requirements-effect  ·  "
    "FCC Report and Order (September 2022), effective September 2024 — "
    "mandatory for all US-licensed and US-market-access satellites.  ·  "
    "IADC-02-01 Rev.4 (2024) international guideline: 25 years.  ·  "
    "Atmosphere: NRLMSISE-00 (Picone et al. 2002) · "
    "Drag: Vallado (2013) §9.5 · "
    "Solar: ECSS-E-ST-10-04C Rev.1 (2020)"
)
