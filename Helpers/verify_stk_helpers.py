"""
verify_stk_helpers.py -- smoke test for stk_helpers.py

WHAT THIS IS FOR
----------------
This is the piece that makes the STK agent reliable. The prompt and the
helper module both make claims about STK's API; this script is the only
thing that checks those claims against a REAL STK install.

Run it:

* after any edit to ``stk_helpers.py``
* after any change to the agent prompt
* once on each new STK version / machine you intend to support

It builds a throwaway scenario, exercises every helper, prints PASS/FAIL per
feature, and closes the scenario WITHOUT saving. Nothing in the user's own
work is touched.

    python verify_stk_helpers.py

Exit code 0 = every check passed. Non-zero = at least one failed, and the
summary names which. Any FAIL line is a bug in the helper module (or a
genuine difference in that STK version) -- fix it there, not in the
generated mission scripts.

REQUIREMENTS
------------
Windows, pywin32, STK open. STK does NOT need a scenario open; this script
creates its own. If a scenario IS open, the script refuses to run rather
than risk polluting it -- close it first.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stk_helpers as stk  # noqa: E402


SCENARIO_NAME = "STKHelpersSmokeTest"
START = "1 Jul 2026 00:00:00.000"
STOP = "1 Jul 2026 06:00:00.000"


PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Results(object):
    """Collects PASS/FAIL/SKIP per feature.

    SKIP matters as much as FAIL here: when a helper fails, everything that
    needed its output is reported as SKIPPED rather than failed. Otherwise a
    single root-cause bug prints as a dozen failures and buries the one line
    you actually need to read.
    """

    def __init__(self):
        self.rows = []

    def check(self, label, func, requires=None):
        """Run ``func``; record the outcome. Returns its value, or None.

        ``requires`` is a dict of ``{name: value}`` that must all be
        non-None; if any is None the check is SKIPPED, naming what was
        missing.
        """
        missing = [name for name, value in (requires or {}).items() if value is None]
        if missing:
            detail = "needs %s" % ", ".join(missing)
            self.rows.append((label, SKIP, detail))
            print("  SKIP  %-44s (%s)" % (label, detail))
            return None
        try:
            value = func()
        except Exception as exc:
            # Keep the FULL text: the first line names the failure, but the
            # lines under it carry STK's own reason for each attempted route,
            # which is what actually identifies the bug.
            detail = str(exc).strip()
            self.rows.append((label, FAIL, detail))
            lines = detail.splitlines()
            print("  FAIL  %-44s %s" % (label, lines[0]))
            for line in lines[1:]:
                print("        %s" % line)
            return None
        self.rows.append((label, PASS, ""))
        print("  PASS  %s" % label)
        return value

    def _of(self, status):
        return [row for row in self.rows if row[1] == status]

    def summary(self):
        failed, skipped = self._of(FAIL), self._of(SKIP)
        passed = self._of(PASS)
        print("\n" + "=" * 72)
        print("RESULT: %d passed, %d failed, %d skipped (of %d checks)"
              % (len(passed), len(failed), len(skipped), len(self.rows)))
        if failed:
            print("\nREAL FAILURES -- fix these in stk_helpers.py:")
            for label, _status, detail in failed:
                print("  * %s" % label)
                for line in detail.splitlines():
                    print("      %s" % line)
        if skipped:
            print("\nSkipped (blocked by a failure above, not bugs of their own):")
            for label, _status, detail in skipped:
                print("  - %-44s %s" % (label, detail))
        print("=" * 72)
        return len(failed)


EXPECTED_API = (
    "attach", "launch", "cmd", "obj", "exists", "unique_path", "enum",
    "ensure_scenario", "set_time_period", "set_units", "rewind",
    "save_scenario", "close_scenario", "new_satellite", "new_walker",
    "set_hpop_gravity", "propagate", "set_epoch", "orbit_radius_km",
    "add_sensor_conic", "add_sensor_rectangular", "point_nadir",
    "point_az_el", "point_at_target", "add_transmitter", "add_receiver",
    "new_facility", "new_planet", "set_min_elevation", "compute_access",
    "access_summary", "write_access_csv",
)


def _assert_api(session):
    missing = [name for name in EXPECTED_API if not hasattr(session, name)]
    if missing:
        raise AssertionError("stk_helpers is missing: %s" % ", ".join(missing))
    return len(EXPECTED_API)


def _assert_prompt_matches_api():
    """The live prompt's API section is generated from stk_helpers.py itself
    (api_reference.py), so it cannot drift; just check it generates."""
    import api_reference
    return bool(api_reference.generate(stk.__file__))


def _assert_stk_home(session):
    home = session.directory("STKHome")
    if not home:
        raise AssertionError("GetDirectory / STKHome returned nothing")
    print("        STKHome: %s" % home)
    return home


def _assert_gravity_file(session, filename):
    path = session.find_data_file(filename)
    if not path:
        raise AssertionError(
            "%s not found; searched %s"
            % (filename, ", ".join(getattr(session, "_last_search", [])[:3]) or "nowhere"))
    print("        %s -> %s" % (filename, path))
    return path


def _assert_gmst():
    """GMST at the J2000 epoch is 280.46061837 deg by definition."""
    value = stk.gmst_deg("1 Jan 2000 12:00:00.000")
    if abs(value - 280.46061837) > 1e-3:
        raise AssertionError("GMST at J2000 came out as %.6f, expected 280.460618" % value)
    # and it must repeat after one sidereal day
    later = stk.gmst_deg("2 Jan 2000 11:56:04.100")
    if abs(((later - value + 180) % 360) - 180) > 0.05:
        raise AssertionError("GMST did not repeat after a sidereal day (%.4f vs %.4f)"
                             % (later, value))
    return value


def _assert_geo_longitude(session, longitude_deg, tol_deg=0.15):
    path = session.new_geo_satellite("GeoProbe", longitude_deg)
    actual = session.subsatellite_longitude_deg(path)
    if actual is None:
        raise AssertionError("could not read the sub-satellite longitude back")
    error = ((actual - longitude_deg + 180) % 360) - 180
    print("        asked for %.3f deg, parked at %.3f deg (off by %.3f)"
          % (longitude_deg, actual, error))
    if abs(error) > tol_deg:
        raise AssertionError(
            "GEO parked at %.3f deg, asked for %.3f (off by %.3f deg) -- check "
            "the sidereal-time conversion. An error near 0.34 deg means the "
            "J2000-to-equinox-of-date precession correction is missing."
            % (actual, longitude_deg, error))
    return path


def _assert_sensor_sees_ground(session, sat_path):
    """A nadir sensor must see the ground; an inverted one sees nothing.

    The facility is placed at the sub-satellite point at the start of the
    window, so the sensor is looking straight at it from t=0. An earlier
    version put the facility at (0, 0) and failed for a satellite whose
    ground track never went near it in six hours -- testing luck rather than
    pointing.
    """
    latitude, longitude = session.subsatellite_point(sat_path)
    if latitude is None:
        raise AssertionError("could not read the sub-satellite point")
    gs = session.new_facility("NadirProofGS", latitude, longitude, 0.0)
    cam = session.add_sensor_conic(sat_path, "NadirProof", 45.0)
    session.point_nadir(cam)
    count, seconds, _ = session.compute_access(cam, gs)
    print("        facility placed under the satellite at %.2f, %.2f; "
          "sensor -> ground: %d passes, %.0f s" % (latitude, longitude, count, seconds))
    if count == 0:
        raise AssertionError(
            "a nadir sensor saw a facility directly beneath it zero times -- it "
            "is pointing at space. Check StkSession.NADIR_ELEVATION_DEG "
            "(measured as +90 on STK 11 by probe_pointing_reports.py).")
    return count


def _assert_styles(session):
    styles = session.report_styles("Satellite")
    if len(styles) < 10:
        raise AssertionError("only %d satellite styles found; expected ~100"
                             % len(styles))
    if "Classical Orbit Elements" not in styles:
        raise AssertionError("'Classical Orbit Elements' missing from %d styles"
                             % len(styles))
    print("        %d satellite styles available" % len(styles))
    return len(styles)


def _assert_bad_style(session, sat_path):
    """A near-miss style name is REPAIRED; an unrecognisable one is rejected.

    A style name only chooses which columns are exported, so it cannot make
    a scenario wrong -- which is why a clear near-miss is corrected rather
    than aborting a run that has already built everything. This checks both
    halves of that behaviour.
    """
    # 1. a near miss resolves to the real style
    chosen = session.open_report(sat_path, "Classical Elements")
    if chosen != "Classical Orbit Elements":
        raise AssertionError(
            "'Classical Elements' should have been repaired to 'Classical "
            "Orbit Elements', got %r" % chosen)

    # 2. a name sharing nothing with any style is still refused, with help
    try:
        session.open_report(sat_path, "Zqx Nonsense Style")
    except stk.StkError as exc:
        if "is not a" not in str(exc):
            raise AssertionError("rejected, but unhelpfully: %s" % exc)
        return True
    raise AssertionError("'Zqx Nonsense Style' was accepted; it should not be")


def _assert_walker_tool(session):
    """STK's Walker tool: same geometry as new_walker(), payload replicated,
    and the seed (a duplicate of member 11) removed."""
    seed = session.new_satellite("SmokeSeed", stk.altitude_to_sma(550.0), 53.0, 0.0)
    session.add_sensor_conic(seed, "Cam", 10.0)
    members, const = session.walker_constellation(seed, "Delta", num_planes=3,
                                                  sats_per_plane=4, phase_f=1)
    if session.exists(seed):
        raise AssertionError("Walker seed still present -- it duplicates member 11")
    if [str(k.InstanceName) for k in session.obj(members[0]).Children] != ["Cam"]:
        raise AssertionError("seed sensor not copied to the members")
    if session.obj(const).Objects.Count != 12:
        raise AssertionError("constellation holds %d, expected 12" % session.obj(const).Objects.Count)
    star_seed = session.new_satellite("SmokeStar", stk.altitude_to_sma(780.0), 86.4, 0.0)
    star, _ = session.walker_constellation(star_seed, "Star", num_planes=6,
                                           sats_per_plane=2, phase_f=1)
    print("        Delta 12/3/1 and Star 12/6/1 built and verified (%d + %d members)"
          % (len(members), len(star)))
    return True


class _LongerPeriod(object):
    """Temporarily extend the smoke scenario (6 h) for checks that need days.

    The drift, decay and transfer checks were written against a 2-day bridge
    scenario and failed here purely on window length: 0.2463 deg/day is
    exactly 0.9856 x 6/24.
    """

    def __init__(self, session, days):
        self.session, self.days = session, days

    def __enter__(self):
        self.session.set_time_period(START, self.session._utcg_offset(START, self.days * 86400.0))
        return self

    def __exit__(self, *exc):
        self.session.set_time_period(START, STOP)
        return False


def _assert_astrogator_transfer(session):
    """The differential corrector reaches GEO -- verified on the ephemeris.

    Point mass must reproduce the textbook Hohmann dv (3.89256 km/s) and an
    exactly circular GEO; with real gravity the dv is a few m/s higher and
    the orbit still sits at GEO radius. The goal must be at the END of the
    coast: attached to the burn it "converged" 175 km short of GEO.
    """
    with _LongerPeriod(session, 2):
        results = {}
        for prop in ("Earth Point Mass", "Earth HPOP Default v10"):
            sat = session.new_astrogator_satellite("SmokeGator%s" % prop.split()[1],
                                                   6678.137, propagator=prop)
            results[prop] = session.plan_circular_transfer(sat, 42164.137, propagator=prop)
        pm, hp = results["Earth Point Mass"], results["Earth HPOP Default v10"]
        if abs(pm["total_dv_kms"] - 3.89256) > 1e-4:
            raise AssertionError("point-mass total dv %.5f, textbook 3.89256" % pm["total_dv_kms"])
        for label, res in (("point mass", pm), ("HPOP", hp)):
            lo, hi = res["final_radius_km"]
            if not (42164.137 - 5.0 < lo and hi < 42164.137 + 5.0):
                raise AssertionError("%s final orbit %.1f-%.1f km, not GEO" % (label, lo, hi))
        if not 0.0005 < hp["total_dv_kms"] - pm["total_dv_kms"] < 0.02:
            raise AssertionError("real-gravity dv %.5f vs point mass %.5f -- J2 penalty implausible"
                                 % (hp["total_dv_kms"], pm["total_dv_kms"]))
        print("        dv point mass %.5f / HPOP %.5f km/s; HPOP orbit %.1f-%.1f km"
              % (pm["total_dv_kms"], hp["total_dv_kms"], *hp["final_radius_km"]))
        return True


def _assert_state_round_trip(session):
    """A satellite rebuilt from another's J2000 state vector flies identically."""
    with _LongerPeriod(session, 2):
        import math
        t0 = session._start
        t1 = session._utcg_offset(t0, 86400.0)
        ref = session.new_satellite("SmokeStateRef", stk.altitude_to_sma(550.0), 53.0, 40.0,
                                    ecc=0.001, arg_perigee_deg=30.0, true_anomaly_deg=10.0)
        r = session.read_data(ref, "Cartesian Position", group="J2000", start=t0, stop=t0)
        v = session.read_data(ref, "Cartesian Velocity", group="J2000", start=t0, stop=t0)
        state = [r.column(c)[0] for c in "xyz"] + [v.column(c)[0] for c in "xyz"]
        copy = session.new_satellite_from_state("SmokeStateCopy", "cartesian", state, frame="J2000")

        def pos(path):
            q = session.read_data(path, "Cartesian Position", group="J2000", start=t1, stop=t1)
            return [q.column(c)[0] for c in "xyz"]
        gap = math.dist(pos(ref), pos(copy))
        if gap > 0.01:
            raise AssertionError("rebuilt satellite %.3f km from the original after a day" % gap)
        print("        rebuilt from its state vector: %.4f km apart after 1 day" % gap)
        return True


