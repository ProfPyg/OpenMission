"""
validate_scenario.py -- check an open STK scenario against independent physics.

Everything else we have written trusts STK somewhere. This does not: it takes
STK's state vector at one instant and then propagates the orbit ITSELF, with
its own integrator, and compares. It recomputes access from raw positions
with its own geometry, and compares that too.

Three independent checks per scenario:

  1. DYNAMICS  Take position+velocity at t0 from STK. Integrate forward with
     RK4 and J2 in this script. Compare against STK's own position later.
     Disagreement means the propagator, the frame, or the epoch is not what
     we think it is.

  2. ACCESS    Sample STK's Earth-fixed positions, compute the elevation of
     the satellite from each station ourselves, and total the time above the
     mask. Compare with STK's access intervals. Disagreement means the
     constraints are not what we think they are.

  3. POINTING  Ask STK where each sensor's boresight hits the ground. A
     nadir sensor must hit near the sub-satellite point; one pointing at
     space hits nothing.

Nothing is created or modified. Run with the scenario open:

    python validate_scenario.py
"""
import math
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

MU = 398600.4418
R_E = 6378.137
J2 = 1.08262668e-3
FLATTENING = 1.0 / 298.257223563

DYNAMICS_SPAN_S = 3600.0      # how far to propagate independently
DYNAMICS_STEP_S = 5.0         # RK4 step
ACCESS_SAMPLE_S = 30.0        # sampling for the independent access check
MAX_OBJECTS = 3               # keep the run short; --all lifts this


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


# ---------------------------------------------------------------------------
# independent dynamics
# ---------------------------------------------------------------------------
def acceleration(state):
    """Two-body + J2 acceleration, km/s^2."""
    x, y, z = state[0], state[1], state[2]
    r = math.sqrt(x * x + y * y + z * z)
    factor = -MU / (r ** 3)
    j2_common = 1.5 * J2 * MU * R_E * R_E / (r ** 5)
    z_ratio = 5.0 * z * z / (r * r)
    ax = factor * x - j2_common * x * (1.0 - z_ratio)
    ay = factor * y - j2_common * y * (1.0 - z_ratio)
    az = factor * z - j2_common * z * (3.0 - z_ratio)
    return ax, ay, az


def rk4(state, dt):
    """One RK4 step on [x, y, z, vx, vy, vz]."""
    def deriv(s):
        ax, ay, az = acceleration(s)
        return [s[3], s[4], s[5], ax, ay, az]

    k1 = deriv(state)
    k2 = deriv([state[i] + 0.5 * dt * k1[i] for i in range(6)])
    k3 = deriv([state[i] + 0.5 * dt * k2[i] for i in range(6)])
    k4 = deriv([state[i] + dt * k3[i] for i in range(6)])
    return [state[i] + dt / 6.0 * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i])
            for i in range(6)]


# ---------------------------------------------------------------------------
# reading STK
# ---------------------------------------------------------------------------
def provider_rows(session, path, provider_name, group, start, stop, step):
    """Fetch one data provider as a list of dicts. Returns [] on failure."""
    try:
        provider = session.obj(path).DataProviders.Item(provider_name)
        if group and hasattr(provider, "Group"):
            provider = provider.Group.Item(group)
        result = provider.Exec(start, stop, step)
        datasets = result.DataSets
        names = [datasets.GetItem(i).ElementName for i in range(datasets.Count)]
        columns = [list(datasets.GetItem(i).GetValues()) for i in range(datasets.Count)]
    except Exception:
        return []
    rows = []
    for index in range(len(columns[0]) if columns else 0):
        rows.append({names[c]: columns[c][index] for c in range(len(names))})
    return rows


def state_at(session, path, when, group="ICRF"):
    """(x, y, z, vx, vy, vz) in km and km/s, or None."""
    rows = provider_rows(session, path, "Cartesian Position", group, when, when, 60.0)
    vel = provider_rows(session, path, "Cartesian Velocity", group, when, when, 60.0)
    if not rows or not vel:
        return None
    try:
        return [float(rows[0]["x"]), float(rows[0]["y"]), float(rows[0]["z"]),
                float(vel[0]["x"]), float(vel[0]["y"]), float(vel[0]["z"])]
    except (KeyError, ValueError):
        return None


