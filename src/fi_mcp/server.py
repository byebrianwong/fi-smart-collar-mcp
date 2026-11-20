"""MCP server that lets Claude read a dog's Fi collar data.

Every tool here is read-only. None of them change collar settings, turn on
the light, or start lost-dog mode.

Run it with `uv run fi-mcp` (Claude starts it for you once registered).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import credentials, health, parse, queries, settings
from .client import FiClient, FiError, FiLoginError, FiNotAuthorized

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Read-only access to the owner's dog data from a Fi smart collar.

Start with get_health_snapshot for a general picture. Called with no dog
named, it covers every dog on the account. It compares the last 7 full days
with the weeks before, lists anything that changed by 20% or more, and
includes Series 3+ behavior data (barking, licking, scratching, eating,
drinking). Use get_activity, get_sleep, get_behavior, get_current_status, and
get_timeline for detail.

Things to keep in mind when you interpret the data:
- Today's numbers are partial; the day is not over.
- Fi files a night's sleep under the date the night started. Today's night
  sleep reads 0 until tonight; yesterday's record holds last night.
- Fi's split between night sleep and naps swings a lot from day to day. Total
  rest (the two added together) is the steadier number, so only that is
  flagged.
- Behavior values for the past week and month are daily averages
  (events_per_day, minutes_per_day). Today's values are totals so far.
- Behavior "change" values are Fi's own up/down markers. Fi does not say what
  it compares against.
- A day with almost no steps usually means the collar was off or dead, not
  that the dog was still. The snapshot leaves those days out and says so.
- The owner can mark a dog whose collar often comes off at night. Tools then
  include a note saying so. For that dog, low night sleep and rest, and
  lower behavior counts, can come from the missing nights, not from the dog.
- Behavior detection is Fi's own estimate from collar motion. Treat it as a
  signal worth asking about, not a diagnosis.
- This data comes from Fi's unofficial API and can be missing or late.
- You are not a vet. Point out changes and suggest a vet visit when a change
  is large, lasts several days, or comes with other symptoms.
"""

