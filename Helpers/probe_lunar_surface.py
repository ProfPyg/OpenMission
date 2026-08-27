"""
probe_lunar_surface.py -- can this STK put a site on the Moon?

A generated Blue Ghost script placed the Mare Crisium landing site at
18.56 N, 61.81 E on EARTH (the Arabian Sea), because central_body was never
passed. That was a documentation gap -- but whether a Facility can sit on the
Moon at all is untested, so test it rather than assume.

Tries Facility, Place and Target on the Moon, and a lunar orbiter, and reads
back what STK says the central body is.

Throwaway scenario. STK open, no scenario loaded.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

START, STOP = "1 Mar 2025 00:00:00.000", "2 Mar 2025 00:00:00.000"
LAT, LON = 18.56, 61.81          # Mare Crisium, near Mons Latreille


def central_body_of(session, path):
    """What does STK think this object orbits / sits on?"""
    for getter in ("CentralBodyName", "CentralBody"):
        try:
            value = getattr(session.obj(path), getter)
            return str(value)
        except Exception:
            continue
    try:
        result = session.root.ExecuteCommand("GetCentralBody %s" % path)
        return str(result.Item(0))
    except Exception:
        return "unknown"


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("LunarSurfaceProbe")
    session.set_time_period(START, STOP)

    print("\n1. SURFACE OBJECTS ON THE MOON")
    print("-" * 66)
    for class_name in ("Facility", "Place", "Target"):
        name = "Lunar%s" % class_name
        path = "*/%s/%s" % (class_name, name)
        created = session.cmd("New / */%s %s CentralBody Moon" % (class_name, name),
                              fatal=False)
        if not created:
            created = session.cmd("New / */%s %s" % (class_name, name), fatal=False)
            note = "created WITHOUT CentralBody keyword"
        else:
            note = "created with CentralBody Moon"
        if not created:
            print("  %-10s could not be created at all" % class_name)
            continue
        positioned = session.cmd("SetPosition %s Geodetic %.4f %.4f 0.0"
                                 % (path, LAT, LON), fatal=False)
        print("  %-10s %-34s position set: %-5s  body: %s"
              % (class_name, note, positioned, central_body_of(session, path)))

    print("\n2. THE HELPER'S OWN new_facility(central_body=...)")
    print("-" * 66)
    try:
        path = session.new_facility("HelperLunarSite", LAT, LON, 0.0, central_body="Moon")
        print("  %-28s body: %s" % (path, central_body_of(session, path)))
    except Exception as exc:
        print("  failed: %s" % exc)

    print("\n3. A LUNAR ORBITER (for comparison -- this one is known to work)")
    print("-" * 66)
    try:
        sat = session.new_satellite("LunarOrbiter",
                                    sma_km=stk.altitude_to_sma(100.0, stk.MOON_RADIUS_KM),
                                    inc_deg=90.0, raan_deg=0.0, central_body="Moon")
        print("  %-28s body: %s  radius: %.1f km"
              % (sat, central_body_of(session, sat), session.orbit_radius_km(sat) or -1))
        try:
            count, seconds, _ = session.compute_access(sat, "*/Facility/HelperLunarSite")
            print("  orbiter -> lunar site: %d passes, %.0f s" % (count, seconds))
        except Exception as exc:
            print("  access orbiter -> lunar site failed: %s" % str(exc).splitlines()[0])
    except Exception as exc:
        print("  failed: %s" % exc)

    session.close_scenario()
    print("\n(probe scenario closed without saving)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
