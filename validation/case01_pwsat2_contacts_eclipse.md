# Validation case 01 — LEO CubeSat: ground-station contacts and eclipses

**Status:** PASS · **Date:** 2026-10-05 · **PRONOIA model:** claude-sonnet-5-5 · **STK:** 11.2

## Ground truth

PW-Sat2 Mission Analysis Report, Critical Design Review (Warsaw University of Technology, 2016),
analysis done in **STK 10**.
Source: https://pw-sat.pl/wp-content/uploads/2014/07/PW-Sat2-C-00.01-MA-CDR.pdf
(orbit: §2.3 and Table 5-2; ground station: Table 2-1, §4.3.1; scenario: Table 4-2; results: Tables 5-3 and 5-4)

| Input | Value in the report |
|---|---|
| Orbit | circular SSO, a = 6953.14 km (575 km), e = 0, i = 97.6177° |
| RAAN / LTAN | 299.227° / 10:30 |
| Propagator | J4 |
| Analysis period | 6 Dec 2017 00:00:00.001 → 16 Jan 2018 11:00:00 UTCG |
| Ground station | Warsaw, 52.2188° N, 21.0107° E, 114 m + 20 m antenna, min elevation 30° |

## Prompts used (blind: no mission name, no expected values)

**Run A** — inputs exactly as stated (RAAN 299.227°):

> A CubeSat in a circular sun-synchronous orbit: semi-major axis 6953.14 km, eccentricity 0,
> inclination 97.6177°, RAAN 299.227° at epoch 6 Dec 2017 00:00:00 UTCG. Use a J4 propagator.
> Analysis period 6 Dec 2017 00:00 to 16 Jan 2018 11:00 UTCG. One ground station at 52.2188° N,
> 21.0107° E, 134 m altitude, with a minimum elevation of 30°. Report the contact time per pass
> (max, min, mean), contact time per day (max, min), the longest gap between contacts, and the
> eclipse duration per orbit (max, min, mean).

**Run B** — same mission, ascending node placed at the stated 10:30 local solar time:

> Same mission, but instead of RAAN 299.227°, place the orbit's ascending node at 10:30 local
> solar time on 6 Dec 2017.

PRONOIA solved RAAN = 229.712° (STK node local time = 10.5000 h, true Sun, at epoch).
Argument of perigee and true anomaly were not given in the report; PRONOIA used 0°.

## Results

| Result | Report (STK 10) | PRONOIA run A | PRONOIA run B | Agreement (B) |
|---|---|---|---|---|
| Contact per pass, max | 4.00 min | 4.00 min | 4.00 min (240.2 s) | identical |
| Contact per pass, mean | 3.11 min | 3.08 min | 3.09 min (185.5 s) | 0.6 % |
| Contact per pass, min | 0.72 min | 0.30 min | 0.68 min (40.6 s) | 2 s |
| Contact per day, max | 7.97 min | 7.94 min | 7.98 min (478.5 s) | 0.1 % |
| Contact per day, min (full days) | 4.44 min | 4.77 min | 4.44 min (266.3 s) | identical |
| Longest gap | 883 min | 883.2 min | 883.2 min (14.72 h) | identical |
| Eclipse per orbit, max | 34.77 min | 30.47 min | 34.59 min | 11 s |
| Eclipse per orbit, min | 34.28 min | 28.99 min | 34.13 min | 9 s |
| Eclipse per orbit, mean | 34.57 min | 29.85 min | 34.40 min | 10 s |

Passes: 89 in both runs. Eclipses: 620 (umbra, STK Lighting Times).

## Findings

1. **PRONOIA reproduces the published STK analysis.** With the orbit as the report describes it
   (10:30 LTAN), every statistic agrees within 0.6 % or a few seconds; four are identical.
2. **PRONOIA exposed an inconsistency in the published report.** The report's RAAN (299.227°) does
   not correspond to its stated 10:30 LTAN on 6 Dec 2017 (the Sun's right ascension is ≈ 251°, so
   299° places the node near 15:10 local time). Run A, using the RAAN literally, gives eclipses
   4.7 min shorter than the report; run B, using the 10:30 LTAN, matches. The report's eclipse
   results were therefore computed for the 10:30 orbit, and the listed RAAN is most likely quoted
   at a different epoch. PRONOIA's results made the inconsistency visible and its own RAAN solution
   resolved it.
3. **Remaining differences are explained:**
   - Eclipse ≈ 10 s shorter: PRONOIA reports umbra only; the report likely includes part of the
     penumbra. STK version also differs (11.2 vs 10).
   - Min pass duration: depends on the starting position in orbit (argument of perigee / true
     anomaly), which the report does not give; grazing passes at the 30° mask are very sensitive
     to it.

## Reproduce

Mission: `missions/20261005_171327_ValidationTest` (run A, then run B as a follow-up in the same chat). Prompts above, verbatim.
