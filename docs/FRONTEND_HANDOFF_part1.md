# Snapdeal Virtual KAM ("Riya") — Frontend Redesign Handoff Packet (Part 1 of 2)

> **Read-only reference specification (Part 1 of 2: Architecture Overview & Verbatim Frontend Sources)** generated directly from the live codebase and real captured runs (`<REDACTED>` applied to all keys, tokens, org/project IDs, and account names).
> Upload **both** `FRONTEND_HANDOFF_part1.md` (Sections 1–2) and `FRONTEND_HANDOFF_part2.md` (Sections 3–9) together to rebuild `frontend/` outside this repo and drop it back in with **zero backend changes** (accounting for post-handoff Section 9 backend improvements).

### Cross-Part Contents
- **Part 1 (`FRONTEND_HANDOFF_part1.md` — this file):**
  - `## 1. Overview` — FastAPI stack, `/` & `/static/*` mount, headers/CORS/CSP, run commands, env vars, `frontend/` tree & line counts.
  - `## 2. Verbatim source of EVERY frontend file` — complete untruncated `index.html`, `styles.css`, `app.js`, `audio.js`, `kam_brain.js` (`2,951` lines / `86,171` bytes total).
- **Part 2 (`FRONTEND_HANDOFF_part2.md`):**
  - `## 3. API contract (the interface I must not break)`
  - `## 4. Voice-loop behaviour spec`
  - `## 5. DOM and state contract`
  - `## 6. Data shapes rendered by the KAM Brain panel`
  - `## 7. Known issues and limits`
  - `## 8. Captured real session logs` (`docs/captures/*.json`)
  - `## 9. Backend gaps that affect the UI (fix these AFTER the packet is written, then update the contract)`

---

## 1. Overview

### 1.1 Tech Stack & Serving Architecture
- **Backend:** Python 3.12 (`requires-python >=3.11`), FastAPI (`>=0.115.0`), Uvicorn (`uvicorn[standard]>=0.32.0`, `--workers 1`), `httpx>=0.27.2`, `google-genai>=1.0.0`, `edge-tts>=7.0.0`, `pydantic>=2.9.0`, `pydantic-settings>=2.6.0`, `python-dotenv>=1.0.1`, `python-multipart>=0.0.12`.
- **Current Frontend:** Zero-bundler Vanilla HTML5 + CSS3 + ES6 Modules (`<script type="module" src="/static/app.js"></script>`). No npm/Vite/Webpack step; any drop-in replacement either ships plain static files to `frontend/` OR compiles a production build (`index.html` + JS/CSS referenced under `/static/...`) directly into `frontend/`.
- **Static Mount & URL Routes (`app/main.py`):**
  - `GET /` -> `FileResponse("frontend/index.html")` (returns JSON `{"status":"ok","message":"Frontend not built yet; API ready."}` if `frontend/index.html` is absent).
  - `GET /static/{path}` -> Starlette `StaticFiles(directory="frontend")`, mounted at `app.mount("/static", ..., name="static")`.
  - `GET /favicon.ico` -> empty `204 No Content`.
  - All backend API endpoints live under `/api/*` (Section 3).
- **Cache Headers, CORS, & CSP:**
  - **Static (`/static/*`) & Root (`/`):** Default Starlette `StaticFiles` / `FileResponse` headers (`ETag`, `Last-Modified`, no custom `Cache-Control` `max-age`).
  - **SSE Streams (`/api/chat`, `/api/session/{id}/greeting`):** Explicitly return `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Content-Type: text/event-stream; charset=utf-8`.
  - **TTS (`/api/tts`):** Returns `X-TTS-Provider`, `X-TTS-Voice`, `X-TTS-Cached` (`"true"` | `"false"`).
  - **CORS:** `CORSMiddleware` with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` (no `allow_credentials=True`). Note `Vary: Origin` appears on responses when CORS middleware runs.
  - **CSP:** **No `Content-Security-Policy` headers** are emitted by FastAPI (`frontend/index.html` loads Google Fonts CDN and uses inline `data:image/svg+xml` favicon + inline `onclick` on dismiss button).

### 1.2 How to Run Locally & Test
- **Single-worker requirement:** Always run with **`--workers 1`** because `SessionStore`, `LLMRouter` cooldowns, and `TTSService._cache` live strictly in process RAM.
- **Direct Local Server (port `7860`):**
  ```powershell
  .\run.ps1 -LocalOnly
  # or:
  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 7860 --workers 1
  ```
- **Local + Cloudflare Quick Tunnel (public `https://*.trycloudflare.com` for remote mic testing):**
  ```powershell
  .\run.ps1          # Windows PowerShell (Unix: ./run.sh)
  ```
- **Docker:** `docker build -t snapdeal-virtual-kam .` then `docker run -p 7860:7860 --env-file .env -e PORT=7860 snapdeal-virtual-kam`.
- **Render Free Blueprint (`render.yaml`):** `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`, health check `/api/health`. Note free tier sleeps after 15 min idle (512 MB RAM; wipes in-memory sessions on sleep/restart).
- **Test & Verification Commands:**
  - Full pytest suite (unit + integration + Playwright Chrome E2E): `.\.venv\Scripts\pytest.exe -v`
  - Live HTTP/SSE/TTS smoke check: `.\.venv\Scripts\python.exe scripts\smoke_test.py --url http://127.0.0.1:7860`

### 1.3 Environment Flags & Ports Affecting the UI (`app/config.py` & `.env.example`)
| Env Variable | Default | Effect on API / UI |
| :--- | :--- | :--- |
| `PORT` | `7860` | Uvicorn listening port (`run.ps1`, `run.sh`, `Dockerfile`, `Makefile`). |
| `GROQ_API_KEY` | `""` | Enables Groq models in `/api/health -> active_llm_chain`, `/api/health -> providers_configured.groq`, and `/api/stt` (`features.whisper_stt_fallback = true`). If all keys are empty & `ALLOW_MOCK_LLM=false`, UI shows `#errorBanner`. |
| `GROQ_MODELS` | `openai/gpt-oss-20b,openai/gpt-oss-120b,qwen/qwen3.6-27b,qwen/qwen3.8-27b` | Startup queries Groq `GET /models` and prunes missing models (e.g. `qwen/qwen3.6-27b` pruned automatically). |
| `GROQ_REASONING_EFFORT` | `low` | Passed to OpenAI-compatible Groq requests. |
| `GEMINI_API_KEY` | `""` | Enables Gemini fallback LLM models & Gemini TTS startup smoke check. |
| `GEMINI_MODELS` | `gemini-3.5-flash-lite,gemini-3.6-flash` | Ordered Gemini LLM fallback chain. |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | `""` / `openrouter/free` | Optional Fallback 2 LLM. |
| `PROVIDER_COOLDOWN_SECONDS` | `60` | Default cooldown on HTTP 429 when `Retry-After` header is absent. |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Model used by `POST /api/stt` (currently unused by `frontend/`). |
| `TTS_PROVIDER` | `auto` | Default TTS strategy (`auto` \| `edge-tts` \| `gemini-tts` \| `browser`). Exposed in `/api/health -> tts_config.provider`. |
| `TTS_VOICE_EN` / `TTS_VOICE_HI` | `en-IN-NeerjaNeural` / `hi-IN-SwaraNeural` | Default Edge neural voices returned in `X-TTS-Voice`. |
| `GEMINI_TTS_MODELS` / `GEMINI_TTS_VOICE` | `gemini-3.8-flash-tts,gemini-2.5-flash-preview-tts` / `Aoede` | Emergency Tier-2 TTS tested at boot (currently fails with HTTP 400 on boot; see Section 7). |
| `TTS_CACHE_ENABLED` | `true` | Enables 200-entry RAM LRU audio cache in `TTSService`. |
| `LLM_TIMEOUT_SECONDS` | `12.0` | Per-provider request/stream timeout before failing over. |
| `ENABLE_GEMINI_LIVE` | `false` | Exposed in `/api/health -> features.enable_gemini_live` (reserved flag; not implemented in UI or backend). |
| `ALLOW_MOCK_LLM` | `false` | Appends `mock:deterministic-test-kam` to `LLMRouter` for deterministic offline testing. |

### 1.4 File Tree of `frontend/`
Every file currently under `frontend/` (no subdirectories, local fonts, images, audio files, or `manifest.json` exist):
```text
frontend/
├── index.html      (168 lines, 7,960 bytes)
├── styles.css      (1239 lines, 25,569 bytes)
├── app.js          (642 lines, 20,607 bytes)
├── audio.js        (238 lines, 7,233 bytes)
└── kam_brain.js    (664 lines, 24,802 bytes)
Total: 5 files, 2,951 lines, 86,171 bytes.
```


---

## 2. Verbatim source of EVERY frontend file

