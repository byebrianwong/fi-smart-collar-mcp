"""Turn raw Fi API answers into small, clearly labeled records.

Units in the raw API (checked against a real account on 2026-11-15):
- totalDistance is in meters.
- Rest (sleepSeconds, napSeconds, restSeconds) is in seconds.
- device.info.temperature is in hundredths of a degree Celsius. Series 3
  collars do not send it, and do not send isCharging either.

Health trends come back as display text. Seen on a real account:
- counts: "3 events", "12,857 steps", "0 interruptions"
- durations: "5hr 31min", "1min", "<1min", "16m"
- week and month values are daily averages and end in "/day":
  "6.3 events/day", "10hr 37min/day", "16m/day"
We turn those into numbers and name each number by its unit.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

# Fi's id prefix for each behavior, mapped to a plain name. Fi calls licking
# "cleaning_self" in the id but titles it "Licking".
BEHAVIOR_NAMES = {
    "barking": "barking",
    "cleaning_self": "licking",
    "scratching": "scratching",
    "eating": "eating",
    "drinking": "drinking",
}

# Collar models that cannot report behavior data, by moduleId prefix.
# Source: hass-tryfi fiDevice.supportsAdvancedBehaviorStats.
_NO_BEHAVIOR_PREFIXES = {"FC1": "Series 1", "FC2": "Series 2", "M1": "Fi Mini"}


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_date(value: str | None) -> str | None:
    """The calendar date of a Fi timestamp, in this computer's time zone.

    Fi's daily summaries start at local midnight, sent as a UTC timestamp.
    Converting back to local time gives the right day, as long as this
    computer is in the same time zone as the dog.
    """
    t = parse_time(value)
    return t.astimezone().date().isoformat() if t else None


def iso_local(value: str | None) -> str | None:
    t = parse_time(value)
    return t.astimezone().isoformat(timespec="minutes") if t else None


# --- Pet profile and collar ------------------------------------------------


def device_info(raw: Any) -> dict[str, Any]:
    """`device.info` is a JSON blob. Sometimes a dict, sometimes a string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def collar_model(module_id: str | None) -> dict[str, Any]:
    if not module_id:
        return {"model": "unknown", "behavior_tracking": False}
    for prefix, name in _NO_BEHAVIOR_PREFIXES.items():
        if module_id.startswith(prefix):
            return {"model": name, "behavior_tracking": False}
    return {"model": "Series 3 or newer", "behavior_tracking": True}


def age_years(pet: dict[str, Any], today: dt.date | None = None) -> float | None:
    year = pet.get("yearOfBirth")
    if not year:
        return None
    today = today or dt.date.today()
    born = dt.date(int(year), int(pet.get("monthOfBirth") or 1), int(pet.get("dayOfBirth") or 1))
    return round((today - born).days / 365.25, 1)


