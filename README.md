---
title: OpenMission
emoji: 🛰️
colorFrom: red
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: gpl-3.0
---

# OpenMission

Preliminary Mission Analysis Tool for Sun-Synchronous Orbit (SSO) Earth
Observation Constellations.

OpenMission is a browser-based tool with three modules:

- **Coverage Analysis** — access and revisit metrics over a Region of Interest
  and ground stations, using a Skyfield/SGP4 propagator on the WGS84 ellipsoid.
- **Orbital Lifetime Estimator** — atmospheric-drag decay using NRLMSISE-00 with
  ECSS-E-ST-10-04C solar activity scenarios, with optional propulsive disposal.
- **Pass Prediction** — ground-station contact windows for a TLE-defined
  satellite, with TLE-age accuracy warnings.

Results are preliminary and should be verified against higher-fidelity tools
(STK, MATLAB Aerospace Toolbox, ESA DRAMA) before final design use.

Copyright © 2026 Marios Louvros. Licensed under GNU GPL v3.
[github.com/ProfPyg/OpenMission](https://github.com/ProfPyg/OpenMission)
