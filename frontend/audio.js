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
