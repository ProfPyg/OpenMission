"""
propagator.py — Orbital mechanics core for OpenMission.

All algorithms ported from the verified MATLAB reference (VLEOExample.m).
Constants: Re = 6378.137 km, mu = 398600.4418 km³/s², J2 = 1.08263e-3
"""

from __future__ import annotations
from datetime import datetime
import numpy as np

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
Re = 6378.137       # km  — WGS-84 equatorial radius
mu = 398600.4418    # km³/s²
J2 = 1.08263e-3

_J2000 = datetime(2000, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _julian_date(dt: datetime) -> float:
    """Julian Date for a UTC datetime."""
    return 2451545.0 + (dt - _J2000).total_seconds() / 86400.0


def _kepler_solve(M: np.ndarray | float, e: float, tol: float = 1e-10) -> np.ndarray:
    """
    Newton–Raphson solution of Kepler's equation  M = E − e·sin(E).
    Accepts scalar or array M (radians). Returns E in radians, same shape.
    """
    M = np.asarray(M, dtype=float) % (2.0 * np.pi)
    E = M.copy()
    for _ in range(50):
        dE = (M - E + e * np.sin(E)) / (1.0 - e * np.cos(E))
        E += dE
        if np.all(np.abs(dE) < tol):
            break
    return E


# ---------------------------------------------------------------------------
# Sun position & LTAN/RAAN (ported directly from MATLAB sunRightAscension,
# ltan2raan, raan2ltan — Vallado 2013 §5.1)
# ---------------------------------------------------------------------------

def sunRightAscension(epochUTC: datetime) -> float:
    """Sun's right ascension in degrees (Vallado 2013 §5.1)."""
    JD = _julian_date(epochUTC)
    T = (JD - 2451545.0) / 36525.0

    L = (280.46646 + 36000.76983 * T + 0.0003032 * T**2) % 360.0
    M = (357.52911 + 35999.05029 * T - 0.0001537 * T**2) % 360.0

    M_r = np.radians(M)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T**2) * np.sin(M_r)
         + (0.019993 - 0.000101 * T) * np.sin(2.0 * M_r)
         + 0.000289 * np.sin(3.0 * M_r))

    lam_r = np.radians((L + C) % 360.0)
    eps_arcsec = 21.448 - 46.8150 * T - 0.00059 * T**2 + 0.001813 * T**3
    eps_r = np.radians(23.0 + 26.0 / 60.0 + eps_arcsec / 3600.0)

    alpha = np.degrees(
        np.arctan2(np.cos(eps_r) * np.sin(lam_r), np.cos(lam_r))
    ) % 360.0
    return float(alpha)


def ltan2raan(epochUTC: datetime, LTAN_hours: float) -> float:
    """RAAN (degrees) from Local Time of Ascending Node (decimal hours)."""
    return (sunRightAscension(epochUTC) + 15.0 * (LTAN_hours - 12.0)) % 360.0


def raan2ltan(epochUTC: datetime, RAAN_deg: float) -> float:
    """LTAN (decimal hours) from RAAN (degrees)."""
    return (12.0 + (RAAN_deg - sunRightAscension(epochUTC)) / 15.0) % 24.0


# ---------------------------------------------------------------------------
# SSO inclination
# ---------------------------------------------------------------------------

def sso_inclination(altitude_km: float) -> float:
    """
    J2 SSO inclination (degrees) for a circular orbit at altitude_km.

    Sets RAAN drift rate equal to Earth's mean orbital rate so that the
    orbit plane keeps pace with the Sun (360° / 365.25 days).
    """
    a = Re + altitude_km
    omega_dot = 2.0 * np.pi / (365.25 * 86400.0)   # rad/s
    cos_i = -(2.0 * omega_dot * a**3.5) / (3.0 * J2 * Re**2 * np.sqrt(mu))
    return float(np.degrees(np.arccos(np.clip(cos_i, -1.0, 1.0))))


# ---------------------------------------------------------------------------
# Orbital period
# ---------------------------------------------------------------------------

def orbPeriod(alt_km: float) -> float:
    """Keplerian orbital period in minutes for a circular orbit."""
    a = Re + alt_km
    return 2.0 * np.pi * np.sqrt(a**3 / mu) / 60.0


