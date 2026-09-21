# AI Status Board

A Hermes Desktop plugin for official service health and locally observed history.

**6 providers · 8 surfaces · no credentials · MIT licensed**

AI Status Board is separate from the `ai-status` hardware-monitoring plugin. It adds an
**AI Status Board** sidebar page at `/ai-status-board`; it does not add agent tools.

## Features

- Dark, line-based service list with current status and official source links.
- Separate official incident and locally observed history bars, including component history.
- Catch up on published incidents from while Hermes was off when it starts or resumes.
- Selectable ranges: 6 hours, 24 hours, 7 / 30 / 90 / 180 days.
- Selectable intervals: 5 / 15 / 30 minutes, 1 / 6 hours, or 1 day (maximum 600 bars).
- Automatic polling and manual Refresh with source-specific backoff.
- Local SQLite history, retained for 180 days.
- Separate source errors, stale values, service outages and official incident history.
- JST display, with UTC timestamps stored internally.

## Provider coverage

| Provider / surface | Official source | Adapter | Poll cadence |
|---|---|---|---|
| GitHub | [GitHub Status](https://www.githubstatus.com/) | Statuspage summary, incidents, maintenance | 5 min |
| ChatGPT / OpenAI | [OpenAI Status](https://status.openai.com/) | Status, complete components, incidents, supplementary RSS | 5 min |
| Claude / Anthropic | [Claude Status](https://status.claude.com/) | Statuspage summary, incidents, maintenance | 5 min |
| Cursor | [Cursor Status](https://status.cursor.com/) | Statuspage summary, incidents, maintenance | 5 min |
| Grok / xAI | [xAI Status](https://status.x.ai/) | Official RSS | 30 min |
| Gemini · Workspace | [Workspace Status](https://www.google.com/appsstatus/dashboard/) | Public JSON, stable product ID | 5 min |
| Gemini · AI Studio / API | [AI Studio Status](https://aistudio.google.com/status) | Experimental public HTML parser | 15 min |
| Gemini · Google Cloud | [Google Cloud Status](https://status.cloud.google.com/) | Public JSON, stable product ID | 5 min |

The three Gemini surfaces are deliberately separate. Perplexity is outside this plugin's scope.

### Known limitations

- **xAI:** RSS provides published incidents, but does not establish current availability or
  authoritative severity. Current and component states remain **Unknown**. Components represent
  the feed's historical inventory, not a complete current list.
- **AI Studio:** the tested public HTTP response is a JavaScript application shell without
  status text. The plugin reports **unsupported** and links to the official page. It does not
  use private RPCs, browser-session credentials or a guessed operational state.
- **Workspace:** its Gemini entry does not establish availability for all consumer Gemini users.
- **Google Cloud:** public incident reporting does not replace project-specific Personalized
  Service Health, which is outside this unauthenticated plugin.
- **OpenAI:** component IDs remain distinct, including duplicate names. Group membership is
  not inferred from names. Public aggregate status may differ from an individual user's service.
- Local observations start when this plugin begins collecting. Official incident history can
  predate installation or cover time while Hermes was off, within each source's published
  history window. Records outside that window cannot be recovered by this plugin.
- Notifications are not included in version 0.2.0.

## Installation

Requires Hermes Desktop with unified plugin support, the Desktop plugin SDK, and the
backend's Python environment with `fastapi` and `httpx`. The plugin uses Python's built-in SQLite.
It was verified on Windows with Hermes Desktop 0.21.1 and Python 3.11; other host versions and
operating systems have not been verified.

1. Download or clone this repository, then build the runtime-only package:

   ```sh
   python package.py
   ```

2. Extract `artifacts/ai-status-board-0.2.0.zip` into
   `<HERMES_HOME>/plugins/ai-status-board/`. `plugin.yaml` must be directly inside that folder.
   Use the home directory belonging to your active Hermes profile.
3. Enable the backend plugin:

   ```sh
   hermes plugins enable ai-status-board
   ```

4. In Hermes Desktop, open **Settings → Plugins** and enable **AI Status Board** under
   Desktop plugins. Restart Hermes to load the backend API, then open the sidebar entry.

Only enable this plugin. Existing `ai-status`, `quota`, and `hermes-memory-ui` installations
are independent. The package builder does not install anything or change Hermes settings.

When updating, preserve `<HERMES_HOME>/plugin-data/ai-status-board/`. The tested Desktop build
required a full app restart to reliably load edited UI source; window reload alone retained the
old render. If the backend is unavailable, verify both enable switches and restart Hermes.

## Reading the timeline

| Color | State |
|---|---|
| Green | Operational |
| Light blue | Maintenance / informational |
| Yellow | Degraded |
| Orange | Partial outage |
| Red | Major outage |
| Gray | Unknown / unavailable data |

**Locally observed availability** describes snapshots collected by this plugin. A bucket uses
the worst known state observed within it; a diagonal shade indicates partial coverage.
Missing observations, failed fetches and sleep gaps are not filled with green.

**Observed operational %** = operational observed duration / known observed duration.
**Coverage** = known observed duration / selected time range. Unknown time is excluded from
the first denominator, so read both numbers together. Neither is official uptime.

**Official incident history** has its own always-visible bar above local observations. It uses
published start/end timestamps and incident-level severity, including incidents that started
and resolved while Hermes was off. Maintenance spans use their published schedule. Gray empty
buckets mean no stored published record, not proven uptime. Bright gray means unknown severity;
a white mark indicates a feed publication or unknown-duration event. xAI RSS publication times
are markers, not asserted outage start times. An unresolved span stops at its last successful
retrieval so a stale incident is not extended indefinitely. Multiple records may describe the
same incident, particularly supplementary RSS updates; counts are records, not outage totals.

Expand a service for source-linked incident details and per-endpoint history synchronization
times. Component bars include only explicitly associated IDs; unscoped events remain at the
service level. Failed history synchronization preserves stored records and displays an error.
The absence of an incident does not prove uninterrupted service. Provider update time and fetch time are
shown separately. Source errors retain the last validated state and label it stale; a 429,
parse error or unavailable status source is not treated as a provider outage.

## Architecture

```text
Official public status sources
  → Provider adapters
  → Python core and scheduler
  → Local SQLite history
  → Scoped FastAPI endpoints
  → Hermes Desktop React UI
```

- `ai_status_board/adapters.py`: fixed official endpoints and pure response normalization.
- `ai_status_board/model.py`: common states, source errors, timestamp/text/URL validation.
- `ai_status_board/transport.py`: bounded HTTPS, conditional requests and transport errors.
- `ai_status_board/core.py`: polling, stale state, retry/backoff and the common response.
- `ai_status_board/history.py`: snapshots, incident upserts, migrations and aggregation.
- `dashboard/plugin_api.py`: `/api/plugins/ai-status-board` routes for board, components and refresh.
- `desktop/plugin.js`: renderer using only the scoped `ctx.rest` API.

The scheduler uses four workers and one in-flight request per surface. Source history sync is
hourly, plus catch-up on the first eligible poll after startup and after an observation gap
longer than the stale threshold. History endpoints synchronize independently of current-status
fetch success. Retry deadlines survive restart, including history-specific 429 Retry-After;
startup does not bypass provider rate limits. Google and xAI publish history in their regular
source response. Requests have a 15-second I/O timeout, a 4 MiB response cap and conditional ETag /
Last-Modified support. Redirects are not followed. HTTP cache Age above one hour is rejected.
Retry/backoff, jitter and Retry-After are respected; manual refresh does not bypass them or
xAI's RSS cadence. Stale threshold is twice the poll cadence plus one minute.

## Local data and privacy

The plugin does not read account credentials, browser cookies, chat history or other plugins'
data. It makes anonymous requests only to its fixed official status endpoints. Those providers
receive normal HTTP request metadata, including the connecting IP address.

Plugin data is stored in `<HERMES_HOME>/plugin-data/ai-status-board/history.sqlite3`:

- SQLite schema v2 with WAL and transactional migration from v1.
- State changes and overlapping, unchanged checkpoints, retained for 180 days.
- Public incident data stored separately from local observations.
- Compressed raw public responses retained for 7 days, with a 32 MiB payload cap.
- Runtime polling/backoff state retained across restarts.

Network collection runs while the Hermes backend is running; the next eligible sync imports
remaining official history from offline periods. No background service is installed.
SQLite may retain reusable allocated
pages after pruning; the raw payload cap is not a cap on the entire database file. Newer,
unsupported database schemas are refused rather than reset automatically.

External responses are treated as untrusted data: text is bounded, URLs are restricted to the
provider's HTTPS origin, XML declarations are rejected, and provider scripts are never run.
Do not publish local databases, response captures, logs or deployment records. They are excluded
from this repository and from the explicit runtime packaging allowlist.

## Development and tests

Use a separate Python 3.11 environment for development:

```sh
python -m venv .venv
# Activate .venv using your shell's usual command.
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The test suite uses **synthetic fixtures and mocked HTTP**; it needs neither credentials nor
live endpoints. Coverage includes degraded/partial/major states, active incidents, maintenance,
429/5xx/timeouts, malformed JSON/XML, schema drift, stale cache, sleep/resume simulation,
duplicate snapshots, retention, migration, restart persistence, component changes and API bounds.
Offline catch-up tests cover restart, resume before the hourly deadline, current-source failure,
history-only 429 across restart, malformed history, corrections, duration/impact uncertainty,
component filtering and unchanged local observation gaps.
Physical PC sleep has not been forced during verification.

An optional isolated UI preview uses your existing Hermes Node dependencies:

```powershell
$env:HERMES_NODE_MODULES = '<path-to-hermes-repository>/node_modules'
node tests/build_preview.cjs
python tests/serve_preview.py
```

Open `http://127.0.0.1:18764`. The preview uses synthetic responses by default. `--live` uses
anonymous official endpoints and a separate ignored preview database. `capture_fixtures.py`
can capture public responses for local investigation; its output is intentionally gitignored.
Review captures locally rather than committing them.

The public repository contains source, synthetic tests and reusable tooling only. Machine-specific
installation scripts, personal paths, real status captures, session content and local history are
not distribution assets.

## License

[MIT](LICENSE). Provider names belong to their respective owners. This is an independent plugin,
not an official status service or a product endorsed by the providers.