### 2.1 `frontend/index.html`
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Snapdeal Virtual KAM — Riya</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='16' fill='%23ff6647'/><text x='50%25' y='54%25' dominant-baseline='middle' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='bold' font-size='17'>R</text></svg>" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="/static/styles.css" />
</head>
<body data-completed-streams="0">
  <!-- Full-Screen Tidewell Fluted Reeded Glass & Sunset Backdrop -->
  <div class="tidewell-canvas" aria-hidden="true">
    <div class="tidewell-glow"></div>
    <div class="tidewell-flutes"></div>
  </div>

  <!-- Top Navigation Bar (Tidewell Layout: Brand Left | Phase Pill Capsule Center | Badges Right) -->
  <header class="tidewell-header">
    <div class="brand-lockup">
      <span class="brand-wave" aria-hidden="true">~</span>
      <span class="brand-name">Snapdeal KAM · Riya</span>
      <span class="brand-divider" aria-hidden="true">/</span>
      <span class="brand-seller">Kumar Home &amp; Kitchen (60 SKUs)</span>
    </div>

    <!-- Center Floating Capsule Nav (Tidewell Top Nav) -->
    <nav id="phaseStrip" class="tidewell-nav-capsule" aria-label="Conversation Phase">
      <span class="phase-pill active" data-phase="GREETING">Greeting</span>
      <span class="phase-pill" data-phase="UNDERSTAND">Understand</span>
      <span class="phase-pill" data-phase="CLARIFY">Clarify</span>
      <span class="phase-pill" data-phase="REVIEW_COMMITMENTS">Review</span>
      <span class="phase-pill" data-phase="DIAGNOSE">Diagnose</span>
      <span class="phase-pill" data-phase="RECOMMEND">Recommend</span>
      <span class="phase-pill" data-phase="COMMIT">Commit</span>
      <span class="phase-pill" data-phase="SUMMARY">Summary</span>
      <span class="phase-pill" data-phase="CLOSE">Close</span>
    </nav>

    <div class="header-pills">
      <span id="ttsBadge" class="meta-pill">TTS: Auto</span>
      <span class="meta-pill meta-pill-proto">PROTOTYPE · MOCK DATA ONLY</span>
    </div>
  </header>

  <!-- Hidden System Banners (Playwright Contract) -->
  <div id="browserBanner" class="alert-banner hidden" role="status">
    <span>Chrome or Edge is recommended for live Web Speech STT. Use the Debug Input box below in other browsers.</span>
  </div>
  <div id="errorBanner" class="alert-banner alert-danger hidden" role="alert">
    <span id="errorBannerText"></span>
    <button class="pill-btn dock-mini-btn" type="button" onclick="document.getElementById('errorBanner').classList.add('hidden')">Dismiss</button>
  </div>

  <!-- Main Spatial Split Layout -->
  <main class="tidewell-stage">
    <!-- LEFT: Open Editorial Voice Hero (Zero Outer Wrapper Boxes) -->
    <section class="hero-voice-column" aria-label="Voice Call Console">
      <div class="hero-top-cluster">
        <!-- Acoustic Voice Core + Call Controls -->
        <div class="voice-orb-cluster">
          <div class="tidewell-orb-wrap">
            <div class="orb-halo" aria-hidden="true"></div>
            <button id="micOrb" class="orb-sphere" data-state="idle" type="button" aria-label="Microphone status orb">Ready</button>
          </div>

          <div class="voice-cluster-meta">
            <div class="kicker-row">
              <span class="live-dot" aria-hidden="true"></span>
              <span class="kicker-text">VOICE CALL CONSOLE</span>
              <span id="callTimer" class="timer-tag">00:00</span>
            </div>
            <p id="orbLabel" class="orb-status-line">Click Start Call to talk with Riya</p>
            <div class="hero-cta-row">
              <button id="btnStartCall" type="button" class="pill-btn pill-btn-coral">Start Call ↗</button>
              <button id="btnInterrupt" type="button" class="pill-btn pill-btn-frost" disabled>Tap to Interrupt (Space)</button>
              <button id="btnEndCall" type="button" class="pill-btn pill-btn-ghost" disabled>End Call</button>
            </div>
          </div>
        </div>

        <!-- Big Tidewell Editorial Headline -->
        <h1 class="tidewell-display-title">
          Revenue recovery,<br />live by voice.
        </h1>
        <p class="tidewell-display-sub">
          Zero-math deterministic store diagnosis, live bilingual voice barge-in, and closed-loop seller commitments—no passive FAQ bots.
        </p>

        <!-- Floating Live Transcript Stream -->
        <div class="transcript-glass-box">
          <div class="transcript-header">
            <span>LIVE TRANSCRIPT</span>
            <span>EN · HI · HINGLISH</span>
          </div>
          <div id="captionsBox" class="transcript-feed" aria-live="polite">
            <div class="captions-empty">Captions will appear here as you and Riya speak...</div>
          </div>
        </div>
      </div>

      <!-- Bottom Compact Control Dock -->
      <div class="control-dock-glass">
        <div class="dock-selects-row">
          <label class="dock-field">
            <span>Mode</span>
            <select id="selMode">
              <option value="inbound">Inbound Call</option>
              <option value="proactive">Proactive Call</option>
              <option value="followup">3-Day Follow-Up</option>
            </select>
          </label>
          <label class="dock-field">
            <span>Language</span>
            <select id="selLanguage">
              <option value="en-IN">English (India)</option>
              <option value="hinglish">Hinglish</option>
              <option value="hi-IN">Hindi (हिंदी)</option>
            </select>
          </label>
          <label class="dock-field">
            <span>Voice Engine</span>
            <select id="selTtsProvider">
              <option value="auto">Auto (Edge/Browser)</option>
              <option value="edge-tts">Edge Neural</option>
              <option value="browser">Browser TTS</option>
            </select>
          </label>
        </div>

        <div class="dock-actions-bar">
          <label class="bargein-toggle">
            <input type="checkbox" id="chkHeadphones" />
            <span>Headphones Barge-In</span>
          </label>
          <div class="dock-btns">
            <button id="btnFollowup" type="button" class="pill-btn dock-mini-btn">Simulate +3d Follow-Up</button>
            <button id="btnToggleDebug" type="button" class="pill-btn dock-mini-btn">Debug Input</button>
          </div>
        </div>

        <div id="debugBox" class="dock-debug-row hidden">
          <input id="txtDebugInput" type="text" placeholder="Type seller line and press Enter..." autocomplete="off" />
          <button id="btnSendDebug" type="button" class="pill-btn pill-btn-coral">Send</button>
        </div>

        <div class="dock-telemetry-strip">
          <span>STT→Token: <strong id="latencySttToToken">-</strong></span>
          <span>STT→Audio: <strong id="latencySttToAudio">-</strong></span>
          <span>Model: <strong id="latencyProvider">-</strong></span>
        </div>
      </div>
    </section>

    <!-- RIGHT: Floating Glass Intelligence Deck -->
    <section class="kam-deck-column" aria-label="KAM Brain Telemetry Deck">
      <div id="kamBrainRoot" class="kam-deck-scroll">
        KAM Brain live diagnostics panel will hydrate here.
      </div>
      <div id="summaryCardSection" class="kam-summary-deck hidden"></div>
    </section>
  </main>

  <script type="module" src="/static/app.js"></script>
</body>
</html>

```

### 2.2 `frontend/styles.css`
```css
/* ==========================================================================
   TIDEWELL FLUTED GLASS & CORAL AURORA — MASTER STYLESHEET (COMPLETE RESET)
   ========================================================================== */

:root {
  --coral: #ff6647;
  --coral-hover: #ff785c;
  --coral-soft: rgba(255, 102, 71, 0.2);
  --coral-border: rgba(255, 102, 71, 0.44);
  --amber: #ffb057;
  --amber-soft: rgba(255, 176, 87, 0.18);
  --amber-border: rgba(255, 176, 87, 0.42);
  --sage: #42e6a4;
  --sage-soft: rgba(66, 230, 164, 0.16);
  --sage-border: rgba(66, 230, 164, 0.4);
  --sky-soft: rgba(96, 165, 250, 0.16);
  --sky-border: rgba(96, 165, 250, 0.38);

  --glass-bg: rgba(22, 31, 44, 0.46);
  --glass-inner: rgba(255, 255, 255, 0.065);
  --glass-border: rgba(255, 255, 255, 0.16);
  --glass-border-subtle: rgba(255, 255, 255, 0.09);
  --glass-specular: rgba(255, 255, 255, 0.22);

  --text-white: #ffffff;
  --text-soft: rgba(255, 255, 255, 0.8);
  --text-muted: rgba(255, 255, 255, 0.56);

  --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  padding: 0;
  width: 100%;
  min-height: 100vh;
  background: #101b29;
  color: var(--text-white);
  font-family: var(--font-sans);
  font-size: 13.5px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
}

/* ==========================================================================
   1. TIDEWELL FLUTED REEDED GLASS & SUNSET GRADIENT CANVAS
   ========================================================================== */

.tidewell-canvas {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
  background: linear-gradient(125deg, #122134 0%, #1c2f44 26%, #6e3b3a 52%, #3a2730 78%, #111c2b 100%);
}

.tidewell-glow {
  position: absolute;
  inset: -10%;
  background:
    radial-gradient(circle at 46% 50%, rgba(242, 98, 68, 0.82) 0%, rgba(228, 122, 72, 0.44) 32%, transparent 66%),
    radial-gradient(circle at 62% 34%, rgba(255, 160, 94, 0.42) 0%, transparent 55%),
    radial-gradient(circle at 16% 24%, rgba(44, 78, 114, 0.78) 0%, transparent 62%);
  filter: blur(36px);
}

/* Crisp Vertical Fluted Glass Ribs (11px repeat, high optical contrast) */
.tidewell-flutes {
  position: absolute;
  inset: 0;
  background-image: repeating-linear-gradient(
    90deg,
    rgba(255, 255, 255, 0.11) 0px,
    rgba(255, 255, 255, 0.11) 1px,
    rgba(255, 255, 255, 0.02) 1px,
    rgba(255, 255, 255, 0.01) 5px,
    rgba(0, 0, 0, 0.28) 5px,
    rgba(0, 0, 0, 0.38) 10px,
    rgba(255, 255, 255, 0.05) 10px,
    rgba(255, 255, 255, 0.05) 11px
  );
}

/* ==========================================================================
   2. TOP BAR (TIDEWELL FLOATING HEADER + CAPSULE NAV)
   ========================================================================== */

.tidewell-header {
  position: relative;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  max-width: 1640px;
  margin: 0 auto;
  padding: 16px 32px 10px;
}

.brand-lockup {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14.5px;
  font-weight: 700;
  letter-spacing: -0.02em;
  white-space: nowrap;
  flex-shrink: 0;
}

.brand-wave {
  font-size: 22px;
  color: var(--coral);
  line-height: 1;
}

.brand-divider {
  opacity: 0.35;
}

.brand-seller {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-soft);
}

.tidewell-nav-capsule {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  padding: 4px 6px;
  border-radius: 999px;
  background: rgba(19, 28, 41, 0.54);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--glass-border);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.22), inset 0 1px 0 var(--glass-specular);
}

.phase-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.72);
  white-space: nowrap;
  transition: all 0.2s ease;
}

.phase-pill.active,
.phase-pill.current,
.phase-pill[data-active="true"] {
  background: rgba(255, 255, 255, 0.16);
  color: #ffffff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2);
}

.phase-pill.active::before,
.phase-pill.current::before,
.phase-pill[data-active="true"]::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--coral);
  box-shadow: 0 0 8px var(--coral);
}

.phase-pill.done,
.phase-pill.completed {
  color: var(--sage);
}

.header-pills {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.meta-pill {
  padding: 5px 12px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  background: rgba(19, 28, 41, 0.48);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--glass-border);
}

.meta-pill-proto {
  background: rgba(255, 176, 87, 0.18);
  border-color: rgba(255, 176, 87, 0.45);
  color: #ffe2ba;
}

/* System Alert Banners */
.alert-banner {
  position: relative;
  z-index: 10;
  max-width: 1640px;
  width: calc(100% - 64px);
  margin: 6px auto 0;
  padding: 10px 16px;
  border-radius: 14px;
  font-size: 12.5px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: rgba(255, 176, 87, 0.18);
  border: 1px solid rgba(255, 176, 87, 0.44);
  color: #ffe6c2;
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}

.alert-danger {
  background: rgba(239, 68, 68, 0.22);
  border-color: rgba(239, 68, 68, 0.48);
  color: #fecaca;
}

/* ==========================================================================
   3. SPATIAL 2-COLUMN STAGE (OPEN HERO LEFT + FLOATING DECK RIGHT)
   ========================================================================== */

.tidewell-stage {
  position: relative;
  z-index: 5;
  display: grid;
  grid-template-columns: minmax(410px, 42%) minmax(0, 58%);
  gap: 34px;
  max-width: 1640px;
  margin: 0 auto;
  padding: 12px 32px 28px;
  align-items: start;
}

/* LEFT COLUMN: ZERO OUTER BOXES — FLOATS ON RAW FLUTED SUNSET */
.hero-voice-column {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: calc(100vh - 104px);
  padding-top: 8px;
  gap: 14px;
}

.hero-top-cluster {
  display: flex;
  flex-direction: column;
}

.voice-orb-cluster {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-bottom: 20px;
}

.tidewell-orb-wrap {
  position: relative;
  width: 96px;
  height: 96px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
}

.orb-halo {
  position: absolute;
  inset: -6px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.25);
  background: radial-gradient(circle, rgba(255, 102, 71, 0.24) 0%, transparent 70%);
  pointer-events: none;
  transition: transform 0.3s ease, border-color 0.3s ease;
}

