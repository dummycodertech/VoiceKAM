# Manual Browser Voice Test Checklist (Google Chrome)

> Automated Playwright checks verify DOM updates, SSE streaming, `/api/tts` audio fetching, failover, and all UI transitions using a stubbed `SpeechRecognition`. Because headless Chrome lacks a real microphone/acoustic loop, complete this **2-minute manual verification** in desktop **Google Chrome** before your live demo.

---

## Pre-Check
- [ ] Start locally with tunnel (`.\run.ps1` -> `https://*.trycloudflare.com`) or direct Chrome localhost (`http://127.0.0.1:7860`), or wake Render URL (`https://<your-service>.onrender.com/api/health`).
- [ ] Confirm Chrome shows a locked HTTPS/localhost icon and allows microphone permission when prompted.
- [ ] Verify **`PROTOTYPE · MOCK DATA ONLY`** badge appears in top-right header.

---

## Core Acoustic & Voice Checks (2 Minutes)

1. **Mic Capture & 800ms End-of-Utterance (EOU)**
   - [ ] Click **Start Call** (Inbound mode). Confirm 2-ring tone plays and Riya speaks greeting aloud.
   - [ ] Notice mic orb pulses green (**Listening...**) after Riya finishes.
   - [ ] Speak naturally: *"My sales have dropped significantly this month. Can you help me?"* Wait ~1 second cleanly -> confirm interim text converts to final seller bubble automatically without clicking Send.
2. **Low-Latency Pipeline (`< 2s` Target)**
   - [ ] Verify 600ms filler plays ONLY if LLM/TTS stalls slightly; confirm natural Indian voice plays cleanly (`TTS: edge-tts` on local tunnel; or `TTS: browser` fallback if cloud host throttles Edge).
   - [ ] Check `STT->Token` and `STT->Audio` timings at bottom of left panel.
3. **Half-Duplex Echo Guard & Tap-to-Interrupt**
   - [ ] Keep **Headphones Mode** UNCHECKED while using laptop speakers. Verify Riya does **NOT** transcribe her own speaker voice while speaking.
   - [ ] While Riya speaks her diagnosis, click **Tap to Interrupt (Space)** (or press **Spacebar**). Confirm her audio stops immediately, orb switches back to green **Listening...**, and your interruption transcribes cleanly.
4. **Headphones True Barge-In (With Earphones)**
   - [ ] Plug in headphones and check **Headphones Mode (True Barge-In)**.
   - [ ] Start talking mid-sentence while Riya is speaking — confirm `onspeechstart` immediately cuts off Riya's audio queue and transcribes your interruption.
5. **6-Second Silence Re-Prompt**
   - [ ] Wait silently ~6–7 seconds in Listening mode — confirm Riya gently asks *"Rajesh ji, are you still there?"* without crashing recognition.
6. **Language Selector (English / Hinglish / Hindi)**
   - [ ] Switch **Language** to **Hinglish** or **Hindi (हिंदी)** and speak Hindi/Hinglish phrases (`"Meri sales kyun kam hui?"`). Confirm `hi-IN` neural voice (`SwaraNeural`) responds naturally in that language.
7. **Summary Card + 3-Day Follow-Up Check**
   - [ ] Complete commitment (`"I'll restock top 5 SKUs within 48 hours"`), verify Call Summary & **Copy WhatsApp Text** / **Download .txt**.
   - [ ] Click **Simulate follow-up call (3 days later)** and confirm Riya calls out `18 -> 9` OOS and `₹1,35,000 -> ₹1,75,500` pace.

