# GTA VI Twitch CCU forecast — `main.py`

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/bardah26/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/bardah26/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts the hourly Twitch viewers of Grand Theft Auto VI on its
release day (2026-11-19 UTC), hours 2–24, as five quantiles per hour,
from everything recorded up to the end of hour 1. This is an entry for
the [make.fun bounty](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
"Forecast GTA VI's launch-day Twitch viewers hour by hour".

## Method

1. **Anchor on the hour-1 print.** The forecast scales the hour-1
   viewer count (`known.viewers`), floored at 1, with a fallback to the
   last pre-launch hourly average if the print is missing.
2. **Ratio curves from past launches.** Each comparable launch is turned
   into a day-relative ratio curve: viewers at day hour *h* divided by
   viewers at day hour 1. The forecast hours are hours of the UTC launch
   day, so day-relative is the consistent frame (the comparables'
   `unlock` field is prose about a rolling midnight rollout, not a
   single instant).
3. **Regime filter.** A comparable whose hour-1 print is under 3% of
   its own launch-day peak was still pre-launch at hour 1 (a single
   global unlock later in the day, e.g. Silksong at 14:00Z, whose ratios
   run to 2000x). Those describe a different launch shape from the
   rolling-midnight target and are left out; the launches kept have
   hour-1 prints from about 8% to 100% of their peak. A comparable from
   the target's own launch day is also skipped, so a practice case never
   predicts itself.
4. **Quantiles in log space.** For each hour, the 5/25/50/75/95
   percentiles of the log-ratios across the kept launches, added to the
   log of the anchor, become the five quantiles. This is the honest
   predictive distribution: the median follows the launch shape (dip in
   the early morning, peak in the US evening) and the spread reflects
   how much launches actually differ.
5. **Anchor-size adjustment.** Across the kept launches, log steepness
   (day peak / hour-1 print) falls as log hour-1 print rises
   (correlation ≈ −0.77): a huge first hour means the launch is already
   at scale and grows little. A log-log line fitted to the kept launches
   predicts the target's steepness and scales the median curve's growth
   above the anchor (clamped); the early-morning dip and the spread
   around the median are left alone.
6. **Light smoothing** across neighbouring hours, then quantiles are
   rounded to whole viewers, clamped to [1, 1e9] and forced
   non-decreasing per hour. If too few comparables cover an hour, a
   sensible default launch shape is used instead.

The GTA VI category's own pre-release history (a comparable in every
case) has no hour-1 point on its premiere day, so it contributes no
ratio curve; its recent level (~2,000 viewers) is far below any launch
print and needs no separate handling.

Everything is deterministic: no randomness, no clock, no network.
Python standard library only.

## Run

```sh
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Without the variables it reads `/input/` and writes `/output/`. It
processes every `*.in` file found (sorted by name) and writes one
`*.out` file per case. Runs in ~0.2 s for the four practice cases.

## Local validation

- **Deterministic:** two runs on all four practice cases produce
  byte-identical outputs.
- **Valid:** every output has exactly 23 rows (hours 2–24), five
  quantiles each, all in (0, 1e9], non-decreasing, files ≈1.2 kB
  (limit 65,536).
- **Leave-one-out over the 38 comparables** (each launch forecast from
  the other 37, scored against its real hours 2–24): pinball loss
  **0.280** on the 29 rolling-midnight-like launches vs **0.360** for
  the house baseline (typical miss 60% vs 83%, 90% coverage 0.885).
  The 8 single-global-unlock launches score badly for every
  anchor-based method (2.47 vs house 3.00) and are not the target's
  regime.

Entered on [make.fun](https://make.fun/bounty/gta6-twitch-ccu-forecast-01).
