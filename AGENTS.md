# Agent guide

## What
Scrappy is a Windows app for finding and reviewing Mexico City rental listings. `Scrappy.exe` starts a Flask server bound to `127.0.0.1`; its browser cockpit is `/favorites` and the Kanban is `/kanban`. The database is `scrappy.db`, the user configuration is `config.json`, and generated HTML reports are under `reports/`. The data directory is `%LOCALAPPDATA%\Scrappy` (Windows per-user Local AppData, falling back to `%USERPROFILE%\AppData\Local\Scrappy` if Windows cannot provide it), overridden exactly by `SCRAPPY_HOME` when set. Runtime files also include `run_meta.json`, `last_run.log`, `scraper_health.json`, and `run.lock`.

## Who
The person using Scrappy may not be technical. The agent explains options, guides the person through the UI, and can perform safe, reversible documentation or source changes when requested. Ask the person before connecting an AI provider, enabling Firecrawl, deleting local data, or uninstalling. Never request or repeat credentials in chat; have the person enter them in Settings.

## When

| Situation | Command or route |
|---|---|
| Check local status | `Scrappy.exe --doctor` (release) or `python launcher.py --doctor` (source checkout) |
| Check version | `Scrappy.exe --version` |
| Check server | `GET /health` |
| Open listing cockpit / Kanban | `GET /favorites` / `GET /kanban` |
| Run a search | UI **Run Now** / `POST /run` |
| Inspect listings / run status | `GET /listings` / `GET /run_meta` |
| Export / import backup | `GET /api/export` / `POST /api/import` |
| Stop server | `POST /api/quit` |

## Why
- The server binds only to loopback so other network devices cannot connect to the local app.
- AI and Firecrawl credentials belong only in Settings and the OS credential store; config and API responses hold provider selection, not keys.
- Never paste keys into chat or logs because these are not credential stores and may be retained or shared.
- Never upload the data directory: it contains listing records, feedback, notes, configuration, and reports that can reveal private preferences.
- Keep configured `cadence_days`; changing scrape frequency can increase load on listing sites and provider use.
- Respect listing sites' access rules. Scrapers make requests to third-party sites; do not evade their controls or increase request rates.

## How
### Install a release
1. Download `Scrappy.exe` and `SHA256SUMS.txt` from the project's GitHub Releases page.
2. In PowerShell, run `Get-FileHash .\Scrappy.exe -Algorithm SHA256` and compare the `Hash` with the `Scrappy.exe` entry in `SHA256SUMS.txt` (case-insensitive).
3. Start the executable. It is an unsigned beta; Windows may show SmartScreen. Explain **More info → Run anyway** and let the person decide.

### Install from source
From a project checkout, create and activate a virtual environment (`python -m venv .venv`, then `.venv\Scripts\Activate.ps1`), install with `python -m pip install -e .`, and start with `python launcher.py`. Use `python launcher.py --no-browser` to avoid opening a browser.

`launcher.py` (and `Scrappy.exe`) keeps running in the foreground until Quit. Start it in its own window or as a background process before running other commands, for example in PowerShell: `Start-Process -WindowStyle Hidden .venv\Scripts\python.exe -ArgumentList 'launcher.py','--no-browser'`.

On Windows, `Activate.ps1` is often blocked by the default PowerShell execution policy. You do not need to activate: call the venv's interpreter directly, for example `.venv\Scripts\python.exe -m pip install -e .` and `.venv\Scripts\python.exe launcher.py`.

If `python -m venv` fails to install pip (an `ensurepip` error, common in locked-down or sandboxed shells), run `python -m venv --without-pip .venv`, then `.venv\Scripts\python.exe -m ensurepip --upgrade`; if that is also blocked, install into the user site instead: `python -m pip install --user -e .`.

### Verify
Run `Scrappy.exe --doctor` (release) or `python launcher.py --doctor` (source checkout, same environment variables). Its JSON fields are `version`, `data_dir`, `db_exists`, `port`, `server_running`, `ai_provider`, `ai_connected`, and `firecrawl_configured`; these report version, selected data location, database existence, active port, server health, provider choice/connectivity, and Firecrawl credential presence. The default port is 5055; `SCRAPPY_PORT` can set the starting port. If busy, the launcher tries higher ports. Check the port shown by `--doctor` and request `http://127.0.0.1:<port>/health`. A healthy response is `{"ok":true,"app":"scrappy","version":"0.5.0"}`.

### Connect AI (ask first)
In Settings → AI → **Sign in**, the person can use OpenRouter OAuth without copying a key. Or they can choose Gemini, OpenAI, Anthropic, or Ollama Cloud and paste a provider key into Settings, or choose **No AI** for metadata scoring. The agent can guide them through any choice. Confirm with `GET /api/ai`; a configured connection reports `{"provider":"…","model":"…","connected":true}`. Never handle or request the key in chat.

No AI is the default until the person picks a provider; `GET /api/ai` then reports `provider: "none"` with `connected: true` (nothing to connect).

### Firecrawl (ask first)
Firecrawl is optional and can incur charges. Explain the tradeoff and ask before enabling. Follow [FIRECRAWL.md](FIRECRAWL.md); the person enters the key in Settings → Firecrawl. Confirm with `GET /api/firecrawl` (`{"connected":true}`).

### Search and review
Set criteria in the Search criteria panel. **Run Now** calls `POST /run`; success returns `{"ok":true,"message":"run triggered"}`. Progress and source health are at `GET /run_meta`: `ai_status`, `sources`, and `log_tail`. `sources` maps source names to status, count, and detail; statuses include `ok`, `error`, and `needs_firecrawl`. `GET /listings` returns `approved`, `rejected`, `inbox`, and `counts`. Review listings in `/favorites`; use `/kanban` to move candidates among `inbox`, `shortlisted`, `contacted`, and `visit_scheduled`.