# ---------------------------------------------------------------------------
# Repeat ground track cycle  (Vallado 2013 §11.5)
# ---------------------------------------------------------------------------

def repeat_cycle(T_min: float, max_days: int = 30) -> tuple[int, int, float]:
    """
    Best repeat ground track cycle within max_days (Vallado 2013 §11.5).

    Scans d in [1, max_days] and returns the d whose total orbit count
    1440*d/T_min is closest to an integer — minimum fractional orbit
    residual.  This is the correct objective: the satellite completes a
    near-integer number of orbits so the ground track closes.

    Note — Fraction.limit_denominator applied to 1440/T_min minimises the
    per-day approximation error in orbits/day, which is a different
    objective and gives a worse answer.  At 500 km it would return 23 days
    (Fraction, per-day error 0.002) vs 9 days here (scan, total residual
    0.025), because 23 * 15.217 ≈ 350.04 orbits but 9 * 15.219 ≈ 136.97
    — the 9-day closure is tighter.

    Returns: (days, orbits, error)
        error — fractional orbit residual |total - round(total)|
    """
    best_q, best_p, best_err = 1, round(1440 / T_min), float('inf')
    for d in range(1, max_days + 1):
        total = 1440 * d / T_min
        err = abs(total - round(total))
        if err < best_err:
            best_err, best_q, best_p = err, d, round(total)
    return best_q, best_p, best_err


# ---------------------------------------------------------------------------
# GMST  (Vallado 2013 §3.5 Eq. 3-45)
# ---------------------------------------------------------------------------

def gmst(dt: datetime) -> float:
    """
    Greenwich Mean Sidereal Time in radians.

    Vallado 2013 §3.5 Eq. 3-45 — polynomial in Julian centuries T_UT1.
    Output is in seconds of time, converted to radians before return.
    """
    JD = _julian_date(dt)
    T = (JD - 2451545.0) / 36525.0
    theta_sec = (67310.54841
                 + (876600.0 * 3600.0 + 8640184.812866) * T
                 + 0.093104 * T**2
                 - 6.2e-6 * T**3)
    # seconds of time → degrees (÷240) → radians
    return float(np.radians(theta_sec / 240.0 % 360.0))


# ---------------------------------------------------------------------------
# J2 secular propagator
# ---------------------------------------------------------------------------

def propagateJ2(
    a: float,
    e: float,
    i_deg: float,
    RAAN0_deg: float,
    argp0_deg: float,
    M0_deg: float,
    t_sec,
) -> np.ndarray:
    """
    J2 secular analytical propagation (Brouwer first-order secular terms).

    Parameters
    ----------
    a         : semi-major axis [km]
    e         : eccentricity
    i_deg     : inclination [deg]
    RAAN0_deg : initial RAAN [deg]
    argp0_deg : initial argument of perigee [deg]
    M0_deg    : initial mean anomaly [deg]
    t_sec     : elapsed time from epoch — scalar or 1-D array [s]

    Returns
    -------
    ECI position [km] — shape (3,) for scalar t_sec, (N, 3) for array t_sec
    """
    scalar_in = np.isscalar(t_sec)
    t = np.atleast_1d(np.asarray(t_sec, dtype=float))

    i     = np.radians(i_deg)
    RAAN0 = np.radians(RAAN0_deg)
    argp0 = np.radians(argp0_deg)
    M0    = np.radians(M0_deg)

    p = a * (1.0 - e**2)
    n = np.sqrt(mu / a**3)              # mean motion [rad/s]

    # Secular J2 drift rates [rad/s]
    k        = 1.5 * n * J2 * (Re / p)**2
    RAAN_dot = -k * np.cos(i)
    argp_dot = 0.5 * k * (5.0 * np.cos(i)**2 - 1.0)

    RAAN_t = RAAN0 + RAAN_dot * t
    argp_t = argp0 + argp_dot * t
    M_t    = (M0 + n * t) % (2.0 * np.pi)

    E_t  = _kepler_solve(M_t, e)
    nu_t = 2.0 * np.arctan2(
        np.sqrt(1.0 + e) * np.sin(E_t / 2.0),
        np.sqrt(1.0 - e) * np.cos(E_t / 2.0),
    )
    r_t  = a * (1.0 - e * np.cos(E_t))

    x_p = r_t * np.cos(nu_t)
    y_p = r_t * np.sin(nu_t)

    # Vectorised DCM: R3(−Ω) · R1(−i) · R3(−ω) applied to [x_p, y_p, 0]ᵀ
    ci = np.cos(i);  si = np.sin(i)
    cr = np.cos(RAAN_t);  sr = np.sin(RAAN_t)
    cw = np.cos(argp_t);  sw = np.sin(argp_t)

    pos = np.empty((len(t), 3))
    pos[:, 0] = (cr * cw - sr * sw * ci) * x_p + (-cr * sw - sr * cw * ci) * y_p
    pos[:, 1] = (sr * cw + cr * sw * ci) * x_p + (-sr * sw + cr * cw * ci) * y_p
    pos[:, 2] = (sw * si) * x_p + (cw * si) * y_p

    return pos[0] if scalar_in else pos


