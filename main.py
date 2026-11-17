#!/usr/bin/env python3
"""Forecast hourly Twitch viewers for a game launch day.

Reads every ``.in`` case file from the input directory and writes one
``.out`` forecast file per case to the output directory.

Method
------
The forecast is anchored on the hour-1 viewer print (``known.viewers``).
Past launches (``comparables``) are turned into ratio curves: for each
launch, the viewers at every hour of its launch day are divided by the
viewers in its first hour.  Working in log space, the distribution of
those log-ratios across launches gives the predictive distribution for
the target game: the 5/25/50/75/95 percentiles of the log-ratios, added
to the log of the hour-1 print, become the five quantiles.

Alignment is day-relative: the forecast hours are hours of the UTC
launch day and the anchor is the day's hour-1 print, so every launch
contributes ratios on that same frame.  The comparables' ``unlock``
field is prose describing a rolling midnight rollout, not a single
instant, so there is no unlock hour to align on.

Regime filter: a comparable whose hour-1 print is under 3% of its own
launch-day peak was still pre-launch at hour 1 (a single global unlock
later in the day).  Its ratios run to hundreds or thousands and describe
a different launch shape from the rolling-midnight target, so it is left
out; the launches kept have hour-1 prints from about 8% to 100% of
their peak.

Anchor-size adjustment: across the launches kept, the log of the
launch-day steepness (peak / hour-1 print) falls as the log of the
hour-1 print rises (correlation about -0.77): a huge first hour means
the launch is already at scale and grows little, a small first hour
means it is still ramping.  A log-log line is fitted to the kept
launches and the target's predicted steepness scales the median curve's
amplitude (clamped, so the shape is kept and the curve never goes flat
or explodes); the spread around the median is kept, not scaled.  A comparable whose launch day is
the same day as the target case is skipped, so a practice case never
predicts itself.  When too few comparables cover an hour, a sensible
default launch shape is used.

Everything is deterministic: no randomness, no clock, no network.
Only the Python standard library is used.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
MAX_VIEWERS = 1_000_000_000.0
MIN_SAMPLES = 3

# Fallback median launch shape (ratio to the hour-1 print) by hour
# since unlock, used only when comparables are missing for an hour.
DEFAULT_MEDIAN_RATIO = [
    1.00, 1.15, 1.10, 1.25, 1.60, 2.00, 2.30, 2.50,
    2.60, 2.70, 2.80, 3.00, 3.20, 3.40, 3.60, 3.90,
    4.20, 4.50, 4.80, 5.00, 4.90, 4.60, 4.20, 3.80,
]


def parse_ts(value):
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def as_number(value, default=None):
    """Coerce a JSON number (or numeric string) to float."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def weighted_percentile(pairs, prob):
    """Weighted percentile of (value, weight) pairs, linear interpolation."""
    items = sorted((float(v), float(w)) for v, w in pairs if w > 0)
    if not items:
        return None
    total = sum(w for _, w in items)
    if total <= 0:
        return None
    target = prob * total
    acc = 0.0
    prev_value = None
    for value, weight in items:
        prev_acc = acc
        acc += weight
        if acc >= target:
            if prev_value is None:
                return value
            frac = (target - prev_acc) / weight
            frac = max(0.0, min(1.0, frac))
            return prev_value + frac * (value - prev_value)
        prev_value = value
    return items[-1][0]


def smooth(values):
    """Light 3-point smoothing of a list of floats (log space).

    The ends keep most of their own value (0.75/0.25) so a sharp
    first-hour move is not dragged halfway toward its neighbour.
    """
    n = len(values)
    if n <= 1:
        return list(values)
    out = []
    for i in range(n):
        if i == 0:
            out.append(0.75 * values[0] + 0.25 * values[1])
        elif i == n - 1:
            out.append(0.25 * values[i - 1] + 0.75 * values[i])
        else:
            out.append(0.25 * values[i - 1] + 0.5 * values[i] + 0.25 * values[i + 1])
    return out


def default_log_quantiles(hour_index):
    """Fallback log-quantiles (relative to log anchor) for an hour."""
    k = max(1, min(24, hour_index))
    med = math.log(DEFAULT_MEDIAN_RATIO[k - 1])
    wide = 0.35 + 0.02 * k
    narrow = 0.15 + 0.01 * k
    return [med - wide, med - narrow, med, med + narrow, med + wide]


