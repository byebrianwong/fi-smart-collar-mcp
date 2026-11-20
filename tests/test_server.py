"""Call each MCP tool against the fake Fi API."""

import datetime as dt
import json

import pytest

from fi_mcp import server
from fi_mcp.client import FiClient

from .fake_fi import FakeFi


@pytest.fixture
def fake(monkeypatch):
    fake = FakeFi()
    monkeypatch.setattr(server, "_client", FiClient("dog@example.com", "pw", transport=fake.transport()))
    return fake


async def call(name: str, **args):
    result = await server.mcp.call_tool(name, args)
    # MCPServer returns (content, structured) for tools with a return type.
    if isinstance(result, tuple):
        return result[1].get("result", result[1]) if isinstance(result[1], dict) else result[1]
    return json.loads(result.content[0].text)


def today_iso() -> str:
    return dt.date.today().isoformat()


async def test_list_pets(fake):
    out = await call("list_pets")
    pet = out["pets"][0]
    assert pet["name"] == "Biscuit" and pet["breed"] == "Beagle" and pet["weight_kg"] == 14.5
    assert pet["collar"]["battery_percent"] == 12 and pet["collar"]["connection"]["via"] == "base station"
    assert out["base_stations"][0]["online"] is True


async def test_health_snapshot(fake):
    out = await call("get_health_snapshot")
    steps = out["recent_vs_baseline"]["steps"]
    assert steps["recent_avg"] == 6000 and steps["baseline_avg"] == 9000
    assert any(f.startswith("Steps: 6,000 steps/day") for f in out["flags"])
    # Barking (100%+) and licking (40%) pass the 20% threshold; eating (7%) and scratching (16%) do not.
    behavior_flags = [f for f in out["flags"] if "Fi's own marker" in f]
    assert [f.split(":")[0] for f in behavior_flags] == ["Barking", "Licking"]
    by_name = {b["name"]: b for b in out["behavior"]["behaviors"]}
    assert set(by_name) == {"barking", "licking", "scratching", "eating", "drinking"}
    assert by_name["barking"]["today_so_far"]["events"] == 3
    assert by_name["barking"]["past_week"]["events_per_day"] == 6.3
    # Today's steps come from Fi's day view; rest comes from the daily rest list.
    today = out["today_so_far"]
    assert today["steps"] == 2500 and today["active_minutes"] == 65 and today["rest_minutes"] == 750
    assert any("battery is at 12%" in n for n in out["notes"])
    # Series 3 collars send no charging or temperature fields.
    assert "charging" not in out["pet"]["collar"] and "temperature_c" not in out["pet"]["collar"]


async def test_health_snapshot_covers_every_dog_when_none_named(monkeypatch):
    fake = FakeFi(two_dogs=True)
    monkeypatch.setattr(server, "_client", FiClient("dog@example.com", "pw", transport=fake.transport()))
    out = await call("get_health_snapshot")
    assert [d["pet"]["name"] for d in out["dogs"]] == ["Biscuit", "Pepper"]
    one = await call("get_health_snapshot", pet="pepper")
    assert one["pet"]["name"] == "Pepper" and "dogs" not in one
    with pytest.raises(Exception, match="more than one dog"):
        await call("get_activity")


async def test_health_snapshot_without_behavior_access(monkeypatch):
    fake = FakeFi(refuse_trends=True)
    monkeypatch.setattr(server, "_client", FiClient("dog@example.com", "pw", transport=fake.transport()))
    out = await call("get_health_snapshot", pet="biscuit")
    assert out["behavior"]["available"] is False
    assert out["recent_vs_baseline"]["steps"]["recent_avg"] == 6000


async def test_activity_and_sleep(fake):
    act = await call("get_activity", period="daily", count=5)
    # Fi's daily activity list starts at yesterday.
    assert len(act["activity"]) == 5 and act["activity"][0]["steps"] == 6000
    assert act["activity"][0]["date"] < today_iso()
    sleep = await call("get_sleep", count=3)
    assert sleep["sleep"][0]["sleep_minutes"] == 600 and sleep["sleep"][0]["nap_minutes"] == 150


async def test_sleep_follows_the_rest_feed_back_month_by_month(fake):
    daily = await call("get_sleep", count=40)
    # Fi sends one month per request, so 40 days always takes at least 2.
    assert len(daily["sleep"]) == 40 and daily["sleep"][0]["date"] == today_iso()
    assert fake.queries.count("FiRestFeed") >= 2

    weekly = await call("get_sleep", period="weekly", count=3)
    assert len(weekly["sleep"]) == 3
    last_full_week = weekly["sleep"][1]
    assert last_full_week["days_with_data"] == 7 and last_full_week["sleep_minutes"] == 4200
    assert last_full_week["rest_minutes_per_day"] == 750

    monthly = await call("get_sleep", period="monthly", count=2)
    this_month = dt.date.today().replace(day=1)
    last_month = (this_month - dt.timedelta(days=1)).replace(day=1)
    assert [m["start_date"] for m in monthly["sleep"]] == [this_month.isoformat(), last_month.isoformat()]


async def test_behavior_with_charts(fake):
    out = await call("get_behavior", period="day", include_charts=True)
    lick = next(b for b in out["behaviors"] if b["name"] == "licking")
    assert lick["events"] == 12 and lick["minutes"] == 3
    assert lick["chart"]["minutes_by_type"] == {"nothing": 10, "event": 3}
    week = await call("get_behavior", period="week", include_charts=True)
    bark = next(b for b in week["behaviors"] if b["name"] == "barking")
    assert bark["events_per_day"] == 6.3 and bark["chart"]["points"] == [1, 2, 9]
    activity = next(t for t in week["other_trends"] if t["name"] == "activity")
    assert activity["steps_per_day"] == 14655


async def test_current_status(fake):
    out = await call("get_current_status")
    assert out["now"]["state"] == "resting" and out["now"]["place"] == "Home"
    assert out["collar_online"] is True


async def test_timeline_filters_other_pets(fake):
    out = await call("get_timeline", limit=10)
    types = [i["type"] for i in out["items"]]
    assert types == ["walk", "notification"]
    assert out["items"][0]["minutes"] == 45 and out["items"][0]["distance_km"] == 2.5


async def test_unknown_pet_name(fake):
    with pytest.raises(Exception, match="No pet called 'Rex'"):
        await call("get_activity", pet="Rex")


async def test_collar_off_at_night_setting_adds_notes(fake, settings_file):
    settings_file.write_text("[dogs.Biscuit]\ncollar_off_at_night = true\n")
    snap = await call("get_health_snapshot")
    assert "collar often comes off at night" in snap["notes"][0]
    assert "Rest is not flagged" in snap["notes"][0]
    sleep = await call("get_sleep", count=3)
    behavior = await call("get_behavior", period="week")
    assert "collar often comes off at night" in sleep["notes"][0]
    assert "collar often comes off at night" in behavior["notes"][0]


async def test_no_owner_notes_without_setting(fake):
    snap = await call("get_health_snapshot")
    assert not any("comes off at night" in n for n in snap["notes"])
    assert "notes" not in await call("get_sleep", count=3)
