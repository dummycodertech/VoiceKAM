"""Hermetic Playwright UI walking skeleton test (runs cleanly offline with MockLLMProvider)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

SCRATCH_DIR = Path(__file__).resolve().parent.parent / "scratch"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hermetic_env(port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ALLOW_MOCK_LLM": "true",
            "GROQ_API_KEY": "",
            "GEMINI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "TTS_PROVIDER": "browser",
            "TTS_CACHE_ENABLED": "false",
            "PORT": str(port),
        }
    )
    return env


def _launch_browser(p):
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


_SPEECH_STUB_SCRIPT = """
window.SpeechRecognition = class MockSpeechRecognition {
  constructor() {
    this.lang = 'en-IN';
    this.interimResults = true;
    this.continuous = true;
    this._started = false;
  }
  start() {
    this._started = true;
    if (this.onstart) this.onstart();
  }
  stop() {
    if (this._started) {
      this._started = false;
      if (this.onend) this.onend();
    }
  }
};
window.webkitSpeechRecognition = window.SpeechRecognition;
window.SpeechSynthesisUtterance = window.SpeechSynthesisUtterance || class MockUtterance {
  constructor(text) { this.text = text; this.lang = 'en-IN'; this.rate = 1; }
};
window.speechSynthesis = {
  getVoices() { return [{ lang: 'en-IN', name: 'MockVoice' }]; },
  cancel() {},
  speak(u) { setTimeout(() => { if (u && typeof u.onend === 'function') u.onend(); }, 50); }
};
HTMLMediaElement.prototype.play = function() {
  setTimeout(() => { if (this.onended) this.onended(); }, 100);
  return Promise.resolve();
};
"""


def test_walking_skeleton_voice_ui_flow():
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    log_path = SCRATCH_DIR / "uvicorn_m5.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=_hermetic_env(port),
    )

    try:
        ready = False
        for _ in range(60):
            try:
                r = httpx.get(f"{base_url}/api/health", timeout=1.5)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.4)
        assert ready, "Uvicorn server did not become healthy in time"

        with sync_playwright() as p:
            browser = _launch_browser(p)
            context = browser.new_context(viewport={"width": 1280, "height": 840})
            page = context.new_page()

            console_errors: list[str] = []
            page_errors: list[str] = []
            tts_statuses: list[int] = []
            failed_urls: list[str] = []

            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.on(
                "response",
                lambda resp: (
                    tts_statuses.append(resp.status) if "/api/tts" in resp.url else None,
                    failed_urls.append(f"{resp.status} {resp.url}") if resp.status >= 400 else None,
                ),
            )

            page.add_init_script(_SPEECH_STUB_SCRIPT)
            page.goto(base_url, wait_until="networkidle")

            # Branding & notice hydrated from /api/health
            header_text = page.inner_text("header").lower()
            assert "prototype" not in header_text
            assert "tts:" in header_text
            assert page.inner_text("#brandName").strip() == "VoiceKAM"
            assert "not affiliated with any marketplace" in page.inner_text("#footerNotice").lower()
            assert "UrbanStride Footwear & Apparel" in page.inner_text("#datasetSummary")

            # 1. Start Call -> greeting plays & streams
            page.click("#btnStartCall")
            page.wait_for_selector("#captionsBox .caption-line.kam", timeout=10000)

            # 2. Drive turn 1 through Debug Text Box against offline MockLLMProvider
            page.click("#btnToggleDebug")
            page.fill("#txtDebugInput", "My sales dropped significantly this month. Can you help me?")
            page.click("#btnSendDebug")

            page.wait_for_function(
                "() => Number(document.body.dataset.completedStreams || '0') >= 2 && document.getElementById('latencySttToAudio').innerText !== '-'",
                timeout=20000,
            )
            page.screenshot(path=str(SCRATCH_DIR / "m5_walking_skeleton.png"), full_page=True)

            captions_text = page.inner_text("#captionsBox")
            assert "Vikram ji" in captions_text
            assert "metis" in captions_text.lower()
            assert "sales dropped" in captions_text.lower()
            assert page.inner_text("#latencySttToToken") != "-"
            assert page.inner_text("#latencySttToAudio") != "-"
            assert all(s == 200 for s in tts_statuses) and len(tts_statuses) >= 1
            assert failed_urls == [], f"Failed HTTP responses: {failed_urls}"
            assert console_errors == [], f"Console errors: {console_errors}"
            assert page_errors == [], f"Page errors: {page_errors}"

            # 3. End Call cleanly
            page.click("#btnEndCall")
            assert page.get_attribute("#micOrb", "data-state") == "idle"
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log_file.close()