def connection(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    kind = state.get("__typename", "")
    out: dict[str, Any] = {"since": iso_local(state.get("date"))}
    if kind == "ConnectedToUser":
        out["via"] = "owner's phone (Bluetooth)"
        out["person"] = (state.get("user") or {}).get("firstName")
    elif kind == "ConnectedToBase":
        out["via"] = "base station"
    elif kind == "ConnectedToCellular":
        out["via"] = "cellular"
        out["signal_percent"] = state.get("signalStrengthPercent")
    else:
        out["via"] = "unknown"
    return out


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def collar(device: dict[str, Any] | None) -> dict[str, Any] | None:
    if not device:
        return None
    info = device_info(device.get("info"))
    params = device.get("operationParams") or {}
    out: dict[str, Any] = {
        **collar_model(device.get("moduleId")),
        "module_id": device.get("moduleId"),
        "battery_percent": _to_int(info.get("batteryPercent")),
    }
    # Older collars report these. Series 3 collars do not, so leave them out
    # rather than show an empty value that looks like "not charging".
    if info.get("isCharging") is not None:
        out["charging"] = info["isCharging"]
    temperature = info.get("temperature")
    if isinstance(temperature, (int, float)):
        out["temperature_c"] = round(float(temperature) / 100, 1)
    out.update(
        firmware=info.get("buildId"),
        lost_dog_mode=params.get("mode") == "LOST_DOG",
        connection=connection(device.get("lastConnectionState")),
        next_location_update_by=iso_local(device.get("nextLocationUpdateExpectedBy")),
    )
    return out


def pet_profile(pet: dict[str, Any]) -> dict[str, Any]:
    weight = pet.get("weight")
    return {
        "id": pet.get("id"),
        "name": pet.get("name"),
        "breed": (pet.get("breed") or {}).get("name"),
        "purebred": pet.get("isPurebred"),
        "gender": pet.get("gender"),
        "age_years": age_years(pet),
        "birth_year": pet.get("yearOfBirth"),
        # The API stores weight in kilograms (hass-tryfi reports it as kg and
        # updates it with a weight_kg value).
        "weight_kg": round(float(weight), 1) if isinstance(weight, (int, float)) else None,
        "collar": collar(pet.get("device")),
    }


# --- Activity and rest summaries ------------------------------------------


def activity_summary(s: dict[str, Any]) -> dict[str, Any]:
    steps = s.get("totalSteps")
    goal = s.get("stepGoal")
    meters = s.get("totalDistance")
    return {
        "date": local_date(s.get("start")),
        "start": iso_local(s.get("start")),
        "end": iso_local(s.get("end")),
        "steps": steps,
        "step_goal": goal,
        "goal_met": (steps >= goal) if isinstance(steps, (int, float)) and goal else None,
        "distance_km": round(meters / 1000, 2) if isinstance(meters, (int, float)) else None,
    }


def period_first_day(start: str | None) -> dt.date | None:
    """The first day of a rest period.

    Fi sends a week or month start as that date at UTC midnight
    ("2026-11-01T00:00:00.000Z"), not as local midnight like its other feeds.
    So a UTC-midnight timestamp is read as a plain date. Anything else is read
    as a local timestamp.
    """
    t = parse_time(start)
    if t is None:
        return None
    if t.utcoffset() == dt.timedelta(0) and t.time() == dt.time(0):
        return t.date()
    return t.astimezone().date()


def rest_month(summary: dict[str, Any], today: dt.date) -> list[dict[str, Any]]:
    """One record per day of a month of rest, newest first, up to today.

    Fi gives one entry per day of the month, oldest first. Days that haven't
    happened yet are null. A day with no rest at all (collar off all day, or no
    data yet) gets None instead of 0, so it can't pull an average down.
    """
    first = period_first_day(summary.get("start"))
    if first is None:
        return []
    out = []
    for i, entry in enumerate(summary.get("restData") or []):
        day = first + dt.timedelta(days=i)
        if day > today:
            break
        entry = entry or {}
        has_data = bool(entry.get("restSeconds"))
        out.append(
            {
                "date": day.isoformat(),
                "sleep_minutes": round((entry.get("sleepSeconds") or 0) / 60) if has_data else None,
                "nap_minutes": round((entry.get("napSeconds") or 0) / 60) if has_data else None,
            }
        )
    out.reverse()
    return out


def group_rest(days: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    """Add daily rest records up into weeks or calendar months, newest first.

    Weeks run Monday to Sunday. Totals cover only the days with data, so
    `days_with_data` says how many days each total covers, and
    `rest_minutes_per_day` is the average over those days.
    """
    if period not in ("weekly", "monthly"):
        raise ValueError(f"period must be weekly or monthly, got {period!r}")
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in days:
        day = dt.date.fromisoformat(d["date"])
        start = day - dt.timedelta(days=day.weekday()) if period == "weekly" else day.replace(day=1)
        groups.setdefault(start.isoformat(), []).append(d)
    out = []
    for start, items in sorted(groups.items(), reverse=True):
        worn = [d for d in items if d.get("sleep_minutes") is not None]
        sleep = sum(d["sleep_minutes"] for d in worn)
        nap = sum(d["nap_minutes"] for d in worn)
        out.append(
            {
                "start_date": start,
                "days_with_data": len(worn),
                "sleep_minutes": sleep if worn else None,
                "nap_minutes": nap if worn else None,
                "rest_minutes_per_day": round((sleep + nap) / len(worn)) if worn else None,
            }
        )
    return out


# --- Behavior and health trends --------------------------------------------

_COUNT_RE = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z]+)?")
_HOURS_RE = re.compile(r"([\d.]+)\s*h", re.IGNORECASE)
_MINUTES_RE = re.compile(r"([\d.]+)\s*m", re.IGNORECASE)
_SECONDS_RE = re.compile(r"([\d.]+)\s*s", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 1)