def _assert_attitude_eccentric(session):
    """The attitude profile is the one ASKED for -- word order matters.

    "Nadir Alignment With ECI Velocity Constraint" used to set the ECI
    VELOCITY alignment profile (same words, swapped roles). On a circular
    orbit the two are identical; on a Molniya orbit a quarter of the way from
    perigee the flight-path angle is ~40 deg, so the velocity-aligned body's
    +Z misses nadir by that much while the nadir-aligned one stays on it.
    """
    import math
    when = session._start

    def nadir_off_z(path):
        r = session.read_data(path, "Vectors(Body)", group="Nadir(Centric)",
                              start=when, stop=when)
        c = {k.lower(): r.column(k)[0] for k in r.columns}
        n = math.sqrt(c["x"] ** 2 + c["y"] ** 2 + c["z"] ** 2)
        return math.degrees(math.acos(max(-1.0, min(1.0, c["z"] / n))))

    angles = {}
    for label, profile in (("nadir", "Nadir Alignment With ECI Velocity Constraint"),
                           ("velocity", "ECI Velocity Alignment With Nadir Constraint")):
        sat = session.new_satellite("SmokeAtt%s" % label.capitalize(), 26554.0, 63.4, 0.0,
                                    ecc=0.74, arg_perigee_deg=270.0, true_anomaly_deg=90.0)
        chosen = session.set_attitude_profile(sat, profile)
        angles[label] = (chosen, nadir_off_z(sat))
    if angles["nadir"][1] > 0.01:
        raise AssertionError("nadir-aligned body misses nadir by %.3f deg (%s)" % (
            angles["nadir"][1], angles["nadir"][0]))
    if angles["velocity"][1] < 10.0:
        raise AssertionError("velocity-aligned body only %.2f deg off nadir -- was the "
                             "nadir profile set instead? (%s)" % (angles["velocity"][1],
                                                                  angles["velocity"][0]))
    print("        Molniya: nadir-aligned %.3f deg off nadir; velocity-aligned %.1f deg"
          % (angles["nadir"][1], angles["velocity"][1]))
    return True


