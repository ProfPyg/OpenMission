"""
diagnose_cross_body_access.py -- why does an Earth station see nothing?

A lunar orbiter reported ZERO access to Canberra over three days. That
contradicts geometry: the Moon is above Canberra's horizon for roughly half
of every day, so a lunar orbiter should be visible for long stretches,
interrupted only by Earth rotation and by passing behind the Moon.

Zero is the shape of answer this project has learned to distrust -- the
inverted sensors and the mis-parked GEO both reported plausible zeros. So
this measures the pieces separately:

  1. Is the Moon itself visible from Canberra? (a Planet object, no orbiter)
  2. Is the orbiter visible with its constraints REMOVED?
  3. Where is the orbiter, in Earth-centred terms? Is it near the Moon?
  4. Does access work the other way round (station -> orbiter)?

Throwaway scenario. STK open, no scenario loaded.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

START = "1 Mar 2026 00:00:00.000"
STOP = "4 Mar 2026 00:00:00.000"


def rule(title):
    print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("CrossBodyProbe")
    session.set_time_period(START, STOP)

    orbiter = session.new_satellite(
        "LunarOrbiter", sma_km=stk.altitude_to_sma(100.0, stk.MOON_RADIUS_KM),
        inc_deg=0.0, raan_deg=0.0, central_body="Moon")
    canberra = session.new_facility("Canberra", -35.40, 148.98, 0.6)
    moon = session.new_planet("Moon")

    rule("1. CAN CANBERRA SEE THE MOON AT ALL? (no constraints yet)")
    try:
        count, seconds, _ = session.compute_access(canberra, moon)
        print("  Canberra -> Moon (planet): %d intervals, %.0f s (%.1f h of 72)"
              % (count, seconds, seconds / 3600.0))
        print("  Expect roughly half the window if the geometry is sane.")
    except stk.StkError as exc:
        print("  failed: %s" % str(exc).splitlines()[0])

    rule("2. THE ORBITER, WITH NO CONSTRAINTS")
    for label, a, b in (("orbiter -> Canberra", orbiter, canberra),
                        ("Canberra -> orbiter", canberra, orbiter)):
        try:
            count, seconds, _ = session.compute_access(a, b)
            print("  %-22s %d intervals, %.0f s" % (label, count, seconds))
        except stk.StkError as exc:
            print("  %-22s failed: %s" % (label, str(exc).splitlines()[0]))

    rule("3. WITH A 10 DEG MASK ON CANBERRA (as the failing script had)")
    session.set_constraint(canberra, "ElevationAngle", minimum=10.0)
    for label, a, b in (("orbiter -> Canberra", orbiter, canberra),
                        ("Canberra -> Moon", canberra, moon)):
        try:
            count, seconds, _ = session.compute_access(a, b)
            print("  %-22s %d intervals, %.0f s" % (label, count, seconds))
        except stk.StkError as exc:
            print("  %-22s failed: %s" % (label, str(exc).splitlines()[0]))

    rule("4. WHERE IS THE ORBITER, IN EARTH-CENTRED TERMS?")
    for label, path in (("orbiter", orbiter), ("Moon (planet)", moon)):
        radius = session.orbit_radius_km(path)
        print("  %-16s distance from the scenario centre: %s km"
              % (label, ("%.0f" % radius) if radius else "unreadable"))
    print("  The orbiter should be ~384,400 km away in Earth-centred terms,")
    print("  i.e. out at the Moon -- not ~1,837 km, which would mean it is")
    print("  being reported in its OWN Moon-centred frame.")

    session.close_scenario()
    print("\n(probe scenario closed without saving)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
