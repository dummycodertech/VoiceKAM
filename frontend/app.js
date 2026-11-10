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
