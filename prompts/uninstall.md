# Uninstall Scrappy

**When to use:** When I want to remove Scrappy from this computer.

**Copy-paste prompt:**
> Help me uninstall Scrappy. Read AGENTS.md first. Explain that quitting and removing Scrappy.exe leaves the local data directory and OS credential-store entries in place. Ask me before deleting local data or removing saved AI/Firecrawl credentials. Never delete these without my explicit choice. Explain how to stop the app with `POST /api/quit` or by closing it.

**Expected result:** Scrappy.exe is removed, and my choices about local data and credentials are respected.

**If it fails:** Do not remove data or credentials. Help me close Scrappy and retry removing only the executable.
