"""A fake Fi API for tests. Answers the queries in fi_mcp.queries with canned data."""

from __future__ import annotations

import datetime as dt
import json

import httpx

PET_ID = "pet-1"


def _day_start(days_ago: int) -> str:
    """UTC timestamp of local midnight `days_ago` days back, like Fi sends."""
    local_midnight = dt.datetime.combine(dt.date.today() - dt.timedelta(days=days_ago), dt.time())
    return local_midnight.astimezone().astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def activity_feed(n: int) -> list[dict]:
    # Like the real API, the daily activity list starts at yesterday.
    out = []
    for i in range(1, n + 1):
        steps = 9000 if i >= 9 else 6000  # last 7 worn full days are lower (day 3 is off)
        if i == 3:
            steps = 0  # collar off
        out.append({"start": _day_start(i), "end": _day_start(i - 1), "totalSteps": steps,
                    "stepGoal": 8000, "totalDistance": steps * 0.6})
    return out


def rest_month_page(cursor: str | None) -> dict:
    """One month of rest, shaped like Fi's restFeed(period: MONTH) (seen 2026-11-17).

    The fake cursor is "YYYY-MM"; Fi's is opaque. One entry per day of the
    month, oldest first, in seconds. Days after today are null, like Fi.
    Every day up to today has 600 minutes of sleep and 150 of naps.
    """
    today = dt.date.today()
    first = dt.date.fromisoformat(cursor + "-01") if cursor else today.replace(day=1)
    next_month = (first + dt.timedelta(days=32)).replace(day=1)
    rest_data = []
    day = first
    while day < next_month:
        if day <= today:
            rest_data.append({"sleepSeconds": 600 * 60, "napSeconds": 150 * 60, "restSeconds": 750 * 60})
        else:
            rest_data.append({"sleepSeconds": None, "napSeconds": None, "restSeconds": None})
        day += dt.timedelta(days=1)
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    return {"prevCursor": prev.strftime("%Y-%m"),
            "restSummary": {"start": f"{first.isoformat()}T00:00:00.000Z", "period": "MONTH", "restData": rest_data}}


HOUSEHOLD = {"currentUser": {"id": "u1", "firstName": "Pat", "userHouseholds": [{"household": {
    "pets": [{"id": PET_ID, "name": "Biscuit", "yearOfBirth": 2020, "monthOfBirth": 5, "dayOfBirth": 1,
              "gender": "FEMALE", "weight": 14.5, "isPurebred": False, "breed": {"id": "b", "name": "Beagle"},
              "device": {"id": "d1", "moduleId": "FC3XYZ", "info": json.dumps({"batteryPercent": 12, "buildId": "5.3.20"}),
                         "nextLocationUpdateExpectedBy": "2026-11-15T20:00:00Z",
                         "operationParams": {"mode": "NORMAL", "ledEnabled": True, "ledOffAt": None},
                         "lastConnectionState": {"__typename": "ConnectedToBase", "date": "2026-11-15T19:00:00Z", "chargingBase": {"id": "base1"}}}}],
    "bases": [{"baseId": "base1", "name": "Home", "online": True, "onlineQuality": "GOOD", "infoLastUpdated": "2026-11-15T19:00:00Z", "networkName": "wifi"}],
}}]}}


SECOND_DOG = {"id": "pet-2", "name": "Pepper", "yearOfBirth": 2023, "gender": "MALE", "weight": 22.0,
              "isPurebred": True, "breed": {"id": "b2", "name": "Border Collie"},
              "device": {"id": "d2", "moduleId": "FC35G0", "info": {"batteryPercent": 80},
                         "operationParams": {"mode": "NORMAL"},
                         "lastConnectionState": {"__typename": "ConnectedToUser", "date": "2026-11-15T19:00:00Z",
                                                 "user": {"id": "u1", "firstName": "Pat"}}}}


def _change(direction, amount):
    return {"direction": direction, "change": amount} if direction else None


def _trend(key, title, period, events=None, duration=None, events_change=None, duration_change=None):
    if period == "DAY":
        chart = {"__typename": "PetHealthTrendSegmentedTimeline", "length": 86400, "dataEnd": 72000,
                 "segments": [{"offset": 0, "length": 600, "intervalType": "NOTHING"},
                              {"offset": 600, "length": 180, "intervalType": "EVENT"}]}
    else:
        chart = {"__typename": "PetHealthTrendGraph", "showAverage": True, "average": 3, "minimum": 1,
                 "maximum": 9, "points": [1, 2, 9]}
    return {"id": f"{key}:{period}", "title": title, "disabled": False, "chart": chart,
            "summaryComponents": {"placeholder": None, "eventsSummary": events, "durationSummary": duration,
                                  "eventsChange": _change(*(events_change or (None, None))),
                                  "durationChange": _change(*(duration_change or (None, None)))}}


