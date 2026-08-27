"""
probe_views.py -- find how this STK re-centres its 3D and 2D windows.

A lunar scenario still draws in a window titled "3D Graphics 1 - Earth":
the objects are right, the picture is centred on the wrong body. The Connect
syntax for changing that is not something to guess at -- earlier guesses at
SetState, targeted pointing and graph creation were all wrong -- so this
tries a spread of forms and reports which ones the build accepts.

Also lists the members of the application's window objects, in case the
answer is in the object model rather than Connect.

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


def members(dispatch):
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
    return sorted(names)


def main():
    session = stk.StkSession.attach()
    if session.scenario_name is not None:
        print("REFUSING TO RUN: close the open scenario first.")
        return 2
    session.ensure_scenario("ViewProbe")
    session.set_time_period("1 Mar 2025 00:00:00.000", "2 Mar 2025 00:00:00.000")
    # something lunar to look at
    session.new_satellite("LunarProbe",
                          sma_km=stk.altitude_to_sma(100.0, stk.MOON_RADIUS_KM),
                          inc_deg=90.0, raan_deg=0.0, central_body="Moon")

    rule("1. CONNECT FORMS FOR RE-CENTRING THE VIEWS ON %s" % BODY)
    candidates = [
        'VO * CentralBody %s',
        'VO * SetCentralBody %s',
        'VO */ CentralBody %s',
        'Window3D * CentralBody %s',
        '3DWindow * CentralBody %s',
        'VO * View CentralBody %s',
        'Map * CentralBody %s',
        'Map * SetCentralBody %s',
        'Window2D * CentralBody %s',
        'MapCreate * CentralBody %s',
        'New / */Window3D %s',
        'Window3D * New CentralBody %s',
        'VO * Window New CentralBody %s',
        'SetCentralBody * %s',
        'Application / CentralBody %s',
    ]
    accepted = []
    for template in candidates:
        command = template % BODY
        ok = session.cmd(command, fatal=False)
        print("  %-46s %s" % (command, "ACCEPTED" if ok else "rejected"))
        if ok:
            accepted.append(command)

    rule("2. OBJECT-MODEL ROUTE -- what does the application expose?")
    for label, getter in (("app", lambda: session.app),
                          ("app.Windows", lambda: session.app.Windows),
                          ("root", lambda: session.root),
                          ("scenario.Graphics", lambda: session.root.CurrentScenario.Graphics),
                          ("scenario.SceneManager",
                           lambda: session.root.CurrentScenario.SceneManager)):
        try:
            obj = getter()
        except Exception as exc:
            print("  %-22s not available (%s)" % (label, str(exc)[:40]))
            continue
        names = members(obj)
        interesting = [n for n in names
                       if any(k in n.lower() for k in
                              ("window", "view", "central", "body", "scene", "camera",
                               "render", "graphic"))]
        print("  %-22s %d members; relevant: %s"
              % (label, len(names), ", ".join(interesting[:14]) or "(none)"))

    rule("SUMMARY")
    if accepted:
        print("  Connect forms this build accepts:")
        for command in accepted:
            print("    %s" % command)
        print("\n  Check STK's window titles now -- 'accepted' does not always")
        print("  mean the view actually moved. Report which title changed.")
    else:
        print("  No Connect form was accepted; the object-model members above")
        print("  are the next place to look.")

    session.close_scenario()
    print("\n(probe scenario closed without saving)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
