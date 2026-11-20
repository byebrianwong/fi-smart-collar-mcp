"""GraphQL queries for the Fi (TryFi) API.

Fi has no public API. These queries copy what the Fi phone app sends to
https://api.tryfi.com/graphql. They come from two open-source projects that
captured the app's traffic:

- pytryfi (github.com/sbabcock23/pytryfi): profile, activity, location.
- open-fi (github.com/gitpancake/open-fi) and tryfi/hass-tryfi: the health
  trends query that returns Series 3+ behavior data (barking, licking,
  scratching, eating, drinking).

Fi can change any of this without notice. If a query starts failing, compare
it with those projects first.

The rest query (REST_FEED) is not from those projects. Fi replaced the old
`restSummaryFeed` field on 2026-11-16 or 17, and the new shape was worked out
from Fi's own error messages, because Fi turns off schema introspection.
"""

API_HOST = "https://api.tryfi.com"
LOGIN_PATH = "/auth/login"
GRAPHQL_PATH = "/graphql"

# Periods accepted by the activity summary feed.
FEED_PERIODS = ("DAILY", "WEEKLY", "MONTHLY")

# Periods accepted by the health trends query.
TREND_PERIODS = ("DAY", "WEEK", "MONTH")


# --- Fragments -------------------------------------------------------------

_POSITION = "fragment Position on Position { latitude longitude }"

_PLACE = "fragment Place on Place { id name address radius position { ...Position } }"

_CONNECTION_STATE = """
fragment ConnectionState on ConnectionState {
  __typename
  date
  ... on ConnectedToUser { user { id firstName } }
  ... on ConnectedToBase { chargingBase { id } }
  ... on ConnectedToCellular { signalStrengthPercent }
  ... on UnknownConnectivity { unknownConnectivity }
}
"""

_DEVICE = """
fragment Device on Device {
  id
  moduleId
  info
  nextLocationUpdateExpectedBy
  operationParams { mode ledEnabled ledOffAt }
  lastConnectionState { ...ConnectionState }
}
"""

_PET_PROFILE = """
fragment PetProfile on Pet {
  id
  name
  yearOfBirth
  monthOfBirth
  dayOfBirth
  gender
  weight
  isPurebred
  breed { id name }
  device { ...Device }
}
"""

_BASE = """
fragment Base on ChargingBase {
  baseId
  name
  online
  onlineQuality
  infoLastUpdated
  networkName
}
"""

_ACTIVITY_SUMMARY = """
fragment ActivitySummary on ActivitySummary {
  start
  end
  totalSteps
  stepGoal
  totalDistance
}
"""


_ONGOING_ACTIVITY = """
fragment OngoingActivity on OngoingActivity {
  __typename
  start
  lastReportTimestamp
  areaName
  ... on OngoingWalk {
    distance
    positions { date errorRadius position { ...Position } }
  }
  ... on OngoingRest {
    position { ...Position }
    place { ...Place }
  }
}
"""

_HEALTH_TREND = """
fragment HealthTrendChange on PetHealthTrendSummaryChange { direction change }

fragment HealthTrendSummary on PetHealthTrendSummary {
  placeholder
  eventsSummary
  eventsChange { ...HealthTrendChange }
  durationSummary
  durationChange { ...HealthTrendChange }
}

fragment HealthTrend on PetHealthTrend {
  id
  title
  disabled
  chart {
    __typename
    ... on PetHealthTrendGraph {
      showAverage
      average
      minimum
      maximum
      points: intervals
    }
    ... on PetHealthTrendSegmentedTimeline {
      length
      dataEnd
      segments: intervals { offset length intervalType }
    }
  }
  summaryComponents { ...HealthTrendSummary }
}
"""


# --- Queries ---------------------------------------------------------------

HOUSEHOLD = (
    """
query FiHousehold {
  currentUser {
    id
    firstName
    userHouseholds {
      household {
        pets { ...PetProfile }
        bases { ...Base }
      }
    }
  }
}
"""
    + _PET_PROFILE
    + _DEVICE
    + _CONNECTION_STATE
    + _BASE
)


def activity_feed(period: str) -> str:
    """Activity history for one pet, newest first.

    `period` is one of FEED_PERIODS. It is checked against that list before it
    goes into the query text, because the GraphQL enum type name is unknown and
    so it cannot be passed as a variable.
    """
    if period not in FEED_PERIODS:
        raise ValueError(f"period must be one of {FEED_PERIODS}, got {period!r}")
    return (
        f"""
query FiActivityFeed($petId: ID!, $limit: Int!) {{
  pet(id: $petId) {{
    id
    activity: activitySummaryFeed(cursor: null, period: {period}, limit: $limit) {{
      activitySummaries {{ ...ActivitySummary }}
    }}
  }}
}}
"""
        + _ACTIVITY_SUMMARY
    )


# One calendar month of rest per request. restData holds one entry per day of
# the month, oldest first, in seconds; days that haven't happened are null.
# prevCursor points at the month before. Leave $cursor null for this month.
# The same field also takes DAY (24 hourly entries), WEEK (7 daily entries),
# and YEAR (12 monthly entries, as daily averages).
REST_FEED = """
query FiRestFeed($petId: ID!, $cursor: String) {
  pet(id: $petId) {
    restFeed(cursor: $cursor, period: MONTH) {
      prevCursor
      restSummary {
        start
        period
        restData { sleepSeconds napSeconds restSeconds }
      }
    }
  }
}
"""


PET_NOW = (
    """
query FiPetNow($petId: ID!) {
  pet(id: $petId) {
    id
    name
    ongoingActivity { ...OngoingActivity }
    device { ...Device }
  }
}
"""
    + _ONGOING_ACTIVITY
    + _POSITION
    + _PLACE
    + _DEVICE
    + _CONNECTION_STATE
)

# Newer collar status (online/offline, out of battery). Kept separate from
# PET_NOW so that if Fi changes it, the rest of the status still works.
PET_COLLAR_STATE = """
query FiPetCollarState($petId: ID!) {
  pet(id: $petId) {
    petCollarState {
      __typename
      timestamp
      ... on NeedsReportsState { needsCollar needsReports }
      ... on HasReportsState {
        ldmState
        collarStatus {
          __typename
          ... on OfflineCollarStatus { outOfBattery offlineDuration }
        }
      }
    }
  }
}
"""

HEALTH_TRENDS = (
    """
query FiHealthTrends($petId: ID!, $period: PetHealthTrendPeriod!) {
  getPetHealthTrendsForPet(petId: $petId, period: $period) {
    period
    genericTrends { ...HealthTrend }
    behaviorTrends { ...HealthTrend }
  }
}
"""
    + _HEALTH_TREND
)

TIMELINE = (
    """
query FiTimeline($pagingInstruction: PagingInstruction, $includeTravel: Boolean!, $filter: FiFeedFilter) {
  currentUser {
    fiFeed(pagingInstruction: $pagingInstruction, includeTravel: $includeTravel, filter: $filter) {
      feedItems {
        __typename
        id
        timestamp
        ... on FiFeedActivityItem {
          activity {
            __typename
            id
            start
            end
            areaName
            presentUserString
            totalSteps
            ... on Walk { distance neighborhood cityState }
            ... on Rest { place { ...Place } }
            ... on Travel { distance }
            ... on Play { place { ...Place } }
          }
          pet { id name }
        }
        ... on FiFeedGenericNotificationItem {
          title
          body { text }
        }
      }
      pageInfo { endCursor hasPreviousPage }
    }
  }
}
"""
    + _PLACE
    + _POSITION
)
