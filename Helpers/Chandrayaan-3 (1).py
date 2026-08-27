"""
Lunar relay demonstration.

Scenario: an S-band lunar orbiter (100 km circular, equatorial) relays data
between the Tranquility surface site on the Moon and Canberra on Earth.

Requires stk_helpers.py in the same folder. Run with STK already open.
"""

import stk_helpers as stk

# ---------------------------------------------------------------------------
# Named constants - all mission numbers live here
# ---------------------------------------------------------------------------

SCENARIO_NAME = "LunarRelayDemo"
START_UTCG = "1 Mar 2026 00:00:00.000"
STOP_UTCG = "4 Mar 2026 00:00:00.000"

# Lunar orbiter - circular, equatorial, low altitude
ORBITER_NAME = "LunarRelaySat"
ORBITER_ALT_KM = 100.0            # circular altitude above the Moon, per request
ORBITER_INC_DEG = 0.0
ORBITER_RAAN_DEG = 0.0
ORBITER_TRUE_ANOM_DEG = 0.0
ORBITER_ECC = 0.0                 # circular
ORBITER_ARG_PERIGEE_DEG = 0.0     # undefined for a circular orbit, kept at 0
ORBITER_PROPAGATOR = "J2Perturbation"

# Lunar surface site
TRANQUILITY_NAME = "Tranquility"
TRANQUILITY_LAT_DEG = 0.7
TRANQUILITY_LON_DEG = 23.5
TRANQUILITY_MIN_EL_DEG = 5.0

# Earth ground station
CANBERRA_NAME = "Canberra"
CANBERRA_LAT_DEG = -35.40
CANBERRA_LON_DEG = 148.98
CANBERRA_ALT_KM = 0.0
CANBERRA_MIN_EL_DEG = 10.0

# S-band payload transmitter on the orbiter
SBAND_FREQ_GHZ = 2.25
SBAND_EIRP_DBW = 13.0

# Nadir-pointed imaging sensor on the orbiter
CAMERA_HALF_ANGLE_DEG = 40.0      # stated as "40 degree half-angle conic"

# Analysis report/export settings
REPORT_TIME_STEP_SEC = 60

CHAIN_NAME = "TranquilityRelayChain"


def build_orbiter(session):
    sma_km = stk.altitude_to_sma(ORBITER_ALT_KM, stk.MOON_RADIUS_KM)
    orbiter = session.new_satellite(
        ORBITER_NAME,
        sma_km,
        ORBITER_INC_DEG,
        ORBITER_RAAN_DEG,
        ecc=ORBITER_ECC,
        arg_perigee_deg=ORBITER_ARG_PERIGEE_DEG,
        true_anomaly_deg=ORBITER_TRUE_ANOM_DEG,
        propagator=ORBITER_PROPAGATOR,
        coord_system="J2000",
        central_body="Moon",
    )
    print(f"Created lunar orbiter: {orbiter}")
    return orbiter


def create_facility_on_body(session, name, lat_deg, lon_deg, central_body,
                             min_el_deg, alt_km=0.0):
    """
    Create a Facility. new_facility()'s documented signature has no
    central_body parameter, so for a non-Earth body (Moon) we try passing it
    anyway (mirroring new_satellite's central_body=) and fall back to a raw
    Connect command if the helper rejects the keyword. Earth-default calls
    never hit the fallback.
    """
    if central_body is None or central_body == "Earth":
        path = session.new_facility(name, lat_deg, lon_deg, alt_km=alt_km)
    else:
        try:
            path = session.new_facility(
                name, lat_deg, lon_deg, alt_km=alt_km, central_body=central_body
            )
        except TypeError:
            # Escape hatch: helper does not expose central_body on facilities.
            session.cmd(
                f"New / */CentralBody/{central_body} Facility {name}",
                label=f"Create {name} on {central_body}",
            )
            path = f"*/Facility/{name}"
            session.cmd(
                f"SetPosition {path} Geodetic {lat_deg} {lon_deg} {alt_km}",
                label=f"Position {name}",
            )
    session.set_min_elevation(path, min_el_deg)
    print(f"Created facility: {path} (central body: {central_body or 'Earth'}, "
          f"min elevation {min_el_deg} deg)")
    return path


