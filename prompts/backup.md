# Backup and restore

**When to use:** When I want to export my data or restore an existing backup.

**Copy-paste prompt:**
> Help me export or restore Scrappy data. Read AGENTS.md first. Explain Settings → Export and Import and the local routes `GET /api/export` and `POST /api/import`. Remind me that backups contain listings, feedback, notes, pipeline state, and criteria, but not credentials. Help me store the export securely and never upload it or ask me to share its contents.

**Expected result:** A JSON backup is saved securely, or the selected backup is imported successfully.

**If it fails:** Follow AGENTS.md Troubleshooting. Keep the backup private and share only the error message with personal details removed.
