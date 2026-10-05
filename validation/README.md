# PRONOIA validation against published analyses

Each case runs PRONOIA **blind** (mission described only by its parameters; no mission name, no
expected values) and compares its STK results with a published analysis.

| # | Case | Source | Tests | Result |
|---|---|---|---|---|
| 01 | LEO CubeSat contacts and eclipses | PW-Sat2 Mission Analysis Report (STK 10) | access, daily contact, gaps, eclipses | PASS — within 0.6 % / seconds; found an input inconsistency in the source |

Planned: orbital lifetime vs altitude (drag), Walker constellation coverage, transfer ΔV.

- [Case 02 — Lunar frozen orbit](case02_lunar_frozen_orbit.md): PASS on behaviour, OPEN on magnitude
