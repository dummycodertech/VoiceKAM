"""Hermetic Playwright UI suite: Scenarios A-D picker, custom CSV upload/errors, KAM Brain, Summary, Follow-up, and 390px layout."""

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
window.SpeechRecognition = class MockSR {
  start() { if (this.onstart) this.onstart(); }
  stop() { if (this.onend) this.onend(); }
};
window.webkitSpeechRecognition = window.SpeechRecognition;
window.SpeechSynthesisUtterance = window.SpeechSynthesisUtterance || class MockUtterance {
  constructor(text) { this.text = text; this.lang = 'en-IN'; this.rate = 1; }
};
window.speechSynthesis = {
  getVoices() { return [{ lang: 'en-IN', name: 'MockVoice' }]; },
  cancel() {},
  speak(u) { setTimeout(() => { if (u && typeof u.onend === 'function') u.onend(); }, 40); }
};
HTMLMediaElement.prototype.play = function() {
  setTimeout(() => { if (this.onended) this.onended(); }, 80);
  return Promise.resolve();
};
"""


def _completed_streams(page) -> int:
    return int(page.evaluate("() => Number(document.body.dataset.completedStreams || '0')"))


def _send_debug(page, text: str, timeout_ms: int = 25000) -> None:
    before = _completed_streams(page)
    page.fill("#txtDebugInput", text)
    page.click("#btnSendDebug")
    try:
        page.wait_for_function(
            f"() => Number(document.body.dataset.completedStreams || '0') > {before}",
            timeout=timeout_ms,
        )
    except Exception:
        err_txt = page.inner_text("#errorBannerText") if page.locator("#errorBanner").is_visible() else ""
        caps = page.inner_text("#captionsBox")
        raise AssertionError(
            f"Turn timed out waiting for completedStreams>{before}. ErrorBanner={err_txt!r}\nCaptions:\n{caps}"
        )


def test_full_ui_scenarios_upload_and_call_flow():
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    log_path = SCRATCH_DIR / "uvicorn_full_ui.log"
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
                if httpx.get(f"{base_url}/api/health", timeout=1.5).status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.4)
        assert ready, "Server did not start cleanly"

        with sync_playwright() as p:
            browser = _launch_browser(p)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()

            console_errors: list[str] = []
            page_errors: list[str] = []
            non_422_failed_urls: list[str] = []

            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on(
                "response",
                lambda r: (
                    non_422_failed_urls.append(f"{r.status} {r.url}")
                    if r.status >= 400 and not (r.status == 422 and "/api/data/validate" in r.url)
                    else None
                ),
            )

            page.add_init_script(_SPEECH_STUB_SCRIPT)
            page.goto(base_url, wait_until="networkidle")

            # 1. Verify initial Scenario A KAM Brain & dataset summary
            page.wait_for_selector(".kb-table", timeout=8000)
            assert "UrbanStride Footwear & Apparel" in page.inner_text("#datasetSummary")
            brain_init = page.inner_text("#kamBrainRoot")
            assert "Sales (GMV)" in brain_init
            assert "Root-Cause Revenue Attribution" in brain_init
            assert "Prioritised Recovery Plan" in brain_init

            # 2. Switch Scenario Picker from A -> B and verify live preview updates
            page.select_option("#selScenario", "B")
            page.wait_for_function(
                "() => document.querySelector('#datasetSummary').innerText.includes('TechNest Accessories')",
                timeout=8000,
            )
            assert "2,69,600" in page.inner_text("#kamBrainRoot")

            # Switch back to Scenario A for the full call flow
            page.select_option("#selScenario", "A")
            page.wait_for_function(
                "() => document.querySelector('#datasetSummary').innerText.includes('UrbanStride Footwear')",
                timeout=8000,
            )

            # 3. Proactive Mode + Full Path to Summary Card
            page.select_option("#selMode", "proactive")
            before_start = _completed_streams(page)
            page.click("#btnStartCall")
            page.wait_for_function(
                f"() => Number(document.body.dataset.completedStreams || '0') > {before_start}",
                timeout=15000,
            )
            page.click("#btnToggleDebug")

            _send_debug(page, "Wait, why did my sales drop? Check everything and tell me.")
            page.wait_for_function(
                "() => document.querySelector('#kamBrainRoot').innerText.includes('Live Diagnosis Verified')",
                timeout=15000,
            )
            _send_debug(page, "That makes sense. What exact actions should I take first?")
            _send_debug(page, "Okay I agree, let's lock the dates.")
            _send_debug(
                page,
                "I commit to restocking top 5 SKUs within 48 hours and syncing stock daily starting today.",
            )
            if page.locator("#summaryCardSection.hidden").count() > 0:
                _send_debug(page, "Yes, please send the call summary and schedule the follow-up.")

            page.wait_for_selector("#summaryCardSection:not(.hidden) .summary-card", timeout=15000)
            summary_text = page.inner_text("#summaryCardSection")
            assert "Call Summary & Mock WhatsApp Recap" in summary_text
            assert "mock whatsapp / sms dispatch" in summary_text.lower()
            assert "UrbanStride Footwear & Apparel" in summary_text
            assert "ACT-1" in summary_text

            # 4. 3-Day Follow-Up Simulation on Scenario A
            before_fu = _completed_streams(page)
            page.click("#btnFollowup")
            page.wait_for_function(
                f"() => Number(document.body.dataset.completedStreams || '0') > {before_fu}",
                timeout=15000,
            )
            page.wait_for_function(
                "() => document.querySelector('#kamBrainRoot').innerText.includes('3-Day Follow-Up')",
                timeout=10000,
            )
            fu_brain = page.inner_text("#kamBrainRoot")
            assert "Prior Commitments Reviewed (3 Days Ago)" in fu_brain
            assert "3-day pace" in fu_brain.lower()
            assert "3,94,336" in fu_brain

            _send_debug(page, "Hi metis, yes I restocked most items. How do my numbers look now?")
            page.click("#btnEndCall")

            assert console_errors == [], f"Console errors during normal call flow: {console_errors}"

            # 5. Custom Upload tab: verify broken CSV inline error, then valid minimal CSV graceful degradation
            page.click("#btnDataModeUpload")
            assert "Do not upload real or confidential seller data" in page.inner_text("#privacyNotice")

            bad_csv = SCRATCH_DIR / "ui_broken.csv"
            bad_csv.write_text(
                "sku_id,name,price,orders_prev,orders_curr\nSKU-1,=cmd|'/C calc'!A0,500,20,10\n",
                encoding="utf-8",
            )
            page.set_input_files("#fileSkuDataset", str(bad_csv))
            page.click("#btnValidateUpload")
            page.wait_for_selector("#uploadError:not(.hidden)", timeout=8000)
            assert "formula safety" in page.inner_text("#uploadError").lower()

            good_csv = SCRATCH_DIR / "ui_minimal.csv"
            good_csv.write_text(
                "sku_id,name,price,orders_prev,orders_curr,days_out_of_stock_curr\n"
                "C-101,Cotton Linen Kurta,800,30,10,12\n"
                "C-102,Handloom Tote Bag,500,20,18,0\n",
                encoding="utf-8",
            )
            page.set_input_files("#fileSkuDataset", str(good_csv))
            page.fill("#txtUploadShop", "Jaipur Looms")
            page.fill("#txtUploadSeller", "Ananya Rao")
            page.click("#btnValidateUpload")
            page.wait_for_function(
                "() => document.querySelector('#datasetSummary').innerText.includes('Jaipur Looms')",
                timeout=8000,
            )
            assert page.locator("#uploadError.hidden").count() == 1
            table_text = page.inner_text("#kamBrainRoot")
            assert "Not assessable" in table_text
            assert "null%" not in table_text.lower()

            # 6. Check mobile 390px viewport has zero horizontal overflow
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert overflow <= 1, f"Horizontal overflow detected at 390px: {overflow}px"

            assert non_422_failed_urls == [], f"Unexpected HTTP errors: {non_422_failed_urls}"
            unexpected_console = [
                e for e in console_errors
                if "422 (Unprocessable Entity)" not in e
            ]
            assert unexpected_console == [], f"Unexpected console errors: {unexpected_console}"
            assert page_errors == [], f"Page errors: {page_errors}"
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log_file.close()