.orb-sphere,
#micOrb {
  position: relative;
  z-index: 2;
  width: 84px;
  height: 84px;
  padding: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-family: var(--font-sans);
  font-weight: 700;
  font-size: 12px;
  color: #ffffff;
  cursor: pointer;
  user-select: none;
  background:
    radial-gradient(circle at 30% 25%, rgba(255, 255, 255, 0.56) 0%, transparent 42%),
    radial-gradient(circle at 50% 55%, #ff6647 0%, #c83b2b 75%, #1d2636 100%);
  border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 0 12px 32px rgba(255, 102, 71, 0.46), inset 0 2px 8px rgba(255, 255, 255, 0.45);
  transition: transform 0.3s ease, box-shadow 0.3s ease, background 0.3s ease;
}

#micOrb[data-state="idle"]:hover {
  transform: translateY(-2px) scale(1.04);
}

#micOrb[data-state="listening"] {
  background:
    radial-gradient(circle at 30% 25%, rgba(255, 255, 255, 0.52) 0%, transparent 44%),
    radial-gradient(circle at 50% 52%, rgba(66, 230, 164, 0.88) 0%, rgba(16, 68, 56, 0.94) 82%);
  box-shadow: 0 0 38px rgba(66, 230, 164, 0.52), inset 0 2px 8px rgba(255, 255, 255, 0.42);
  animation: pulseOrb 1.5s ease-in-out infinite alternate;
}

#micOrb[data-state="thinking"] {
  background:
    radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.5) 0%, transparent 44%),
    radial-gradient(circle at 50% 52%, rgba(255, 176, 87, 0.9) 0%, rgba(84, 46, 18, 0.94) 82%);
  box-shadow: 0 0 38px rgba(255, 176, 87, 0.5), inset 0 2px 8px rgba(255, 255, 255, 0.4);
  animation: pulseOrb 1.1s ease-in-out infinite alternate;
}

#micOrb[data-state="speaking"] {
  animation: pulseOrb 1.2s ease-in-out infinite alternate;
}

.tidewell-orb-wrap:has(#micOrb[data-state="speaking"]) .orb-halo,
.tidewell-orb-wrap:has(#micOrb[data-state="listening"]) .orb-halo {
  animation: haloRipple 1.6s ease-out infinite;
}

@keyframes pulseOrb {
  0% {
    transform: scale(0.97);
  }
  100% {
    transform: scale(1.06);
    box-shadow: 0 0 42px rgba(255, 102, 71, 0.78);
  }
}

@keyframes haloRipple {
  0% {
    transform: scale(0.96);
    opacity: 0.85;
  }
  100% {
    transform: scale(1.24);
    opacity: 0;
  }
}

.voice-cluster-meta {
  min-width: 0;
}

.kicker-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--coral);
  box-shadow: 0 0 8px var(--coral);
}

.kicker-text {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.86);
}

.timer-tag {
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
  font-weight: 700;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
}

.orb-status-line {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--text-soft);
}

.hero-cta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pill-btn,
button {
  font-family: var(--font-sans);
  font-size: 12px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 999px;
  border: 1px solid var(--glass-border);
  background: rgba(20, 30, 44, 0.52);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  color: #ffffff;
  cursor: pointer;
  transition: all 0.2s ease;
}

.pill-btn:hover:not(:disabled),
button:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.34);
  transform: translateY(-1px);
}

.pill-btn-coral,
#btnStartCall:not(:disabled) {
  background: var(--coral) !important;
  border-color: rgba(255, 175, 158, 0.65) !important;
  color: #ffffff !important;
  box-shadow: 0 8px 24px rgba(255, 102, 71, 0.45);
}

.pill-btn-coral:hover:not(:disabled),
#btnStartCall:hover:not(:disabled) {
  background: var(--coral-hover) !important;
}

.pill-btn-ghost {
  background: rgba(239, 68, 68, 0.16);
  border-color: rgba(239, 68, 68, 0.4);
  color: #fecaca;
}

.pill-btn:disabled,
button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
  transform: none;
}

/* Iconic Tidewell Display Typography */
.tidewell-display-title {
  margin: 0 0 10px;
  font-size: clamp(34px, 3.2vw, 46px);
  font-weight: 600;
  letter-spacing: -0.04em;
  line-height: 1.04;
  color: #ffffff;
  text-shadow: 0 10px 30px rgba(0, 0, 0, 0.24);
}

.tidewell-display-sub {
  margin: 0 0 16px;
  max-width: 500px;
  font-size: 13.5px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.84);
}

/* Floating Transcript Card */
.transcript-glass-box {
  background: rgba(18, 27, 40, 0.42);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--glass-border);
  border-radius: 20px;
  padding: 13px 15px;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.22), inset 0 1px 0 var(--glass-specular);
}

.transcript-header {
  display: flex;
  justify-content: space-between;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.transcript-feed,
#captionsBox {
  height: 148px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}

.captions-empty {
  color: var(--text-muted);
  font-size: 12.5px;
}

.caption-line {
  padding: 9px 13px;
  border-radius: 14px;
  font-size: 12.5px;
  line-height: 1.44;
  max-width: 90%;
}

.caption-line.kam {
  align-self: flex-start;
  background: rgba(255, 255, 255, 0.09);
  border: 1px solid rgba(255, 255, 255, 0.14);
}

.caption-line.seller {
  align-self: flex-end;
  background: rgba(255, 102, 71, 0.24);
  border: 1px solid rgba(255, 102, 71, 0.45);
}

.caption-line.interim {
  opacity: 0.68;
  font-style: italic;
}

.caption-who {
  display: block;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(255, 255, 255, 0.66);
  margin-bottom: 2px;
}

/* Bottom Compact Control Dock */
.control-dock-glass {
  background: rgba(15, 22, 33, 0.48);
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
  border: 1px solid var(--glass-border);
  border-radius: 20px;
  padding: 12px 15px;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.22), inset 0 1px 0 var(--glass-specular);
}

.dock-selects-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 9px;
}

.dock-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-soft);
  min-width: 0;
}

select,
input[type="text"] {
  width: 100%;
  min-width: 0;
  padding: 6px 9px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #ffffff;
  font-family: var(--font-sans);
  font-size: 12px;
  outline: none;
}

select:focus,
input[type="text"]:focus {
  border-color: var(--coral);
}

select option {
  background: #142030;
  color: #ffffff;
}

.dock-actions-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 7px;
  border-top: 1px solid var(--glass-border-subtle);
}

.bargein-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-soft);
  cursor: pointer;
}

.dock-btns {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.dock-mini-btn {
  padding: 5px 11px;
  font-size: 11px;
}

.dock-debug-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.dock-debug-row input[type="text"] {
  flex: 1;
}

.dock-telemetry-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 8px;
  padding-top: 7px;
  border-top: 1px solid var(--glass-border-subtle);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}

.dock-telemetry-strip strong {
  color: var(--text-white);
  font-weight: 600;
}

/* ==========================================================================
   4. RIGHT COLUMN: TIDEWELL FLOATING GLASS CARD DECK
   ========================================================================== */

.kam-deck-column {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.kam-deck-scroll,
#kamBrainRoot {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.kb-status-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 2px 2px 0;
}

/* Frosted Glass Deck Cards */
.kb-bento-card,
.kb-block,
.summary-card {
  background: var(--glass-bg);
  backdrop-filter: blur(24px) saturate(140%);
  -webkit-backdrop-filter: blur(24px) saturate(140%);
  border: 1px solid var(--glass-border);
  border-radius: 22px;
  padding: 18px 20px;
  box-shadow: 0 22px 48px rgba(0, 0, 0, 0.26), inset 0 1px 0 var(--glass-specular);
}

.kb-followup-box {
  background: rgba(24, 48, 42, 0.46);
  border-color: var(--sage-border);
}

.kb-block-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: -0.015em;
  color: #ffffff;
  margin-bottom: 10px;
}

.kb-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 11px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.09);
  color: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(255, 255, 255, 0.14);
}

.kb-pill-coral,
.kb-tag.coral {
  background: var(--coral-soft);
  border-color: var(--coral-border);
  color: #ffd0c6;
}

.kb-pill-sage,
.kb-tag.good {
  background: var(--sage-soft);
  border-color: var(--sage-border);
  color: #9ff7d4;
}

.kb-pill-amber,
.kb-tag.warn {
  background: var(--amber-soft);
  border-color: var(--amber-border);
  color: #ffe2b8;
}

/* Card 1: Store Performance Table — Clean Geometric Sans-Serif (NO Monospace!) */
.kb-table-wrap {
  overflow-x: auto;
}

.kb-table,
#kamBrainRoot table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 4px;
  font-family: var(--font-sans) !important;
  font-variant-numeric: tabular-nums;
}

.kb-table th,
#kamBrainRoot table th {
  text-align: left;
  padding: 8px 10px;
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--glass-border-subtle);
  white-space: nowrap;
}

.kb-table td,
#kamBrainRoot table td {
  padding: 9px 10px;
  font-family: var(--font-sans) !important;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: #ffffff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  white-space: nowrap;
}

.kb-table td:first-child {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-soft);
}

.kb-table tr:last-child td {
  border-bottom: none;
}

.kb-pace-cell {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1.25;
}

.kb-pace-sub {
  font-size: 11px;
  font-weight: 500;
  color: rgba(159, 247, 212, 0.84);
}

.kb-delta {
  display: inline-block;
  font-size: 13px;
}

.kb-delta.bad {
  color: #ff9c87;
  font-weight: 600;
}

.kb-delta.good {
  color: var(--sage);
  font-weight: 700;
}

.kb-delta.neutral {
  color: var(--text-muted);
}

.kb-kpi-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--glass-border-subtle);
}

.kb-kpi-tile {
  background: var(--glass-inner);
  border: 1px solid var(--glass-border-subtle);
  border-radius: 14px;
  padding: 10px 13px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.kb-kpi-label {
  font-size: 10.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
}

.kb-kpi-val {
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.025em;
  color: #ffffff;
}

.kb-kpi-val.coral {
  color: #ff9c87;
}

/* Card 2: Root-Cause Revenue Attribution & SKU Hotspots */
.kb-drivers {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kb-driver-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
  font-size: 12.5px;
  font-weight: 600;
  margin-bottom: 5px;
  color: #ffffff;
}

.kb-driver-meta strong {
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  white-space: nowrap;
}

.kb-driver-track,
.kb-bar-track {
  width: 100%;
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.11);
  overflow: hidden;
  margin-bottom: 4px;
}

.kb-driver-fill,
.kb-bar-fill {
  height: 100%;
  border-radius: 999px;
}

.kb-sub {
  font-size: 11.5px;
  color: var(--text-soft);
  line-height: 1.42;
}

/* SKU 2-Column Grid with clean single-line horizontal rows */
.kb-sku-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.kb-mini-card {
  background: rgba(11, 17, 26, 0.42);
  border: 1px solid var(--glass-border-subtle);
  border-radius: 14px;
  padding: 11px 13px;
  min-width: 0;
}

.kb-mini-title {
  font-size: 10.5px;
  font-weight: 700;
  color: #ffd0a6;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 6px;
}

.kb-sku-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 10px;
  border-radius: 10px;
  background: var(--glass-inner);
  border: 1px solid var(--glass-border-subtle);
  margin-top: 6px;
  min-width: 0;
}

