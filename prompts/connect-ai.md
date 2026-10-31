# Connect AI

**When to use:** When I want to choose or reconnect an AI provider.

**Copy-paste prompt:**
> Help me connect my preferred AI provider in Scrappy. Read AGENTS.md first and follow its safety rules. Explain OpenRouter OAuth sign-in via Settings → AI → Sign in (no key copy-paste), provider-key setup for Gemini, OpenAI, Anthropic, or Ollama Cloud through Settings, and No AI. Ask which option I choose. Never ask me to put a key in chat or inspect a key. Confirm using the boolean `connected` field from `GET /api/ai`.

**Expected result:** My selected provider reports connected, or No AI is selected.

**If it fails:** Follow AGENTS.md Troubleshooting; try Settings again or select No AI. Do not request credential contents.