def _assert_lifetime(session):
    """STK's Lifetime tool: plausible, scales with mass, reports no-decay.

    Measured on STK 11: 3U at 400 km ~223 days near the 2026 solar maximum;
    double mass ~x2.1; a 3U at 800 km does not decay within 50 years.
    """
    low = session.new_satellite("SmokeLife400", stk.altitude_to_sma(400.0), 51.6, 0.0,
                                propagator="J2Perturbation")
    a = session.satellite_lifetime(low, spacecraft="3U")
    b = session.satellite_lifetime(low, spacecraft="custom", mass_kg=8.0, area_m2=0.039)
    if not (a["decays"] and b["decays"]):
        raise AssertionError("a 400 km 3U did not decay: %s" % a["message"])
    if not 30.0 < a["lifetime_days"] < 1500.0:
        raise AssertionError("3U at 400 km lifetime %.0f days is implausible" % a["lifetime_days"])
    ratio = b["lifetime_days"] / a["lifetime_days"]
    if not 1.7 < ratio < 2.6:
        raise AssertionError("double mass changed lifetime x%.2f, expected ~2" % ratio)
    high = session.new_satellite("SmokeLife800", stk.altitude_to_sma(800.0), 51.6, 0.0,
                                 propagator="J2Perturbation")
    c = session.satellite_lifetime(high, spacecraft="3U", max_years=50)
    if c["decays"]:
        raise AssertionError("a 3U at 800 km decayed within 50 years: %s" % c["message"])
    print("        3U@400 %.0f d; 2x mass x%.2f; 3U@800 no decay in 50 y"
          % (a["lifetime_days"], ratio))
    return True


def _assert_propagator_physics(session):
    """Propagators match physics; HPOP drag follows the spacecraft's area/mass.

    Measured on STK 11: SSO plane drift J2 +0.9852 deg/day (target +0.9856);
    with mean elements, drag-off decay ~0 and drag decay ~2x for 2x area/mass
    on the first day. Osculating elements showed -3.7 km of false "decay".
    """
    with _LongerPeriod(session, 3):
        import math
        start = session._start
        stop = session._utcg_offset(start, 2 * 86400.0)
        a600 = stk.altitude_to_sma(600.0)
        inc = math.degrees(math.acos(-(a600 / 12352.0) ** 3.5))
        sso = session.new_satellite("SmokeJ2", a600, inc, 0.0, propagator="J2Perturbation")
        raan = session.read_data(sso, "Classical Elements", group="TrueOfDate",
                                 elements=["Time", "RAAN"], start=start, stop=stop,
                                 step_sec=86400).column("RAAN")
        drift = (((raan[1] - raan[0]) + 540) % 360) - 180
        if abs(drift - 0.9856) > 0.005:
            raise AssertionError("J2 SSO plane drifts %.4f deg/day, expected +0.9856" % drift)
        a300 = stk.altitude_to_sma(300.0)
        decay = {}
        for amr in (None, 0.01, 0.02):
            p = session.new_satellite("SmokeDrag%s" % ("Off" if amr is None else str(amr)[2:]),
                                      a300, 51.6, 0.0, propagator="HPOP")
            session.set_hpop_forces(p, drag=amr is not None, drag_area_mass=amr,
                                    f107=150, f107_avg=150, kp=3)
            m = session.mean_elements(p, step_sec=86400, start=start, stop=stop)
            a = m.column("Mean Semi-major Axis")
            decay[amr] = a[1] - a[0]
        if abs(decay[None]) > 0.5:
            raise AssertionError("drag OFF but mean SMA moved %.2f km in a day" % decay[None])
        ratio = decay[0.02] / decay[0.01]
        if not 1.8 <= ratio <= 2.4:
            raise AssertionError("first-day decay ratio %.2f for 2x area/mass, expected ~2" % ratio)
        print("        J2 SSO drift %+.4f deg/day; day-1 decay %.2f / %.2f km (ratio %.2f), drag off %+.3f"
              % (drift, decay[0.01], decay[0.02], ratio, decay[None]))
        return True


def _assert_orbit_wizard(session):
    """The Orbit Wizard builds what was asked -- and refuses its silent traps.

    Measured on STK 11: distances are metres and a wrong unit is accepted
    (0.6 km altitude), local times must be HH:MM:SS, and RepeatingSunSync
    ignores the requested local time (88 minutes off).
    """
    sso = session.orbit_wizard("SmokeSSO", "SunSynchronous", altitude_km=600,
                               local_time_desc_node="10:30")
    _ltan, ltdn = session.local_time_of_nodes(sso)
    if abs(ltdn - 10.5) * 60.0 > 20.0:
        raise AssertionError("SSO descending node at %.2f h, asked 10:30" % ltdn)
    geo = session.orbit_wizard("SmokeGEO", "Geosynchronous", subsatellite_point_deg=23.7)
    lon = session.read_data(geo, "LLA State", group="Fixed", elements=["Time", "Lon"],
                            start=session._start, stop=session._start).column("Lon")[0]
    if abs(lon - 23.7) > 0.01:
        raise AssertionError("GEO parked at %.3f E, asked 23.700" % lon)
    try:
        session.orbit_wizard("SmokeRSS", "RepeatingSunSync", approx_revs_per_day=15,
                             revs_to_repeat=15, local_time_desc_node=10.5)
    except stk.StkError as exc:
        if "ignores" not in str(exc):
            raise AssertionError("RepeatingSunSync refused for the wrong reason: %s" % exc)
    else:
        raise AssertionError("RepeatingSunSync's ignored local time was accepted")
    print("        SSO LTDN %.2f h; GEO %.3f E; RepeatingSunSync trap refused" % (ltdn, lon))
    return True


