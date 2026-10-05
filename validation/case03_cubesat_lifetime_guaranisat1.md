# Case 03 — CubeSat orbital lifetime (GuaraníSat-1, real decay)

**Verdict: PASS (−7.8%) with real observed solar flux.** First run with STK's 2017 forecast flux: +21% (explained below).

## Ground truth (measured, not another tool)
- Satellite: GuaraníSat-1, 1U, 1.3 kg, released from the ISS (NORAD 47931, catalogued as 1998-067SH because ISS-deployed objects use the ISS designator).
- First TLE after release (space-track GP history), epoch 19 Mar 2021 08:08 UTC:
  ```
  1 47931U 98067SH  21078.33908831  .00007425  00000-0  13914-3 0  9996
  2 47931  51.6436  67.0913 0003333  57.9566 302.1747 15.49750797   791
  ```
- Decay (space-track SATCAT): **5 Jul 2022 → 473 days from TLE epoch**.

## Prompt (blind; satellite not named)
1U, 10×10×10 cm, 1.3 kg, tumbling, TLE above; predict lifetime with realistic drag and the actual solar activity; lifetime tool only.

## Result
| Case | Re-entry | Lifetime | vs real 473 d |
|---|---|---|---|
| Nominal (Jacchia 70, 0σ flux) | 12 Oct 2022 | 571.8 d | +21% |
| High activity (+2σ) | 9 Aug 2022 | 508.4 d | +7.5% |
| Low activity (−2σ) | 6 Jan 2023 | 658.5 d | +39% |
| Density models (nominal flux) | — | 537–646 d (Jacchia-Roberts best, +13.5%) | — |

Assumptions (sensible): area 0.015 m² (tumbling, surface/4), Cd 2.2, A/m 0.0115 m²/kg, start ~421 km.
API cost: low (single lifetime tool runs).

## Why it overpredicts
- **Solar flux.** STK 11.2 has no 2021–22 space-weather data, so it used predicted flux. Solar Cycle 25 rose much faster than forecast in 2021–22, so the air was denser and the satellite came down sooner. All model variations point the same way, and the +2σ (high activity) run lands closest.
- Pronoia **reported this limitation itself** ("not based on actual solar activity") — correct, honest behaviour.

## Good behaviour observed
- Used the cheap lifetime tool, not HPOP.
- Reported sensitivity to flux and density model instead of a single number.
- Flagged the missing-data limitation unprompted.

## Re-run with real solar flux → PASS
Built `SolFlx_CSSI_real.dat` (in this folder): STK's CSSI format, 2021+ months replaced by monthly means of observed F10.7, 81-day centred F10.7 and Ap (CelesTrak SW CSV). Real flux in early 2022 was 20–30% above STK's 2017 forecast (e.g. Apr 2022: 131 vs 100).

| Case | Re-entry | Lifetime | vs real 473 d |
|---|---|---|---|
| Nominal (Jacchia 70, real flux) | 29 May 2022 | 436 d | **−7.8% ✅** |
| Range over 5 density models | 10 May – 13 Aug 2022 | ~417–512 d | real value inside the range ✅ |

The only change between the runs was the flux file, and the result moved 135 days. So STK did use the new file.

## Conclusion
Pronoia set the problem up correctly (geometry, A/m, Cd, TLE, cheap lifetime tool). The remaining error is set by the solar-flux input, which is the dominant uncertainty in any lifetime prediction.

## Product action
Pronoia should ship/auto-update the CelesTrak space-weather data (converted to STK's flux format) so lifetime runs always use real past flux and the latest forecast.
