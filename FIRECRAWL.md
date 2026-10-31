# Firecrawl

## What
Firecrawl (https://www.firecrawl.dev) is a hosted page-fetching and rendering service. Scrappy can use it to retrieve listing pages that direct source adapters cannot read.

## Why
Firecrawl is optional. `scrapers/pincali.py` uses Firecrawl to render Pincali's JavaScript listing app. `scrapers/inmuebles24.py` tries native retrieval first and uses Firecrawl-rendered HTML as fallback. `scrapers/mercadolibre.py` and `scrapers/propiedades.py` try native retrieval and fall back to Firecrawl when it fails or returns no listings. Without Firecrawl, direct retrieval continues for sources that support it, while Pincali and those fallback results may be unavailable. Firecrawl may incur charges; check current terms at https://www.firecrawl.dev/pricing. No prices are quoted here.

## How
1. Decide whether the additional source coverage is useful; enabling is optional.
2. Sign up at https://www.firecrawl.dev.
3. Create an API key at https://www.firecrawl.dev/app/api-keys.
4. In Scrappy, open Settings → Firecrawl and enter the key there. Do not send it to an agent or save it in a file.
5. Confirm with `GET /api/firecrawl`; a configured key returns `{"connected":true}`.
6. Remove it in Settings → Firecrawl, or use `DELETE /api/firecrawl`. Rotate by removing the old key, creating a replacement in Firecrawl, and entering the replacement in Settings. Confirm again.

## Troubleshooting
- `connected:false`: key was not saved or is unavailable in the OS credential store. Re-enter it in Settings and check with `GET /api/firecrawl`.
- `needs_firecrawl` in `/run_meta`: the configured source needs Firecrawl or its fallback could not run. Check the credential and retry the search.
- Source still errors with Firecrawl configured: check connectivity and source status after a later retry; external sites and service responses can change.
- Credential store unavailable: check Windows credential-store access and retry through Settings. Never put the key in `config.json`.

## Security
Scrappy stores the Firecrawl key in the operating system credential store under the Scrappy service, with the account name `firecrawl`. It is not part of exported backups. Never place the key in files, source control, logs, or chat. Treat search URLs and listing data sent to Firecrawl as third-party data.

## For AI agents
Explain Firecrawl's purpose, source coverage, optional nature, and possible cost before enabling it. Ask the person before connecting it. Guide them to create a key and enter it in Settings → Firecrawl; never ask them to paste it into chat or inspect it. Verify only the boolean response from `GET /api/firecrawl`. Follow the safety instructions in [AGENTS.md](AGENTS.md).