def _assert_relay_and_rfi(session):
    """Bent pipe combines BOTH legs; interference actually degrades the link.

    Guards the re-transmitter's -700 MHz default translation (negative
    downlink frequency, total == uplink alone) and the CommSystem's
    constellation + PreData requirements (empty results otherwise).
    """
    import math
    sat = session.new_satellite("SmokeRelay", stk.altitude_to_sma(500.0), 97.4, 0.0)
    a = session.new_facility("SmokeAthens", 37.98, 23.73, 0.1)
    b = session.new_facility("SmokeSofia", 42.70, 23.32, 0.6)
    ya = session.add_tracking_ground_antenna(a, sat, "SmokeYA", 0.435, 14.0)
    yb = session.add_tracking_ground_antenna(b, sat, "SmokeYB", 0.437, 14.0)
    up = session.add_radio_transmitter(a, "SmokeUp", 0.435, 10.0, 9.6, antenna=ya)
    srx, rt = session.add_bent_pipe(sat, "SmokePipe", 0.435, 0.437, 0.0)
    dn = session.add_radio_receiver(b, "SmokeDn", antenna=yb, required_ebno_db=9.6)
    lb = session.chain_link_budget(session.new_chain("SmokePipeChain", [up, srx, rt, dn]))
    if len(lb) == 0:
        raise AssertionError("bent pipe produced no samples")
    for i in range(len(lb)):
        u, d, t = lb.column("C/No1")[i], lb.column("C/No2")[i], lb.column("C/No Tot.2")[i]
        if abs(-10 * math.log10(10 ** (-u / 10) + 10 ** (-d / 10)) - t) > 0.01:
            raise AssertionError("total C/No %.2f does not combine legs %.2f/%.2f" % (t, u, d))
    wanted = session.add_radio_transmitter(sat, "SmokeWanted", 0.437, 0.0, 9.6,
                                           antenna_model="Hemispherical")
    jsat = session.new_satellite("SmokeJammer", stk.altitude_to_sma(520.0), 97.4, 0.0,
                                 true_anomaly_deg=-3.0)
    jam = session.add_radio_transmitter(jsat, "SmokeJam", 0.437, 0.0, 9.6,
                                        antenna_model="Hemispherical")
    victim = session.add_radio_receiver(b, "SmokeVictim", antenna=yb)
    ib = session.interference_budget(
        session.new_interference_study("SmokeRFI", [wanted], [victim], [jam]), victim,
        elements=["Eb/No", "Eb/(No+I)", "C/I"])
    if len(ib) == 0:
        raise AssertionError("interference study returned no samples (%s)" % ib.message)
    worst = max(ib.column("Eb/No")[i] - ib.column("Eb/(No+I)")[i] for i in range(len(ib)))
    if worst < 10.0:
        raise AssertionError("co-channel interferer degraded Eb/No by only %.1f dB" % worst)
    print("        bent pipe %d samples, legs combine; interference costs up to %.1f dB"
          % (len(lb), worst))
    return True


def _assert_link_budget(session):
    """A UHF downlink to a tracking Yagi must come out physically right.

    Guards every trap measured in bridge jobs 0046-0059, each of which STK
    accepts silently: a gain antenna embedded with a 14.5 GHz design
    frequency (-16.4 dBi instead of 14), a receiver on a sensor that cannot
    link, a margin stuck at -9999.99, and access data whose later passes are
    dropped (37 rows of a six-pass day).
    """
    import math
    sat = session.new_satellite("SmokeLinkSat", stk.altitude_to_sma(500.0), 97.4, 0.0)
    gs = session.new_facility("SmokeLinkGS", 38.58, 23.65, 0.1)
    session.set_min_elevation(gs, 5.0)
    yagi = session.add_tracking_ground_antenna(gs, sat, "SmokeYagi", 0.437, 14.0)
    rx = session.add_radio_receiver(gs, "SmokeRx", antenna=yagi,
                                    lna_noise_figure_db=1.0, required_ebno_db=9.6)
    tx = session.add_radio_transmitter(sat, "SmokeTx", 0.437, 0.0, 9.6,
                                       antenna_model="Hemispherical")
    lb = session.link_budget(tx, rx)
    if len(lb.segments) < 2:
        raise AssertionError("only %d pass(es) returned in 24 h" % len(lb.segments))
    rng = lb.column("Range")
    k = rng.index(min(rng))
    gain = lb.column("Rcvr Gain")[k]
    if abs(gain - 14.0) > 0.2:
        raise AssertionError("tracking Yagi gain %.2f dBi at the peak, expected 14" % gain)
    if lb.column("Link Margin")[k] < -9000:
        raise AssertionError("link margin not enabled (-9999.99)")
    fspl = 20 * math.log10(4 * math.pi * rng[k] * 1e3 * 0.437e9 / 299792458.0)
    if abs(fspl - lb.column("Free Space Loss")[k]) > 0.05:
        raise AssertionError("free-space loss %.2f dB vs Friis %.2f dB"
                             % (lb.column("Free Space Loss")[k], fspl))
    print("        %d passes; peak %.0f km, Yagi %.2f dBi, Eb/No %.1f dB, margin %.1f dB"
          % (len(lb.segments), rng[k], gain, lb.column("Eb/No")[k], lb.column("Link Margin")[k]))
    return True


def _assert_read_data(session):
    """The generic data-provider reader returns the RIGHT shape of answer.

    An equatorial 500 km orbit eclipses once per orbit for ~34 minutes, so a
    6-hour window must hold 4 umbra intervals. Reading STK's result.Intervals
    as the events reported ONE interval spanning the window instead -- a
    plausible number that was wrong. This pins the correct reading.
    """
    start = session._start
    stop = session._utcg_offset(start, 6 * 3600.0)
    eq = session.new_satellite("SmokeEq", stk.altitude_to_sma(500.0), 0.0, 0.0)

    umbra = session.read_data(eq, "Lighting Times", group="Umbra",
                              start=start, stop=stop)
    if umbra.kind != "interval" or len(umbra.intervals) != 4:
        raise AssertionError("expected 4 umbra intervals in 6 h, got %d (%s)"
                             % (len(umbra.intervals), umbra.message))
    durations = umbra.column("Duration")
    if not all(abs(d - 2066.0) < 30.0 for d in durations[:3]):
        raise AssertionError("eclipse durations %r, expected ~2066 s" % durations[:3])

    elements = session.read_data(eq, "Classical Elements", group="J2000",
                                 elements=["Time", "Semi-major Axis"],
                                 start=start, stop=stop, step_sec=600)
    sma = elements.column("Semi-major Axis")[0]
    if elements.kind != "time-varying" or abs(sma - stk.altitude_to_sma(500.0)) > 0.01:
        raise AssertionError("SMA read back as %.3f km" % sma)

    try:
        session.read_data(eq, "Classical Element")
    except stk.StkError as exc:
        if "Classical Elements" not in str(exc):
            raise AssertionError("wrong name refused without a suggestion: %s" % exc)
    else:
        raise AssertionError("a misspelled provider name was accepted")

    print("        4 eclipses of %.0f s; SMA %.3f km" % (durations[0], sma))
    return True


