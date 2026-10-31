# Privacy

## What stays on your computer

Scrappy's data directory holds `scrappy.db` (listings, feedback, notes, and pipeline state), user `config.json`, generated files under `reports/`, and operational files such as `last_run.log`, `run_meta.json`, `scraper_health.json`, and `run.lock`. On Windows, the directory is `%LOCALAPPDATA%\Scrappy` (fallback `%USERPROFILE%\AppData\Local\Scrappy`); `SCRAPPY_HOME` replaces it when set. AI and Firecrawl credentials are stored in the operating system credential store, not in the database or exported backup.

## What goes to third parties

- **Listing sites:** enabled source adapters make requests to the listing sites during a search. Those sites receive the request and network information associated with it.
- **Chosen AI provider:** when AI scoring is used, Scrappy sends listing photo URLs and scoring prompts to the configured provider. OpenRouter OAuth also contacts OpenRouter for sign-in and credential exchange.
- **Firecrawl:** when configured and used by a source/fallback, Scrappy sends page URLs to Firecrawl for retrieval/rendering. See [FIRECRAWL.md](FIRECRAWL.md).
- **OpenStreetMap:** the cockpit and Kanban request map tiles from `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.

Each third party operates under its own terms and privacy policy. The app's local server binds to loopback (`127.0.0.1`).

## What is never collected

The repository code provides no Scrappy-operated analytics or telemetry collection route. Credentials are not included in `/api/export`; exports do include listing records, feedback, notes, pipeline status, and criteria. Avoid sharing the data directory or backups because they contain private listing data and notes.
