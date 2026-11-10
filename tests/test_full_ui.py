"""M6 & M7 Playwright UI tests: KAM Brain live panel, Proactive mode, Summary card, and Follow-up (+3d)."""

from __future__ import annotations

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


def _completed_streams(page) -> int:
    val = page.evaluate("() => Number(document.body.dataset.completedStreams || '0')")
    return int(val)


def _send_debug(page, text: str, timeout_ms: int = 35000) -> None:
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


def test_m6_m7_kam_brain_proactive_summary_and_followup():
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    log_path = SCRATCH_DIR / "uvicorn_full_ui.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
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
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1360, "height": 900})
            page = context.new_page()

            console_errors: list[str] = []
            page_errors: list[str] = []
            failed_urls: list[str] = []

            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on(
                "response",
                lambda r: failed_urls.append(f"{r.status} {r.url}") if r.status >= 400 else None,
            )

            page.add_init_script(
                """
                window.SpeechRecognition = class MockSR {
                  start() { if (this.onstart) this.onstart(); }
                  stop() { if (this.onend) this.onend(); }
                };
                window.webkitSpeechRecognition = window.SpeechRecognition;
                HTMLMediaElement.prototype.play = function() {
                  setTimeout(() => { if (this.onended) this.onended(); }, 100);
                  return Promise.resolve();
                };
                """
            )

            page.goto(base_url, wait_until="networkidle")

            # Verify initial KAM Brain snapshot hydrated
            page.wait_for_selector(".kb-table", timeout=8000)
            brain_init = page.inner_text("#kamBrainRoot")
            assert "Sales (GMV)" in brain_init
            assert "Root-Cause Revenue Attribution" in brain_init
            assert "Prioritised Recovery Plan" in brain_init

            # --- Test Proactive Mode + Full Inbound/Proactive Path to Summary ---
            page.select_option("#selMode", "proactive")
            before_start = _completed_streams(page)
            page.click("#btnStartCall")
            page.wait_for_function(
                f"() => Number(document.body.dataset.completedStreams || '0') > {before_start}",
                timeout=15000,
            )
            page.click("#btnToggleDebug")

            # Turn 1: Trigger DIAGNOSE directly from proactive greeting ("what happened?")
            _send_debug(page, "Wait, why did my sales drop? Check everything and tell me.")
            page.wait_for_function(
                "() => document.querySelector('#kamBrainRoot').innerText.includes('Live Diagnosis Verified')",
                timeout=15000,
            )

            # Turn 2: Move DIAGNOSE -> RECOMMEND
            _send_debug(page, "That makes sense. What exact actions should I take first?")
            # Turn 3: Move RECOMMEND -> COMMIT
            _send_debug(page, "Okay I agree, let's lock the dates.")
            # Turn 4: Make explicit commitment in COMMIT
            _send_debug(
                page,
                "I commit to restocking top 5 SKUs within 48 hours and syncing stock daily starting today.",
            )
            # Turn 5: Advance to SUMMARY (or already there -> ensure summary card appears)
            if page.locator("#summaryCardSection.hidden").count() > 0:
                _send_debug(page, "Yes, please send the call summary and schedule the follow-up.")

            page.wait_for_selector("#summaryCardSection:not(.hidden) .summary-card", timeout=15000)
            summary_text = page.inner_text("#summaryCardSection")
            assert "Call Summary & Mock WhatsApp Recap" in summary_text
            assert "mock whatsapp / sms dispatch" in summary_text.lower()
            assert "Kumar Home & Kitchen" in summary_text
            assert "ACT-1" in summary_text

            page.wait_for_timeout(400)
            page.screenshot(path=str(SCRATCH_DIR / "m6_kam_brain_summary.png"), full_page=True)

            # --- Test M7: 3-Day Follow-Up Simulation ---
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
            assert "1,75,500" in fu_brain

            # Drive 1 follow-up turn -> REVIEW_COMMITMENTS
            _send_debug(page, "Hi Riya, yes I restocked most items. How do my numbers look now?")
            page.wait_for_timeout(800)
            page.screenshot(path=str(SCRATCH_DIR / "m7_followup_call.png"), full_page=True)

            assert failed_urls == [], f"Failed HTTP responses: {failed_urls}"
            assert console_errors == [], f"Console errors: {console_errors}"
            assert page_errors == [], f"Page errors: {page_errors}"

            page.click("#btnEndCall")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log_file.close()