def _assert_country_area_target(session):
    """Built-in Natural Earth outlines build real area targets.

    The alternative -- a lat/lon box, or an outline recalled from memory --
    is the failure that produces numbers rather than errors: a Greece box
    also covers Albania and North Macedonia, so revisit statistics average
    over countries nobody asked about.
    """
    countries = session.list_countries()
    if not countries:
        raise AssertionError(
            "no built-in country outlines; country_polygons.py is not next "
            "to stk_helpers.py")
    if "Greece" not in countries:
        raise AssertionError("Greece missing from %d outlines" % len(countries))
    print("        %d built-in outlines available" % len(countries))

    path = session.new_area_target_from_country("SmokeGreece", "Greece")

    # ring 1 is Crete -- multi-part countries must be reachable
    crete = session.new_area_target_from_country("SmokeCrete", "Greece", ring=1)
    print("        mainland: %s ; Crete: %s" % (path, crete))

    # an unknown country must name what IS available, not fail obscurely
    try:
        session.new_area_target_from_country("SmokeNowhere", "Wakanda")
    except stk.StkError as exc:
        if "Available" not in str(exc):
            raise AssertionError("rejected without listing alternatives: %s" % exc)
    else:
        raise AssertionError("'Wakanda' was accepted as a country")
    return path


def _assert_output_dir_join(session):
    """output_dir() must behave as a string AND accept pathlib's ``/``.

    Generated scripts write ``session.output_dir() / "results.csv"``. That
    used to raise TypeError after the whole scenario had been built, which
    is the most expensive possible moment for a one-character syntax
    preference to matter.
    """
    folder = session.output_dir()
    joined = folder / "smoke_join.csv"
    expected = os.path.join(str(folder), "smoke_join.csv")
    if str(joined) != expected:
        raise AssertionError("'/' gave %r, expected %r" % (str(joined), expected))
    if os.path.join(folder, "x.csv") != os.path.join(str(folder), "x.csv"):
        raise AssertionError("os.path.join no longer works on output_dir()")
    if not isinstance(folder, str):
        raise AssertionError("output_dir() must still be a str, got %s" % type(folder))
    return True