def parse_count(text: str | None) -> tuple[int | float | None, str | None]:
    """'3 events' -> (3, 'events'), '12,857 steps' -> (12857, 'steps'),
    '6.3 events/day' -> (6.3, 'events'), '1 event' -> (1, 'events')."""
    if not text:
        return None, None
    m = _COUNT_RE.match(text)
    if not m:
        return None, None
    value = _number(float(m.group(1).replace(",", "")))
    unit = (m.group(2) or "").lower() or None
    if unit and not unit.endswith("s"):
        unit += "s"
    return value, unit


def parse_duration_minutes(text: str | None) -> int | float | None:
    """'1hr 5min' -> 65, '46min' -> 46, '16m/day' -> 16, '<1min' -> 0.5."""
    if not text:
        return None
    text = text.split("/")[0].strip()
    if text.startswith("<"):
        # "<1min" means some activity, but under a minute. Count it as half.
        inner = parse_duration_minutes(text[1:])
        return round(inner / 2, 1) if inner else 0.0
    hours = _HOURS_RE.search(text)
    minutes = _MINUTES_RE.search(text)
    seconds = _SECONDS_RE.search(text) if not minutes else None
    if hours or minutes or seconds:
        total = 0.0
        if hours:
            total += float(hours.group(1)) * 60
        if minutes:
            total += float(minutes.group(1))
        if seconds:
            total += float(seconds.group(1)) / 60
        return _number(round(total, 1))
    try:
        return round(float(text), 1)
    except ValueError:
        return None


def _change(raw: dict[str, Any] | None, measure: str) -> tuple[str | None, int | None]:
    """Fi's marker {direction: UP, change: '40%'} -> ('events up 40%', 40).

    The number is signed: negative for down. '100%+' becomes 100.
    """
    if not raw or not raw.get("direction"):
        return None, None
    direction = raw["direction"].lower()
    amount = raw.get("change") or ""
    m = _PERCENT_RE.search(amount)
    percent = round(float(m.group(1))) if m else None
    if percent is not None and direction == "down":
        percent = -percent
    return f"{measure} {direction} {amount}".strip(), percent


def _summary_component(trend: dict[str, Any]) -> dict[str, Any]:
    # An object on the real API. open-fi types it as a list, so accept both.
    comp = trend.get("summaryComponents") or {}
    if isinstance(comp, list):
        comp = comp[0] if comp else {}
    return comp


def chart(raw: dict[str, Any] | None, value_unit: str | None = None) -> dict[str, Any] | None:
    """Fi's chart data.

    - Week and month: a graph with one value per day, oldest first.
    - Day: a timeline of segments. offset and length are seconds since
      local midnight.
    """
    if not raw:
        return None
    kind = raw.get("__typename")
    if kind == "PetHealthTrendGraph":
        return {
            "type": "graph (one value per day, oldest first)",
            "unit": value_unit,
            "average": raw.get("average"),
            "minimum": raw.get("minimum"),
            "maximum": raw.get("maximum"),
            "points": raw.get("points"),
        }
    if kind == "PetHealthTrendSegmentedTimeline":
        seconds: dict[str, float] = {}
        for seg in raw.get("segments") or []:
            label = (seg.get("intervalType") or "unknown").lower()
            seconds[label] = seconds.get(label, 0) + (seg.get("length") or 0)
        return {
            "type": "timeline (offset and length in seconds since local midnight)",
            "data_until_second": raw.get("dataEnd"),
            "minutes_by_type": {k: round(v / 60, 1) for k, v in seconds.items()},
            "segments": raw.get("segments"),
        }
    return {"type": kind, "raw": raw}


