import datetime as dt

from fi_mcp import health

TODAY = dt.date(2026, 11, 15)


def _days(steps_by_age: dict[int, int], n: int = 40) -> list[dict]:
    out = []
    for i in range(n):
        d = (TODAY - dt.timedelta(days=i)).isoformat()
        out.append({"date": d, "steps": steps_by_age.get(i, 9000), "distance_km": 5.0,
                    "sleep_minutes": 600, "nap_minutes": 150, "rest_minutes": None})
    for d in out:
        d["rest_minutes"] = d["sleep_minutes"] + d["nap_minutes"]
    return out


def test_flags_drop_and_skips_today_and_collar_off_days():
    steps = {0: 100, 3: 0}  # today partial, day 3 collar off
    steps.update({i: 6000 for i in range(1, 9) if i != 3})  # 7 worn recent days at 6000
    result = health.compare(_days(steps), TODAY)
    m = result["recent_vs_baseline"]["steps"]
    assert m["recent_days"] == 7 and m["recent_avg"] == 6000
    assert m["baseline_avg"] == 9000 and m["change_percent"] == -33.3
    assert "Steps: 6,000 steps/day over the last 7 days, 33% below the 28-day baseline of 9,000 steps/day." in result["flags"]
    assert result["today_so_far"]["steps"] == 100
    assert result["yesterday"]["date"] == "2026-11-14"
    assert any("2026-11-12" in n for n in result["notes"])  # day 3 reported as not worn
    # Rest did not change, so it is not flagged.
    assert not any("rest" in f.lower() for f in result["flags"])
    assert result["recent_vs_baseline"]["rest_minutes"]["recent_avg"] == 750


def test_distance_and_rest_flags_use_readable_units():
    days = _days({})
    for d in days[1:8]:
        d["distance_km"], d["sleep_minutes"], d["nap_minutes"], d["rest_minutes"] = 2.7, 300, 150, 450
    for d in days[8:]:
        d["distance_km"] = 1.8
    flags = health.compare(days, TODAY)["flags"]
    assert "Distance: 2.7 km/day over the last 7 days, 50% above the 28-day baseline of 1.8 km/day." in flags
    assert any(f.startswith("Total rest (night sleep plus naps): 7.5 h/day") and "40% below" in f for f in flags)


def test_night_sleep_and_naps_are_not_flagged_on_their_own():
    days = _days({})
    for d in days[1:8]:
        d["sleep_minutes"], d["nap_minutes"] = 300, 450  # split changes, total stays 750
    result = health.compare(days, TODAY)
    assert result["recent_vs_baseline"]["sleep_minutes"]["change_percent"] == -50
    assert result["flags"] == []


def test_no_flags_without_enough_baseline():
    result = health.compare(_days({i: 3000 for i in range(1, 8)}, n=10), TODAY)
    assert result["flags"] == []
    assert any("baseline days" in n for n in result["notes"])


def test_merge_behavior_and_flags():
    by_period = {
        "DAY": {"behaviors": [
            {"name": "licking", "title": "Licking", "events": 12, "minutes": 3, "change": "time down 71%",
             "change_percent": -71, "fi_text": "12 events, 3min"}]},
        "WEEK": {"behaviors": [
            {"name": "licking", "title": "Licking", "minutes_per_day": 16, "change": "time up 40%",
             "change_percent": 40, "fi_text": "16m/day"},
            {"name": "eating", "title": "Eating", "events_per_day": 2.3, "change": "events up 7%",
             "change_percent": 7, "fi_text": "2.3 events/day"}]},
    }
    merged = health.merge_behavior(by_period)
    lick = merged["behaviors"][0]
    assert lick["today_so_far"] == {"events": 12, "minutes": 3, "change": "time down 71%", "change_percent": -71}
    assert lick["past_week"]["minutes_per_day"] == 16
    # Eating's 7% marker is under the 20% threshold, so only licking is flagged.
    assert health.behavior_flags(merged) == [
        "Licking: 16 minutes/day over the past week, up 40% by Fi's own marker "
        "(Fi does not say what it compares against)."
    ]
