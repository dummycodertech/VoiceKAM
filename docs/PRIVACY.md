# Privacy & Data Handling

VoiceKAM is designed for synthetic demonstration and rapid diagnostics with minimal data retention:

## 1. Synthetic Built-In Scenarios
Scenarios A–D (`app/data/scenarios.py`) are deterministically generated fictional store datasets. They contain zero real merchant, customer, or marketplace records.

## 2. Custom Seller Uploads (CSV / JSON)
If you upload your own dataset via **"Use my own data"** (`/api/data/validate` or `/api/session/start`):
- **RAM-only storage:** Validated rows are parsed directly into memory inside the session's `SellerContext` and bounded `UploadedDatasetStore`. Nothing is written to disk, databases, or server logs.
- **Automatic expiry:** Uploaded datasets and session histories automatically expire after `SESSION_TTL_SECONDS` (default **2 hours**) or via LRU eviction (`MAX_SESSIONS = 200`), and disappear immediately on server restart.
- **Safety warning:** Do **not** upload confidential or personally identifiable merchant data. Free-tier external LLM APIs (Groq, Google Gemini, OpenRouter) configured by the host receive compact session prompts and aggregated tool summaries during active calls and may retain/use free-tier prompts according to their respective provider terms.

## 3. Speech & Voice Processing
- **Web Speech API (Chrome/Edge):** Browser-native speech recognition and browser TTS run according to browser vendor privacy settings.
- **Optional Groq Whisper fallback (`/api/stt`):** Only invoked when audio is recorded for server transcription; audio bytes are forwarded directly in memory to Groq and never persisted locally.
- **Neural TTS (`/api/tts`):** Text segments are cached in RAM (`TTS_CACHE_ENABLED=true`, LRU bounded at 200 entries) and synthesized via Microsoft Edge TTS (`edge-tts`) or emergency Gemini TTS when configured.

## 4. Telemetry & Cookies
VoiceKAM does not set analytics tracking cookies, browser fingerprinting scripts, or third-party ad pixels.