def health_trend(trend: dict[str, Any], *, include_chart: bool = False) -> dict[str, Any]:
    """One Fi health trend as a record whose keys name their units.

    Day:   {"name": "barking", "events": 3, "minutes": 1.0, "change": "events down 40%"}
    Week:  {"name": "barking", "events_per_day": 6.3, "change": "events up 100%+"}
    """
    trend_id = trend.get("id") or ""
    key = trend_id.split(":")[0]
    comp = _summary_component(trend)
    events_text, duration_text = comp.get("eventsSummary"), comp.get("durationSummary")
    per_day = any("/day" in (t or "") for t in (events_text, duration_text))
    suffix = "_per_day" if per_day else ""

    out: dict[str, Any] = {
        "name": BEHAVIOR_NAMES.get(key, key or (trend.get("title") or "").lower()),
        "title": trend.get("title"),
    }
    count, unit = parse_count(events_text)
    if count is not None:
        out[f"{unit or 'count'}{suffix}"] = count
    minutes = parse_duration_minutes(duration_text)
    if minutes is not None:
        out[f"minutes{suffix}"] = minutes

    change, percent = _change(comp.get("eventsChange"), unit or "count")
    if change is None:
        change, percent = _change(comp.get("durationChange"), "time")
    out["change"] = change
    out["change_percent"] = percent
    out["fi_text"] = ", ".join(t for t in (events_text, duration_text) if t) or None

    if trend.get("disabled"):
        out["disabled"] = True
    if comp.get("placeholder"):
        out["note"] = comp.get("placeholder")
    if include_chart:
        # Graph points are counts for count-based trends and seconds for
        # time-based ones (licking, scratching, rest).
        value_unit = f"{unit} per day" if unit else ("seconds per day" if duration_text else None)
        out["chart"] = chart(trend.get("chart"), value_unit)
    return out


def health_trends(raw: dict[str, Any], *, include_chart: bool = False) -> dict[str, Any]:
    return {
        "period": (raw.get("period") or "").lower() or None,
        "behaviors": [health_trend(t, include_chart=include_chart) for t in raw.get("behaviorTrends") or []],
        "other_trends": [health_trend(t, include_chart=include_chart) for t in raw.get("genericTrends") or []],
    }


# --- Current activity and timeline -----------------------------------------


def ongoing_activity(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    kind = raw.get("__typename")
    out: dict[str, Any] = {
        "state": {"OngoingWalk": "walking", "OngoingRest": "resting"}.get(kind, kind),
        "since": iso_local(raw.get("start")),
        "last_report": iso_local(raw.get("lastReportTimestamp")),
        "area": raw.get("areaName"),
    }
    if kind == "OngoingWalk":
        meters = raw.get("distance")
        out["distance_km"] = round(meters / 1000, 2) if isinstance(meters, (int, float)) else None
        points = raw.get("positions") or []
        if points:
            last = points[-1]
            out["position"] = last.get("position")
            out["position_time"] = iso_local(last.get("date"))
            out["error_radius_m"] = last.get("errorRadius")
    else:
        place = raw.get("place") or {}
        out["place"] = place.get("name")
        out["address"] = place.get("address")
        out["position"] = raw.get("position") or place.get("position")
    return out


def timeline_item(item: dict[str, Any]) -> dict[str, Any] | None:
    kind = item.get("__typename")
    if kind == "FiFeedActivityItem":
        act = item.get("activity") or {}
        akind = act.get("__typename")
        start, end = parse_time(act.get("start")), parse_time(act.get("end"))
        meters = act.get("distance")
        place = act.get("place") or {}
        return {
            "type": (akind or "activity").lower(),
            "pet_id": (item.get("pet") or {}).get("id"),
            "start": iso_local(act.get("start")),
            "end": iso_local(act.get("end")),
            "minutes": round((end - start).total_seconds() / 60) if start and end else None,
            "steps": act.get("totalSteps"),
            "distance_km": round(meters / 1000, 2) if isinstance(meters, (int, float)) else None,
            "area": act.get("areaName") or act.get("neighborhood"),
            "place": place.get("name"),
            "with": act.get("presentUserString"),
        }
    if kind == "FiFeedGenericNotificationItem":
        return {
            "type": "notification",
            "time": iso_local(item.get("timestamp")),
            "title": item.get("title"),
            "text": (item.get("body") or {}).get("text"),
        }
    return None
