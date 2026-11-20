"""Build a short health summary that compares recent days to the dog's normal.

The idea: one number on its own ("6,200 steps") says little. A change against
the same dog's usual level ("28% below its 4-week average") is what a person
or a vet would notice. So the snapshot:

1. Drops today, because today is not finished.
2. Drops days with almost no steps. Those are almost always days the collar
   was off or dead, not days the dog stayed still.
3. Averages the last 7 full days ("recent") and the days before that
   ("baseline", up to 4 weeks).
4. Flags a change of 20% or more in steps, distance, or total rest, when the
   baseline has at least 7 days.

Night sleep and naps are reported but not flagged on their own. On a real
account, Fi's split between them swings a lot: some days show 0 night sleep
and 10+ hours of naps. Their total (Fi calls it "rest") is much steadier.

Fi files a night's sleep under the date the night started. So today's night
sleep reads 0 until tonight, and yesterday's record holds last night.

Behavior data (barking, licking, and so on) comes from Fi already summarized
for today, the past week, and the past month, with Fi's own up/down marker.
We pass that through instead of computing our own, and flag markers of 20%
or more for the past week.
"""

from __future__ import annotations

import datetime as dt
from statistics import mean
from typing import Any

RECENT_DAYS = 7
BASELINE_DAYS = 28
MIN_BASELINE_DAYS = 7
FLAG_PERCENT = 20
# A day with fewer steps than this is treated as "collar not worn".
MIN_STEPS_FOR_WORN_DAY = 200

# key: (label for flags, unit for flags, minutes shown as hours?)
METRICS = {
    "steps": ("Steps", "steps/day", False),
    "distance_km": ("Distance", "km/day", False),
    "rest_minutes": ("Total rest (night sleep plus naps)", "h/day", True),
    "sleep_minutes": ("Night sleep", "h/day", True),
    "nap_minutes": ("Naps", "h/day", True),
}
FLAGGED_METRICS = ("steps", "distance_km", "rest_minutes")


def _fmt(key: str, value: float) -> str:
    if METRICS[key][2]:
        return f"{value / 60:.1f}"
    if key == "distance_km":
        return f"{value:.1f}"
    return f"{value:,.0f}"


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return round((new - old) / old * 100, 1)


def merge_days(activity: list[dict[str, Any]], rest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join activity and rest records by date, newest first."""
    by_date: dict[str, dict[str, Any]] = {}
    for a in activity:
        if a.get("date"):
            by_date.setdefault(a["date"], {"date": a["date"]}).update(
                steps=a.get("steps"), distance_km=a.get("distance_km"), step_goal=a.get("step_goal")
            )
    for r in rest:
        if r.get("date"):
            sleep, nap = r.get("sleep_minutes"), r.get("nap_minutes")
            by_date.setdefault(r["date"], {"date": r["date"]}).update(
                rest_minutes=(sleep + nap) if sleep is not None and nap is not None else None,
                sleep_minutes=sleep,
                nap_minutes=nap,
            )
    return sorted(by_date.values(), key=lambda d: d["date"], reverse=True)


def compare(
    days: list[dict[str, Any]], today: dt.date, *, flagged: tuple[str, ...] = FLAGGED_METRICS
) -> dict[str, Any]:
    """Recent-vs-baseline comparison over merged daily records.

    Every metric is reported. Only the ones in `flagged` can produce a flag.
    """
    today_s = today.isoformat()
    full_days = [d for d in days if d["date"] < today_s]
    worn = [d for d in full_days if (d.get("steps") or 0) >= MIN_STEPS_FOR_WORN_DAY]
    not_worn = [d["date"] for d in full_days if (d.get("steps") or 0) < MIN_STEPS_FOR_WORN_DAY]

    recent = worn[:RECENT_DAYS]
    baseline = worn[RECENT_DAYS : RECENT_DAYS + BASELINE_DAYS]

    metrics: dict[str, Any] = {}
    flags: list[str] = []
    for key, (label, unit, _) in METRICS.items():
        r_vals = [d[key] for d in recent if d.get(key) is not None]
        b_vals = [d[key] for d in baseline if d.get(key) is not None]
        r_avg, b_avg = _avg(r_vals), _avg(b_vals)
        change = _pct(r_avg, b_avg)
        enough = len(b_vals) >= MIN_BASELINE_DAYS
        metrics[key] = {
            "recent_avg": r_avg,
            "baseline_avg": b_avg,
            "change_percent": change,
            "recent_days": len(r_vals),
            "baseline_days": len(b_vals),
        }
        if key in flagged and enough and change is not None and abs(change) >= FLAG_PERCENT:
            direction = "above" if change > 0 else "below"
            flags.append(
                f"{label}: {_fmt(key, r_avg)} {unit} over the last {len(r_vals)} days, "
                f"{abs(change):.0f}% {direction} the {len(b_vals)}-day baseline of {_fmt(key, b_avg)} {unit}."
            )

    yesterday = (today - dt.timedelta(days=1)).isoformat()
    y = next((d for d in full_days if d["date"] == yesterday), None)
    today_rec = next((d for d in days if d["date"] == today_s), None)

    notes = []
    if not_worn:
        notes.append(
            f"{len(not_worn)} of the last {len(full_days)} days have under "
            f"{MIN_STEPS_FOR_WORN_DAY} steps and were left out (collar probably off or not reporting): "
            + ", ".join(not_worn[:10])
            + ("..." if len(not_worn) > 10 else "")
        )
    if len(baseline) < MIN_BASELINE_DAYS:
        notes.append(
            f"Only {len(baseline)} baseline days available, so no changes are flagged yet."
        )

    return {
        "today_so_far": today_rec,
        "yesterday": y,
        "recent_vs_baseline": metrics,
        "flags": flags,
        "notes": notes,
    }


_BEHAVIOR_PERIODS = (("DAY", "today_so_far"), ("WEEK", "past_week"), ("MONTH", "past_month"))


def merge_behavior(by_period: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Combine day/week/month behavior trends into one record per behavior.

    today_so_far holds totals for today. past_week and past_month hold daily
    averages, and their keys say so (events_per_day, minutes_per_day).
    """
    out: dict[str, dict[str, Any]] = {}
    for period_key, label in _BEHAVIOR_PERIODS:
        trends = by_period.get(period_key)
        if not trends:
            continue
        for b in trends.get("behaviors", []):
            entry = out.setdefault(b["name"], {"name": b["name"]})
            entry[label] = {
                k: v for k, v in b.items() if k not in ("name", "title", "fi_text") and v is not None
            }
    return {"behaviors": list(out.values())}


def behavior_flags(merged: dict[str, Any]) -> list[str]:
    """Plain-language lines for behaviors whose past-week marker is 20% or more."""
    lines = []
    for b in merged.get("behaviors", []):
        week = b.get("past_week") or {}
        percent = week.get("change_percent")
        if percent is None or abs(percent) < FLAG_PERCENT:
            continue
        values = [
            f"{v} {k.removesuffix('_per_day')}/day" for k, v in week.items() if k.endswith("_per_day")
        ]
        lines.append(
            f"{b['name'].capitalize()}: {', '.join(values) or 'no value'} over the past week, "
            f"{week['change'].split(' ', 1)[1]} by Fi's own marker "
            f"(Fi does not say what it compares against)."
        )
    return lines
