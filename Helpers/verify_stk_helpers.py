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
    """The prompt must not document anything stk_helpers.py lacks."""
    import check_prompt_api
    problems = check_prompt_api.check(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "prompts", "stk_agent.md"),
        stk)
    if problems:
        raise AssertionError("; ".join(problems))
    return True


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
    """A wrong style must fail with a suggestion, not a bare STK error."""
    try:
        session.open_report(sat_path, "Classical Elements")
    except stk.StkError as exc:
        if "Did you mean" not in str(exc):
            raise AssertionError("rejected, but without a suggestion: %s" % exc)
        return True
    raise AssertionError("'Classical Elements' was accepted; it should not be")


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
        # Use the area target, which the check above proved has access, so a
        # zero-pass result means the CSV writer is broken rather than the
        # geometry being unlucky.
        results.check("per-pass CSV lists every pass",
                      lambda: _assert_pass_list(session, sat, roi),
                      requires={"satellite": sat, "area target": roi})

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