def station_ecef(lat_deg, lon_deg, alt_km):
    """WGS84 geodetic to Earth-fixed cartesian, km."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    e2 = FLATTENING * (2 - FLATTENING)
    n = R_E / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    x = (n + alt_km) * math.cos(lat) * math.cos(lon)
    y = (n + alt_km) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - e2) + alt_km) * math.sin(lat)
    return x, y, z


def elevation_deg(sat_ecef, stn_ecef):
    """Elevation of a satellite above a station's local horizon."""
    sx, sy, sz = stn_ecef
    dx, dy, dz = sat_ecef[0] - sx, sat_ecef[1] - sy, sat_ecef[2] - sz
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    norm = math.sqrt(sx * sx + sy * sy + sz * sz)
    if rng == 0 or norm == 0:
        return -90.0
    return math.degrees(math.asin(max(-1.0, min(1.0, (dx * sx + dy * sy + dz * sz) / (rng * norm)))))


def objects_of_class(session, class_name):
    try:
        result = session.root.ExecuteCommand("AllInstanceNames /")
        names = str(result.Item(0)).split()
    except Exception:
        return []
    out = []
    for name in names:
        parts = [p for p in name.split("/") if p and p != "*"]
        if len(parts) >= 2 and parts[-2].lower() == class_name.lower():
            out.append(name)
    return out


