"""
check_view.py -- does re-centring the view actually move the camera?

probe_views.py found that STK ACCEPTS 'VO * CentralBody Moon'. Accepted is
not the same as worked: a SetState form was once accepted and produced no
ephemeris at all. This applies the change and LEAVES THE SCENARIO OPEN so the
window titles can be read.

    python check_view.py            # Moon
    python check_view.py Mars
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

body = sys.argv[1] if len(sys.argv) > 1 else "Moon"

session = stk.StkSession.attach()
if session.scenario_name is None:
    session.ensure_scenario("ViewCheck")
    session.set_time_period("1 Mar 2025 00:00:00.000", "2 Mar 2025 00:00:00.000")
    session.new_satellite("LunarProbe",
                          sma_km=stk.altitude_to_sma(100.0, stk.MOON_RADIUS_KM),
                          inc_deg=90.0, raan_deg=0.0, central_body=body)
    print("created scenario 'ViewCheck' with one %s orbiter" % body)
else:
    print("using the open scenario: %s" % session.scenario_name)

print("\nobjects by central body: %s" % (session.central_bodies_in_use() or "unknown"))
result = session.set_view_central_body(body)
print("\nforms used: 3D=%s  2D=%s"
      % (getattr(session, "_view_form_3d", None), getattr(session, "_view_form_2d", None)))
print("""
NOW LOOK AT STK -- the scenario is deliberately left open:

  * Does the 3D window title say "3D Graphics 1 - %s"?
  * Does the 2D window title say "2D Graphics 1 - %s"?
  * Is the body drawn actually %s, or still Earth?

Report which of the two changed. If the title changed but the drawing did
not, or vice versa, that matters -- say which.
""" % (body, body, body))
