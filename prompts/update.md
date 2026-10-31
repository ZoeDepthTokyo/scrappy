# Update Scrappy

**When to use:** When I want to install a newer release and keep my local data.

**Copy-paste prompt:**
> Help me update Scrappy from GitHub Releases. Read AGENTS.md first. Guide me to verify the new Scrappy.exe against SHA256SUMS.txt with PowerShell Get-FileHash, quit the running app, and replace the executable. Explain that the data directory and credentials remain. Never delete them or ask me to share credentials.

**Expected result:** The new executable starts with my existing data.

**If it fails:** Follow AGENTS.md Troubleshooting and keep the previous data directory unchanged.
