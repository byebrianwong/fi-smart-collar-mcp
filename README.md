# Fi Smart Collar MCP

Lets Claude read your dog or cat's data from a [Fi](https://tryfi.com) smart
collar. You can ask things like "How long did Mochi walk today?", "How has
Biscuit been sleeping this week?", or "Is Biscuit scratching more than
usual?" and get answers from real numbers, compared with your pet's own
normal.

It's an MCP server. MCP (Model Context Protocol) is the standard way to give
an AI app like Claude Code or the Claude desktop app new tools. This server
adds read-only tools for activity, sleep, location, and, on Series 3 and newer
collars, behavior: barking, licking, scratching, eating, and drinking. Nothing
here changes collar settings, turns on the light, or starts lost-dog mode.

Unofficial: not affiliated with or endorsed by Fi. It uses the same private
API as the Fi app, so it can break when Fi changes things.

## What you need

- A Fi collar and a Fi account that signs in with an email and password. If
  you only sign in to the Fi app with Apple or Google, set a password first
  (see Setup).
- A Mac. The login is saved in the macOS Keychain. Environment variables work
  instead, but only macOS has been tested.
- [uv](https://docs.astral.sh/uv/) (`brew install uv`).
- Claude Code, the Claude desktop app, or another app that supports MCP.

The tools can show Claude where your pet is right now, which is often your
home address.

## About the unofficial API

Fi has no public API. This server sends the same GraphQL queries the Fi phone
app sends to `api.tryfi.com`. Those queries come from open-source projects
that captured the app's traffic:

- [pytryfi](https://github.com/sbabcock23/pytryfi): login, profile, activity, location
- [tryfi/hass-tryfi](https://github.com/tryfi/hass-tryfi) and
  [open-fi](https://github.com/gitpancake/open-fi): the health trends query
  that returns Series 3+ behavior data
- The rest query was worked out from Fi's own error messages after Fi
  changed it in November 2026. See the note in `src/fi_mcp/queries.py`.

What that means for you:

- Fi can change its API at any time, and some or all tools will stop working
  until the queries are updated. If that happens, compare `src/fi_mcp/queries.py`
  with the projects above.
- It logs in with your real Fi email and password.
- Automated access is probably outside Fi's terms of service. The server keeps
  answers for 5 minutes and only calls Fi when a tool is used, so it sends
  very few requests.

## Setup

Get the code and run every step below from its folder:

```bash
git clone https://github.com/byebrianwong/fi-smart-collar-mcp.git
cd fi-smart-collar-mcp
```

1. Save your Fi login to the macOS Keychain:

   ```bash
   uv run fi-mcp-setup
   ```

   It asks for your email and password (the password prompt is hidden), tests
   them against Fi, and saves them only if the login works. You get 3 tries.
   To test the saved login later, run `uv run fi-mcp-setup --check`. To delete
   it, run `uv run fi-mcp-setup --clear`.

   If Fi says the email or password is wrong and you sign in to the Fi app with
   Apple or Google, your account may have no Fi password. Set one with "Forgot
   password" on the Fi app's sign-in screen.

   You can use the `FI_EMAIL` and `FI_PASSWORD` environment variables instead
   of the Keychain.

2. Add the server to Claude Code (available in every project). Run this from
   the project folder:

   ```bash
   claude mcp add --scope user fi -- "$(which uv)" run --directory "$PWD" fi-mcp
   ```

   The command saves the full path to `uv` and to this folder, because Claude
   Code may not start servers with your shell's `PATH`.

   Check it with `claude mcp list`. It should show `fi: ... ✓ Connected`.
   Sessions that were already open need a restart to see the tools.

   For the Claude desktop chat app, add this to
   `~/Library/Application Support/Claude/claude_desktop_config.json`. Replace
   the two paths with the output of `which uv` and the full path to this
   folder:

   ```json
   {
     "mcpServers": {
       "fi": {
         "command": "/full/path/to/uv",
         "args": ["run", "--directory", "/full/path/to/fi-smart-collar-mcp", "fi-mcp"]
       }
     }
   }
   ```

## Per-pet settings

Some things about a pet are true but invisible in the data. Write them in
`~/.config/fi-mcp/settings.toml` (or set `FI_MCP_SETTINGS` to another path).
Name each section after the pet as it appears in the Fi app, or use its Fi
pet id. The table is called `dogs`, but it works for cats too:

```toml
[dogs.Biscuit]
collar_off_at_night = true
```

The server reads the file on every call, so edits apply without a restart.
If the file has a mistake, the server logs a warning and ignores the file.

| Setting | Effect |
|---|---|
| `collar_off_at_night` | The collar often comes off at night, so Fi records no night sleep and no night-time behavior. The snapshot stops flagging this pet's rest. The snapshot, `get_sleep`, and `get_behavior` add a note so Claude knows low rest and lower behavior counts may come from missing nights. |

## Tools

| Tool | What it returns |
|---|---|
| `get_health_snapshot` | The best first call. With no pet named, it returns one snapshot per pet. Each has profile and collar state, yesterday and today, the last 7 full days compared with up to 4 weeks before (steps, distance, total rest, night sleep, naps), behavior for today, the past week, and the past month, and a list of flags for anything that changed by 20% or more. |
| `list_pets` | Pets on the account: breed, age, weight (kg), collar model, battery, connection. Base stations. |
| `get_activity` | Steps, step goal, and distance (km) per day, week, or month. Starts at the last finished period (yesterday, last full week, last full month). |
| `get_sleep` | Night sleep and nap minutes per day. "weekly" and "monthly" give totals over the days with data, the number of those days, and the average rest per day. Starts at the current period. |
| `get_behavior` | Series 3+ only. Barking, licking, scratching, eating, and drinking, with Fi's up/down marker. "day" gives totals so far today; "week" and "month" give daily averages. Also Fi's activity and rest trends. Optional chart data. |
| `get_current_status` | Where the dog is now, walking or resting, collar battery, charging, connection, lost-dog mode. |
| `get_timeline` | Recent walks, rests, play, and travel, plus Fi notifications. |

### How the snapshot decides what to flag

- Today is left out of the comparison because the day is not over.
- Days with fewer than 200 steps are left out and listed separately. Those
  are almost always days the collar was off or dead.
- "Recent" is the last 7 full days with data. "Baseline" is up to 28 days
  before that. A change of 20% or more in steps, distance, or total rest is
  flagged, but only when the baseline has at least 7 days.
- Night sleep and naps are shown but not flagged on their own. Fi's split
  between them swings a lot from day to day (some days show 0 night sleep
  and 10+ hours of naps). Their total is much steadier.
- Behavior changes use Fi's own up/down marker for the past week. Markers of
  20% or more are flagged. Fi does not say what it compares against, so treat
  the marker as a hint.

These numbers are tuned by hand in `src/fi_mcp/health.py`.

## Data notes

- Distance comes from Fi in meters and is shown in km. Sleep comes in seconds
  and is shown in minutes. Weight is in kg.
- Fi files a night's sleep under the date the night started. Today's night
  sleep reads 0 until tonight, and yesterday's record holds last night.
- Rest comes from Fi's `restFeed`, which returns one calendar month per
  request, so longer histories take a few requests. Fi switched to this feed
  on 2026-11-16 or 17. A day with no rest at all (collar off all day) shows
  null, not 0.
- Fi's daily activity list starts at yesterday. Today's steps so far come from
  Fi's day view instead.
- Behavior keys name their units: `events` and `minutes` are totals for
  today; `events_per_day` and `minutes_per_day` are averages for the past week
  or month. Fi measures barking, eating, and drinking mainly in events, and
  licking and scratching in time.
- With `include_charts`, week and month charts have one value per day, oldest
  first: counts for count-based trends, seconds for time-based ones. Day
  charts are segments in seconds since local midnight.
- Series 3 collars don't report charging state or temperature, so those
  fields are left out.
- Daily dates use this computer's time zone. If you travel without your pet,
  dates can shift by a day.
- Behavior data is Fi's estimate from collar motion. It is a signal worth
  asking about, not a diagnosis.

## Using it without MCP

`fi-mcp-snapshot` prints the same health snapshot as JSON, for scripts and
scheduled jobs on your computer that can run a command but can't load an MCP
server:

```bash
uv run --directory /full/path/to/fi-smart-collar-mcp fi-mcp-snapshot --pet Biscuit
```

Leave out `--pet` to get every pet. If Fi can't be read, it prints
`{"error": ...}` and exits with code 1.

The command needs the login saved on your computer, so it only runs there.
To give the data to a job that runs in the cloud, run the command locally on a
schedule and copy its output somewhere the cloud job can read, such as a
Notion page or a cloud file.

## Development

```bash
uv run pytest                          # offline tests against a fake Fi API (real response formats)
uv run python scripts/live_check.py    # call every tool against your real account
uv run python scripts/live_check.py --raw   # also print raw API answers
```

Code layout:

- `src/fi_mcp/queries.py`: the GraphQL queries
- `src/fi_mcp/client.py`: login, requests, retry on expired session, 5-minute cache
- `src/fi_mcp/parse.py`: turns raw answers into labeled records with units
- `src/fi_mcp/health.py`: recent-vs-baseline comparison and flags
- `src/fi_mcp/server.py`: the MCP tools
- `src/fi_mcp/settings.py`: per-pet settings file
- `src/fi_mcp/credentials.py`, `setup_cli.py`: Keychain login storage
