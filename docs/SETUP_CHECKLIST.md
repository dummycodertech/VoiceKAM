# Setup Checklist: Free APIs, GitHub, Cloudflare Tunnel & Render Free Hosting

> **Golden Security Rule:** Never commit `.env` or paste secrets into tracked files (`*.py`, `*.md`, `Dockerfile`, `render.yaml`, `README.md`).  
> - **Local secrets:** go in `.env` (gitignored via `.gitignore`).  
> - **Cloud secrets:** go inside Render's encrypted **Environment Variables** UI.

---

## 1. Free-Tier API Keys (Zero Credit Card Required)

### A. Groq API Key (`GROQ_API_KEY`) — Primary LLM + Whisper STT Fallback
1. Sign in at **https://console.groq.com** (Google or GitHub login).
2. Open **https://console.groq.com/keys** -> **Create API Key** -> name it `voicekam`.
3. Copy `gsk_...`:
   - **Local:** `.env` -> `GROQ_API_KEY=gsk_...`
   - **Render:** Dashboard -> Service -> **Environment** -> `GROQ_API_KEY`

### B. Google Gemini API Key (`GEMINI_API_KEY`) — Automatic Failover LLM + Optional Emergency TTS
1. Sign in at **https://aistudio.google.com/apikey**.
2. Click **Create API key** -> select/create a free Google Cloud project (no billing card needed) -> copy key.
3. Save:
   - **Local:** `.env` -> `GEMINI_API_KEY=<your-key>`
   - **Render:** **Environment** -> `GEMINI_API_KEY`

### C. OpenRouter API Key (`OPENROUTER_API_KEY`) — Optional 3rd-Tier Free Fallback
1. Sign in at **https://openrouter.ai/keys** -> **Create Key**.
2. Save as `OPENROUTER_API_KEY=sk-or-...` in `.env` / Render **Environment**. Works with `openrouter/free` with $0 balance.

---

## 2. Primary Live Demo Path: Local + Cloudflare Quick Tunnel (Zero Account / Best Latency)

1. Install `cloudflared` once on Windows (choose either):
   - **WinGet:** `winget install --id Cloudflare.Cloudflared -e`
   - **Direct EXE (no admin needed):**  
     `curl.exe -L -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe`
2. Start app + public HTTPS tunnel with one command:
   ```powershell
   .\run.ps1
   ```
   *(On macOS/Linux: `./run.sh`)*
3. Copy the printed `https://*.trycloudflare.com` URL and open it in **Google Chrome**.

---

## 3. Cloud Public URL: GitHub Repo -> Render Free Web Service ($0/mo, Zero Card)

1. **Create a GitHub repository:**
   - Open **https://github.com/new**, name it `voicekam`, leave Public or Private, click **Create repository**.
   - Push this repo (`git push -u origin main`). Confirm `.env` is ignored and NOT listed in `git status`.
2. **Deploy on Render:**
   - Go to **https://dashboard.render.com** and sign in with GitHub (**no credit card required for Hobby/Free plan**).
   - Click **New + -> Blueprint** (recommended — uses `render.yaml`) or **Web Service**, and select your `voicekam` GitHub repository.
   - Verify **Instance Type** is **Free ($0 / month)** and Region is **Singapore**.
   - Under **Environment Variables**, enter values for `GROQ_API_KEY` and `GEMINI_API_KEY` (and optional `OPENROUTER_API_KEY`).
   - Click **Deploy**. Render builds the Python 3.12 runtime and gives you `https://<your-service>.onrender.com`.
3. **Post-Deploy Verification (`edge-tts` Datacenter IP Check):**
   - Run:
     ```bash
     python scripts/smoke_test.py --url https://<your-service>.onrender.com
     ```
   - Inspect `/api/tts-probe`: if `edge_tts_ok: true`, Render gives neural voice; if `edge_tts_ok: false` (Microsoft throttling shared datacenter IP), the browser seamlessly uses `window.speechSynthesis` on Render and neural voice over **Local + Cloudflare Tunnel**.
