"""
probe_views2.py -- the 2D map's central body, and creating extra windows.

Round one: 'VO * CentralBody Moon' moves the 3D view (confirmed by eye), but
every Map spelling was rejected and the 2D window stays on Earth.

Two questions here:
  1. What re-centres the 2D map?
  2. Can a second window be opened, so an Earth+Moon scenario can show both?

Unlike round one this dumps members UNFILTERED -- the keyword filter last
time hid whatever app.Windows actually offers.

Throwaway scenario. STK open, no scenario loaded.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stk_helpers as stk

BODY = "Moon"


def rule(title):
    print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


def all_members(dispatch):
    try:
        info = dispatch._oleobj_.GetTypeInfo()
        attr = info.GetTypeAttr()
    except Exception:
        return []
    names = set()
    try:
        for i in range(attr.cFuncs):
            names.add(info.GetNames(info.GetFuncDesc(i).memid)[0])
        for i in range(attr.cVars):
            names.add(info.GetNames(info.GetVarDesc(i).memid)[0])
    except Exception:
        pass
    return sorted(n for n in names
                  if n not in ("AddRef", "Release", "QueryInterface",
                               "GetTypeInfo", "GetTypeInfoCount", "Invoke",
                               "GetIDsOfNames"))


def dump(label, getter):
    try:
        obj = getter()
    except Exception as exc:
        print("  %-26s unavailable (%s)" % (label, str(exc)[:44]))
        return None
    names = all_members(obj)
    print("  %-26s %s" % (label, ", ".join(names) or "(no members)"))
    return obj


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("ViewProbe2")
    session.set_time_period("1 Mar 2025 00:00:00.000", "2 Mar 2025 00:00:00.000")
    session.new_satellite("LunarProbe",
                          sma_km=stk.altitude_to_sma(100.0, stk.MOON_RADIUS_KM),
                          inc_deg=90.0, raan_deg=0.0, central_body="Moon")
    session.new_satellite("EarthProbe", sma_km=stk.altitude_to_sma(550.0),
                          inc_deg=97.6, raan_deg=0.0)

    rule("1. MORE 2D SPELLINGS")
    for template in (
            'Map */1 CentralBody %s', 'Map 1 CentralBody %s',
            'Map * SetCB %s', 'Map * Body %s',
            'MapWindow * CentralBody %s', 'Map2D * CentralBody %s',
            'Map * CentralBody "%s"', 'Map */Map1 CentralBody %s',
            'SetMapCentralBody * %s', 'Map * Projection CentralBody %s',
            'Map * Load CentralBody %s', 'Map * CBody %s'):
        command = template % BODY
        print("  %-44s %s" % (command,
                              "ACCEPTED" if session.cmd(command, fatal=False) else "rejected"))

    rule("2. CREATING A SECOND WINDOW")
    for command in ('VO * Window Add', 'New / */VO', 'Window * New 3D',
                    'NewWindow / 3D', 'Application / NewWindow 3D',
                    'VO * New', 'Map * New', 'NewMap /', 'New / */Map'):
        print("  %-44s %s" % (command,
                              "ACCEPTED" if session.cmd(command, fatal=False) else "rejected"))

    rule("3. OBJECT MODEL, UNFILTERED")
    windows = dump("app.Windows", lambda: session.app.Windows)
    if windows is not None:
        try:
            print("  app.Windows count: %s" % windows.Count)
            for index in range(min(int(windows.Count), 4)):
                dump("  window[%d]" % index, lambda i=index: windows.Item(i))
        except Exception as exc:
            print("  could not enumerate windows: %s" % str(exc)[:60])

    manager = dump("scenario.SceneManager", lambda: session.root.CurrentScenario.SceneManager)
    if manager is not None:
        scenes = dump("  SceneManager.Scenes", lambda: manager.Scenes)
        if scenes is not None:
            try:
                print("  scene count: %s" % scenes.Count)
                scene = scenes.Item(0)
                dump("  scene[0]", lambda: scene)
            except Exception as exc:
                print("  could not enumerate scenes: %s" % str(exc)[:60])

    dump("root.CentralBodies", lambda: session.root.CentralBodies)

    session.close_scenario()
    print("\n(probe scenario closed without saving)")
    print("\nIf nothing here re-centres the 2D map or opens a window, this gets")
    print("recorded as a limitation rather than chased further.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
