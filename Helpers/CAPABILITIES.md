# STK agent capability register

What the helper module can do, what it cannot, and — importantly — the
difference between *"not wrapped"* and *"does not work"*.

Every "verified" line below was measured against a real STK 11 install
(`STK.Application`, STKHome `C:\Program Files\AGI\STK 11\`) by
`verify_stk_helpers.py` or one of the `probe_*.py` scripts. Nothing here is
assumed. Update this file whenever a probe settles something.

Status meanings:

| Status | Means |
|---|---|
| **verified** | wrapped in `stk_helpers.py` and covered by a smoke-test check |
| **works, unwrapped** | the Connect/OM command was accepted on this STK; no helper yet |
| **syntax unknown** | STK supports it, but the forms we tried were rejected — needs discovery |
| **untested** | nobody has tried |

---

## Verified

| Capability | Helper |
|---|---|
| Attach to a running STK across versions | `StkSession.attach` |
| Create / reuse scenario, set analysis period, units | `ensure_scenario`, `set_time_period`, `set_units` |
| Satellite from classical elements | `new_satellite` |
| Walker Delta constellations (T/P/F) | `new_walker` |
| Geostationary satellite parked over a longitude | `new_geo_satellite` |
| Satellite from a TLE, propagated by SGP4 | `new_satellite_from_tle` |
| HPOP with a central-body gravity file | `set_hpop_gravity` |
| Locate STK's own data files (e.g. `GL0420A.grv`) | `find_data_file`, `directory` |
| Conic and rectangular sensors | `add_sensor_conic`, `add_sensor_rectangular` |
| Nadir / az-el / targeted sensor pointing | `point_nadir`, `point_az_el`, `point_at_target` |
| Transmitters and receivers (simple models) | `add_transmitter`, `add_receiver` |
| Facilities, planets, minimum-elevation constraint | `new_facility`, `new_planet`, `set_min_elevation` |
| Access intervals, totals, next pass, AER peak | `compute_access`, `access_summary`, `next_pass`, `pass_details` |
| STK report and graph windows, exported reports | `open_report`, `open_graph`, `export_report`, `open_access_report` |
| Report style discovery and validation | `report_styles` |
| Runtime enum lookup from the type library | `enum`, `find_enum` |

### Independently cross-checked

Not just "it ran" — a second, unrelated computation agreed:

- Access totals vs **STK's own access reports**: 6 pairs, agreement to 0.001 s
  (`compare_access_to_stk.py`)
- Molniya visibility vs an **independent Kepler propagation**: 39.22 h vs
  39.22 h
- Sun-synchronous inclination, swath→half-angle, LTDN→RAAN, GEO longitude
  (0.000° error), sidereal time vs the J2000 anchor

---

## Works on this STK, not yet wrapped

Measured by `probe_capabilities.py`. A helper is a small job, not research.

| Capability | Command that was accepted |
|---|---|
| Range constraint | `SetConstraint <path> Range Max <km>` |
| Altitude constraint | `SetConstraint <path> Altitude Min <km>` |
| Sun-elevation constraint | `SetConstraint <path> SunElevationAngle Min <deg>` |
| Lighting constraint | `SetConstraint <path> Lighting DirectSun` |
| Eclipse / lighting / beta angle data | `export_report(sat, "Eclipse Times" \| "Lighting Times" \| "Beta Angle")` |
| Per-pass detail export | none needed — `compute_access` already returns every interval; only a CSV writer is missing |

---

## Syntax unknown — STK supports it, our guesses were wrong

`probe_capabilities.py` created each object successfully; every
*configuration* command tried was rejected. `probe_object_model.py`
interrogates the type library to find the real member names.

| Capability | What failed |
|---|---|
| Area targets (regions of interest) | `AreaTarget … AreaType Pattern`, `… Ellipse`, `SetAreaType …` |
| Coverage definitions | `Cover … Grid Bounds`, `… Grid Resolution`, `… Asset Add`, `… Access Compute` |
| Figure of merit (revisit, coverage %) | `FOM … Define RevisitTime`, `SetFOM …` |
| Chains (satellite → relay → ground) | `Chain … Add`, `ChainAdd`, `Chain … Compute` |
| Satellite attitude profiles | `SetAttitude … Profile …` (three spellings) |
| Ground-track lighting constraint | `SetConstraint … LightingSunElevAngle Min` |

---

## Known limitations

| Limitation | What was tried |
|---|---|
| **The 2D map cannot be re-centred on another central body.** A lunar scenario draws its 3D view on the Moon (`VO * CentralBody Moon`, confirmed by eye) but the 2D map stays on Earth. | 16 Connect spellings across `Map`, `MapWindow`, `Map2D`, `SetMapCentralBody`, with `*`, `*/`, `*/1`, `1` and quoted-body variants — all rejected (`probe_views.py`, `probe_views2.py`). No object-model route found: `app.Windows` items expose only geometry and docking; `SceneManager.Scenes` holds a single scene. **Hypothesis, untested:** the 2D map follows the SCENARIO's central body, fixed when the scenario is created, so building a lunar scenario inside an Earth scenario cannot move it. Testing that means creating a scenario on a non-Earth body. |
| **Extra graphics windows cannot be opened**, so a scenario spanning Earth and the Moon cannot show one view per body. | 9 Connect forms rejected. `app.Windows.Add` exists in the type library and is the obvious next place to look; its signature is unknown. |

## Untested

Aircraft, ground vehicles, ships, missiles, launch vehicles · Astrogator
manoeuvres · link budgets (C/N, Eb/No — transmitters carry EIRP but no link
is computed) · terrain and az-el masks · ephemeris import/export (`.e`) ·
generated TLEs · constellation grouping objects · Analysis Workbench vectors
and angles (needed for pointing-error work) · conjunction screening (CAT /
ACAT collision probability).

---

## Known traps

Things that produce **plausible wrong answers** rather than errors. Each cost
a debugging round; each now has a guard.

| Trap | Guard |
|---|---|
| Nadir is elevation **+90**, not −90. Sensors pointed at space for every scenario built before this was measured, and no test caught it because access was computed satellite-to-facility, which ignores pointing | `NADIR_ELEVATION_DEG`, plus a smoke check that a nadir sensor sees a facility placed directly beneath it |
| `SetState` wants the semi-major axis in **metres** and ignores the distance unit preference | orbit radius is read back and checked against the perigee/apogee band |
| A hand-placed GEO parks at an epoch-dependent longitude — effectively random | `new_geo_satellite` derives it from sidereal time; smoke test asserts ≤0.15° |
| GMST is referenced to the equinox **of date**, elements are J2000 — 0.34° of drift by 2026 (~250 km at GEO) | `precession_ra_deg` correction |
| A TLE can propagate only **~1.5 h from its epoch** rather than across the scenario; access later in the window then returns nothing, reading as "no passes" | `new_satellite_from_tle` checks there is ephemeris at the end of the window (trap documented by the [stk-mcp](https://github.com/zhang-forever/stk-mcp) project) |
| Targeted pointing silently failing left sensors unpointed, reporting zero access | `point_at_target` raises if every route fails |
| `write_access_csv` used one fixed filename, so per-station calls overwrote each other | default filename includes the target |
| Style names must be exact — "Classical Elements" is rejected, "Classical Orbit Elements" is right | `_check_style` suggests near matches |
| Streamlit cached the prompt, so prompt edits never reached the agent | prompt and helper reads are keyed on file mtime |

---

## Notes from other projects

[stk-mcp](https://github.com/zhang-forever/stk-mcp) drives STK from an LLM
over **Connect TCP on port 5001** rather than COM, which removes the pywin32
and Windows dependency and would allow driving STK across a network. It also
documents the ~1.5 h TLE propagation limit above. Its tool taxonomy (scenario,
objects, orbit, conjunction, analysis, util) is a reasonable checklist for
what a complete STK agent covers; conjunction screening is the notable
capability absent from our list entirely.
