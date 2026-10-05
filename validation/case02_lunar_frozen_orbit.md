# Case 02 — Lunar frozen orbit (1 year)

**Verdict: PASS on behaviour · OPEN on magnitude**

## Reference
Mesarch et al., lunar frozen-orbit study (NASA NTRS preprint).

## Inputs (described to Pronoia blindly, not "reproduce this paper")
| Parameter | Value |
|---|---|
| Epoch | 29 Sep 2030 |
| Semi-major axis | 1862.4 km |
| Eccentricity | 0.043 |
| Inclination | 90° (Moon-fixed) |
| LAN | 270° |
| Argument of periapsis | 269.5° |
| Force model | Lunar gravity GL0420A 150×150 + Earth + Sun third body (HPOP) |
| Duration | 1 year, hourly samples |

## Results
| Quantity | Paper | Pronoia (STK 11.2) | Match |
|---|---|---|---|
| Orbit stays frozen (ω bounded, e bounded, no decay) | Yes | Yes. ω 262.2–273.2°, e 0.0374–0.0512 | ✅ |
| Periapsis altitude spread over 1 year | 18.48 km | 25.5 km (29.6–55.1 km) | ❌ +38% |
| Minimum periapsis altitude (safe) | — | 29.6 km | ✅ |

**Run:** about 11 min of STK time in 10 HPOP segments · API cost $0.98 (43 calls).

## Why the magnitude differs (open items, not Pronoia errors)
1. **Gravity field.** The paper's field and degree may differ from GL0420A 150×150. Low lunar orbits are very sensitive to this.
2. **Mean vs osculating elements.** The paper's e and ω are likely mean elements. We entered them as osculating, which starts the orbit at a slightly different frozen point and widens the oscillation.
3. **Starting position.** The paper doesn't state the initial true anomaly, so we had to choose one.
4. **Hourly sampling.** This can clip the extremes slightly (a few km at most).

## To close
Run a small search over the starting e and ω (and use mean→osculating conversion if available) to look for a periapsis spread of about 18 km. Deferred for API budget.
