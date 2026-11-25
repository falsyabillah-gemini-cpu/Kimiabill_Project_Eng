# International Everyday Sci-Tech Shorts — 8-Agent Automation Studio

Automated 8-agent YouTube Shorts / Reels / TikTok studio explaining **everyday science and applied technology** (gadgets, home & urban engineering, materials, transport & energy, bio-sensors) for a global English-speaking audience.

> **Official Channel Brand:** Configured in [`config/brand.yaml`](config/brand.yaml) as **`Bill_Explains` (`@bill_explains`)** with YouTube Channel Banner ([`assets/channel_banner.png`](assets/channel_banner.png)), Profile Picture ([`assets/profile_picture.png`](assets/profile_picture.png)), Shorts Watermark ([`assets/watermark.png`](assets/watermark.png)), and YouTube Player Watermark ([`assets/youtube_branding_watermark.png`](assets/youtube_branding_watermark.png)).
> To regenerate branding assets after editing `config/brand.yaml`, run:
> ```bash
> python -m src.tools.brand_assets --branding-kit
> ```

## Content Schedule (2 Videos / Day)
- **Weekday Slots (Mon–Fri, `EVERYDAY_TECH`):** Relatable daily-life technology across 5 balanced pillars (`gadgets`, `engineering`, `materials`, `energy`, `biotech`) + queued viewer requests.
- **Weekend Slots (Sat–Sun, `TECH_RADAR`):** Latest science & technology breakthroughs translated into everyday impact.
- **Global Prime-Time Cron (`.github/workflows/daily_studio.yml`):**
  - **04:00 WIB** (`16:00 EST` — US afternoon/evening peak)
  - **21:00 WIB** (`09:00 EST` / `14:00 UTC` — EU afternoon & US morning peak)
  - Intentionally offset from `Kimiabill_Project` (`12:00` / `19:00 WIB`) so shared Gemini API quotas do not collide.

## Voiceover (ElevenLabs Primary + Automatic Edge-TTS Fallback)
- Uses **`ELEVENLABS_API_KEY`** + **`ELEVENLABS_VOICE_ID`** when configured and credits are available.
- Automatically pre-checks ElevenLabs credits and cleanly falls back to **`en-US-AndrewNeural`** (Edge-TTS, free, word-level subtitle timestamps) for the full episode if ElevenLabs hits a quota/rate/auth limit.

## Setup GitHub Actions Secrets
Add in **Settings → Secrets and variables → Actions**:
1. **Required (LLM):** `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`)
2. **Recommended (Footage):** `PEXELS_API_KEY`, `PIXABAY_API_KEY`
3. **Voiceover:** `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (from your second ElevenLabs account; falls back automatically to Edge-TTS if limited)
4. **Publishing & Delivery:** `GDRIVE_WEBHOOK_URL`, `GDRIVE_WEBHOOK_TOKEN`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