.kb-sku-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-white);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.kb-sku-metric {
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  font-size: 12px;
  color: #ffd2c9;
  white-space: nowrap;
  flex-shrink: 0;
}

/* Card 3: 3 Horizontal Recovery Action Cards (Tidewell Card Deck Style) */
.kb-actions-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 4px;
}

.kb-action-card {
  display: flex;
  flex-direction: column;
  background: var(--glass-inner);
  border: 1px solid var(--glass-border-subtle);
  border-radius: 16px;
  padding: 13px 14px;
  transition: transform 0.18s ease, background 0.18s ease, border-color 0.18s ease;
}

.kb-action-card:hover {
  background: rgba(255, 255, 255, 0.09);
  border-color: rgba(255, 255, 255, 0.22);
}

.kb-action-card.committed {
  border-color: var(--sage-border);
  background: rgba(66, 230, 164, 0.12);
}

.kb-action-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.kb-priority {
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 700;
  color: #ffb8a8;
}

.kb-action-title {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: -0.015em;
  color: #ffffff;
  margin-bottom: 5px;
}

.kb-action-rec {
  margin-top: auto;
  padding-top: 10px;
  font-size: 11px;
  color: #ffe2ba;
}

.kb-action-rec strong {
  display: inline-block;
  margin-top: 2px;
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  font-weight: 700;
  color: #ffffff;
}

.kb-prior-list,
.kb-extra-commits {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
}

.kb-prior-item {
  background: var(--glass-inner);
  border: 1px solid var(--glass-border-subtle);
  border-radius: 12px;
  padding: 9px 12px;
}

.kb-prior-top {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
}

.kb-followup-pill {
  margin-top: 10px;
  font-size: 11.5px;
  padding: 8px 12px;
  border-radius: 12px;
  background: var(--sky-soft);
  border: 1px solid var(--sky-border);
  color: #dbeafe;
}

/* Card 4: Compact Telemetry & Failover Audit */
.kb-failover-banner {
  background: var(--amber-soft);
  border: 1px solid var(--amber-border);
  color: #ffe2b8;
  border-radius: 10px;
  padding: 6px 10px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  margin-bottom: 7px;
}

.kb-tool-log {
  max-height: 95px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.kb-audit-row,
.kb-tool-row {
  display: grid;
  grid-template-columns: auto auto auto 1fr;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  padding: 5px 9px;
  border-radius: 9px;
  background: rgba(8, 12, 18, 0.44);
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.kb-tool-name {
  font-family: var(--font-mono);
  color: #93c5fd;
  font-weight: 600;
}

.kb-tool-src {
  font-family: var(--font-mono);
  font-size: 9.5px;
  padding: 1px 6px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.09);
  color: var(--text-soft);
}

.kb-tool-ms {
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--sage);
  font-size: 11px;
}

.kb-tool-summary {
  color: #ffffff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Card 5: Call Summary & Mock WhatsApp Recap */
.kam-summary-deck {
  margin-top: 2px;
}

.summary-card {
  background: rgba(20, 42, 38, 0.5);
  border: 1px solid var(--sage-border);
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
}

.summary-list {
  margin: 4px 0 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 1.5;
  color: #ffffff;
}

#whatsappPreview,
.whatsapp-pre {
  margin: 4px 0 0;
  padding: 11px 13px;
  border-radius: 12px;
  background: rgba(8, 14, 18, 0.72);
  border: 1px solid rgba(66, 230, 164, 0.3);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.48;
  white-space: pre-wrap;
  max-height: 210px;
  overflow-y: auto;
  color: #e2f5ec;
}

/* Scrollbars */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.32);
}

/* Responsive Breakpoints */
@media (max-width: 1180px) {
  .tidewell-header {
    flex-wrap: wrap;
    padding: 14px 20px 8px;
  }
  .tidewell-stage {
    grid-template-columns: 1fr;
    padding: 10px 20px 24px;
  }
  .hero-voice-column {
    position: static;
    min-height: auto;
  }
}

@media (max-width: 820px) {
  .kb-actions-grid,
  .kb-sku-columns,
  .kb-kpi-strip,
  .dock-selects-row {
    grid-template-columns: 1fr;
  }
}

/* Strict Playwright Visibility Contract */
.hidden {
  display: none !important;
}

```

### 2.3 `frontend/app.js`
```javascript
/**
 * Snapdeal Virtual KAM - Walking Skeleton + Full Voice Loop Controller.
 */

import { AudioPlaybackQueue } from "/static/audio.js";
import { createKamBrainController } from "/static/kam_brain.js";

const state = {
  inCall: false,
  sessionId: null,
  mode: "inbound",
  language: "en-IN",
  headphonesMode: false,
  ttsProvider: "auto",
  orbState: "idle", // idle | listening | thinking | speaking
  callStartMs: 0,
  timerHandle: null,
  recognition: null,
  recognizing: false,
  eouTimer: null,
  silencePromptTimer: null,
  didSilenceReprompt: false,
  interimTranscript: "",
  finalTranscript: "",
  sttEndMs: 0,
  firstTokenClientMs: null,
  firstAudioClientMs: null,
  chatAbortController: null,
  kamBrainHook: null,
  completedStreams: 0,
};

const el = {};

function cacheElements() {
  const ids = [
    "browserBanner", "errorBanner", "errorBannerText", "btnStartCall", "btnEndCall",
    "btnInterrupt", "callTimer", "micOrb", "orbLabel", "captionsBox",
    "selMode", "selLanguage", "chkHeadphones", "selTtsProvider", "btnToggleDebug",
    "debugBox", "txtDebugInput", "btnSendDebug", "phaseStrip", "ttsBadge",
    "latencySttToToken", "latencySttToAudio", "latencyProvider",
    "btnFollowup", "kamBrainRoot", "summaryCardSection",
  ];
  for (const id of ids) {
    el[id] = document.getElementById(id);
  }
}

const audioQueue = new AudioPlaybackQueue({
  onSpeakingStart: () => {
    setOrbState("speaking");
    if (!state.headphonesMode) {
      stopRecognition();
    }
    if (el.btnInterrupt) el.btnInterrupt.disabled = false;
  },
  onSpeakingEnd: () => {
    if (el.btnInterrupt) el.btnInterrupt.disabled = true;
    if (state.inCall && state.orbState !== "thinking") {
      startListening();
    }
  },
  onTtsProviderUsed: (provider) => {
    if (el.ttsBadge) el.ttsBadge.textContent = `TTS: ${provider}`;
  },
});

export function setKamBrainHook(hook) {
  state.kamBrainHook = hook;
}

export function getAppState() {
  return state;
}

function setOrbState(next) {
  state.orbState = next;
  if (el.micOrb) {
    el.micOrb.dataset.state = next;
    const labels = {
      idle: "Ready",
      listening: "Listening...",
      thinking: "Checking...",
      speaking: "Speaking",
    };
    el.micOrb.textContent = labels[next] || next;
  }
  if (el.orbLabel) {
    const desc = {
      idle: "Click Start Call to talk with Riya",
      listening: "Listening to Rajesh ji (800ms pause sends)",
      thinking: "Riya is analyzing your store data...",
      speaking: state.headphonesMode
        ? "Riya speaking (Headphones mode: speak anytime to interrupt)"
        : "Riya speaking (Tap Interrupt or Spacebar to speak)",
    };
    el.orbLabel.textContent = desc[next] || "";
  }
}

function showError(msg) {
  if (!el.errorBanner || !el.errorBannerText) return;
  el.errorBannerText.textContent = msg;
  el.errorBanner.classList.remove("hidden");
}

function clearError() {
  if (el.errorBanner) el.errorBanner.classList.add("hidden");
}

function appendCaption(who, text, { interim = false, id = null } = {}) {
  if (!el.captionsBox) return null;
  let node = id ? document.getElementById(id) : null;
  if (!node) {
    node = document.createElement("div");
    if (id) node.id = id;
    node.className = `caption-line ${who}${interim ? " interim" : ""}`;
    const label = document.createElement("div");
    label.className = "caption-who";
    label.textContent = who === "kam" ? "Riya (Virtual KAM)" : "Rajesh ji (Seller)";
    const body = document.createElement("div");
    body.className = "caption-body";
    node.appendChild(label);
    node.appendChild(body);
    el.captionsBox.appendChild(node);
  }
  node.className = `caption-line ${who}${interim ? " interim" : ""}`;
  node.querySelector(".caption-body").textContent = text;
  el.captionsBox.scrollTop = el.captionsBox.scrollHeight;
  return node;
}

function updatePhaseStrip(activePhase, mode = state.mode) {
  if (!el.phaseStrip) return;
  const pills = el.phaseStrip.querySelectorAll(".phase-pill");
  const isFollowup = mode === "followup";
  let seenActive = false;
  pills.forEach((pill) => {
    const p = pill.dataset.phase;
    pill.classList.remove("active", "done", "hidden");
    if (!isFollowup && p === "REVIEW_COMMITMENTS") {
      pill.classList.add("hidden");
      return;
    }
    if (isFollowup && (p === "UNDERSTAND" || p === "CLARIFY" || p === "DIAGNOSE")) {
      pill.classList.add("hidden");
      return;
    }
    if (p === activePhase) {
      pill.classList.add("active");
      seenActive = true;
    } else if (!seenActive) {
      pill.classList.add("done");
    }
  });
}

function startCallTimer() {
  state.callStartMs = Date.now();
  clearInterval(state.timerHandle);
  state.timerHandle = setInterval(() => {
    const sec = Math.floor((Date.now() - state.callStartMs) / 1000);
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    if (el.callTimer) el.callTimer.textContent = `${mm}:${ss}`;
  }, 500);
}

function stopCallTimer() {
  clearInterval(state.timerHandle);
  state.timerHandle = null;
}

function getSpeechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function stopRecognition() {
  clearTimeout(state.eouTimer);
  clearTimeout(state.silencePromptTimer);
  if (state.recognition && state.recognizing) {
    try {
      state.recognition.stop();
    } catch {
      // ignore
    }
  }
  state.recognizing = false;
}

function scheduleSilenceReprompt() {
  clearTimeout(state.silencePromptTimer);
  if (!state.inCall || state.didSilenceReprompt) return;
  state.silencePromptTimer = setTimeout(() => {
    if (!state.inCall || state.orbState !== "listening" || state.didSilenceReprompt) return;
    state.didSilenceReprompt = true;
    const prompt =
      state.language === "hi-IN"
        ? "राजेश जी, क्या आप सुन रहे हैं? आप बोल सकते हैं।"
        : state.language === "hinglish"
        ? "Rajesh ji, aap line par hain? Aap bata sakte hain."
        : "Rajesh ji, are you still there? I am listening whenever you are ready.";
    appendCaption("kam", prompt);
    audioQueue.enqueueSentence(prompt);
  }, 6000);
}

