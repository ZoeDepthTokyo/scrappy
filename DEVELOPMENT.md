# Development

Repository: https://github.com/ZoeDepthTokyo/scrappy; releases: https://github.com/ZoeDepthTokyo/scrappy/releases.

## Architecture map

| Module | Responsibility |
|---|---|
| `launcher.py` | CLI, local server startup, port selection, browser launch, doctor/version, DB import |
| `feedback_server.py` | Flask routes, configuration, backup, search launch, listing and feedback APIs |
| `kanban.py` | `/kanban` view and `/api/stage` persistence |
| `run.py` | Search orchestration and report generation |
| `scrapers/` | Source adapters, fetching, parsing, source health |
| `pipeline/` | Deduplication, filtering, ranking |
| `vision.py`, `ai_providers.py` | Listing scoring and provider credential/scoring integration |
| `favorites.py` | Cockpit HTML and browser interactions |
| `db.py` | SQLite schema and persistence |
| `paths.py`, `config_loader.py` | Data paths and bundled-default/user-override configuration |

`feedback_server.py` routes: `/health`, `/api/setup`, `/api/quit`, `/api/ai`, `/api/firecrawl`, `/api/export`, `/api/import`, `/auth/openrouter/start`, `/auth/openrouter/callback/<state>`, `/feedback`, `/`, `/favorites`, `/cadence`, `/criteria`, `/run`, `/listings`, `/run_meta`, `/taste`, `/note`, and `/pipeline_status`. `kanban.py` registers `/kanban` and `/api/stage` on the same Flask app.

## Run data flow

`POST /run` starts a child process and writes output to `last_run.log`. `run.py` loads bundled `config.json` merged with the data-directory `config.json`, constructs enabled adapters from `config["sources"]`, fetches each independently, and records source errors/status. The candidate pipeline prefilters, deduplicates against current SQLite records, applies hard criteria, scores with AI or metadata, and saves listings. It writes `run_meta.json` with `ai_status` and `sources`, creates `reports/report_<date>.html`, and regenerates `reports/favorites.html`.

## Tests

Install test dependencies with `python -m pip install -e ".[test]"`. Run all tests with `python -m pytest -q -p no:cacheprovider`. The `tests/conftest.py` fixture sets `SCRAPPY_HOME` to a temporary test directory, disables socket connections, and isolates app storage. The publishable-text check is `python -m pytest -q -p no:cacheprovider tests/test_public_hygiene.py`; it scans tracked and untracked non-ignored files for credential patterns, prohibited terms, and absolute Windows paths. Do not put credentials or private data in fixtures.

## Build Scrappy.exe

On Windows, install the project and PyInstaller, then run `./build.ps1`. It invokes `python -m PyInstaller scrappy.spec --noconfirm`; the spec bundles `config.json` and `FIRECRAWL.md` and builds `dist/Scrappy.exe` without a console window.

## Add a scraper

1. Add an adapter in `scrapers/`, implementing the common `RawListing` contract from `scrapers/base.py` and setting a stable `SOURCE_NAME`.
2. Add its enablement key to `config.json` under `sources` and register its class/key in the adapter list in `run.py`.
3. Keep failures isolated per source and return source health through the existing adapter mechanism.
4. Add fixture-based tests for parsing and mocked network behavior; do not rely on real sites or credentials.
5. Run the relevant tests and the full suite.

## Add an AI provider

Provider metadata and dispatch are in `ai_providers.py`. Add provider/model handling there and in the Settings UI in `favorites.py`; ensure credentials are accessed through the OS credential store and never written to config, logs, test output, or API responses. Update provider validation in `feedback_server.py` as needed. Add mocked tests for connection and scoring, then run the full suite.

## Release process

`.github/workflows/ci.yml` runs on branch pushes, pull requests, and `v*` tags. It installs Python 3.12, runs the suite and public-hygiene test, and scans with Gitleaks. A `v*` tag triggers the Windows release job after tests: it builds with `build.ps1`, scans `dist`, computes SHA256 for `dist/Scrappy.exe`, writes `dist/SHA256SUMS.txt`, and attaches both files to a GitHub Release.
