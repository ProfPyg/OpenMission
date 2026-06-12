"""
access.py — Access window computation for OpenMission using Skyfield (SGP4).

Upgraded to use:
  1. Skyfield's timescale for accurate Earth Precession and Nutation.
  2. WGS84 Ellipsoid for high-fidelity ground station positioning and zenith vectors.
  3. Native SGP4 library via Skyfield to include full periodic variations.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import numpy as np
from math import sqrt
from skyfield.api import load, wgs84
from sgp4.api import Satrec, WGS84 as sgp4_wgs84
from skyfield.sgp4lib import EarthSatellite

# Standard Earth gravitational constant used for SGP4 mean motion derivation
_MU_KM3_S2 = 398600.4418

# Shared timescale — created once to avoid repeated I/O on every call
_ts = load.timescale()

# ---------------------------------------------------------------------------
# Private Vectorized Window & Metric Engines
# ---------------------------------------------------------------------------

def _detect_windows(
    vis:   np.ndarray,
    times: list[datetime],
    elev:  np.ndarray | None = None,
) -> list[dict]:
    """
    Convert a boolean visibility vector into access-window dicts.

    Each dict has:
        's'      : start datetime
        'e'      : end datetime
        'max_el' : maximum elevation [deg] reached during the window
                   (NaN when elev is not supplied)
    """
    if not np.any(vis):
        return []
    vis    = vis.astype(bool)
    padded = np.concatenate([[False], vis, [False]])

    starts = np.where(~padded[:-1] &  padded[1:])[0]
    ends   = np.where( padded[:-1] & ~padded[1:])[0] - 1

    return [
        {
            's':      times[s],
            'e':      times[e],
            'max_el': float(np.max(elev[s:e + 1])) if elev is not None else float('nan'),
        }
        for s, e in zip(starts, ends)
    ]


def _merge_windows(windows: list[dict]) -> list[dict]:
    """
    Sort then merge overlapping/adjacent access windows.

    Each window dict has keys 's', 'e', 'max_el'.
    Merged windows carry the maximum max_el of all constituent windows.
    """
    if not windows:
        return []
    windows = sorted(windows, key=lambda w: w['s'])
    merged  = []
    cur     = dict(windows[0])          # mutable copy
    for w in windows[1:]:
        if w['s'] <= cur['e']:          # overlap — extend
            if w['e'] > cur['e']:
                cur['e'] = w['e']
            # propagate max_el, treating NaN as missing
            cur_nan = cur['max_el'] != cur['max_el']
            w_nan   = w['max_el']   != w['max_el']
            if not cur_nan and not w_nan:
                cur['max_el'] = max(cur['max_el'], w['max_el'])
            elif cur_nan:
                cur['max_el'] = w['max_el']
        else:                           # gap — save and start fresh
            merged.append(cur)
            cur = dict(w)
    merged.append(cur)
    return merged


def _metrics_from_merged(
    merged:       list[dict],
    sim_duration: timedelta,
) -> dict:
    """
    Compute access ratio, mean revisit, and max revisit from merged windows.

    Each element of *merged* is a dict with keys 's', 'e', 'max_el'.
    """
    if not merged:
        return {
            'merged_windows': [],
            'mean_revisit':   None,
            'max_revisit':    None,
            'access_ratio':   0.0,
            'has_access':     False,
        }

    durations    = [w['e'] - w['s'] for w in merged]
    total_access = sum(durations, timedelta(0))
    access_ratio = 100.0 * total_access.total_seconds() / sim_duration.total_seconds()

    if len(merged) >= 2:
        gaps         = [merged[i + 1]['s'] - merged[i]['e'] for i in range(len(merged) - 1)]
        mean_revisit = sum(gaps, timedelta(0)) / len(gaps)
        max_revisit  = max(gaps)
    else:
        mean_revisit = None
        max_revisit  = None

    return {
        'merged_windows': merged,
        'mean_revisit':   mean_revisit,
        'max_revisit':    max_revisit,
        'access_ratio':   access_ratio,
        'has_access':     True,
    }


# ---------------------------------------------------------------------------
# Public API (Upgraded to Skyfield & SGP4)
# ---------------------------------------------------------------------------

def computeAccess(
    satellites: list[dict],
    gs_list:    list[dict],
    roi_list:   list[dict],
    startTime:  datetime,
    stopTime:   datetime,
    sampleTime: float = 60.0,
) -> dict:
    """
    Compute access windows, revisit times, and coverage metrics using Skyfield.
    Matches the exact entry point and output schema of the front-end app.py.
    """
    # 1. Initialize high-fidelity timescale
    sim_duration = stopTime - startTime
    n_steps  = int(sim_duration.total_seconds() / sampleTime) + 1
    t_sec    = np.arange(n_steps, dtype=float) * sampleTime
    times    = [startTime + timedelta(seconds=float(s)) for s in t_sec]

    # ts.from_datetimes() requires timezone-aware datetimes
    start_utc = startTime if startTime.tzinfo is not None else startTime.replace(tzinfo=timezone.utc)
    times_utc = [start_utc + timedelta(seconds=float(s)) for s in t_sec]
    skyfield_times = _ts.from_datetimes(times_utc)

    # 2. Build High-Fidelity SGP4 Skyfield Satellites directly from Keplerian Elements
    skyfield_sats: list[EarthSatellite] = []
    for idx, s in enumerate(satellites):
        satrec = Satrec()

        # SGP4 initialization expects mean motion in radians per minute
        # Derived cleanly from semi-major axis (a) using fundamental standard mu
        mean_motion_rad_min = 60.0 * sqrt(_MU_KM3_S2 / (s['a'] ** 3))

        # Fix: use total_seconds()/86400 for sub-day precision, not integer .days + 1
        epoch_days = (startTime - datetime(1949, 12, 31)).total_seconds() / 86400.0

        # sgp4init positional args:
        # whichconst, opsmode, satnum, epoch,
        # ndot, nddot, bstar,
        # ecco, argpo, inclo, mo, no_kozai, nodeo
        satrec.sgp4init(
            sgp4_wgs84, 'i', idx + 1, epoch_days,
            0.0, 0.0, 0.0,          # Zero-drag idealized operational baselines
            s['e'],
            np.radians(s['argp_deg']),
            np.radians(s['i_deg']),
            np.radians(s['M0_deg']),
            mean_motion_rad_min,
            np.radians(s['RAAN_deg']),
        )
        skyfield_sats.append(EarthSatellite.from_satrec(satrec, _ts))

    # 3. Ground Stations Geometry via WGS84 Oblate Spheroid
    gs_results: list[dict] = []
    for gs in gs_list:
        min_el     = float(gs['min_elevation'])
        target_geo = wgs84.latlon(gs['lat'], gs['lon'], elevation_m=gs.get('alt', 0))

        all_windows: list[dict] = []
        for sat in skyfield_sats:
            difference  = sat - target_geo
            topocentric = difference.at(skyfield_times)
            alt, az, distance = topocentric.altaz()

            vis = alt.degrees >= min_el
            all_windows.extend(_detect_windows(vis, times, elev=alt.degrees))

        merged  = _merge_windows(all_windows)
        metrics = _metrics_from_merged(merged, sim_duration)
        metrics.update({'name': gs['name'], 'lat': gs['lat'], 'lon': gs['lon']})
        gs_results.append(metrics)

    # 4. Region of Interest (ROI) Geometry via WGS84 Oblate Spheroid
    roi_results: list[dict] = []
    for idx, roi in enumerate(roi_list):
        min_el     = 90.0 - float(roi['max_off_nadir']) if 'max_off_nadir' in roi else float(roi.get('min_elevation', 20.0))
        target_geo = wgs84.latlon(roi['lat'], roi['lon'], elevation_m=0)

        all_windows: list[dict] = []
        for sat in skyfield_sats:
            difference  = sat - target_geo
            topocentric = difference.at(skyfield_times)
            alt, az, distance = topocentric.altaz()

            vis = alt.degrees >= min_el
            all_windows.extend(_detect_windows(vis, times, elev=alt.degrees))

        merged  = _merge_windows(all_windows)
        metrics = _metrics_from_merged(merged, sim_duration)

        # Overall max elevation across all passes for this point
        valid_els      = [w['max_el'] for w in merged if w['max_el'] == w['max_el']]
        overall_max_el = round(max(valid_els), 1) if valid_els else None

        metrics.update({
            'name':           roi.get('name', f'ROI_{idx + 1:03d}'),
            'lat':            roi['lat'],
            'lon':            roi['lon'],
            'overall_max_el': overall_max_el,
        })
        roi_results.append(metrics)

    # 5. Aggregate ROI Metrics Summary Block
    covered      = [r for r in roi_results if r['has_access']]
    roi_coverage = 100.0 * len(covered) / len(roi_results) if roi_results else 0.0

    valid_mean = [r['mean_revisit'] for r in covered if r['mean_revisit'] is not None]
    valid_max  = [r['max_revisit']  for r in covered if r['max_revisit']  is not None]
    valid_ar   = [r['access_ratio'] for r in covered]

    roi_mean_revisit      = (sum(valid_mean, timedelta(0)) / len(valid_mean)) if valid_mean else None
    roi_max_revisit       = max(valid_max)                                     if valid_max  else None
    roi_mean_access_ratio = (sum(valid_ar)  / len(valid_ar))                  if valid_ar   else 0.0

    return {
        'time_steps':            times,
        'sim_duration':          sim_duration,
        'gs_results':            gs_results,
        'roi_results':           roi_results,
        'roi_coverage':          roi_coverage,
        'roi_mean_revisit':      roi_mean_revisit,
        'roi_max_revisit':       roi_max_revisit,
        'roi_mean_access_ratio': roi_mean_access_ratio,
    }


# ---------------------------------------------------------------------------
# Self-test  (python access.py)
# Verification scenario: 3 planes x 3 sats, 500 km SSO, LTAN=10.5h,
# epoch 2026-03-02 08:23 UTC, 9 days, 60 s step, off-nadir 45 deg
#
# Expected (STK/MATLAB baselines):
#   Helmos   (min el 30 deg):  access ratio ~1.58 %
#   Psachna  (min el  5 deg):  access ratio ~15.30 %
#   Athens single point:       mean revisit ~3 h
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from propagator import sso_inclination, ltan2raan, Re

    start = datetime(2026, 3, 2, 8, 23, 0)
    stop  = start + timedelta(days=9)

    alt   = 500.0
    inc   = sso_inclination(alt)
    raan0 = ltan2raan(start, 10.5)

    n_planes, n_sats_per_plane = 3, 3
    sats = [
        {
            'a':        Re + alt,
            'e':        0.001,
            'i_deg':    inc,
            'RAAN_deg': (raan0 + p * 360.0 / n_planes) % 360.0,
            'argp_deg': 0.0,
            'M0_deg':   (s * 360.0 / n_sats_per_plane) % 360.0,
        }
        for p in range(n_planes)
        for s in range(n_sats_per_plane)
    ]

    gs_list = [
        {'name': 'Helmos Observatory, NOA',    'lat': 37.9856,   'lon': 22.1983,   'min_elevation': 30.0},
        {'name': 'UoA Ground Station, Psachna','lat': 38.569708, 'lon': 23.648572, 'min_elevation':  5.0},
    ]

    roi_list = [
        {'name': 'Athens', 'lat': 37.9838, 'lon': 23.7275, 'max_off_nadir': 45.0},
    ]

    print(f"Skyfield/SGP4 self-test -- {len(sats)} sats "
          f"(3 planes x 3, 500 km SSO, LTAN=10.5 h)")
    print(f"Simulation: {start}  to  {stop}  (9 days, 60 s step)\n")

    res = computeAccess(sats, gs_list, roi_list, start, stop, sampleTime=60.0)

    for gs in res['gs_results']:
        n = len(gs['merged_windows'])
        print(f"{gs['name']}: {n} pass(es), access ratio {gs['access_ratio']:.2f}%")
        for w in gs['merged_windows'][:5]:
            dur = (w['e'] - w['s']).total_seconds()
            print(f"    {w['s'].strftime('%Y-%m-%d %H:%M')} - {w['e'].strftime('%H:%M')}"
                  f"  ({dur/60:.1f} min)  max el {w['max_el']:.1f} deg")
        if n > 5:
            print(f"    ... ({n - 5} more passes)")
        if gs['mean_revisit']:
            print(f"    Mean revisit: {gs['mean_revisit']}  |  Max: {gs['max_revisit']}")
        print()

    for pt in res['roi_results']:
        mr = pt['mean_revisit']
        print(f"{pt['name']}:  {len(pt['merged_windows'])} pass(es)"
              f"  mean revisit {str(mr) if mr else 'n/a'}"
              f"  max el {pt['overall_max_el']} deg")

    print(f"\nROI coverage          : {res['roi_coverage']:.1f}%")
    print(f"ROI mean access ratio : {res['roi_mean_access_ratio']:.2f}%")
