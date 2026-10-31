# Install Scrappy

**When to use:** When I want help installing or starting the Windows release.

**Copy-paste prompt:**
> Help me install Scrappy from its GitHub Releases page. First read the repository's AGENTS.md and follow it as the operating guide. Guide me to download Scrappy.exe and SHA256SUMS.txt, verify the executable in PowerShell with Get-FileHash, and explain the unsigned beta SmartScreen prompt so I choose whether to continue. Never ask for credentials in chat. Confirm the local health check.

**Expected result:** Scrappy starts, the release hash has been checked, and `GET /health` responds successfully.

**If it fails:** Follow AGENTS.md Troubleshooting. Share only non-sensitive error text; do not share local database contents or credentials.