def trends(period: str) -> dict:
    """Health trends in the text formats seen on a real account (2026-11-15)."""
    t = lambda *a, **k: _trend(a[0], a[1], period, *a[2:], **k)  # noqa: E731
    if period == "DAY":
        generic = [t("rest", "Rest", "0 interruptions", "5hr 31min"),
                   t("activity", "Activity", "2,500 steps", "1hr 5min", events_change=("DOWN", "24%"))]
        behaviors = [t("barking", "Barking", "3 events", "1min", events_change=("DOWN", "40%")),
                     t("cleaning_self", "Licking", "12 events", "3min", duration_change=("DOWN", "71%")),
                     t("scratching", "Scratching", "4 events", "<1min"),
                     t("eating", "Eating", "3 events", "6min", events_change=("UP", "50%")),
                     t("drinking", "Drinking", "1 event", "<1min")]
    else:
        generic = [t("rest", "Rest", None, "10hr 37min/day"),
                   t("activity", "Activity", "14,655 steps/day", events_change=("UP", "12%"))]
        behaviors = [t("barking", "Barking", "6.3 events/day", events_change=("UP", "100%+")),
                     t("cleaning_self", "Licking", None, "16m/day", duration_change=("UP", "40%")),
                     t("scratching", "Scratching", None, "2m/day", duration_change=("DOWN", "16%")),
                     t("eating", "Eating", "2.3 events/day", events_change=("UP", "7%")),
                     t("drinking", "Drinking", "9.3 events/day")]
    return {"getPetHealthTrendsForPet": {"period": period, "genericTrends": generic, "behaviorTrends": behaviors}}


class FakeFi:
    def __init__(self, *, refuse_trends: bool = False, expire_session_once: bool = False, bad_password: bool = False,
                 two_dogs: bool = False):
        self.refuse_trends = refuse_trends
        self.two_dogs = two_dogs
        self.expire_session_once = expire_session_once
        self.bad_password = bad_password
        self.logins = 0
        self.queries: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            self.logins += 1
            if self.bad_password:
                # Fi's real reply to a wrong email or password (checked 2026-11-15).
                return httpx.Response(401, json={"error": {"code": "error", "message": "Unauthorized"}})
            return httpx.Response(200, json={"userId": "u1", "sessionId": "s1"},
                                  headers={"set-cookie": "session=abc; Path=/"})
        body = json.loads(request.content)
        q, v = body["query"], body.get("variables") or {}
        name = q.split("query ", 1)[1].split("(")[0].split("{")[0].strip()
        self.queries.append(name)
        if self.expire_session_once:
            self.expire_session_once = False
            return httpx.Response(200, json={"errors": [{"message": "Unauthenticated"}], "data": None})
        if name == "FiHousehold":
            data = json.loads(json.dumps(HOUSEHOLD))
            if self.two_dogs:
                data["currentUser"]["userHouseholds"][0]["household"]["pets"].append(SECOND_DOG)
        elif name == "FiActivityFeed":
            data = {"pet": {"id": v["petId"], "activity": {"activitySummaries": activity_feed(v["limit"])}}}
        elif name == "FiRestFeed":
            if "period: MONTH" not in q:
                return httpx.Response(200, json={"errors": [{"message": "fake only knows MONTH"}]})
            data = {"pet": {"restFeed": rest_month_page(v.get("cursor"))}}
        elif name == "FiHealthTrends":
            if self.refuse_trends:
                return httpx.Response(200, json={"errors": [{"message": "Unauthorized"}], "data": None})
            data = trends(v["period"])
        elif name == "FiPetNow":
            data = {"pet": {"id": PET_ID, "name": "Biscuit", "device": HOUSEHOLD["currentUser"]["userHouseholds"][0]["household"]["pets"][0]["device"],
                            "ongoingActivity": {"__typename": "OngoingRest", "start": "2026-11-15T18:00:00Z", "lastReportTimestamp": "2026-11-15T19:00:00Z",
                                                "areaName": "Home", "position": {"latitude": 1.0, "longitude": 2.0},
                                                "place": {"id": "p", "name": "Home", "address": "1 Main St", "radius": 50, "position": {"latitude": 1.0, "longitude": 2.0}}}}}
        elif name == "FiPetCollarState":
            data = {"pet": {"petCollarState": {"__typename": "HasReportsState", "timestamp": "2026-11-15T19:00:00Z", "ldmState": None,
                                               "collarStatus": {"__typename": "OnlineCollarStatus"}}}}
        elif name == "FiTimeline":
            data = {"currentUser": {"fiFeed": {"feedItems": [
                {"__typename": "FiFeedActivityItem", "id": "a1", "timestamp": "2026-11-15T16:00:00Z",
                 "activity": {"__typename": "Walk", "id": "w1", "start": "2026-11-15T16:00:00Z", "end": "2026-11-15T16:45:00Z",
                              "areaName": "Park", "presentUserString": "Pat", "totalSteps": 4000, "distance": 2500,
                              "neighborhood": "Downtown", "cityState": "X"},
                 "pet": {"id": PET_ID, "name": "Biscuit"}},
                {"__typename": "FiFeedActivityItem", "id": "a2", "timestamp": "2026-11-15T15:00:00Z",
                 "activity": {"__typename": "Walk", "id": "w2", "start": "2026-11-15T15:00:00Z", "end": "2026-11-15T15:30:00Z",
                              "totalSteps": 100, "distance": 50},
                 "pet": {"id": "other-pet", "name": "Other"}},
                {"__typename": "FiFeedGenericNotificationItem", "id": "n1", "timestamp": "2026-11-15T14:00:00Z",
                 "title": "Low battery", "body": {"text": "Biscuit's collar is at 10%"}},
            ], "pageInfo": {"endCursor": "c1", "hasPreviousPage": False}}}}
        else:
            return httpx.Response(200, json={"errors": [{"message": f"unknown query {name}"}]})
        return httpx.Response(200, json={"data": data})