def _write_test_shapefile(path, ring, projected=False):
    """Write a minimal one-polygon shapefile, so this check needs no test data.

    Only the .shp geometry file is written -- read_shapefile_rings() does not
    read the .dbf. ``ring`` is a list of (lon, lat) pairs, closed.
    """
    import struct

    body = struct.pack("<i", 5)
    xs = [x for x, _y in ring]
    ys = [y for _x, y in ring]
    body += struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
    body += struct.pack("<ii", 1, len(ring)) + struct.pack("<i", 0)
    for x, y in ring:
        body += struct.pack("<2d", x, y)

    header = struct.pack(">i", 9994) + b"\0" * 20
    header += struct.pack(">i", (100 + 8 + len(body)) // 2)
    header += struct.pack("<i", 1000) + struct.pack("<i", 5)
    header += struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
    header += struct.pack("<4d", 0, 0, 0, 0)

    with open(path, "wb") as handle:
        handle.write(header + struct.pack(">ii", 1, len(body) // 2) + body)

    if projected:
        with open(os.path.splitext(path)[0] + ".prj", "w") as handle:
            handle.write('PROJCS["WGS_84_UTM_zone_34N",GEOGCS["GCS_WGS_1984"]]')


def _assert_shapefile_area_target(session):
    """A geographic shapefile builds an area target; a projected one is refused.

    A country outline recalled from memory looks plausible and is wrong by
    tens of kilometres. Reading the real boundary from a shapefile is the
    honest route -- provided a PROJECTED file (coordinates in metres) is
    caught, because those would otherwise place the region near the Gulf of
    Guinea without any error at all.
    """
    folder = session.output_dir()
    good = os.path.join(folder, "_smoke_geographic.shp")
    bad = os.path.join(folder, "_smoke_projected.shp")

    # a 1 deg square over the Aegean, as (lon, lat)
    ring = [(23.0, 38.0), (24.0, 38.0), (24.0, 39.0), (23.0, 39.0), (23.0, 38.0)]
    _write_test_shapefile(good, ring)
    _write_test_shapefile(bad, [(500000.0, 4200000.0), (510000.0, 4200000.0),
                                (510000.0, 4210000.0), (500000.0, 4210000.0),
                                (500000.0, 4200000.0)], projected=True)

    rings = session.read_shapefile_rings(good)
    if len(rings) != 1 or len(rings[0]) != 5:
        raise AssertionError("expected one 5-vertex ring, got %s"
                             % [len(r) for r in rings])
    if rings[0][0] != (38.0, 23.0):
        raise AssertionError(
            "rings must come back as (lat, lon); got %r" % (rings[0][0],))

    path = session.new_area_target_from_shapefile("SmokeShapeROI", good)
    print("        shapefile area target: %s" % path)

    # a projected file must RAISE, not silently build a region in the wrong place
    try:
        session.new_area_target_from_shapefile("SmokeShapeBad", bad)
    except stk.StkError as exc:
        if "PROJECTED" not in str(exc) and "projected" not in str(exc):
            raise AssertionError("refused, but not as a projection problem: %s" % exc)
    else:
        raise AssertionError(
            "a projected shapefile was accepted; its metre coordinates would "
            "place the area target in the wrong hemisphere")
    return path


def _assert_coverage_regions(session, sat_path, roi_path, box_cov_path):
    """A region-bounded grid must exist AND be smaller than the box.

    A lat/lon box is the wrong shape for a country: a "Greece" box also
    covers Albania, a "Japan" box covers both Koreas, and revisit statistics
    then average over places nobody asked about. Bounding the grid to an
    area target fixes that -- but only if STK actually applied the polygon
    rather than silently leaving the grid global, which is what this checks.

    ProbeROI is a 1 deg x 1 deg square at (38-39 N, 23-24 E); the box
    coverage is 15 deg x 15 deg. At the same resolution the polygon grid
    must therefore have FEWER points. Equal counts mean the region was
    ignored.
    """
    region_cov = session.new_coverage(
        "ProbeCovRegion", regions=[roi_path], resolution_deg=0.5,
        assets=[sat_path], compute=False)

    region_points = session.grid_point_count(region_cov)
    box_points = session.grid_point_count(box_cov_path)
    print("        polygon grid: %s points ; box grid: %s points"
          % (region_points, box_points))

    if region_points is None:
        raise AssertionError(
            "grid_point_count() could not read a point count on this STK; "
            "the region grid cannot be verified")
    if region_points <= 0:
        raise AssertionError(
            "the region-bounded grid has no points -- the area target was "
            "not applied, or the resolution is coarser than the region")
    if box_points is not None and region_points >= box_points:
        raise AssertionError(
            "region grid (%d points) is not smaller than the 15x15 deg box "
            "grid (%d) -- the polygon was probably ignored"
            % (region_points, box_points))
    return region_cov


def _assert_export(session, sat_path):
    path = session.export_report(sat_path, "Classical Orbit Elements",
                                 time_step_sec=600)
    if not os.path.isfile(path) or os.path.getsize(path) < 100:
        raise AssertionError("report file missing or empty: %s" % path)
    print("        wrote %d bytes to %s" % (os.path.getsize(path), os.path.basename(path)))
    return path


def _assert_access_nonzero(session, from_path, to_path):
    count, seconds, _ = session.compute_access(from_path, to_path)
    print("        %s -> %s: %d passes, %.0f s"
          % (from_path.rsplit("/", 1)[-1], to_path.rsplit("/", 1)[-1], count, seconds))
    if count == 0:
        raise AssertionError("no access at all -- suspicious for this geometry")
    return count


def _assert_pass_list(session, sat_path, gs_path):
    path = session.write_pass_list_csv(sat_path, gs_path)
    with open(path) as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    if len(lines) < 2:
        raise AssertionError("pass list has a header but no passes: %s" % path)
    if not lines[0].startswith("Pass,Start,Stop"):
        raise AssertionError("unexpected header: %s" % lines[0])
    print("        %d passes listed" % (len(lines) - 1))
    return path


def _assert_body_not_sticky(session):
    """Earth objects must not inherit a previous object's central body."""
    lunar = session.new_facility("StickyLunar", 0.0, 20.0, 0.0, central_body="Moon")
    earthly = session.new_facility("StickyEarth", -35.4, 148.98, 0.6)
    lunar_body = session.central_body_of(lunar)
    earth_body = session.central_body_of(earthly)
    print("        lunar site -> %s ; Earth site created next -> %s"
          % (lunar_body, earth_body))
    if not earth_body or earth_body.lower() != "earth":
        raise AssertionError(
            "a facility created without a central body landed on %s, not Earth "
            "-- STK carried the previous body forward" % earth_body)
    return earth_body


def _assert_bodies(session):
    names = session.central_body_names()
    if len(names) < 10:
        raise AssertionError("only %d central bodies found; expected ~31" % len(names))
    for expected in ("Earth", "Moon", "Mars"):
        if expected not in names:
            raise AssertionError("%s missing from the %d bodies" % (expected, len(names)))
    print("        %d bodies, including Mars, Venus, Titan" % len(names))
    return len(names)


def _assert_radii(session):
    """Radii must match known values, or a Mars orbit would be built wrong."""
    expected = {"Earth": 6378.1, "Moon": 1737.4, "Mars": 3396.2}
    out = {}
    for body, want in expected.items():
        got = session.body_radius_km(body)
        out[body] = got
        # Mars' equatorial radius is 3396.2; mean is 3389.5. Allow either.
        if abs(got - want) > 12.0:
            raise AssertionError("%s radius read as %.1f km, expected ~%.1f"
                                 % (body, got, want))
    print("        " + ", ".join("%s %.1f km" % (b, r) for b, r in sorted(out.items())))
    return out


def _assert_mars(session):
    sma = session.sma_for_altitude(400.0, central_body="Mars")
    sat = session.new_satellite("MarsProbe", sma_km=sma, inc_deg=93.0,
                                raan_deg=0.0, central_body="Mars")
    body = session.central_body_of(sat)
    radius = session.orbit_radius_km(sat)
    print("        Mars orbiter: body %s, sma %.1f km, radius %.1f km"
          % (body, sma, radius or -1))
    if not body or body.lower() != "mars":
        raise AssertionError("satellite landed on %s, not Mars" % body)
    return sat


def main():
    print("=" * 68)
    print("stk_helpers smoke test")
    print("=" * 68)

    results = Results()

    # --- prompt/API agreement (no STK needed) -----------------------------
    # Runs first because a drifted prompt breaks every generated script
    # regardless of whether the module itself works.
    results.check("prompt documents only real functions and keywords",
                  _assert_prompt_matches_api)

    # --- connection -------------------------------------------------------
    session = results.check("attach to running STK", stk.StkSession.attach)
    if session is None:
        print("\nCannot continue without an STK connection. Open STK and re-run.")
        return 2
    print("        (connected via ProgID: %s)" % session.progid)
    try:
        enum_count = len(session._load_enums())
        print("        (%d enum values read from this STK's type library)" % enum_count)
    except Exception as exc:
        print("        (could not read enums from the type library: %s)" % exc)

    open_scenario = session.scenario_name
    if open_scenario is not None:
        print("\nREFUSING TO RUN: scenario '%s' is already open.\n"
              "This test creates and then discards its own scenario; close "
              "yours first so nothing of yours is affected." % open_scenario)
        return 2

    # --- API surface ------------------------------------------------------
    # Catches an edit that silently drops a helper (this exact regression
    # happened once: new_walker was removed by a careless block replacement
    # and only surfaced as a confusing AttributeError mid-run).
    results.check("helper API surface is complete",
                  lambda: _assert_api(session))

    # --- scenario ---------------------------------------------------------
    results.check("create scenario",
                  lambda: session.ensure_scenario(SCENARIO_NAME))
    results.check("set unit preferences", session.set_units)
    results.check("set analysis time period",
                  lambda: session.set_time_period(START, STOP))

    try:
        # --- satellites ---------------------------------------------------
        sma = stk.altitude_to_sma(500.0)

        sat = results.check(
            "create satellite + classical state (J2Perturbation)",
            lambda: session.new_satellite("SmokeSat", sma_km=sma, inc_deg=97.4,
                                          raan_deg=0.0))

        if sat is not None:
            print("        (SetState form accepted by this STK build: %s)"
                  % getattr(session, "_classical_form", "?"))

        results.check(
            "ephemeris is real (position query returns data)",
            lambda: _assert_has_ephemeris(session, sat), requires={"satellite": sat})

        results.check(
            "orbit radius reads back at the right scale (no unit error)",
            lambda: _assert_radius(session, sat, sma), requires={"satellite": sat})

        results.check("unique_path avoids collisions",
                      lambda: _assert_unique(session, "*/Satellite/SmokeSat"))

        results.check(
            "Walker element generator (4/4/1 -> RAANs 0/90/180/270)",
            _assert_walker_math)

        walker = results.check(
            "create Walker constellation (4/4/1)",
            lambda: session.new_walker("WalkSat", total_sats=4, num_planes=4,
                                       phase_f=1, sma_km=sma, inc_deg=97.4))

        # Regression: a generated script passed raan_deg= to new_walker (the
        # name new_satellite uses) and got "multiple values for keyword
        # argument". new_walker must absorb that, not explode.
        results.check(
            "new_walker tolerates single-satellite argument names",
            lambda: session.new_walker("AliasSat", total_sats=2, num_planes=2,
                                       phase_f=1, sma_km=sma, inc_deg=97.4,
                                       raan_deg=45.0))

        # Every other orbit here is circular; an eccentric one exercises
        # argument of perigee and the perigee/apogee radius band.
        results.check(
            "eccentric orbit (Molniya: e=0.74, argp=270)",
            lambda: session.new_satellite(
                "MolniyaProbe", sma_km=26554.0, inc_deg=63.4, raan_deg=0.0,
                ecc=0.74, arg_perigee_deg=270.0))

        results.check("two-body propagator name accepted",
                      lambda: session.new_satellite("TwoBodySat", sma_km=sma,
                                                    inc_deg=45.0, raan_deg=10.0,
                                                    propagator="TwoBody"))

        # --- STK's own data files -----------------------------------------
        # A generated script passes a bare filename; if resolution breaks,
        # every HPOP scenario fails with a confusing "does not exist".
        results.check("STKHome directory is reported",
                      lambda: _assert_stk_home(session))
        results.check("lunar gravity file GL0420A.grv resolves",
                      lambda: _assert_gravity_file(session, "GL0420A.grv"))

        # A facility early, since the constraint/chain checks below need one.
        gs = results.check(
            "facility for constraint and chain checks",
            lambda: session.new_facility("ProbeStation", 38.5333, 23.6333, 0.2))

        # --- GEO placement -------------------------------------------------
        # A GEO placed by hand lands at an epoch-dependent longitude, which
        # silently produces zero access to the ground station it was meant to
        # serve. Check the satellite actually parks where it was asked to.
        results.check("sidereal time matches the J2000 anchor",
                      _assert_gmst)
        geo = results.check(
            "GEO parks over the requested longitude",
            lambda: _assert_geo_longitude(session, -3.7))

        # --- sensors ------------------------------------------------------
        cam = results.check(
            "add rectangular sensor",
            lambda: session.add_sensor_rectangular(sat, "RectCam", 1.11, 0.83),
            requires={"satellite": sat})
        results.check("nadir pointing", lambda: session.point_nadir(cam),
                      requires={"sensor": cam})
        # The check that was missing: every other access here is
        # satellite-to-facility, which ignores pointing, so an inverted
        # sensor looked identical to a correct one. Access FROM THE SENSOR
        # is the only thing that proves it faces the ground.
        results.check(
            "a nadir sensor actually sees the ground",
            lambda: _assert_sensor_sees_ground(session, sat),
            requires={"satellite": sat})
        # Connect's Point ... Targeted Track was rejected on STK 11; the
        # helper falls back to the object model. If both fail the sensor is
        # left unpointed and reports zero access, so this must not be silent.
        results.check(
            "targeted pointing (sensor tracks another object)",
            lambda: session.point_at_target(
                session.add_sensor_conic(sat, "TrackCam", 3.0), geo),
            requires={"satellite": sat, "target": geo})
        results.check("add conic sensor",
                      lambda: session.add_sensor_conic(sat, "ConicCam", 22.5, 0.1),
                      requires={"satellite": sat})

        # --- RF -----------------------------------------------------------
        results.check(
            "add transmitter (.Eirp casing + SetEmbeddedModel method)",
            lambda: session.add_transmitter(sat, "SmokeTX", freq_ghz=0.437,
                                            eirp_dbw=0.0, data_rate_kbps=19.2),
            requires={"satellite": sat})

        # --- new capabilities: constraints, ROI, coverage, chains, attitude
        # Each was "syntax unknown" until probe_object_model.py read the real
        # member names off the type library. These checks prove the wrappers
        # work rather than that the objects merely got created.
        # IMPORTANT: apply constraints to throwaway objects. An earlier
        # version put a 3000 km range limit on the shared facility and a
        # DirectSun constraint on the shared satellite, which then silently
        # filtered every later access check against them -- the per-pass CSV
        # came back with zero passes and looked like a CSV bug. Test
        # constraints must not leak into other tests.
        constraint_gs = results.check(
            "facility for constraint tests only",
            lambda: session.new_facility("ConstraintProbeGS", 10.0, 20.0, 0.0))
        results.check("range constraint applies",
                      lambda: session.set_constraint(constraint_gs, "Range", maximum=3000),
                      requires={"facility": constraint_gs})
        results.check("lighting constraint applies",
                      lambda: session.set_constraint(constraint_gs, "Lighting",
                                                     value="DirectSun"),
                      requires={"facility": constraint_gs})
        def _offnadir_refused_on_facility():
            try:
                session.set_constraint(constraint_gs, "OffNadirAngle", maximum=45)
            except ValueError:
                return True
            raise AssertionError("off-nadir on a facility was guessed as 90 - x")
        results.check("off-nadir refused on a facility (no flat 90-x)",
                      _offnadir_refused_on_facility,
                      requires={"facility": constraint_gs})
        roi = results.check(
            "area target from a boundary",
            lambda: session.new_area_target(
                "ProbeROI", [(38, 23), (39, 23), (39, 24), (38, 24)]))
        results.check("satellite sees the area target",
                      lambda: _assert_access_nonzero(session, sat, roi),
                      requires={"satellite": sat, "area target": roi})
        results.check("attitude profile set on the bus",
                      lambda: session.set_attitude_profile(sat),
                      requires={"satellite": sat})
        chain = results.check(
            "chain satellite -> facility",
            lambda: session.new_chain("ProbeChain", [sat, gs]),
            requires={"satellite": sat, "facility": gs})
        cov = results.check(
            "coverage definition over a lat/lon box",
            lambda: session.new_coverage("ProbeCov", 30, 45, 15, 30,
                                         resolution_deg=5.0, assets=[sat]),
            requires={"satellite": sat})
        results.check("figure of merit (revisit time)",
                      lambda: session.add_figure_of_merit(cov, "Revisit"),
                      requires={"coverage": cov})
        results.check(
            "Walker tool: Delta matches ours, Star spans 180, payload copied, no seed",
            lambda: _assert_walker_tool(session))
        results.check(
            "Astrogator: Hohmann to GEO, exact on point mass, real orbit on HPOP",
            lambda: _assert_astrogator_transfer(session))
        results.check(
            "orbit state from a J2000 state vector: exact round trip",
            lambda: _assert_state_round_trip(session))
        results.check(
            "attitude: nadir vs velocity alignment differ on an eccentric orbit",
            lambda: _assert_attitude_eccentric(session))
        results.check(
            "orbital lifetime: 3U presets, mass scaling, no-decay case",
            lambda: _assert_lifetime(session))
        results.check(
            "propagators and drag: SSO plane drift, decay scales with area/mass",
            lambda: _assert_propagator_physics(session))
        results.check(
            "Orbit Wizard: SSO local time, GEO longitude, silent traps refused",
            lambda: _assert_orbit_wizard(session))
        results.check(
            "bent pipe and interference: both legs, real C/(N+I)",
            lambda: _assert_relay_and_rfi(session))
        results.check(
            "link budget: tracking antenna, real gain, every pass",
            lambda: _assert_link_budget(session))
        results.check(
            "read_data: any provider kind, events as rows",
            lambda: _assert_read_data(session))
        results.check(
            "area target from a built-in country outline",
            lambda: _assert_country_area_target(session))
        results.check(
            "output_dir() supports both os.path.join and pathlib's /",
            lambda: _assert_output_dir_join(session))
        results.check(
            "area target from a shapefile polygon",
            lambda: _assert_shapefile_area_target(session))
        results.check(
            "coverage grid confined to an area target (polygon, not a box)",
            lambda: _assert_coverage_regions(session, sat, roi, cov),
            requires={"satellite": sat, "area target": roi, "coverage": cov})
        # Use the area target, which the check above proved has access, so a
        # zero-pass result means the CSV writer is broken rather than the
        # geometry being unlucky.
        results.check("per-pass CSV lists every pass",
                      lambda: _assert_pass_list(session, sat, roi),
                      requires={"satellite": sat, "area target": roi})

        # REGRESSION: STK carries the last-used central body forward when
        # "New" does not name one. A scenario that created a lunar site and
        # then an Earth ground station put the ground station on the Moon,
        # while the script printed "Earth". Create one of each, in that
        # order, and check where they actually landed.
        results.check(
            "an Earth facility created after a lunar one stays on Earth",
            lambda: _assert_body_not_sticky(session))

        # Bodies beyond Earth and the Moon: radii read from STK rather than
        # hardcoded, so any of the 31 bodies works without a magic number.
        results.check("STK's central bodies are enumerable",
                      lambda: _assert_bodies(session))
        results.check("radii come from STK (Earth, Moon, Mars)",
                      lambda: _assert_radii(session))
        results.check("an object can be built on Mars",
                      lambda: _assert_mars(session))

        # --- STK reports and graphs ---------------------------------------
        results.check("report styles are discoverable",
                      lambda: _assert_styles(session),
                      requires={"satellite": sat})
        results.check("a wrong style name is rejected helpfully",
                      lambda: _assert_bad_style(session, sat),
                      requires={"satellite": sat})
        results.check("export an STK report to file",
                      lambda: _assert_export(session, sat),
                      requires={"satellite": sat})
        results.check("open an STK graph window",
                      lambda: session.open_graph(sat, "Classical Orbit Elements"),
                      requires={"satellite": sat})

        # --- ground -------------------------------------------------------
        gs = results.check(
            "create facility at geodetic position",
            lambda: session.new_facility("SmokeGS", 78.229772, 15.407786, 0.458))
        results.check("facility position round-trips",
                      lambda: _assert_position(session, gs, 78.229772, 15.407786),
                      requires={"facility": gs})
        # NOTE: no freq_ghz here. A receiver's frequency is read-only in
        # STK -- it is derived from the transmitter on the other end of the
        # link. Verified by discover_stk_api.py.
        results.check("add receiver",
                      lambda: session.add_receiver(gs, "SmokeRX"),
                      requires={"facility": gs})
        results.check("min elevation constraint",
                      lambda: session.set_min_elevation(gs, 5.0),
                      requires={"facility": gs})

        # --- analysis -----------------------------------------------------
        results.check("compute access satellite -> facility",
                      lambda: _assert_access(session, sat, gs),
                      requires={"satellite": sat, "facility": gs})
        rows = results.check(
            "access summary over several satellites",
            lambda: session.access_summary(walker or [sat], gs),
            requires={"satellites": walker or sat, "facility": gs})
        results.check("write access CSV",
                      lambda: _assert_csv(session, rows or [], "SmokeGS"),
                      requires={"access rows": rows})

        # --- optional / environment dependent -----------------------------
        print("\n  -- optional features (a FAIL here only limits scope) --")
        results.check("add planet object (Earth reference)",
                      lambda: session.new_planet("Earth"))
        results.check("satellite on an alternate central body (Moon)",
                      lambda: session.new_satellite(
                          "MoonSat", sma_km=stk.altitude_to_sma(100.0, stk.MOON_RADIUS_KM),
                          inc_deg=90.0, raan_deg=0.0, central_body="Moon"))

    finally:
        # Always clean up, even if a check blew up unexpectedly.
        try:
            session.close_scenario()
            print("\n  (smoke-test scenario closed without saving)")
        except Exception:
            print("\n  [warn] could not close the smoke-test scenario; "
                  "close '%s' manually." % SCENARIO_NAME)

    return 1 if results.summary() else 0


# ---------------------------------------------------------------------------
# Assertions -- each raises with a clear message on failure
# ---------------------------------------------------------------------------
def _assert_has_ephemeris(session, sat_path):
    """A satellite with no ephemeris silently returns nothing downstream."""
    provider = session.obj(sat_path).DataProviders.Item("Cartesian Position")
    group = provider.Group.Item("ICRF") if hasattr(provider, "Group") else provider
    result = group.Exec(START, STOP, 3600.0)
    count = result.DataSets.Count
    if count == 0:
        raise AssertionError("satellite has no ephemeris -- SetState did not propagate")
    return count


def _assert_radius(session, sat_path, expected_sma_km, tol_frac=0.05):
    """Catches a silent unit error: an orbit accepted but 1000x wrong."""
    radius = session.orbit_radius_km(sat_path)
    if radius is None:
        raise AssertionError("no ephemeris -- could not read a position")
    if abs(radius - expected_sma_km) > tol_frac * expected_sma_km:
        raise AssertionError(
            "orbit radius read back as %.1f km, expected ~%.1f km -- the "
            "distance unit is being misinterpreted" % (radius, expected_sma_km))
    return radius


def _assert_unique(session, base_path):
    if not session.exists(base_path):
        raise AssertionError("expected %s to exist by now" % base_path)
    candidate = session.unique_path(base_path)
    if candidate == base_path:
        raise AssertionError("unique_path returned an already-taken path")
    return candidate


def _assert_walker_math():
    rows = list(stk.walker_delta_elements(4, 4, 1))
    raans = [round(r[2], 6) for r in rows]
    if raans != [0.0, 90.0, 180.0, 270.0]:
        raise AssertionError("expected RAANs 0/90/180/270, got %s" % raans)
    anomalies = [round(r[3], 6) for r in rows]
    if anomalies != [0.0, 90.0, 180.0, 270.0]:
        raise AssertionError("expected f=1 phasing 0/90/180/270, got %s" % anomalies)
    return raans


def _assert_position(session, gs_path, lat, lon, tol=1e-4):
    position = session.obj(gs_path).Position
    got_lat, got_lon, _alt = position.QueryPlanetodetic()
    if abs(got_lat - lat) > tol or abs(got_lon - lon) > tol:
        raise AssertionError("facility position mismatch: set (%.6f, %.6f), "
                             "read back (%.6f, %.6f) -- check unit preferences"
                             % (lat, lon, got_lat, got_lon))
    return got_lat, got_lon


def _assert_access(session, sat_path, gs_path):
    count, total, _intervals = session.compute_access(sat_path, gs_path)
    if count == 0:
        raise AssertionError(
            "zero access intervals -- plausible for a short window, but check "
            "the orbit actually propagated and the ground station is placed")
    if total <= 0:
        raise AssertionError("access intervals have zero total duration")
    return count, total


def _assert_csv(session, rows, target_name):
    path = session.write_access_csv(rows, target_name, filename="smoke_access.csv")
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise AssertionError("CSV not written to %s" % path)
    return path


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)