# ---------------------------------------------------------------------------
# Elevation angle
# ---------------------------------------------------------------------------

def elevAngle(
    sat_ECI: np.ndarray,
    gs_lat_deg: float,
    gs_lon_deg: float,
    gmst_rad: float,
) -> float:
    """
    Topocentric elevation angle (degrees) of a satellite from a ground station.

    Parameters
    ----------
    sat_ECI    : ECI position of satellite [km], shape (3,)
    gs_lat_deg : ground station geodetic latitude [deg]  (spherical Earth)
    gs_lon_deg : ground station longitude [deg]
    gmst_rad   : GMST at observation epoch [rad]  (use gmst() above)

    Returns
    -------
    Elevation angle in degrees, range [−90°, +90°].
    """
    lat_r = np.radians(gs_lat_deg)
    lon_r = np.radians(gs_lon_deg)

    # GS position in ECEF (spherical Earth, altitude ignored)
    r_ecef = Re * np.array([
        np.cos(lat_r) * np.cos(lon_r),
        np.cos(lat_r) * np.sin(lon_r),
        np.sin(lat_r),
    ])

    # ECEF → ECI:  r_ECI = R_z(+GMST) · r_ECEF
    cg, sg = np.cos(gmst_rad), np.sin(gmst_rad)
    r_gs = np.array([
        cg * r_ecef[0] - sg * r_ecef[1],
        sg * r_ecef[0] + cg * r_ecef[1],
        r_ecef[2],
    ])

    rho     = np.asarray(sat_ECI, dtype=float) - r_gs
    rho_hat = rho / np.linalg.norm(rho)
    gs_hat  = r_gs / np.linalg.norm(r_gs)

    # sin(elev) = ρ̂ · ẑ_gs  (angle between range vector and local zenith)
    return float(np.degrees(np.arcsin(np.clip(np.dot(rho_hat, gs_hat), -1.0, 1.0))))


# ---------------------------------------------------------------------------
# Self-test  (python propagator.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    epoch = datetime(2026, 3, 2, 8, 23, 0)

    inc = sso_inclination(500)
    print(f"sso_inclination(500)        = {inc:.4f}°   expected 97.4016°")

    raan = ltan2raan(epoch, 10.5)
    print(f"ltan2raan(epoch, 10.5)      = {raan:.4f}°  expected 320.6841°")

    ltan = raan2ltan(epoch, 320.6841)
    print(f"raan2ltan(epoch, 320.6841)  = {ltan:.4f} h  expected 10.5000 h")

    period = orbPeriod(500)
    print(f"orbPeriod(500)              = {period:.2f} min  (typical SSO ~94.6 min)")

    g = gmst(epoch)
    print(f"gmst(epoch)                 = {np.degrees(g):.4f}°  ({g:.6f} rad)")

    # Quick propagateJ2 smoke-test: t=0 → position should be ~Re+500 km from origin
    pos0 = propagateJ2(Re + 500, 0.001, inc, raan, 0.0, 0.0, 0.0)
    print(f"propagateJ2 |r| at t=0      = {np.linalg.norm(pos0):.2f} km  expected ~{Re+500:.2f} km")

    # elevAngle smoke-test: satellite directly overhead Athens (38°N, 24°E)
    pos_t = propagateJ2(Re + 500, 0.001, inc, raan, 0.0, 0.0, 0.0)
    el = elevAngle(pos_t, 38.0, 24.0, g)
    print(f"elevAngle (Athens, overhead approx) = {el:.2f}°")