def ratio_samples(case, target_day):
    """Collect log-ratio samples per day-relative hour from comparables.

    Returns (samples, anchors): samples is a dict hour index (1..24) ->
    list of (log_ratio, weight); anchors is a list of (log hour-1 print,
    log steepness) for the launches kept, used for the anchor-size
    adjustment.
    """
    samples = {k: [] for k in range(1, 25)}
    anchors = []
    comparables = case.get("comparables") or []
    for comp in comparables:
        if not isinstance(comp, dict):
            continue
        comp_day = parse_ts(comp.get("day_start"))
        if comp_day is not None and target_day is not None:
            if comp_day.date() == target_day.date():
                continue  # same launch day: would predict the case itself
        hourly = comp.get("hourly") or []
        if comp_day is None or not hourly:
            continue
        by_hour = {}
        for row in hourly:
            if not isinstance(row, dict):
                continue
            start = parse_ts(row.get("from"))
            viewers = as_number(row.get("viewers"))
            if start is None or viewers is None:
                continue
            k = int((start - comp_day).total_seconds() // 3600) + 1
            if 1 <= k <= 24 and k not in by_hour:
                by_hour[k] = max(viewers, 1.0)
        base = by_hour.get(1)
        if base is None or base <= 0:
            continue
        peak = max(by_hour.values())
        if base < 0.03 * peak:
            continue  # still pre-launch at hour 1: a different regime
        anchors.append((math.log(base), math.log(peak / base)))
        for k, viewers in by_hour.items():
            if k == 1:
                continue
            samples[k].append((math.log(viewers / base), 1.0))
    return samples, anchors


def fit_steepness_line(points):
    """Least-squares line log(steepness) = a + b * log(anchor)."""
    n = len(points)
    if n < 5:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx <= 0:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    b = sxy / sxx
    return my - b * mx, b


def anchor_level(case):
    """Hour-1 print, with fallbacks from pre-launch history."""
    known = case.get("known")
    if isinstance(known, list):
        known = known[0] if known else None
    if isinstance(known, dict):
        value = as_number(known.get("viewers"))
        if value is not None and value > 0:
            return value
    for row in reversed(case.get("history_hourly") or []):
        if isinstance(row, dict):
            value = as_number(row.get("viewers"))
            if value is not None and value > 0:
                return value
    for row in reversed(case.get("history") or []):
        if isinstance(row, dict):
            value = as_number(row.get("viewers"))
            if value is not None and value > 0:
                return value
    return 1.0


def forecast_case(case):
    """Return the output dict for one case."""
    day_start = parse_ts(case.get("day_start"))
    unlock = day_start  # target origin: start of hour 1 (launch day start)
    anchor = max(anchor_level(case), 1.0)
    log_anchor = math.log(anchor)
    samples, anchors = ratio_samples(case, day_start)

    forecast = case.get("forecast") or []
    rows = []
    if isinstance(forecast, list) and forecast:
        for row in forecast:
            if isinstance(row, dict):
                hour = int(as_number(row.get("hour"), 0) or 0)
                start = parse_ts(row.get("from"))
                rows.append((hour, start))
    if not rows:
        rows = [(h, None) for h in range(2, 25)]
    rows.sort(key=lambda item: item[0])

    # Per-hour log-quantiles, before smoothing.
    log_q = []  # list of [lq05, lq25, lq50, lq75, lq95]
    for hour, start in rows:
        if start is not None and unlock is not None:
            k = int((start - unlock).total_seconds() // 3600) + 1
        else:
            k = hour
        k = max(1, min(24, k))
        points = samples.get(k, [])
        if len(points) >= MIN_SAMPLES:
            vals = [weighted_percentile(points, p) for p in LEVELS]
            if all(v is not None for v in vals):
                log_q.append(vals)
                continue
        log_q.append(default_log_quantiles(k))

    # Anchor-size adjustment: scale the median curve's amplitude so its
    # peak matches the steepness the target's anchor size predicts.  Only
    # the growth above the anchor is scaled (positive median log-ratios);
    # the early-morning dip below the anchor is left alone, so the
    # adjustment never lifts the first hours.  The spread around the
    # median is kept, not scaled: the adjustment moves the centre, it
    # does not claim the launch is more or less certain.
    line = fit_steepness_line(anchors)
    if line is not None and log_q:
        peak_log = max(vals[2] for vals in log_q)
        if peak_log > 0.05:
            a, b = line
            pred_log = a + b * log_anchor
            pred_log = max(0.0, min(math.log(20.0), pred_log))
            factor = pred_log / peak_log
            factor = max(0.25, min(2.5, factor))
            scaled = []
            for vals in log_q:
                m = vals[2]
                shift = (factor - 1.0) * m if m > 0 else 0.0
                scaled.append([v + shift for v in vals])
            log_q = scaled

    # Light smoothing across hours, per quantile level.
    series = [[log_q[i][j] for i in range(len(log_q))] for j in range(5)]
    series = [smooth(s) for s in series]
    log_q = [[series[j][i] for j in range(5)] for i in range(len(log_q))]

    hours = []
    for (hour, _start), vals in zip(rows, log_q):
        qs = []
        prev = 0
        for v in vals:
            q = int(round(math.exp(log_anchor + v)))
            q = max(1, min(int(MAX_VIEWERS), q))
            q = max(q, prev)
            qs.append(q)
            prev = q
        hours.append({"hour": hour, "q": qs})
    return {"hours": hours}


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input/")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output/")
    os.makedirs(output_dir, exist_ok=True)
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".in"):
            continue
        in_path = os.path.join(input_dir, name)
        out_path = os.path.join(output_dir, name[:-3] + ".out")
        try:
            with open(in_path, "r", encoding="utf-8") as f:
                case = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(case, dict):
            continue
        result = forecast_case(case)
        text = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