`POST /run` only starts the search in a background process and returns at once. Poll `GET /run_meta` every 15-30 seconds: `running` is `true` while the search works and becomes `false` when it ends. While it runs, `stage` moves `scraping` → `scoring` → `done`, `elapsed_s` grows, `sources` fills in one source at a time (`status`, `count`), and `log_tail` shows the latest log lines. A full run usually takes 5-10 minutes (measured: about 6 minutes for 4 sources and 12 neighborhoods); a slow site can make it longer. Do not quit Scrappy while `running` is `true` — that stops the search. If `running` stays `true` with no new `log_tail` lines for 15 minutes, stop the app (`POST /api/quit`), start it again and retry; the partial run is discarded and saved listings are kept. When it ends, read `ai_status`, `sources` and `GET /listings`. A second `POST /run` while `running` is `true` returns HTTP 409.

Per-source saved counts: every item in `approved`, `rejected` and `inbox` from `GET /listings` has a `source` field; group by it. The log line `Valid listings: N` counts listings whose links passed a reachability check (failures are logged as `dead_link` and the listing is still saved), so it can be lower than the saved total.

### Backup, stop, update, uninstall
Settings → Export/Import uses `GET /api/export` and `POST /api/import`. Export contains listings, feedback, notes, pipeline status, and criteria, but not credentials. Keep backups private. Stop using `POST /api/quit`. To update, quit and replace the executable; data remains in the data directory. To uninstall, quit and remove the executable. Data and saved credentials remain. Ask before deleting either. To remove saved AI/Firecrawl credentials, use their Settings controls; remove local data only with the person's explicit choice.

## Customize
Fork the project and create a branch. Criteria defaults are in `config.json`; adapters are in `scrapers/` and selected in `run.py`; filtering/ranking live in `pipeline/` and scoring in `vision.py`; UI lives in `favorites.py` and `kanban.py`. Install test dependencies with `python -m pip install -e ".[test]"`, run `python -m pytest -q -p no:cacheprovider`, and build on Windows with `./build.ps1` (requires PyInstaller).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Port busy | Another service uses 5055 | The launcher moves to the next free port. While Scrappy runs, `--doctor` reports the real `port` (read from `runtime.json` in the data folder, which also holds the full `url`); a source run also prints `Scrappy running at <url>`. |
| SmartScreen warning | Release executable is unsigned beta | Verify SHA256 against `SHA256SUMS.txt`; explain **More info → Run anyway** and leave the choice to the person. |
| AI reconnect or degraded status | Provider credential/network unavailable; scorer falls back or asks to reconnect | Check Settings → AI and `GET /api/ai`; reconnect there, or select No AI. |
| `needs_firecrawl` | A source requires Firecrawl or a fallback was unavailable | Explain optional service and cost, ask first, then follow [FIRECRAWL.md](FIRECRAWL.md). |
| A source is `error` | Site response or network/parser issue | Check connectivity and `/run_meta`; retry later and respect the site's access rules. |
| Empty results | Criteria too narrow, sources disabled/unavailable, or no current matching listings | Review criteria and source statuses; adjust criteria with the person, then Run Now. |
| Blank map tiles | Tile service or network unavailable | Check internet access and retry; listing data remains available in the cockpit. |
| Health does not respond | App stopped, startup failed, or using wrong port | Check `--doctor`; start the app and request `http://127.0.0.1:<port>/health`. |

## Reference
### Environment variables and flags
- `SCRAPPY_HOME`: exact replacement data directory.
- `SCRAPPY_PORT`: starting server port; default `5055`.
- `SCRAPPY_NO_BROWSER`: suppress auto-opening browser when set.
- Files in the data folder: `scrappy.db` (all listings and decisions), `config.json` (your settings, no keys), `reports/`, `last_run.log`, `runtime.json` (port, pid and url of the running app; removed on Quit).
- Flags: `--doctor`, `--no-browser`, `--import-db <path>`, `--version`. `--run` is an internal child-process flag.

Scripted state-changing calls and `GET /api/export` must send `Host: 127.0.0.1:<port>`; omit Origin or use a local HTTP origin. Foreign Origin headers receive 403.

Repository: https://github.com/ZoeDepthTokyo/scrappy; releases: https://github.com/ZoeDepthTokyo/scrappy/releases.

### HTTP routes
All routes use `http://127.0.0.1:<port>`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Health and version JSON |
| GET, POST | `/api/setup` | Read/mark setup complete |
| POST | `/api/quit` | Stop server |
| GET | `/api/ai` | Read AI selection/connectivity |
| POST, DELETE | `/api/ai` | Set provider/model/key or reset AI |
| GET, POST, DELETE | `/api/firecrawl` | Check, set, or remove Firecrawl credential |
| GET | `/api/export` | Export backup JSON |
| POST | `/api/import` | Merge backup JSON |
| GET | `/auth/openrouter/start`, `/auth/openrouter/callback/<state>` | OpenRouter OAuth flow |
| POST | `/run` | Start search |
| GET | `/run_meta` | `running` flag, progress log tail, source and AI status |
| GET | `/listings` | Listing groups and counts |
| GET, POST | `/criteria` | Read/update search criteria |
| POST | `/feedback` | Save listing feedback |
| POST | `/note` | Save a listing note |
| POST | `/pipeline_status` | Save status, stars, note, or order |
| POST | `/api/stage` | Set Kanban stage |
| GET | `/favorites`, `/kanban` | Cockpit and Kanban pages |
| GET | `/taste` | Taste profile summary |
| POST | `/cadence` | Set configured cadence days |
| GET | `/FIRECRAWL.md` | Bundled Firecrawl help |
