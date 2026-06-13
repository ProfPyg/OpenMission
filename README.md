---
title: OpenMission
emoji: 🛰️
colorFrom: red
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: gpl-3.0
tags:
  - streamlit
  - orbital-mechanics
  - satellite
  - earth-observation
  - mission-analysis
  - sso
---

# OpenMission

**Preliminary Mission Analysis Tool for Sun-Synchronous Orbit (SSO) Earth Observation Constellations.**

🚀 **[Launch the live app »](https://louvros-openmission.hf.space)**
📘 **[User Guide (PDF)](https://github.com/ProfPyg/OpenMission/blob/main/OpenMission_User_Guide.pdf)**

OpenMission is a browser-based tool for early-stage mission analysis of SSO
constellations for Earth Observation. It brings coverage analysis, orbital
lifetime estimation, and pass prediction into a single interface, with results
validated against industry-standard tools (STK, MATLAB Aerospace Toolbox, and
ESA DRAMA).

---

## Modules

### 🌍 Coverage Analysis
Computes access and revisit metrics over a user-defined Region of Interest and
for one or more ground stations, using a Skyfield/SGP4 propagator on the WGS84
ellipsoid. Supports Walker-style constellations, Sun-synchronous orbit
auto-inclination, live-linked LTAN/RAAN, repeat-ground-track analysis, and
custom shapefile or built-in country boundaries.

### ⏳ Orbital Lifetime Estimator
Estimates time-to-decay under atmospheric drag using the NRLMSISE-00 model with
ECSS-E-ST-10-04C solar activity scenarios (Min / Mean / Max). Supports natural
decay, two-burn Hohmann circularization, and continuous-thrust disposal, with a
5-year post-mission compliance check against ESA, FCC, and IADC requirements.

### 📡 Pass Prediction
Predicts ground-station contact windows for a TLE-defined satellite using
Skyfield/SGP4, with rise/set times, peak elevation, azimuth, pass quality, and
TLE-age accuracy warnings.

---

## Validation

| Module | Validated against | Agreement |
|---|---|---|
| Coverage / revisit | MATLAB Aerospace Toolbox, STK | within time-step quantisation |
| Orbital lifetime | ESA DRAMA OSCAR | within ~10% at 350-450 km |
| Pass prediction | Heavens-Above | mean timing difference ~6 s |

Full methodology, equations, and validation tables are in the
[Technical Documentation / User Guide](https://github.com/ProfPyg/OpenMission/blob/main/OpenMission_User_Guide.pdf).

---

## Tech stack

Python - Streamlit - Skyfield / SGP4 - NRLMSISE-00 - NumPy / SciPy - Shapely -
Plotly. Deployed via Docker on Hugging Face Spaces.

## Running locally

```bash
git clone https://github.com/ProfPyg/OpenMission.git
cd OpenMission
pip install -r requirements.txt
streamlit run app.py
```

## Limitations

OpenMission is intended for early trade-space exploration. It uses an equatorial
atmosphere approximation, a circular-orbit assumption, point-target ROI
sampling, and a 500 km altitude ceiling for the lifetime model. Results should
be verified against higher-fidelity tools before final design use. See the User
Guide for the complete limitations list.

## License

Copyright (c) 2026 Marios Louvros. Licensed under the
[GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.en.html). Any use,
modification, or distribution must retain the original copyright notice and
attribution.

## Citation

> Louvros, M. (2026). *OpenMission: Preliminary Mission Analysis Tool for SSO
> Earth Observation Constellations.* https://github.com/ProfPyg/OpenMission
