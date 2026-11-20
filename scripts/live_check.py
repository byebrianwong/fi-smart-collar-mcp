"""Call every tool against the real Fi API and print what comes back.

    uv run python scripts/live_check.py            # summary of each tool
    uv run python scripts/live_check.py --raw      # also print raw API answers

Uses the login saved by `uv run fi-mcp-setup`. Read-only.
"""

import asyncio
import json
import sys

from fi_mcp import queries, server


def _structured(result):
    """Pull the JSON result out of whatever call_tool returned."""
    if isinstance(result, tuple):
        return result[1]
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)


async def _run(name: str, args: dict) -> None:
    print(f"\n===== {name} {args}")
    try:
        out = _structured(await server.mcp.call_tool(name, args))
        print(json.dumps(out, indent=2, default=str)[:6000])
    except Exception as exc:  # show every failure, keep going
        print(f"FAILED: {type(exc).__name__}: {exc}")


async def main(raw: bool) -> None:
    await _run("list_pets", {})
    await _run("get_health_snapshot", {})  # no dog named: covers every dog
    pets, _ = await server._household()
    for pet in pets:
        name = pet["name"]
        for tool, args in [
            ("get_health_snapshot", {}),
            ("get_activity", {"count": 5}),
            ("get_sleep", {"count": 5}),
            ("get_behavior", {"period": "week"}),
            ("get_current_status", {}),
        ]:
            await _run(tool, {"pet": name, **args})
    await _run("get_timeline", {"pet": pets[0]["name"], "limit": 8})

    if raw:
        client = await server._get_client()
        pets, _ = await server._household()
        pet_id = pets[0]["id"]
        for label, q, v in [
            ("raw activity feed", queries.activity_feed("DAILY"), {"petId": pet_id, "limit": 2}),
            ("raw rest feed (this month)", queries.REST_FEED, {"petId": pet_id, "cursor": None}),
            ("raw health trends DAY", queries.HEALTH_TRENDS, {"petId": pet_id, "period": "DAY"}),
            ("raw collar state", queries.PET_COLLAR_STATE, {"petId": pet_id}),
        ]:
            print(f"\n===== {label}")
            try:
                print(json.dumps(await client.query(q, v), indent=2)[:6000])
            except Exception as exc:
                print(f"FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main("--raw" in sys.argv))