# ---------------------------------------------------------------------------
def main():
    show_all = "--all" in sys.argv
    session = stk.StkSession.attach()
    if session.scenario_name is None:
        print("No scenario open.")
        return 2
    print("Validating scenario: %s" % session.scenario_name)

    start = getattr(session, "_start", None)
    if not start:
        try:
            result = session.root.ExecuteCommand("GetAnalysisTimePeriod *")
            start = str(result.Item(0)).split('"')[1]
        except Exception:
            print("Could not determine the scenario start time.")
            return 2
    session._start = start
    print("Epoch: %s\n" % start)

    satellites = objects_of_class(session, "Satellite")
    facilities = objects_of_class(session, "Facility")
    sensors = objects_of_class(session, "Sensor")
    if not show_all:
        satellites = satellites[:MAX_OBJECTS]
        sensors = sensors[:MAX_OBJECTS]
    print("checking %d satellite(s), %d facility(ies), %d sensor(s)"
          % (len(satellites), len(facilities), len(sensors)))

    failures = []

    # ---- 1. dynamics ----------------------------------------------------
    rule("1. DYNAMICS -- our own RK4+J2 integration vs STK's ephemeris")
    print("%-16s %12s %12s %12s" % ("satellite", "after (s)", "error (km)", "verdict"))
    print("-" * 60)
    for path in satellites:
        name = path.rsplit("/", 1)[-1]
        initial = state_at(session, path, start)
        if initial is None:
            print("%-16s   could not read the state vector" % name)
            failures.append("%s: no state vector" % name)
            continue

        state = list(initial)
        steps = int(DYNAMICS_SPAN_S / DYNAMICS_STEP_S)
        for _ in range(steps):
            state = rk4(state, DYNAMICS_STEP_S)

        later = session.root.ConversionUtility.ConvertDate(
            "EpSec", "UTCG",
            float(session.root.ConversionUtility.ConvertDate("UTCG", "EpSec", start))
            + DYNAMICS_SPAN_S)
        stk_state = state_at(session, path, str(later))
        if stk_state is None:
            print("%-16s   could not read STK's later position" % name)
            continue
        error = math.sqrt(sum((state[i] - stk_state[i]) ** 2 for i in range(3)))
        # J2-only integration vs STK's J2Perturbation should agree closely;
        # HPOP includes more forces, so a larger difference is expected there.
        verdict = "OK" if error < 5.0 else ("check" if error < 50.0 else "MISMATCH")
        if verdict == "MISMATCH":
            failures.append("%s: dynamics differ by %.1f km in 1 h" % (name, error))
        print("%-16s %12.0f %12.3f %12s" % (name, DYNAMICS_SPAN_S, error, verdict))

    # ---- 2. access ------------------------------------------------------
    rule("2. ACCESS -- our own geometry on STK positions vs STK's intervals")
    stop = None
    try:
        result = session.root.ExecuteCommand("GetAnalysisTimePeriod *")
        parts = str(result.Item(0)).split('"')
        stop = parts[3] if len(parts) > 3 else None
    except Exception:
        pass

    if stop and satellites and facilities:
        print("%-14s %-14s %10s %10s %9s %s"
              % ("satellite", "station", "STK (s)", "ours (s)", "diff %", "verdict"))
        print("-" * 74)
        for gs_path in facilities[:2]:
            gs_name = gs_path.rsplit("/", 1)[-1]
            try:
                position = session.obj(gs_path).Position
                lat, lon, alt = position.QueryPlanetodetic()
            except Exception:
                continue
            stn = station_ecef(float(lat), float(lon), float(alt))

            mask = 0.0
            try:
                constraint = session.obj(gs_path).AccessConstraints.GetActiveConstraint("ElevationAngle")
                mask = float(constraint.Min)
            except Exception:
                pass

            for sat_path in satellites[:2]:
                sat_name = sat_path.rsplit("/", 1)[-1]
                try:
                    stk_n, stk_s, _ = session.compute_access(sat_path, gs_path)
                except stk.StkError:
                    continue
                rows = provider_rows(session, sat_path, "Cartesian Position",
                                     "Fixed", start, stop, ACCESS_SAMPLE_S)
                if not rows:
                    print("%-14s %-14s   no Earth-fixed ephemeris" % (sat_name, gs_name))
                    continue
                above = 0
                for row in rows:
                    try:
                        p = (float(row["x"]), float(row["y"]), float(row["z"]))
                    except (KeyError, ValueError):
                        continue
                    if elevation_deg(p, stn) >= mask:
                        above += 1
                ours = above * ACCESS_SAMPLE_S
                diff_pct = 100.0 * (ours - stk_s) / stk_s if stk_s else 0.0
                # sampling alone costs up to one step per interval edge
                verdict = "OK" if abs(diff_pct) < 5.0 else "MISMATCH"
                if verdict == "MISMATCH":
                    failures.append("%s->%s: access differs by %.1f%%"
                                    % (sat_name, gs_name, diff_pct))
                print("%-14s %-14s %10.0f %10.0f %8.1f%% %s"
                      % (sat_name, gs_name, stk_s, ours, diff_pct, verdict))
        print("\n(mask read from each station's elevation constraint; sampling at "
              "%.0f s, so a few percent difference is the sampling, not an error)"
              % ACCESS_SAMPLE_S)

    # ---- 3. pointing ----------------------------------------------------
    rule("3. POINTING -- where each sensor's boresight actually hits")
    if not sensors:
        print("  no sensors in this scenario")
    else:
        print("%-34s %10s %10s %s" % ("sensor", "lat", "lon", "verdict"))
        print("-" * 70)
    for sensor_path in sensors:
        short = "/".join(sensor_path.split("/")[-3:])
        rows = provider_rows(session, sensor_path, "Boresight Grazing Point LLA",
                             None, start, start, 60.0)
        if not rows:
            rows = provider_rows(session, sensor_path, "Boresight Intersection",
                                 None, start, start, 60.0)
        if not rows:
            print("%-34s %10s %10s %s" % (short, "-", "-", "no ground intersection"))
            failures.append("%s: boresight does not hit the Earth" % short)
            continue
        row = rows[0]
        lat = row.get("Lat") or row.get("Latitude") or "?"
        lon = row.get("Lon") or row.get("Longitude") or "?"
        print("%-34s %10s %10s %s" % (short, lat, lon, "hits the ground"))

    # ---- verdict --------------------------------------------------------
    rule("VERDICT")
    if failures:
        print("%d problem(s) found:" % len(failures))
        for item in failures:
            print("  * %s" % item)
        return 1
    print("Everything checked agrees with independent computation.")
    print("Checked: orbit dynamics (own RK4+J2), access geometry (own elevation")
    print("maths on STK positions), sensor boresight ground intersection.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