function startListening() {
  if (!state.inCall) return;
  setOrbState("listening");
  state.interimTranscript = "";
  state.finalTranscript = "";
  scheduleSilenceReprompt();

  const SR = getSpeechRecognitionCtor();
  if (!SR) {
    if (el.browserBanner) el.browserBanner.classList.remove("hidden");
    return;
  }

  if (state.recognizing) return;

  try {
    const rec = new SR();
    rec.lang = state.language === "hi-IN" ? "hi-IN" : "en-IN";
    rec.interimResults = true;
    rec.continuous = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      state.recognizing = true;
    };

    rec.onspeechstart = () => {
      clearTimeout(state.silencePromptTimer);
      if (state.headphonesMode && audioQueue.isPlaying) {
        handleInterrupt();
      }
    };

    rec.onresult = (event) => {
      clearTimeout(state.silencePromptTimer);
      let finalChunk = "";
      let interimChunk = "";
      let lowConfidence = false;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        const alt = res[0];
        if (res.isFinal) {
          finalChunk += alt.transcript + " ";
          if (typeof alt.confidence === "number" && alt.confidence > 0 && alt.confidence < 0.45) {
            lowConfidence = true;
          }
        } else {
          interimChunk += alt.transcript;
        }
      }
      if (finalChunk) state.finalTranscript += finalChunk;
      state.interimTranscript = interimChunk;

      const combined = (state.finalTranscript + " " + state.interimTranscript).trim();
      if (combined) {
        appendCaption("seller", combined + (lowConfidence ? " (confirming...)" : ""), {
          interim: true,
          id: "seller-live-caption",
        });
        // 800ms end-of-utterance silence timer
        clearTimeout(state.eouTimer);
        state.eouTimer = setTimeout(() => {
          const toSend = (state.finalTranscript + " " + state.interimTranscript).trim();
          state.finalTranscript = "";
          state.interimTranscript = "";
          if (toSend) {
            const liveNode = document.getElementById("seller-live-caption");
            if (liveNode) liveNode.removeAttribute("id");
            if (liveNode) liveNode.classList.remove("interim");
            stopRecognition();
            sendUserUtterance(toSend);
          }
        }, 800);
      }
    };

    rec.onerror = (ev) => {
      state.recognizing = false;
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
        showError("Microphone permission denied. Enable mic access or use the Debug Text box in Settings.");
      }
    };

    rec.onend = () => {
      state.recognizing = false;
      if (state.inCall && (state.orbState === "listening" || state.headphonesMode)) {
        // Keep listening unless half-duplex speaking
        setTimeout(() => {
          if (state.inCall && (state.orbState === "listening" || state.headphonesMode) && !state.recognizing) {
            try {
              rec.start();
            } catch {
              // ignore
            }
          }
        }, 180);
      }
    };

    state.recognition = rec;
    rec.start();
  } catch (err) {
    showError("Speech recognition unavailable: " + err.message);
  }
}

export function handleInterrupt() {
  if (!state.inCall) return;
  audioQueue.stopAll();
  if (state.chatAbortController) {
    state.chatAbortController.abort();
    state.chatAbortController = null;
  }
  startListening();
}

async function readSseStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      if (!part.trim()) continue;
      let eventName = "message";
      let dataJson = null;
      for (const line of part.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) {
          try {
            dataJson = JSON.parse(line.slice(5).trim());
          } catch {
            // ignore partial
          }
        }
      }
      if (dataJson !== null) {
        onEvent(eventName, dataJson);
      }
    }
  }
}

export async function startCall({ modeOverride = null, priorSessionId = null } = {}) {
  clearError();
  audioQueue.stopAll();
  stopRecognition();

  if (modeOverride) {
    state.mode = modeOverride;
    if (el.selMode) el.selMode.value = modeOverride;
  } else if (el.selMode) {
    state.mode = el.selMode.value;
  }

  state.language = el.selLanguage ? el.selLanguage.value : "en-IN";
  state.headphonesMode = el.chkHeadphones ? el.chkHeadphones.checked : false;
  state.ttsProvider = el.selTtsProvider ? el.selTtsProvider.value : "auto";
  audioQueue.configure({ language: state.language, ttsProvider: state.ttsProvider });

  state.inCall = true;
  state.didSilenceReprompt = false;
  if (el.captionsBox) el.captionsBox.innerHTML = "";
  if (el.summaryCardSection) el.summaryCardSection.classList.add("hidden");
  if (el.btnStartCall) el.btnStartCall.disabled = true;
  if (el.btnEndCall) el.btnEndCall.disabled = false;

  audioQueue.playRingtone();
  setOrbState("thinking");
  startCallTimer();

  try {
    const resp = await fetch("/api/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: state.mode,
        language: state.language,
        prior_session_id: priorSessionId,
      }),
    });
    if (!resp.ok) throw new Error(`Failed to start session (${resp.status})`);
    const payload = await resp.json();
    state.sessionId = payload.session_id;
    updatePhaseStrip(payload.session?.phase || "GREETING", payload.session?.mode || state.mode);
    if (state.kamBrainHook && state.kamBrainHook.onSessionStarted) {
      state.kamBrainHook.onSessionStarted(payload);
    }

    // Stream greeting
    await consumeAgentStream(`/api/session/${state.sessionId}/greeting`, {});
  } catch (err) {
    showError(err.message);
    endCall();
  }
}

export function endCall() {
  state.inCall = false;
  stopCallTimer();
  stopRecognition();
  audioQueue.stopAll();
  if (state.chatAbortController) {
    state.chatAbortController.abort();
    state.chatAbortController = null;
  }
  setOrbState("idle");
  if (el.btnStartCall) el.btnStartCall.disabled = false;
  if (el.btnEndCall) el.btnEndCall.disabled = true;
  if (el.btnInterrupt) el.btnInterrupt.disabled = true;
}

export async function sendUserUtterance(text) {
  const clean = (text || "").trim();
  if (!clean) return;
  if (!state.inCall || !state.sessionId) {
    await startCall();
  }
  clearError();
  audioQueue.stopAll();
  state.didSilenceReprompt = false;
  state.sttEndMs = performance.now();
  state.firstTokenClientMs = null;
  state.firstAudioClientMs = null;

  // Avoid duplicate caption if live interim caption didn't just commit it
  const existingLast = el.captionsBox?.lastElementChild;
  if (!existingLast || !existingLast.classList.contains("seller") || existingLast.querySelector(".caption-body")?.textContent !== clean) {
    appendCaption("seller", clean);
  }

  setOrbState("thinking");
  audioQueue.scheduleLatencyFiller(600);

  await consumeAgentStream("/api/chat", {
    session_id: state.sessionId,
    text: clean,
    language: state.language,
  });
}

async function consumeAgentStream(url, bodyObj) {
  if (state.chatAbortController) {
    state.chatAbortController.abort();
  }
  const controller = new AbortController();
  state.chatAbortController = controller;

  const captionId = "kam-live-" + Date.now();
  let streamText = "";

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyObj),
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`Chat HTTP ${resp.status}`);

    await readSseStream(resp, (ev, data) => {
      if (ev === "phase") {
        if (data.mode) state.mode = data.mode;
        updatePhaseStrip(data.phase, data.mode || state.mode);
        if (state.kamBrainHook?.onPhase) state.kamBrainHook.onPhase(data);
      } else if (ev === "tool_call") {
        if (state.kamBrainHook?.onToolCall) state.kamBrainHook.onToolCall(data);
      } else if (ev === "tool_result") {
        if (state.kamBrainHook?.onToolResult) state.kamBrainHook.onToolResult(data);
      } else if (ev === "failover") {
        if (state.kamBrainHook?.onFailover) state.kamBrainHook.onFailover(data);
      } else if (ev === "token") {
        if (state.firstTokenClientMs === null && state.sttEndMs) {
          state.firstTokenClientMs = Math.round(performance.now() - state.sttEndMs);
          if (el.latencySttToToken) el.latencySttToToken.textContent = `${state.firstTokenClientMs} ms`;
        }
        streamText += data.text || "";
        appendCaption("kam", streamText, { id: captionId });
      } else if (ev === "sentence") {
        audioQueue.enqueueSentence(data.speakable || data.text, () => {
          if (state.firstAudioClientMs === null && state.sttEndMs) {
            state.firstAudioClientMs = Math.round(performance.now() - state.sttEndMs);
            if (el.latencySttToAudio) el.latencySttToAudio.textContent = `${state.firstAudioClientMs} ms`;
          }
        });
      } else if (ev === "summary_card") {
        if (state.kamBrainHook?.onSummaryCard) state.kamBrainHook.onSummaryCard(data);
      } else if (ev === "latency") {
        if (el.latencyProvider && data.provider) {
          el.latencyProvider.textContent = data.provider;
        }
        if (state.kamBrainHook?.onLatency) state.kamBrainHook.onLatency(data);
      } else if (ev === "done") {
        if (data.text) {
          appendCaption("kam", data.text, { id: captionId });
        }
        updatePhaseStrip(data.phase, state.mode);
        state.completedStreams += 1;
        document.body.dataset.completedStreams = String(state.completedStreams);
        if (state.kamBrainHook?.onDone) state.kamBrainHook.onDone(data);
        // If no sentences triggered audio playback (e.g. empty), resume listening
        if (!audioQueue.isPlaying && audioQueue.queue.length === 0 && state.inCall) {
          audioQueue.clearLatencyFiller();
          startListening();
        }
      } else if (ev === "error") {
        audioQueue.clearLatencyFiller();
        showError(data.message || "LLM provider error.");
        if (state.inCall) startListening();
      }
    });
  } catch (err) {
    if (err.name !== "AbortError") {
      audioQueue.clearLatencyFiller();
      showError(err.message);
      if (state.inCall) startListening();
    }
  } finally {
    if (state.chatAbortController === controller) {
      state.chatAbortController = null;
    }
  }
}

