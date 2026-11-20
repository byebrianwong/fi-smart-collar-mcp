import datetime as dt
import json

import pytest

from fi_mcp import parse


@pytest.mark.parametrize("text,minutes", [
    ("46min", 46), ("1hr 5min", 65), ("1.5hr", 90), ("2hr", 120), ("<1min", 0.5),
    ("30s", 0.5), ("10.1", 10.1), (None, None), ("", None),
    # Formats seen on a real account:
    ("5hr 31min", 331), ("1min", 1), ("16m/day", 16), ("2m/day", 2), ("10hr 37min/day", 637),
])
def test_duration_minutes(text, minutes):
    assert parse.parse_duration_minutes(text) == minutes


@pytest.mark.parametrize("text,count,unit", [
    ("24 events", 24, "events"), ("1 event", 1, "events"), ("1,204 events", 1204, "events"),
    (None, None, None), ("7", 7, None),
    # Formats seen on a real account:
    ("12,857 steps", 12857, "steps"), ("0 interruptions", 0, "interruptions"),
    ("6.3 events/day", 6.3, "events"), ("14,655 steps/day", 14655, "steps"),
])
def test_count(text, count, unit):
    assert parse.parse_count(text) == (count, unit)


def test_day_trend_keys_name_their_units():
    trend = {"id": "cleaning_self:DAY", "title": "Licking",
             "summaryComponents": {"eventsSummary": "12 events", "durationSummary": "3min",
                                   "eventsChange": None, "durationChange": {"direction": "DOWN", "change": "71%"}}}
    out = parse.health_trend(trend)
    assert out["name"] == "licking"
    assert out["events"] == 12 and out["minutes"] == 3
    assert out["change"] == "time down 71%" and out["change_percent"] == -71
    assert out["fi_text"] == "12 events, 3min"
    assert "chart" not in out


def test_week_trend_values_are_per_day():
    trend = {"id": "barking:WEEK", "title": "Barking",
             "summaryComponents": {"eventsSummary": "6.3 events/day", "durationSummary": None,
                                   "eventsChange": {"direction": "UP", "change": "100%+"}},
             "chart": {"__typename": "PetHealthTrendGraph", "average": 6.29, "minimum": 3, "maximum": 12,
                       "points": [5, 12, 7, 3, 7, 5, 5]}}
    out = parse.health_trend(trend, include_chart=True)
    assert out["events_per_day"] == 6.3 and "events" not in out and "minutes_per_day" not in out
    assert out["change"] == "events up 100%+" and out["change_percent"] == 100
    assert out["chart"]["unit"] == "events per day" and out["chart"]["points"][1] == 12


def test_time_based_week_chart_is_in_seconds():
    trend = {"id": "cleaning_self:WEEK", "title": "Licking",
             "summaryComponents": {"eventsSummary": None, "durationSummary": "16m/day"},
             "chart": {"__typename": "PetHealthTrendGraph", "average": 973.14, "points": [564, 1255]}}
    out = parse.health_trend(trend, include_chart=True)
    assert out["minutes_per_day"] == 16 and out["chart"]["unit"] == "seconds per day"


def test_list_summary_and_timeline_chart():
    trend = {"id": "rest:DAY", "title": "Rest",
             "summaryComponents": [{"eventsSummary": "0 interruptions", "durationSummary": "2min"}],
             "chart": {"__typename": "PetHealthTrendSegmentedTimeline", "length": 86400, "dataEnd": 80,
                       "segments": [{"offset": 0, "length": 600, "intervalType": "EVENT"},
                                    {"offset": 900, "length": 300, "intervalType": "EVENT"}]}}
    out = parse.health_trend(trend, include_chart=True)
    assert out["interruptions"] == 0 and out["change"] is None
    assert out["chart"]["minutes_by_type"] == {"event": 15}


# A month as Fi's restFeed sends it (shape seen 2026-11-17): one entry per day,
# oldest first, in seconds; future days are null.
NOV = {"start": "2026-11-01T00:00:00.000Z", "period": "MONTH", "restData": [
    {"sleepSeconds": 0, "napSeconds": 17040, "restSeconds": 17040},   # Nov 1
    {"sleepSeconds": 36000, "napSeconds": 5400, "restSeconds": 41400},  # Nov 2
    {"sleepSeconds": 0, "napSeconds": 0, "restSeconds": 0},           # Nov 3: collar off
    {"sleepSeconds": None, "napSeconds": None, "restSeconds": None},  # Nov 4: future
]}


def test_month_start_at_utc_midnight_is_a_plain_date():
    # Read as local time, this would be Oct 31 in the US.
    assert parse.period_first_day("2026-11-01T00:00:00.000Z") == dt.date(2026, 11, 1)


def test_rest_month_gives_minutes_per_day_newest_first():
    out = parse.rest_month(NOV, today=dt.date(2026, 11, 3))
    assert [d["date"] for d in out] == ["2026-11-03", "2026-11-02", "2026-11-01"]
    assert out[1] == {"date": "2026-11-02", "sleep_minutes": 600, "nap_minutes": 90}
    assert out[2]["sleep_minutes"] == 0 and out[2]["nap_minutes"] == 284


def test_rest_month_day_without_rest_is_none_not_zero():
    out = parse.rest_month(NOV, today=dt.date(2026, 11, 3))
    assert out[0]["sleep_minutes"] is None and out[0]["nap_minutes"] is None


def test_rest_month_stops_at_today():
    assert len(parse.rest_month(NOV, today=dt.date(2026, 11, 30))) == 4
    assert parse.rest_month(NOV, today=dt.date(2026, 10, 31)) == []


def test_group_rest_by_week_skips_days_without_data():
    days = parse.rest_month(NOV, today=dt.date(2026, 11, 3))  # Sun Nov 1, Mon Nov 2, Tue Nov 3
    weeks = parse.group_rest(days, "weekly")
    assert [w["start_date"] for w in weeks] == ["2026-11-02", "2026-10-26"]
    assert weeks[0] == {"start_date": "2026-11-02", "days_with_data": 1, "sleep_minutes": 600,
                        "nap_minutes": 90, "rest_minutes_per_day": 690}
    months = parse.group_rest(days, "monthly")
    assert months[0]["days_with_data"] == 2 and months[0]["rest_minutes_per_day"] == (690 + 284) / 2


def test_activity_summary_converts_meters_and_goal():
    out = parse.activity_summary({"start": "2026-11-14T08:00:00Z", "totalSteps": 9000, "stepGoal": 8000, "totalDistance": 5400})
    assert out["distance_km"] == 5.4 and out["goal_met"] is True


def test_collar_reads_info_json_string():
    c = parse.collar({"moduleId": "FC3ABC", "info": json.dumps({"batteryPercent": "87", "temperature": 2650}),
                      "operationParams": {"mode": "LOST_DOG"}, "lastConnectionState": None})
    assert c["battery_percent"] == 87 and c["temperature_c"] == 26.5
    assert c["lost_dog_mode"] is True and c["behavior_tracking"] is True


@pytest.mark.parametrize("module_id,tracking", [("FC1A", False), ("FC2B", False), ("M1C", False), ("FC3D", True), (None, False)])
def test_collar_model(module_id, tracking):
    assert parse.collar_model(module_id)["behavior_tracking"] is tracking