def main():
    session = stk.StkSession.attach()
    session.ensure_scenario(SCENARIO_NAME)
    session.announce_output_dir()

    # Must be set before creating satellites.
    session.set_time_period(START_UTCG, STOP_UTCG)

    # --- Space and ground/surface segment -------------------------------
    orbiter = build_orbiter(session)

    tranquility = create_facility_on_body(
        session, TRANQUILITY_NAME, TRANQUILITY_LAT_DEG, TRANQUILITY_LON_DEG,
        central_body="Moon", min_el_deg=TRANQUILITY_MIN_EL_DEG,
    )

    canberra = create_facility_on_body(
        session, CANBERRA_NAME, CANBERRA_LAT_DEG, CANBERRA_LON_DEG,
        central_body=None, min_el_deg=CANBERRA_MIN_EL_DEG, alt_km=CANBERRA_ALT_KM,
    )

    # --- Payloads ---------------------------------------------------------
    tx = session.add_transmitter(
        orbiter, "SBandTx", SBAND_FREQ_GHZ, SBAND_EIRP_DBW
    )
    print(f"Added transmitter: {tx} ({SBAND_FREQ_GHZ} GHz, {SBAND_EIRP_DBW} dBW EIRP)")

    cam = session.add_sensor_conic(orbiter, "PayloadCam", CAMERA_HALF_ANGLE_DEG)
    session.point_nadir(cam)
    print(f"Added nadir-pointed conic sensor: {cam} "
          f"({CAMERA_HALF_ANGLE_DEG} deg half-angle)")

    rx_tranquility = session.add_receiver(tranquility, "TranquilityRx")
    rx_canberra = session.add_receiver(canberra, "CanberraRx")
    print(f"Added receivers: {rx_tranquility}, {rx_canberra}")

    # --- Analysis -----------------------------------------------------
    # 1) Orbiter -> Tranquility: every pass with duration, plus totals
    session.write_pass_list_csv(orbiter, tranquility, with_details=True)
    tranquility_rows = session.access_summary([orbiter], tranquility)
    session.write_access_csv(tranquility_rows, TRANQUILITY_NAME)
    print("Wrote per-pass and total access CSVs for orbiter -> Tranquility")

    # 2) Orbiter -> Canberra: the Earth-to-Moon link
    count, seconds, intervals = session.compute_access(orbiter, canberra)
    canberra_rows = session.access_summary([orbiter], canberra)
    session.write_access_csv(canberra_rows, CANBERRA_NAME)
    print(f"Orbiter -> Canberra: {count} accesses, {seconds:.0f} s total "
          f"(CSV written)")

    # 3) End-to-end relay chain: Tranquility -> orbiter -> Canberra
    chain = session.new_chain(CHAIN_NAME, [tranquility, orbiter, canberra])
    print(f"Created relay chain: {chain}")
    # STK's own chain access report window (best available style for
    # end-to-end chain access; adjust in STK if your install names it
    # differently).
    session.open_report(chain, "Complete Chain")

    # Access report windows for both pair-wise legs, as requested
    session.open_access_report(orbiter, tranquility)
    session.open_access_report(orbiter, canberra)
    print("Opened access report windows: orbiter/Tranquility, orbiter/Canberra, "
          f"and chain report for {chain}")

    # Supporting exported reports over the whole window
    session.export_report(orbiter, "Classical Orbit Elements",
                           time_step_sec=REPORT_TIME_STEP_SEC)
    session.export_report(orbiter, "LLA Position",
                           time_step_sec=REPORT_TIME_STEP_SEC)

    # Focus the 3D view on the Moon, since this mission is lunar-centric.
    # Exact Connect syntax for switching the 3D window's central body can
    # vary by STK version; this is best-effort and non-fatal so the rest of
    # the script still completes if it's rejected -- switch it manually from
    # the 3D Graphics window toolbar if needed.
    session.cmd("VO * CentralBody Set Moon",
                label="Focus 3D view on Moon", fatal=False)

    session.save_scenario()

    print("\nSummary:")
    print(f"  Scenario: {SCENARIO_NAME} ({START_UTCG} - {STOP_UTCG})")
    print(f"  Orbiter: {orbiter} (Moon, 100 km circular, equatorial)")
    print(f"  Tranquility: {tranquility} (Moon, 5 deg elevation mask)")
    print(f"  Canberra: {canberra} (Earth, 10 deg elevation mask)")
    print(f"  Chain: {chain}")
    print("  Report windows opened: orbiter/Tranquility access, "
          "orbiter/Canberra access, chain report")
    print("  CSV/report files exported to stk_output/ (see paths above)")
    print("  3D view focus set to Moon (best-effort)")


if __name__ == "__main__":
    try:
        main()
    except stk.StkError as e:
        print(f"STK automation failed: {e}")