async function init() {
  cacheElements();
  setKamBrainHook(
    createKamBrainController({
      rootEl: el.kamBrainRoot,
      summaryEl: el.summaryCardSection,
      getAppState,
    })
  );
  updatePhaseStrip("GREETING", el.selMode ? el.selMode.value : "inbound");
  if (!getSpeechRecognitionCtor() && el.browserBanner) {
    el.browserBanner.classList.remove("hidden");
  }

  // Load initial health & demo data preview
  try {
    const [hResp, dResp] = await Promise.all([fetch("/api/health"), fetch("/api/demo-data")]);
    if (hResp.ok) {
      const h = await hResp.json();
      if (!h.active_llm_chain || h.active_llm_chain.length === 0) {
        showError("No LLM API keys configured in .env. Add GROQ_API_KEY or GEMINI_API_KEY.");
      }
    }
    if (dResp.ok && state.kamBrainHook?.onDemoData) {
      state.kamBrainHook.onDemoData(await dResp.json());
    }
  } catch {
    // ignore startup fetch errors
  }

  el.btnStartCall?.addEventListener("click", () => startCall());
  el.btnEndCall?.addEventListener("click", () => endCall());
  el.btnInterrupt?.addEventListener("click", () => handleInterrupt());
  el.btnFollowup?.addEventListener("click", () => {
    startCall({
      modeOverride: "followup",
      priorSessionId: state.sessionId,
    });
  });

  el.selMode?.addEventListener("change", () => {
    state.mode = el.selMode.value;
    updatePhaseStrip("GREETING", state.mode);
  });

  el.micOrb?.addEventListener("click", () => {
    if (!state.inCall) startCall();
    else if (state.orbState === "speaking") handleInterrupt();
  });

  el.selLanguage?.addEventListener("change", async () => {
    state.language = el.selLanguage.value;
    audioQueue.configure({ language: state.language });
    if (state.recognition && state.recognizing) {
      stopRecognition();
      if (state.orbState === "listening" || state.headphonesMode) {
        startListening();
      }
    }
    if (state.sessionId) {
      fetch(`/api/session/${state.sessionId}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: state.language }),
      }).catch(() => {});
    }
  });

  el.chkHeadphones?.addEventListener("change", () => {
    state.headphonesMode = el.chkHeadphones.checked;
  });

  el.selTtsProvider?.addEventListener("change", () => {
    state.ttsProvider = el.selTtsProvider.value;
    audioQueue.configure({ ttsProvider: state.ttsProvider });
  });

  el.btnToggleDebug?.addEventListener("click", () => {
    el.debugBox?.classList.toggle("hidden");
  });

  el.btnSendDebug?.addEventListener("click", () => {
    const txt = el.txtDebugInput?.value || "";
    if (el.txtDebugInput) el.txtDebugInput.value = "";
    sendUserUtterance(txt);
  });

  el.txtDebugInput?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      el.btnSendDebug?.click();
    }
  });

  // Spacebar push-to-interrupt when not typing inside an input
  window.addEventListener("keydown", (ev) => {
    if (ev.code === "Space" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "SELECT") {
      if (state.inCall && state.orbState === "speaking") {
        ev.preventDefault();
        handleInterrupt();
      }
    }
  });
}

window.addEventListener("DOMContentLoaded", init);

```

### 2.4 `frontend/audio.js`
```javascript
/**
 * Sentence-pipelined TTS audio queue with browser speechSynthesis fallback & 600ms filler masking.
 */

export class AudioPlaybackQueue {
  constructor({ onSpeakingStart, onSpeakingEnd, onTtsProviderUsed }) {
    this.onSpeakingStart = onSpeakingStart || (() => {});
    this.onSpeakingEnd = onSpeakingEnd || (() => {});
    this.onTtsProviderUsed = onTtsProviderUsed || (() => {});
    this.queue = [];
    this.isPlaying = false;
    this.currentAudio = null;
    this.aborted = false;
    this.language = "en-IN";
    this.ttsProvider = "auto";
    this.fillerTimer = null;
    this.abortControllers = new Set();
    this._generation = 0;
    this._currentResolve = null;
  }

  configure({ language, ttsProvider }) {
    if (language) this.language = language;
    if (ttsProvider) this.ttsProvider = ttsProvider;
  }

  playRingtone() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const now = ctx.currentTime;
      for (let i = 0; i < 2; i++) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = 440;
        gain.gain.setValueAtTime(0.0001, now + i * 0.35);
        gain.gain.exponentialRampToValueAtTime(0.06, now + i * 0.35 + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.35 + 0.25);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + i * 0.35);
        osc.stop(now + i * 0.35 + 0.26);
      }
      setTimeout(() => ctx.close().catch(() => {}), 900);
    } catch {
      // Autoplay/AudioContext blocked or unsupported; harmless
    }
  }

  scheduleLatencyFiller(delayMs = 600) {
    this.clearLatencyFiller();
    this.fillerTimer = setTimeout(() => {
      if (this.isPlaying || this.queue.length > 0) return;
      const fillerText =
        this.language === "hi-IN"
          ? "जी, एक सेकंड, मैं आपका डेटा देख रही हूँ।"
          : this.language === "hinglish"
          ? "Ji, ek second, main aapka data dekh rahi hoon."
          : "One moment Rajesh ji, checking your account data.";
      this._speakBrowser(fillerText, { isFiller: true }).catch(() => {});
    }, delayMs);
  }

  clearLatencyFiller() {
    if (this.fillerTimer) {
      clearTimeout(this.fillerTimer);
      this.fillerTimer = null;
    }
  }

  enqueueSentence(speakableText, onFirstAudioCallback) {
    const clean = (speakableText || "").trim();
    if (!clean) return;
    this.clearLatencyFiller();
    this.aborted = false;

    const controller = new AbortController();
    this.abortControllers.add(controller);

    // Start fetching TTS audio immediately so sentence N+1 loads while N plays
    const fetchPromise = this._fetchTtsAudio(clean, controller.signal).finally(() => {
      this.abortControllers.delete(controller);
    });

    this.queue.push({ text: clean, fetchPromise, onFirstAudioCallback });
    if (!this.isPlaying) {
      this._drainQueue();
    }
  }

  async _fetchTtsAudio(text, signal) {
    if (this.ttsProvider === "browser") {
      return { useBrowser: true, provider: "browser" };
    }
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          language: this.language,
          provider: this.ttsProvider,
        }),
        signal,
      });
      const contentType = resp.headers.get("content-type") || "";
      if (!resp.ok || contentType.includes("application/json")) {
        return { useBrowser: true, provider: "browser" };
      }
      const blob = await resp.blob();
      const provider = resp.headers.get("X-TTS-Provider") || "edge-tts";
      return { useBrowser: false, blob, provider };
    } catch {
      return { useBrowser: true, provider: "browser" };
    }
  }

  async _drainQueue() {
    if (this.isPlaying) return;
    const myGen = ++this._generation;
    this.isPlaying = true;
    this.onSpeakingStart();

    while (this.queue.length > 0 && !this.aborted && myGen === this._generation) {
      const item = this.queue.shift();
      const audioRes = await item.fetchPromise;
      if (this.aborted || myGen !== this._generation) break;

      if (item.onFirstAudioCallback) {
        item.onFirstAudioCallback();
      }

      this.onTtsProviderUsed(audioRes.provider);

      if (audioRes.useBrowser) {
        await this._speakBrowser(item.text);
      } else {
        await this._playBlob(audioRes.blob);
      }
    }

    if (myGen === this._generation) {
      this.isPlaying = false;
      this.onSpeakingEnd();
    }
  }

  _playBlob(blob) {
    return new Promise((resolve) => {
      if (this.aborted) {
        resolve();
        return;
      }
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      this.currentAudio = audio;
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        if (this._currentResolve === done) this._currentResolve = null;
        if (this.currentAudio === audio) this.currentAudio = null;
        URL.revokeObjectURL(url);
        resolve();
      };
      this._currentResolve = done;
      audio.onended = done;
      audio.onerror = done;
      audio.play().catch(done);
    });
  }

  _speakBrowser(text, { isFiller = false } = {}) {
    return new Promise((resolve) => {
      if (!("speechSynthesis" in window) || (this.aborted && !isFiller)) {
        resolve();
        return;
      }
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        if (!isFiller && this._currentResolve === done) this._currentResolve = null;
        resolve();
      };
      try {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(text);
        utter.lang = this.language === "hi-IN" ? "hi-IN" : "en-IN";
        utter.rate = 1.03;
        const voices = window.speechSynthesis.getVoices() || [];
        const match =
          voices.find((v) => v.lang.toLowerCase().includes(utter.lang.toLowerCase())) ||
          voices.find((v) => v.lang.toLowerCase().startsWith("en-in"));
        if (match) utter.voice = match;
        if (!isFiller) this._currentResolve = done;
        utter.onend = done;
        utter.onerror = done;
        window.speechSynthesis.speak(utter);
      } catch {
        done();
      }
    });
  }

  stopAll() {
    this.clearLatencyFiller();
    this._generation++;
    this.aborted = true;
    this.queue = [];
    for (const c of this.abortControllers) {
      c.abort();
    }
    this.abortControllers.clear();
    if (this.currentAudio) {
      try {
        this.currentAudio.pause();
        this.currentAudio.currentTime = 0;
      } catch {
        // ignore
      }
      this.currentAudio = null;
    }
    if (this._currentResolve) {
      const fn = this._currentResolve;
      this._currentResolve = null;
      fn();
    }
    if ("speechSynthesis" in window) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        // ignore
      }
    }
    this.isPlaying = false;
  }
}

```

### 2.5 `frontend/kam_brain.js`
```javascript
/**
 * Snapdeal Virtual KAM — Live KAM Brain Diagnostics & Summary Card Panel (M6 + M7).
 */

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtInr(num) {
  if (typeof num !== "number" || Number.isNaN(num)) return String(num ?? "-");
  return "₹" + num.toLocaleString("en-IN");
}

export function createKamBrainController({
  rootEl,
  summaryEl,
  getAppState,
}) {
  const brain = {
    mode: "inbound",
    snapshot: "current",
    activeProvider: "-",
    demoData: null,
    diagnosedLive: false,
    actionPlanLive: false,
    toolLogs: [],
    failovers: [],
    commitments: [],
    priorCommitments: [],
    followupSchedule: null,
    summaryCard: null,
    lastLatency: null,
    ungroundedTotal: 0,
  };

  function resetForNewSession(sessionPayload) {
    const sess = sessionPayload?.session || {};
    brain.mode = sess.mode || "inbound";
    brain.snapshot = sess.snapshot || "current";
    brain.activeProvider = sess.active_provider || "-";
    brain.demoData = sessionPayload?.demo_data || brain.demoData;
    brain.diagnosedLive = false;
    brain.actionPlanLive = false;
    brain.toolLogs = [];
    brain.failovers = [];
    brain.commitments = Array.isArray(sess.commitments) ? [...sess.commitments] : [];
    brain.priorCommitments = Array.isArray(sess.prior_commitments) ? [...sess.prior_commitments] : [];
    brain.followupSchedule = sess.followup_schedule || null;
    brain.summaryCard = null;
    brain.lastLatency = null;
    brain.ungroundedTotal = 0;
    render();
    renderSummaryCard();
  }

  function applyToolResult(name, result, latencyMs, sourceHint) {
    if (!result || typeof result !== "object") return;
    if (!brain.demoData) brain.demoData = {};

    if (name === "get_metric_comparison") {
      brain.demoData.metrics = result;
    } else if (name === "get_seller_overview") {
      brain.demoData.overview = result;
    } else if (name === "run_diagnosis") {
      brain.demoData.diagnosis = result;
      brain.diagnosedLive = true;
    } else if (name === "get_out_of_stock_skus") {
      brain.demoData.oos_skus = result;
    } else if (name === "get_cancellation_breakdown") {
      brain.demoData.cancellations = result;
    } else if (name === "get_pricing_gaps") {
      brain.demoData.pricing = result;
    } else if (name === "get_visibility_funnel") {
      brain.demoData.funnel = result;
    } else if (name === "build_action_plan") {
      brain.demoData.action_plan = result;
      brain.actionPlanLive = true;
    } else if (name === "commit_action" && result.commitment) {
      const idx = brain.commitments.findIndex(
        (c) => c.action_id === result.commitment.action_id
      );
      if (idx >= 0) brain.commitments[idx] = result.commitment;
      else brain.commitments.push(result.commitment);
    } else if (name === "schedule_followup" && result.scheduled) {
      brain.followupSchedule = result;
    } else if (name === "send_call_summary" && result.summary_card) {
      brain.summaryCard = result.summary_card;
    }

    // Attach latency/summary to matching running entry or push new
    let summary = "Completed";
    if (name === "run_diagnosis") {
      summary = `Attributed -${result.total_lost_orders} orders (${fmtInr(result.total_lost_revenue)})`;
    } else if (name === "get_out_of_stock_skus") {
      summary = `${result.total_oos_count} OOS SKUs (${result.newly_oos_count} newly OOS)`;
    } else if (name === "get_pricing_gaps") {
      summary = `${result.overpriced_sku_count} overpriced SKUs (-${result.total_pricing_lost_orders} orders)`;
    } else if (name === "get_cancellation_breakdown") {
      summary = `Cancel rate ${result.baseline_cancellation_rate_pct}% -> ${result.current_cancellation_rate_pct}%`;
    } else if (name === "get_visibility_funnel") {
      summary = `Impressions ${result.changes?.impressions_pct}%`;
    } else if (name === "build_action_plan") {
      summary = `${(result.actions || []).length} prioritised actions (${result.estimated_combined_recovery_rs || ""})`;
    } else if (name === "commit_action" && result.commitment) {
      summary = `Committed ${result.commitment.action_id} due ${result.commitment.due_date}`;
    } else if (name === "schedule_followup") {
      summary = `Scheduled: ${result.followup_date}`;
    } else if (name === "send_call_summary") {
      summary = "Generated Call Summary & WhatsApp text";
    } else if (name === "get_metric_comparison") {
      const drop = result.metrics?.sales_gmv?.delta_pct;
      summary = `Sales ${drop}% | ASP flat ₹${result.metrics?.asp?.current}`;
    } else if (name === "get_seller_overview") {
      summary = `Loaded ${result.seller?.shop_name || "Seller"} (${result.snapshot})`;
    }

    // Update latest pending log entry for this tool if any
    const pendingIdx = [...brain.toolLogs]
      .reverse()
      .findIndex((t) => t.name === name && t.status === "running");
    if (pendingIdx >= 0) {
      const realIdx = brain.toolLogs.length - 1 - pendingIdx;
      brain.toolLogs[realIdx] = {
        ...brain.toolLogs[realIdx],
        status: "done",
        latency_ms: latencyMs ?? 0,
        summary,
      };
    } else {
      brain.toolLogs.push({
        name,
        source: sourceHint || "tool",
        status: "done",
        latency_ms: latencyMs ?? 0,
        summary,
      });
    }
    render();
  }

  function renderMetricsSection() {
    const metricsPayload = brain.demoData?.metrics;
    const m = metricsPayload?.metrics;
    if (!m) return `<div class="kb-bento-card kb-empty">Loading account snapshot...</div>`;

    const isFollowup = brain.mode === "followup" || Boolean(metricsPayload.followup);
    const fu = metricsPayload.followup;

    const rows = [
      {
        label: "Sales (GMV)",
        base: fmtInr(m.sales_gmv.baseline),
        curr: fmtInr(m.sales_gmv.current),
        delta: `${m.sales_gmv.delta_pct}%`,
        deltaClass: "bad",
        fuPrimary: fu ? fmtInr(fu.sales_gmv_pace) : "",
        fuSub: fu ? `+${fu.sales_vs_crisis_pct}% pace` : "",
      },
      {
        label: "Orders Placed",
        base: String(m.orders.baseline),
        curr: String(m.orders.current),
        delta: `${m.orders.delta} (${m.orders.delta_pct}%)`,
        deltaClass: "bad",
        fuPrimary: fu ? String(fu.orders_pace) : "",
        fuSub: fu ? `+${fu.orders_vs_crisis_pct}% pace` : "",
      },
      {
        label: "ASP (Avg Price)",
        base: fmtInr(m.asp.baseline),
        curr: fmtInr(m.asp.current),
        delta: "0.0% (Flat)",
        deltaClass: "neutral",
        fuPrimary: fu ? fmtInr(fu.asp) : "",
        fuSub: fu ? "Flat" : "",
      },
      {
        label: "Out-of-Stock SKUs",
        base: String(m.out_of_stock_skus.baseline),
        curr: String(m.out_of_stock_skus.current),
        delta: `+${m.out_of_stock_skus.newly_oos} new`,
        deltaClass: "bad",
        fuPrimary: fu ? `${fu.out_of_stock_skus} SKUs` : "",
        fuSub: fu ? `Target <= ${fu.oos_target_max}` : "",
      },
      {
        label: "Cancellation Rate",
        base: `${m.cancellation_rate_pct.baseline}%`,
        curr: `${m.cancellation_rate_pct.current}%`,
        delta: `+${m.cancellation_rate_pct.delta_pp} pp`,
        deltaClass: "bad",
        fuPrimary: fu ? `${fu.cancellation_rate_pct}%` : "",
        fuSub: fu ? `Target <= ${fu.cancellation_target_max}%` : "",
      },
    ];

    const gmvDeltaRs =
      typeof m.sales_gmv.baseline === "number" && typeof m.sales_gmv.current === "number"
        ? fmtInr(Math.abs(m.sales_gmv.baseline - m.sales_gmv.current))
        : "₹65,000";

    return `
      <div class="kb-block kb-bento-card">
        <div class="kb-block-head">
          <span>Store Performance Comparison</span>
          <span class="kb-tag ${isFollowup ? "good kb-pill-sage" : "kb-pill-coral"}">
            ${isFollowup ? "3-Day Recovery Active" : "Volume Problem · Flat ASP"}
          </span>
        </div>
        <div class="kb-table-wrap">
          <table class="kb-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Last Mo</th>
                <th>This Mo</th>
                <th>Change</th>
                ${isFollowup && fu ? "<th>3-Day Pace</th>" : ""}
              </tr>
            </thead>
            <tbody>
              ${rows
        .map(
          (r) => `
                <tr>
                  <td>${esc(r.label)}</td>
                  <td>${esc(r.base)}</td>
                  <td>${esc(r.curr)}</td>
                  <td><span class="kb-delta ${esc(r.deltaClass)}">${esc(r.delta)}</span></td>
                  ${isFollowup && fu
              ? `<td><div class="kb-pace-cell"><span class="kb-delta good">${esc(r.fuPrimary)}</span><span class="kb-pace-sub">${esc(r.fuSub)}</span></div></td>`
              : ""
            }
                </tr>`
        )
        .join("")}
            </tbody>
          </table>
        </div>
        <div class="kb-kpi-strip">
          <div class="kb-kpi-tile">
            <span class="kb-kpi-label">Monthly GMV Gap</span>
            <strong class="kb-kpi-val coral">-${esc(gmvDeltaRs)}</strong>
          </div>
          <div class="kb-kpi-tile">
            <span class="kb-kpi-label">Orders Lost</span>
            <strong class="kb-kpi-val coral">${esc(m.orders.delta)} orders</strong>
          </div>
          <div class="kb-kpi-tile">
            <span class="kb-kpi-label">Active / OOS SKUs</span>
            <strong class="kb-kpi-val">${esc(m.out_of_stock_skus.current)} of 60 OOS</strong>
          </div>
        </div>
      </div>
    `;
  }

  function renderPriorCommitments() {
    if (brain.mode !== "followup" || !brain.priorCommitments.length) return "";
    return `
      <div class="kb-block kb-bento-card kb-followup-box bento-span-full">
        <div class="kb-block-head">
          <span>Prior Commitments Reviewed (3 Days Ago)</span>
          <span class="kb-tag good kb-pill-sage">Follow-Up Accountability</span>
        </div>
        <div class="kb-prior-list">
          ${brain.priorCommitments
        .map((c) => {
          const statusRaw = String(c.status || "pending");
          const isDone = statusRaw.includes("done") && !statusRaw.includes("partial");
          const badgeCls = isDone ? "good kb-pill-sage" : "warn kb-pill-amber";
          const label = c.summary || c.action_summary || c.action_id;
          return `
                <div class="kb-prior-item">
                  <div class="kb-prior-top">
                    <strong>${esc(c.action_id || "ACT")}: ${esc(label)}</strong>
                    <span class="kb-tag ${badgeCls}">${esc(statusRaw.replaceAll("_", " "))}</span>
                  </div>
                  ${c.detail ? `<div class="kb-sub">${esc(c.detail)}</div>` : ""}
                </div>
              `;
        })
        .join("")}
        </div>
      </div>
    `;
  }

  function renderDriversSection() {
    const diag = brain.demoData?.diagnosis;
    const oos = brain.demoData?.oos_skus;
    const pricing = brain.demoData?.pricing;
    if (!diag || !Array.isArray(diag.drivers)) return "";

    const colors = {
      stock_outs: "linear-gradient(90deg, #ff6647 0%, #fb7185 100%)",
      pricing_gaps: "linear-gradient(90deg, #f7a145 0%, #fbbf24 100%)",
      cancellations_visibility: "linear-gradient(90deg, #60a5fa 0%, #38bdf8 100%)",
    };

    const topOos = (oos?.top_oos_skus || []).slice(0, 3);
    const allPriceSkus = pricing?.pricing_gaps || [];
    const remSkuId =
      typeof pricing?.remaining_overpriced_sku === "string"
        ? pricing.remaining_overpriced_sku
        : pricing?.remaining_overpriced_sku?.sku_id;
    const priceSkus =
      brain.mode === "followup" && remSkuId
        ? allPriceSkus.filter((g) => g.sku_id === remSkuId || String(remSkuId).includes(g.sku_id))
        : allPriceSkus.slice(0, 3);

    return `
      <div class="kb-block kb-bento-card">
        <div class="kb-block-head">
          <span>Root-Cause Revenue Attribution (-${esc(diag.total_lost_orders)} · ${esc(fmtInr(diag.total_lost_revenue))})</span>
          <span class="kb-tag ${brain.diagnosedLive ? "good kb-pill-sage" : ""}">
            ${brain.diagnosedLive ? "Live Diagnosis Verified" : "Snapshot Model"}
          </span>
        </div>
        <div class="kb-drivers">
          ${diag.drivers
        .map((d) => {
          const col = colors[d.driver] || "linear-gradient(90deg, #ff6647 0%, #f7a145 100%)";
          return `
                <div class="kb-driver-row">
                  <div class="kb-driver-meta">
                    <span>#${esc(d.rank)} ${esc(d.title)}</span>
                    <strong>${esc(d.share_pct)}% · ${esc(d.lost_orders)} ord</strong>
                  </div>
                  <div class="kb-bar-track kb-driver-track">
                    <div class="kb-bar-fill kb-driver-fill" style="width: ${Math.min(100, Number(d.share_pct) || 0)}%; background: ${col};"></div>
                  </div>
                  <div class="kb-sub">${esc(d.evidence)}</div>
                </div>
              `;
        })
        .join("")}
        </div>
        ${topOos.length || priceSkus.length
        ? `
          <div class="kb-sku-columns">
            ${topOos.length
          ? `
              <div class="kb-mini-card">
                <div class="kb-mini-title">Top Out-of-Stock SKUs</div>
                ${topOos
            .map(
              (s) =>
                `<div class="kb-sku-row"><span class="kb-sku-name" title="${esc(s.sku_id)} ${esc(s.name)}">${esc(s.sku_id)} ${esc(s.name)}</span><span class="kb-sku-metric">${esc(fmtInr(s.estimated_lost_rev_per_day))}/d</span></div>`
            )
            .join("")}
              </div>`
          : ""
        }
            ${priceSkus.length
          ? `
              <div class="kb-mini-card">
                <div class="kb-mini-title">Price Gap SKUs</div>
                ${priceSkus
            .map(
              (g) =>
                `<div class="kb-sku-row"><span class="kb-sku-name" title="${esc(g.sku_id)} ${esc(g.name)}">${esc(g.sku_id)} ${esc(g.name)}</span><span class="kb-sku-metric">${esc(fmtInr(g.seller_price))} vs ${esc(fmtInr(g.competitor_lowest_price))} (+${esc(g.price_gap_pct)}%)</span></div>`
            )
            .join("")}
              </div>`
          : ""
        }
          </div>`
        : ""
      }
      </div>
    `;
  }

  function renderActionPlanAndCommitments() {
    const plan = brain.demoData?.action_plan;
    const actions = plan?.actions || [];
    if (!actions.length && !brain.commitments.length) return "";

    return `
      <div class="kb-block kb-bento-card bento-span-full">
        <div class="kb-block-head">
          <span>Prioritised Recovery Plan &amp; Seller Commitments</span>
          <span class="kb-tag ${brain.commitments.length ? "good kb-pill-sage" : "kb-pill-amber"}">
            ${brain.commitments.length} Committed
          </span>
        </div>
        <div class="kb-actions-grid">
          ${actions
        .map((a) => {
          const matchedCommit = brain.commitments.find((c) => c.action_id === a.action_id);
          const priorityPill = a.priority === 1 ? "kb-pill-coral" : a.priority === 2 ? "kb-pill-amber" : "";
          return `
                <div class="kb-action-card ${matchedCommit ? "committed" : ""}">
                  <div class="kb-action-top">
                    <span class="kb-priority">P${esc(a.priority)} · ${esc(a.action_id)}</span>
                    <span class="kb-tag ${priorityPill}">${esc(a.impact)}</span>
                    ${matchedCommit
              ? `<span class="kb-tag good kb-pill-sage">Committed · ${esc(matchedCommit.due_date)}</span>`
              : `<span class="kb-sub">Due: ${esc(a.recommended_due)}</span>`
            }
                  </div>
                  <div class="kb-action-title">${esc(a.title)}</div>
                  <div class="kb-sub">${esc(a.details)}</div>
                  <div class="kb-action-rec">Indicative Recovery:<br /><strong>${esc(a.estimated_recovery_range_rs)}</strong></div>
                </div>
              `;
        })
        .join("")}
        </div>
        ${brain.commitments.filter((c) => !actions.some((a) => a.action_id === c.action_id)).length
        ? `
          <div class="kb-extra-commits">
            ${brain.commitments
          .filter((c) => !actions.some((a) => a.action_id === c.action_id))
          .map(
            (c) => `
                <div class="kb-prior-item">
                  <div class="kb-prior-top">
                    <strong>${esc(c.action_id)}: ${esc(c.action_summary)}</strong>
                    <span class="kb-tag good kb-pill-sage">${esc(c.owner)} · Due ${esc(c.due_date)}</span>
                  </div>
                </div>`
          )
          .join("")}
          </div>`
        : ""
      }
        ${brain.followupSchedule
        ? `
          <div class="kb-followup-pill">
            <strong>Next Follow-Up Check-In:</strong> ${esc(brain.followupSchedule.followup_date)}
            &nbsp;|&nbsp; Targets: ${esc((brain.followupSchedule.success_targets || []).join("; "))}
          </div>`
        : ""
      }
      </div>
    `;
  }

  function renderToolLogSection() {
    return `
      <div class="kb-block kb-bento-card bento-span-full">
        <div class="kb-block-head">
          <span>Live Agent Tool &amp; Failover Audit</span>
          <span class="kb-tag ${brain.ungroundedTotal === 0 ? "good kb-pill-sage" : "warn kb-pill-amber"}">
            ${brain.ungroundedTotal === 0
        ? "Grounding Check: 0 Ungrounded Numbers"
        : `Warning: ${brain.ungroundedTotal} Ungrounded`
      }
          </span>
        </div>
        ${brain.failovers.length
        ? `
          <div class="kb-failover-banner">
            Failover triggered: ${brain.failovers
          .map((f) => `${esc(f.from)} -> ${esc(f.to)} (${esc(f.reason)})`)
          .join(" | ")}
          </div>`
        : ""
      }
        <div class="kb-tool-log">
          ${brain.toolLogs.length
        ? [...brain.toolLogs]
          .reverse()
          .slice(0, 12)
          .map(
            (t) => `
                  <div class="kb-tool-row kb-audit-row">
                    <span class="kb-tool-name">${esc(t.name)}</span>
                    <span class="kb-tool-src">${esc(t.source)}</span>
                    <span class="kb-tool-ms">${t.status === "running" ? "running..." : `${esc(t.latency_ms)} ms`
              }</span>
                    <span class="kb-tool-summary">${esc(t.summary)}</span>
                  </div>`
          )
          .join("")
        : `<div class="kb-sub">Tool calls from the Deterministic Diagnosis Engine will stream here live during the call.</div>`
      }
        </div>
      </div>
    `;
  }

  function render() {
    if (!rootEl) return;
    const modeLabel =
      brain.mode === "followup"
        ? "3-Day Follow-Up"
        : brain.mode === "proactive"
          ? "Proactive Call (Riya)"
          : "Inbound Call";

    const modeBadgeClass =
      brain.mode === "followup"
        ? "good kb-pill-sage"
        : brain.mode === "proactive"
          ? "kb-pill-amber"
          : "kb-pill-coral";

    rootEl.innerHTML = `
      <div class="kb-status-strip">
        <span class="kb-tag ${modeBadgeClass}">${esc(modeLabel)}</span>
        <span class="kb-tag">LLM: ${esc(brain.activeProvider || "-")}</span>
        ${brain.lastLatency
        ? `<span class="kb-tag">First Token: ${esc(brain.lastLatency.llm_first_token_ms)} ms · Req/Turn: ${esc(brain.lastLatency.llm_requests)}</span>`
        : ""
      }
      </div>
      ${renderPriorCommitments()}
      ${renderMetricsSection()}
      ${renderDriversSection()}
      ${renderActionPlanAndCommitments()}
      ${renderToolLogSection()}
    `;
  }

  function renderSummaryCard() {
    if (!summaryEl) return;
    const card = brain.summaryCard;
    if (!card) {
      summaryEl.classList.add("hidden");
      summaryEl.innerHTML = "";
      return;
    }

    summaryEl.classList.remove("hidden");
    const commits = card.commitments || [];
    const fu = card.followup || {};
    summaryEl.innerHTML = `
      <div class="summary-card kb-bento-card">
        <div class="kb-block-head">
          <span>Call Summary &amp; Mock WhatsApp Recap</span>
          <div style="display:flex; gap:8px;">
            <button id="btnCopyWhatsapp" class="pill-btn dock-mini-btn" type="button">Copy WhatsApp Text</button>
            <button id="btnDownloadSummary" class="pill-btn pill-btn-coral dock-mini-btn" type="button">Download .txt</button>
          </div>
        </div>
        <div class="summary-grid">
          <div>
            <div class="kb-mini-title">Key Issues Covered</div>
            <ul class="summary-list">
              ${(card.key_issues || []).map((i) => `<li>${esc(i)}</li>`).join("")}
            </ul>
            <div class="kb-mini-title" style="margin-top:10px;">Committed Actions (${commits.length})</div>
            ${commits.length
        ? `<ul class="summary-list">
                    ${commits
          .map(
            (c) =>
              `<li><strong>${esc(c.action_id)}</strong>: ${esc(c.action_summary)} (${esc(c.owner)} · Due: ${esc(c.due_date)})</li>`
          )
          .join("")}
                   </ul>`
        : `<div class="kb-sub">Standard recovery action targets included in WhatsApp message.</div>`
      }
            <div class="kb-followup-pill" style="margin-top:8px;">
              <strong>Follow-Up:</strong> ${esc(fu.followup_date || "In 3 days")}
              &nbsp;|&nbsp; ${(fu.success_targets || []).map((t) => esc(t)).join("; ")}
            </div>
          </div>
          <div>
            <div class="kb-mini-title">Mock WhatsApp / SMS Dispatch</div>
            <pre id="whatsappPreview" class="whatsapp-pre">${esc(card.whatsapp_text || "")}</pre>
          </div>
        </div>
      </div>
    `;

    const btnCopy = document.getElementById("btnCopyWhatsapp");
    if (btnCopy) {
      btnCopy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(card.whatsapp_text || "");
          btnCopy.textContent = "Copied!";
          setTimeout(() => {
            btnCopy.textContent = "Copy WhatsApp Text";
          }, 1800);
        } catch {
          btnCopy.textContent = "Copy Failed";
        }
      });
    }

    const btnDownload = document.getElementById("btnDownloadSummary");
    if (btnDownload) {
      btnDownload.addEventListener("click", () => {
        const blob = new Blob([card.whatsapp_text || ""], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "snapdeal-kam-call-summary.txt";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      });
    }
  }

  return {
    onDemoData(data) {
      brain.demoData = data;
      const appSt = getAppState ? getAppState() : null;
      if (appSt?.mode) brain.mode = appSt.mode;
      render();
    },
    onSessionStarted(payload) {
      resetForNewSession(payload);
    },
    onPhase(data) {
      if (data?.mode) brain.mode = data.mode;
      render();
    },
    onToolCall(data) {
      brain.toolLogs.push({
        name: data.name,
        source: data.source || "llm",
        status: "running",
        latency_ms: 0,
        summary: "Executing...",
      });
      render();
    },
    onToolResult(data) {
      applyToolResult(data.name, data.result, data.latency_ms, null);
    },
    onFailover(data) {
      brain.failovers.push(data);
      render();
    },
    onLatency(data) {
      brain.lastLatency = data;
      if (data?.provider) brain.activeProvider = data.provider;
      if (Array.isArray(data?.ungrounded_numbers) && data.ungrounded_numbers.length) {
        brain.ungroundedTotal += data.ungrounded_numbers.length;
      }
      render();
    },
    onSummaryCard(card) {
      brain.summaryCard = card;
      renderSummaryCard();
    },
    onDone(data) {
      if (data?.provider) brain.activeProvider = data.provider;
      if (Array.isArray(data?.commitments) && data.commitments.length) {
        brain.commitments = data.commitments;
      }
      if (data?.followup_schedule) {
        brain.followupSchedule = data.followup_schedule;
      }
      if (data?.summary_card && !brain.summaryCard) {
        brain.summaryCard = data.summary_card;
        renderSummaryCard();
      }
      render();
    },
  };
}

```

### 2.6 Other Assets Inventory
- **Local static assets under `frontend/`:** None (0 files beyond the 5 files above).
- **Favicon:** Inline `data:image/svg+xml` `<link rel="icon">` inside `frontend/index.html` line 7 (Coral circle `#ff6647` with white `R`); `/favicon.ico` returns `204`.
- **Fonts:** External Google Fonts stylesheet (`Plus Jakarta Sans` weights 400/500/600/700 and `JetBrains Mono` weights 400/500) loaded via `https://fonts.googleapis.com` / `https://fonts.gstatic.com`.
- **Sounds:** No `.mp3`/`.wav` assets; ringtone (`440 Hz` dual pulse) is synthesized in browser via `AudioContext.createOscillator()` in `frontend/audio.js`.
- **Manifest:** None (`manifest.json` does not exist).