mcp = MCPServer("fi", instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

PetArg = Annotated[
    str | None,
    Field(description="Pet name or Fi pet id. Leave empty if there is only one dog on the account."),
]

SnapshotPetArg = Annotated[
    str | None,
    Field(description="Pet name or Fi pet id. Leave empty to get a snapshot of every dog on the account."),
]

_client: FiClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> FiClient:
    global _client
    async with _client_lock:
        if _client is None:
            try:
                email, password = credentials.load()
            except credentials.MissingCredentials as exc:
                raise ToolError(str(exc)) from exc
            _client = FiClient(email, password)
        return _client


async def _query(
    query: str, variables: dict[str, Any] | None = None, *, raise_not_authorized: bool = False
) -> dict[str, Any]:
    """Run a query and turn Fi errors into messages Claude can show the user.

    With raise_not_authorized=True, a per-query refusal is passed up as
    FiNotAuthorized so the caller can report "not available" instead of failing.
    """
    client = await _get_client()
    try:
        return await client.query(query, variables)
    except FiLoginError as exc:
        raise ToolError(
            f"{exc} The saved Fi login may be out of date. Run `uv run fi-mcp-setup` "
            "in the fi-mcp project folder to save it again."
        ) from exc
    except FiNotAuthorized as exc:
        if raise_not_authorized:
            raise
        raise ToolError(f"Fi refused this request: {exc}") from exc
    except FiError as exc:
        raise ToolError(f"Fi API problem: {exc}") from exc


async def _household() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = await _query(queries.HOUSEHOLD)
    pets, bases = [], []
    for membership in data["currentUser"]["userHouseholds"]:
        household = membership["household"]
        pets.extend(household.get("pets") or [])
        bases.extend(household.get("bases") or [])
    return pets, bases


async def _resolve_pet(pet: str | None) -> dict[str, Any]:
    pets, _ = await _household()
    with_collar = [p for p in pets if p.get("device")]
    candidates = with_collar or pets
    if not candidates:
        raise ToolError("No pets found on this Fi account.")
    if not pet:
        if len(candidates) == 1:
            return candidates[0]
        names = ", ".join(p["name"] for p in candidates)
        raise ToolError(f"This account has more than one dog ({names}). Say which one.")
    wanted = pet.strip().lower()
    for p in pets:
        if p["id"] == pet or (p.get("name") or "").lower() == wanted:
            return p
    names = ", ".join(p["name"] for p in pets)
    raise ToolError(f"No pet called {pet!r}. Pets on this account: {names}.")


async def _activity(pet_id: str, period: str, count: int) -> list[dict]:
    data = await _query(queries.activity_feed(period), {"petId": pet_id, "limit": count})
    pet = data.get("pet") or {}
    return [parse.activity_summary(s) for s in (pet.get("activity") or {}).get("activitySummaries") or []]


# Enough monthly pages for 12 months, or 26 weeks plus spare.
MAX_REST_PAGES = 13


async def _rest_days(pet_id: str, *, min_days: int = 0, months: int = 0) -> list[dict]:
    """Daily rest records, newest first, from today back.

    Fi's rest feed returns one calendar month per request, so this follows the
    feed back a month at a time. It stops once it has `months` months, or (if
    `months` is 0) at least `min_days` days.
    """
    today = dt.date.today()
    days: list[dict] = []
    cursor = None
    for page in range(1, MAX_REST_PAGES + 1):
        data = await _query(queries.REST_FEED, {"petId": pet_id, "cursor": cursor})
        feed = (data.get("pet") or {}).get("restFeed") or {}
        days.extend(parse.rest_month(feed.get("restSummary") or {}, today))
        cursor = feed.get("prevCursor")
        done = page >= months if months else len(days) >= min_days
        if done or not cursor:
            break
    return days


async def _feeds(pet_id: str, days: int) -> tuple[list[dict], list[dict]]:
    """Daily activity and rest for about the last `days` days, newest first."""
    activity, rest = await asyncio.gather(_activity(pet_id, "DAILY", days), _rest_days(pet_id, min_days=days))
    return activity, rest


async def _trends(pet_id: str, period: str, include_chart: bool = False) -> dict[str, Any]:
    data = await _query(queries.HEALTH_TRENDS, {"petId": pet_id, "period": period}, raise_not_authorized=True)
    return parse.health_trends(data.get("getPetHealthTrendsForPet") or {}, include_chart=include_chart)


NIGHT_OFF_NOTE = (
    "The owner says this dog's collar often comes off at night. Fi records no night sleep and no "
    "night-time licking, scratching, barking, or drinking on those nights, so rest and those "
    "behavior counts read low."
)


def _owner_notes(p: dict[str, Any]) -> list[str]:
    """Notes from the owner's per-dog settings file."""
    return [NIGHT_OFF_NOTE] if settings.for_pet(p).collar_off_at_night else []


_PERIOD_LIMITS = {"daily": ("DAILY", 90), "weekly": ("WEEKLY", 26), "monthly": ("MONTHLY", 12)}


def _feed_args(period: str, count: int) -> tuple[str, int]:
    api_period, max_count = _PERIOD_LIMITS[period]
    return api_period, max(1, min(count, max_count))


def _not_available(pet: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"pet": pet.get("name"), "available": False, "reason": reason}


# --- Tools -----------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def list_pets() -> dict[str, Any]:
    """List the dogs on the Fi account with breed, age, weight, and collar details
    (model, battery, connection). Also lists base stations."""
    pets, bases = await _household()
    return {
        "pets": [parse.pet_profile(p) for p in pets],
        "base_stations": [
            {
                "name": b.get("name"),
                "online": b.get("online"),
                "online_quality": b.get("onlineQuality"),
                "last_update": parse.iso_local(b.get("infoLastUpdated")),
            }
            for b in bases
        ],
    }


def _today_so_far(days: list[dict[str, Any]], day_trends: dict[str, Any] | None) -> dict[str, Any]:
    """Today's partial numbers.

    Fi's daily activity list starts at yesterday, so today's steps come from
    Fi's day view (the "activity" health trend). Rest comes from the daily
    rest list, the same source as the baseline.
    """
    today = dt.date.today().isoformat()
    record = next((d for d in days if d["date"] == today), {})
    out: dict[str, Any] = {"date": today}
    activity = next((t for t in (day_trends or {}).get("other_trends", []) if t["name"] == "activity"), None)
    if activity:
        out["steps"] = activity.get("steps")
        out["active_minutes"] = activity.get("minutes")
    for key in ("rest_minutes", "sleep_minutes", "nap_minutes"):
        if record.get(key) is not None:
            out[key] = record[key]
    out["note"] = "Partial day. Night sleep for today is counted once tonight's sleep happens."
    return out


async def _snapshot(p: dict[str, Any]) -> dict[str, Any]:
    pet_id = p["id"]
    profile = parse.pet_profile(p)
    days_needed = 1 + health.RECENT_DAYS + health.BASELINE_DAYS + 7  # spare for collar-off days

    feeds_task = _feeds(pet_id, days_needed)
    trend_tasks = {period: _trends(pet_id, period) for period in queries.TREND_PERIODS}
    results = await asyncio.gather(feeds_task, *trend_tasks.values(), return_exceptions=True)

    if isinstance(results[0], BaseException):
        raise results[0]
    activity, rest = results[0]
    days = health.merge_days(activity, rest)
    dog_settings = settings.for_pet(p)
    flagged = health.FLAGGED_METRICS
    if dog_settings.collar_off_at_night:
        # Missing nights make rest look low, so a rest flag would be noise.
        flagged = tuple(m for m in flagged if m != "rest_minutes")
    comparison = health.compare(days, dt.date.today(), flagged=flagged)

    behavior_by_period: dict[str, Any] = {}
    behavior_error = None
    for period, result in zip(trend_tasks, results[1:]):
        if isinstance(result, FiNotAuthorized):
            behavior_error = "Fi refused the behavior query for this collar (it may not include behavior tracking)."
        elif isinstance(result, BaseException):
            behavior_error = f"Could not load behavior data: {result}"
        else:
            behavior_by_period[period] = result
    behavior = health.merge_behavior(behavior_by_period)
    if behavior_error and not behavior_by_period:
        behavior = {"available": False, "reason": behavior_error}

    comparison["today_so_far"] = _today_so_far(days, behavior_by_period.get("DAY"))
    notes = comparison.pop("notes")
    if dog_settings.collar_off_at_night:
        notes.insert(0, NIGHT_OFF_NOTE + " Rest is not flagged for this dog.")
    flags = comparison.pop("flags") + health.behavior_flags(behavior)
    collar = profile.get("collar") or {}
    if collar.get("battery_percent") is not None and collar["battery_percent"] <= 15 and not collar.get("charging"):
        notes.append(f"Collar battery is at {collar['battery_percent']}%; upcoming data may have gaps.")
    if collar.get("lost_dog_mode"):
        notes.append("Lost-dog mode is ON for this collar.")

    return {
        "pet": profile,
        "flags": flags,
        **comparison,
        "behavior": behavior,
        "notes": notes,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_health_snapshot(pet: SnapshotPetArg = None) -> dict[str, Any]:
    """The best first call for "how is my dog doing?" or "how are the dogs?".

    With no dog named, returns one snapshot per dog. Each snapshot has the
    dog's profile and collar state, yesterday's and today's numbers, the last
    7 full days compared with the dog's own baseline (up to 4 weeks before
    that) for steps, distance, and rest, Series 3+ behavior data for today,
    the past week, and the past month, and plain-language flags for anything
    that changed by 20% or more."""
    generated_at = dt.datetime.now().astimezone().isoformat(timespec="minutes")
    if pet:
        return {"generated_at": generated_at, **await _snapshot(await _resolve_pet(pet))}
    pets, _ = await _household()
    dogs = [p for p in pets if p.get("device")]
    if not dogs:
        raise ToolError("No dogs with a Fi collar found on this account.")
    if len(dogs) == 1:
        return {"generated_at": generated_at, **await _snapshot(dogs[0])}
    snapshots = await asyncio.gather(*(_snapshot(p) for p in dogs))
    return {"generated_at": generated_at, "dogs": list(snapshots)}


@mcp.tool(annotations=READ_ONLY)
async def get_activity(
    pet: PetArg = None,
    period: Annotated[Literal["daily", "weekly", "monthly"], Field(description="Size of each bucket.")] = "daily",
    count: Annotated[int, Field(description="How many buckets, newest first. Max 90 daily, 26 weekly, 12 monthly.")] = 14,
) -> dict[str, Any]:
    """Step count, step goal, and distance (km) per day, week, or month, newest
    first. Fi starts this list at the last finished period: yesterday, last
    full week (Monday to Monday), or last full month. For today's steps so
    far, use get_health_snapshot or get_behavior with period "day"."""
    p = await _resolve_pet(pet)
    api_period, n = _feed_args(period, count)
    activity = await _activity(p["id"], api_period, n)
    return {"pet": p["name"], "period": period, "activity": activity}


@mcp.tool(annotations=READ_ONLY)
async def get_sleep(
    pet: PetArg = None,
    period: Annotated[Literal["daily", "weekly", "monthly"], Field(description="Size of each bucket.")] = "daily",
    count: Annotated[int, Field(description="How many buckets, newest first. Max 90 daily, 26 weekly, 12 monthly.")] = 14,
) -> dict[str, Any]:
    """Night sleep and daytime nap minutes, newest first. The first entry is
    the current, unfinished period.

    "daily" gives minutes per day; a day with no rest recorded (collar off all
    day) shows null. "weekly" (Monday to Sunday) and "monthly" give totals over
    the days with data, how many days that is, and the average rest per day.

    Fi files a night's sleep under the date the night started, so today's
    night sleep reads 0 until tonight. Fi's split between night sleep and naps
    swings a lot from day to day; their total is the steadier number."""
    p = await _resolve_pet(pet)
    _, n = _feed_args(period, count)
    if period == "daily":
        rest = (await _rest_days(p["id"], min_days=n))[:n]
    elif period == "weekly":
        # One spare week, so the oldest week returned is complete.
        rest = parse.group_rest(await _rest_days(p["id"], min_days=7 * n + 7), "weekly")[:n]
    else:
        rest = parse.group_rest(await _rest_days(p["id"], months=n), "monthly")[:n]
    out: dict[str, Any] = {"pet": p["name"], "period": period}
    if notes := _owner_notes(p):
        out["notes"] = notes
    return {**out, "sleep": rest}


@mcp.tool(annotations=READ_ONLY)
async def get_behavior(
    pet: PetArg = None,
    period: Annotated[
        Literal["day", "week", "month"],
        Field(description="day: today so far (totals). week: past 7 days, as daily averages. "
                          "month: past 4 weeks, as daily averages."),
    ] = "week",
    include_charts: Annotated[
        bool, Field(description="Also return Fi's chart data (per-interval values). Larger output.")
    ] = False,
) -> dict[str, Any]:
    """Series 3+ behavior data: barking, licking, scratching, eating, and drinking,
    with Fi's own up/down change marker. Each value's key names its unit:
    "events" and "minutes" for today, "events_per_day" and "minutes_per_day"
    for the week and month. Fi measures barking, eating, and drinking mainly
    in events, and licking and scratching in time. Also returns Fi's activity
    (steps, active minutes) and rest trends for the period.

    Licking and scratching changes can point to skin problems or allergies;
    eating and drinking changes can point to appetite or thirst changes. These
    are Fi's estimates from collar motion, not a diagnosis."""
    p = await _resolve_pet(pet)
    collar = parse.collar(p.get("device")) or {}
    try:
        trends = await _trends(p["id"], period.upper(), include_chart=include_charts)
    except FiNotAuthorized as exc:
        return _not_available(
            p,
            f"Fi refused the behavior query ({exc}). Collar model: {collar.get('model', 'unknown')}. "
            "Behavior tracking needs a Series 3+ collar on a plan that includes it.",
        )
    out: dict[str, Any] = {"pet": p["name"]}
    if notes := _owner_notes(p):
        out["notes"] = notes
    return {**out, **trends}


@mcp.tool(annotations=READ_ONLY)
async def get_current_status(pet: PetArg = None) -> dict[str, Any]:
    """Where the dog is right now and what it is doing (walking or resting, and
    where), plus collar battery, charging, connection, and lost-dog mode."""
    p = await _resolve_pet(pet)
    data = await _query(queries.PET_NOW, {"petId": p["id"]})
    pet_now = data.get("pet") or {}
    out: dict[str, Any] = {
        "pet": p["name"],
        "now": parse.ongoing_activity(pet_now.get("ongoingActivity")),
        "collar": parse.collar(pet_now.get("device")),
    }
    try:
        state = (await _query(queries.PET_COLLAR_STATE, {"petId": p["id"]})).get("pet", {}).get("petCollarState")
    except ToolError as exc:
        log.info("collar state query failed: %s", exc)
        state = None
    if state:
        status = state.get("collarStatus") or {}
        out["collar_online"] = status.get("__typename") != "OfflineCollarStatus" if status else None
        if status.get("__typename") == "OfflineCollarStatus":
            out["collar_offline"] = {
                "out_of_battery": status.get("outOfBattery"),
                "offline_for": status.get("offlineDuration"),
            }
        if state.get("needsCollar"):
            out["collar_note"] = "Fi says this pet has no collar set up."
    return out


@mcp.tool(annotations=READ_ONLY)
async def get_timeline(
    pet: PetArg = None,
    limit: Annotated[int, Field(description="How many items to return, newest first (max 100).")] = 25,
) -> dict[str, Any]:
    """Recent walks, rests, play, and travel from the Fi app's activity feed,
    with start and end times, steps, distance, and place. Also includes Fi
    notifications (for example, escape or low-battery alerts)."""
    p = await _resolve_pet(pet)
    limit = max(1, min(limit, 100))
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(5):  # at most 5 pages
        variables: dict[str, Any] = {
            "filter": "ALL",
            "includeTravel": True,
            "pagingInstruction": {"cursor": cursor, "direction": "BACKWARD"} if cursor else None,
        }
        data = await _query(queries.TIMELINE, variables)
        feed = (data.get("currentUser") or {}).get("fiFeed") or {}
        for raw in feed.get("feedItems") or []:
            item = parse.timeline_item(raw)
            if item is None:
                continue
            if item.get("pet_id") not in (None, p["id"]):
                continue
            item.pop("pet_id", None)
            items.append(item)
        page = feed.get("pageInfo") or {}
        cursor = page.get("endCursor")
        if len(items) >= limit or not page.get("hasPreviousPage") or not cursor:
            break
    items.sort(key=lambda i: i.get("start") or i.get("time") or "", reverse=True)
    return {"pet": p["name"], "items": items[:limit]}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


async def _snapshot_json(pet: str | None) -> dict[str, Any]:
    try:
        return await get_health_snapshot(pet)
    except ToolError as exc:
        return {"error": str(exc)}
    finally:
        if _client is not None:
            await _client.aclose()


def snapshot_cli() -> None:
    """`fi-mcp-snapshot [--pet NAME]`: print the health snapshot as JSON.

    For jobs that can run a command on this Mac but can't load the MCP server,
    such as a cloud routine that reaches the Mac through remote devices.
    Read-only. Prints {"error": ...} and exits 1 if Fi can't be read.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="fi-mcp-snapshot", description=snapshot_cli.__doc__.split("\n")[0])
    parser.add_argument("--pet", help="dog name or Fi pet id; leave out to get every dog")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # no per-request lines mixed into the output
    out = asyncio.run(_snapshot_json(args.pet))
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if "error" in out:
        sys.exit(1)


if __name__ == "__main__":
    main()
