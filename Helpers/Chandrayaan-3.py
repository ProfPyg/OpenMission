"""
Chandrayaan-3 mission scenario builder for STK.

Models the mission as it actually flew: Chandrayaan-3's propulsion module
carried the lander/rover stack from Earth parking orbit to the Moon; once
there the stack settled into a low, near-polar lunar orbit before the lander
separated and touched down near the lunar south pole. This script builds the
LUNAR-ORBIT phase (the well-defined, STK-modelable segment) plus the landing
site and the primary Earth ground station used for tracking/telemetry.
It does NOT attempt to model the trans-lunar trajectory or the descent -
those are non-Keplerian, mission-specific trajectories that don't reduce to
a single orbital-element set; see the comment at the bottom for how to add
them if the user has ephemeris data for that phase.

Numbers used (documented per line below) are drawn from ISRO's public
mission material for Chandrayaan-3 (2023).
"""

import stk_helpers as stk

# ---------------------------------------------------------------------------
# Named mission constants
# ---------------------------------------------------------------------------

SCENARIO_NAME = "Chandrayaan3"

# Scenario time window: lunar orbit insertion (5 Aug 2023) through the
# landing date (23 Aug 2023), per ISRO's published mission timeline.
SCENARIO_START = "5 Aug 2023 00:00:00.000"
SCENARIO_STOP = "23 Aug 2023 23:59:59.000"

# Final circularized lunar orbit prior to lander separation was ~153 km x
# 163 km; treated here as a circular 150 km orbit for simplicity (assumption
# - refine with a real ecc if the user has post-insertion OD data).
LUNAR_ORBIT_ALT_KM = 150.0
LUNAR_ORBIT_ECC = 0.0

# Chandrayaan-3 flew a near-polar lunar orbit to overfly the south-pole
# landing site; 90 deg used as the polar assumption (ISRO did not publish an
# exact inclination figure).
LUNAR_ORBIT_INC_DEG = 90.0

# RAAN is a free/unpublished parameter of the actual trajectory - 0 deg is
# an assumption; adjust if the user has ephemeris-derived value.
LUNAR_ORBIT_RAAN_DEG = 0.0

# Landing site: near Manzinus crater region, lunar south pole,
# ~69.37S, 32.32E, per ISRO's post-landing coordinates announcement.
LANDING_SITE_LAT_DEG = -69.37
LANDING_SITE_LON_DEG = 32.32

# Primary Earth tracking station: ISTRAC, Bengaluru, India (approximate
# public coordinates of the ISTRAC ground station complex).
GROUND_STATION_NAME = "ISTRAC_Bengaluru"
GROUND_STATION_LAT_DEG = 13.13
GROUND_STATION_LON_DEG = 77.63
GROUND_STATION_ALT_KM = 0.0

# Minimum elevation for a usable Earth-Moon communications pass (assumption
# - typical deep-space tracking mask).
MIN_ELEVATION_DEG = 5.0


def main():
    session = stk.StkSession.attach()
    session.ensure_scenario(SCENARIO_NAME)
    session.announce_output_dir()
    session.set_time_period(SCENARIO_START, SCENARIO_STOP)

    # --- Lunar orbiter (Chandrayaan-3 propulsion module + lander stack) ---
    sma_km = stk.altitude_to_sma(LUNAR_ORBIT_ALT_KM, stk.MOON_RADIUS_KM)
    sat = session.new_satellite(
        "Chandrayaan3",
        sma_km,
        LUNAR_ORBIT_INC_DEG,
        LUNAR_ORBIT_RAAN_DEG,
        ecc=LUNAR_ORBIT_ECC,
        arg_perigee_deg=0.0,
        true_anomaly_deg=0.0,
        propagator="TwoBody",  # no lunar J2 model needed for this fidelity
        coord_system="J2000",
        central_body="Moon",
    )
    print(f"Created lunar orbiter: {sat}")

    r_km = session.orbit_radius_km(sat)
    print(f"Sanity check - orbit radius: {r_km:.1f} km")

    # --- Landing site as an area target (small footprint around the point) ---
    landing_site = session.new_area_target_ellipse(
        "LandingSite",
        LANDING_SITE_LAT_DEG,
        LANDING_SITE_LON_DEG,
        semi_major_km=5.0,   # nominal small footprint around the touchdown point
        semi_minor_km=5.0,
        bearing_deg=0.0,
    )
    print(f"Created landing site area target: {landing_site}")

    # --- Earth ground station for orbiter comms ---
    gs = session.new_facility(
        GROUND_STATION_NAME,
        GROUND_STATION_LAT_DEG,
        GROUND_STATION_LON_DEG,
        alt_km=GROUND_STATION_ALT_KM,
    )
    print(f"Created ground station: {gs}")

    session.set_constraint(gs, "ElevationAngle", minimum=MIN_ELEVATION_DEG)

    # --- Access: orbiter <-> ground station ---
    count, seconds, intervals = session.compute_access(sat, gs)
    print(
        f"Chandrayaan3 -> {GROUND_STATION_NAME}: "
        f"{count} passes, {seconds:.0f} s total access"
    )

    rows = session.access_summary([sat], gs)
    session.write_access_csv(rows, GROUND_STATION_NAME)
    session.write_pass_list_csv(sat, gs, with_details=True)

    session.save_scenario()
    print("Done. Trans-lunar cruise and powered-descent phases were not "
          "modeled - they are non-Keplerian trajectories that need mission "
          "ephemeris data, not a single orbital-element set. If you have "
          "that ephemeris, it can be imported as an External/Ephemeris "
          "satellite via a separate Connect command.")


if __name__ == "__main__":
    try:
        main()
    except stk.StkError as e:
        print(f"STK automation error: {e}")