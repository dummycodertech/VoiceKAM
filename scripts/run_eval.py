"""Evaluation harness for VoiceKAM across 6 scripted personas and Scenarios A-D.

Supports deterministic offline evaluation (--offline, using MockLLMProvider for
hermetic CI) and throttled live LLM evaluation (--live, with .eval_cache/ disk
caching, exponential backoff, real TTS first-audio latency timing, and strict
cross-model LLM-as-judge).

Never invents or estimates numbers: any failed or unmeasured metric is recorded
literally as "not measured". Offline Mock tables and Live tables are strictly
labeled and separated in docs/EVALUATION.md.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Ensure repo root is importable when invoked as `python scripts/run_eval.py`
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent.orchestrator import AgentOrchestrator
from app.agent.session import Phase, SessionStore
from app.agent.speakable import find_ungrounded_numbers
from app.config import PROJECT_NAME, Settings
from app.data.scenarios import SCENARIO_SPECS
from app.data.seed_data import get_scenario_context
from app.llm.base import AllProvidersUnavailableError, CanonicalMessage, ProviderRateLimitOrTransientError
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter
from app.tts.service import TTSService

SCENARIO_IDS: tuple[str, ...] = tuple(SCENARIO_SPECS.keys())
EVAL_CACHE_DIR = REPO_ROOT / ".eval_cache"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "docs" / "EVALUATION.md"

_NUMBER_TOKEN_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

_OFFLINE_BEGIN = "<!-- BEGIN_OFFLINE_EVAL -->"
_OFFLINE_END = "<!-- END_OFFLINE_EVAL -->"
_LIVE_BEGIN = "<!-- BEGIN_LIVE_EVAL -->"
_LIVE_END = "<!-- END_LIVE_EVAL -->"

EXPECTED_PHASE_TOOLS: dict[str, set[str]] = {
    "DIAGNOSE": {
        "run_diagnosis",
        "get_out_of_stock_skus",
        "get_cancellation_breakdown",
        "get_pricing_gaps",
        "get_visibility_funnel",
    },
    "RECOMMEND": {"build_action_plan"},
    "COMMIT": {"commit_action"},
    "SUMMARY": {"send_call_summary"},
}


@dataclass(frozen=True)
class ScriptedPersona:
    persona_id: str
    label: str
    language: str
    utterances: tuple[str, ...]


PERSONAS: tuple[ScriptedPersona, ...] = (
    ScriptedPersona(
        persona_id="cooperative",
        label="Cooperative & Detailed",
        language="en-IN",
        utterances=(
            "Hi, my sales dropped significantly this month. Can you tell me what happened?",
            "Yes, several top-selling items ran out of stock last week.",
            "Please check my data and walk me through the root causes.",
            "That makes sense. What priority actions should I take first?",
            "Yes okay I agree, I commit to restocking priority items within 48 hours.",
            "Yes, please send the call summary and schedule the follow-up.",
        ),
    ),
    ScriptedPersona(
        persona_id="vague",
        label="Vague / Low-Detail",
        language="en-IN",
        utterances=(
            "Sales feel slower recently.",
            "Not completely sure why, maybe demand dipped a little.",
            "Could be stock or pricing, hard to say.",
            "Check my data and tell me why orders dropped.",
            "Okay, what should I fix first?",
            "Yes ok I commit to doing the priority fix within 48 hours.",
            "Alright, send the call summary please.",
        ),
    ),
    ScriptedPersona(
        persona_id="frustrated",
        label="Frustrated & Urgent",
        language="en-IN",
        utterances=(
            "I am very frustrated, revenue keeps dropping every week and it is a big problem.",
            "Orders fell and customers keep cancelling.",
            "Stop asking questions and check my data right now.",
            "Fine, tell me what exact steps will recover sales.",
            "Yes alright, I commit to starting priority recovery within 48 hours.",
            "Send me the summary and follow-up details now.",
        ),
    ),
    ScriptedPersona(
        persona_id="off_topic",
        label="Off-Topic / Tangential",
        language="en-IN",
        utterances=(
            "Wait first can you update my GST bank details and courier pickup pin code?",
            "Okay leaving that aside, my sales dropped heavily this month.",
            "Please tell me why sales dropped and check my data.",
            "Got it, what about packaging costs? Anyway tell me the recommended plan.",
            "Yes okay let's lock the priority recovery timeline.",
            "I commit to executing the top action tomorrow.",
            "Please send the call summary.",
        ),
    ),
    ScriptedPersona(
        persona_id="interrupting",
        label="Interrupting / Negotiating",
        language="en-IN",
        utterances=(
            "Hold on, why did my sales drop? Check my data right away.",
            "Skip extra questions and check my data right now.",
            "Cut straight to the action plan.",
            "No, I cannot commit to ads or promo campaigns today.",
            "Okay restocking and pricing alone works, let's do within 48 hours.",
            "Yes I commit within 48 hours.",
            "Send the summary now.",
        ),
    ),
    ScriptedPersona(
        persona_id="hinglish",
        label="Hinglish (Code-Mixed)",
        language="hinglish",
        utterances=(
            "Namaste, is mahine mere orders kaafi kam ho gaye hain.",
            "Haan kuch fast-selling items stock se bahar gaye the.",
            "Aap check karke batayein ki main issue kya hai, check my data.",
            "Theek hai samajh aaya, ab pehle kaunsa action lena chahiye?",
            "Haan theek hai, main 48 hours mein top priority fix karne ke liye commit karta hoon.",
            "Ji haan, call summary aur follow-up schedule bhej dijiye.",
        ),
    ),
)


@dataclass
class TrialMetrics:
    scenario_id: str
    persona_id: str
    trial_idx: int
    succeeded: bool
    reached_summary_or_close: bool
    commitments_count: int
    total_turns: int
    grounded_turns: int
    total_number_tokens: int
    grounded_number_tokens: int
    expected_tools_matched: int
    expected_tools_total: int
    word_counts: list[int] = field(default_factory=list)
    first_token_ms: list[int] = field(default_factory=list)
    first_audio_ms: list[int] = field(default_factory=list)
    provider_models: list[str] = field(default_factory=list)
    judge_scores: dict[str, float] | None = None
    judge_model: str | None = None
    transcript_lines: list[str] = field(default_factory=list)
    error: str | None = None


def _percentile(sorted_vals: list[int | float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    s = sorted(sorted_vals)
    idx = min(len(s) - 1, max(0, math.ceil((pct / 100.0) * len(s)) - 1))
    return float(s[idx])


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache_path: Path, data: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False).replace("\r\n", "\n")
    cache_path.write_bytes(payload.encode("utf-8"))


async def _run_single_trial(
    orchestrator: AgentOrchestrator,
    store: SessionStore,
    tts_service: TTSService | None,
    scenario_id: str,
    persona: ScriptedPersona,
    trial_idx: int,
    *,
    measure_audio: bool,
    live_mode: bool,
    call_cache: dict[str, Any],
    cache_path: Path,
) -> tuple[TrialMetrics, bool]:
    """Run one full multi-turn call with disk caching and exponential backoff on rate limits."""
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "v": 2,
                "mode": "live" if live_mode else "offline",
                "scenario": scenario_id,
                "persona": persona.persona_id,
                "language": persona.language,
                "trial": trial_idx,
                "utterances": list(persona.utterances),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if live_mode and cache_key in call_cache:
        cached_raw = dict(call_cache[cache_key])
        if cached_raw.get("succeeded"):
            return TrialMetrics(**cached_raw), True

    ctx = get_scenario_context(scenario_id)
    max_attempts = 4 if live_mode else 1
    last_err: str | None = None

    for attempt in range(max_attempts):
        if attempt > 0:
            backoff = min(35.0, (2 ** (attempt + 1)) + random.uniform(0.3, 1.5))
            await asyncio.sleep(backoff)

        session = store.create(
            mode="inbound",
            language=persona.language,  # type: ignore[arg-type]
            seller_context=ctx,
        )

        try:
            greeting_text = ""
            async for ev in orchestrator.start_call(session):
                if ev["event"] == "done":
                    greeting_text = ev["data"].get("text") or ""

            transcript: list[str] = [f"Agent (GREETING): {greeting_text}"]
            tools_by_phase: dict[str, set[str]] = {}
            visited_phases: set[str] = {session.phase.value}

            total_turns = 0
            grounded_turns = 0
            total_num_tokens = 0
            grounded_num_tokens = 0
            word_counts: list[int] = []
            first_token_ms: list[int] = []
            first_audio_ms: list[int] = []
            provider_models: set[str] = set()

            for utt in persona.utterances:
                transcript.append(f"Seller ({persona.persona_id}): {utt}")
                active_phase = session.phase.value
                turn_reply = ""
                turn_ungrounded: list[str] = []
                first_speakable: str | None = None
                first_sentence_elapsed_ms: int | None = None
                turn_start = time.perf_counter()

                async for ev in orchestrator.run_turn(session, utt):
                    if ev["event"] == "phase":
                        active_phase = ev["data"].get("phase") or active_phase
                        visited_phases.add(active_phase)
                    elif ev["event"] == "tool_result":
                        tname = ev["data"].get("name")
                        if tname:
                            tools_by_phase.setdefault(active_phase, set()).add(tname)
                    elif ev["event"] == "sentence":
                        if first_speakable is None:
                            sp = (ev["data"].get("speakable") or ev["data"].get("text") or "").strip()
                            if sp:
                                first_speakable = sp
                                first_sentence_elapsed_ms = round((time.perf_counter() - turn_start) * 1000)
                    elif ev["event"] == "error":
                        raise RuntimeError(ev["data"].get("message") or "SSE error emitted")
                    elif ev["event"] == "latency":
                        lat = ev["data"]
                        if lat.get("llm_first_token_ms") is not None:
                            first_token_ms.append(int(lat["llm_first_token_ms"]))
                        if lat.get("provider"):
                            provider_models.add(str(lat["provider"]))
                        turn_ungrounded = list(lat.get("ungrounded_numbers") or [])
                    elif ev["event"] == "done":
                        turn_reply = ev["data"].get("text") or ""
                        if ev["data"].get("phase"):
                            visited_phases.add(ev["data"]["phase"])

                if measure_audio and tts_service is not None and first_speakable and first_sentence_elapsed_ms is not None:
                    t_tts0 = time.perf_counter()
                    try:
                        audio, _meta = await tts_service.synthesize(first_speakable, language=persona.language)
                        if audio is not None and len(audio.audio_bytes) > 0:
                            tts_ms = round((time.perf_counter() - t_tts0) * 1000)
                            first_audio_ms.append(first_sentence_elapsed_ms + tts_ms)
                    except Exception:
                        pass

                total_turns += 1
                words = len(turn_reply.split())
                word_counts.append(words)

                if not turn_ungrounded:
                    turn_ungrounded = find_ungrounded_numbers(
                        turn_reply,
                        session.grounded_numbers,
                        seller_context=session.seller_context,
                    )
                if not turn_ungrounded:
                    grounded_turns += 1

                nums_in_reply = _NUMBER_TOKEN_RE.findall(turn_reply)
                total_num_tokens += len(nums_in_reply)
                grounded_num_tokens += max(0, len(nums_in_reply) - len(turn_ungrounded))
                transcript.append(f"Agent ({active_phase}): {turn_reply}")

            matched_tools = 0
            expected_tools = 0
            for phase_name, req_tools in EXPECTED_PHASE_TOOLS.items():
                if phase_name in visited_phases or phase_name in tools_by_phase:
                    expected_tools += len(req_tools)
                    called = tools_by_phase.get(phase_name, set())
                    matched_tools += len(req_tools & called)

            reached_end = bool(
                session.summary_card is not None
                and (Phase.SUMMARY.value in visited_phases or Phase.CLOSE.value in visited_phases)
            )
            models_sorted = sorted(provider_models or ({"mock:deterministic-test-kam"} if not live_mode else set()))

            tm = TrialMetrics(
                scenario_id=scenario_id,
                persona_id=persona.persona_id,
                trial_idx=trial_idx,
                succeeded=True,
                reached_summary_or_close=reached_end,
                commitments_count=len(session.commitments),
                total_turns=total_turns,
                grounded_turns=grounded_turns,
                total_number_tokens=total_num_tokens,
                grounded_number_tokens=grounded_num_tokens,
                expected_tools_matched=matched_tools,
                expected_tools_total=expected_tools,
                word_counts=word_counts,
                first_token_ms=first_token_ms,
                first_audio_ms=first_audio_ms,
                provider_models=models_sorted,
                transcript_lines=transcript,
            )
            if live_mode:
                call_cache[cache_key] = asdict(tm)
                _save_cache(cache_path, call_cache)
            return tm, False

        except (AllProvidersUnavailableError, ProviderRateLimitOrTransientError, Exception) as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:180]}"

    return (
        TrialMetrics(
            scenario_id=scenario_id,
            persona_id=persona.persona_id,
            trial_idx=trial_idx,
            succeeded=False,
            reached_summary_or_close=False,
            commitments_count=0,
            total_turns=0,
            grounded_turns=0,
            total_number_tokens=0,
            grounded_number_tokens=0,
            expected_tools_matched=0,
            expected_tools_total=0,
            error=last_err or "Unknown live evaluation error",
        ),
        False,
    )


async def _judge_transcript_live(
    router: LLMRouter,
    responder_providers: list[str],
    transcript: list[str],
    judge_cache: dict[str, Any],
    judge_cache_path: Path,
) -> tuple[dict[str, float] | None, str | None]:
    """Run rubric LLM-as-judge strictly using a different model than the responder(s)."""
    resp_set = set(responder_providers)
    resp_models = {r.split(":", 1)[1] if ":" in r else r for r in resp_set}
    resp_prefixes = {r.split(":", 1)[0] for r in resp_set if ":" in r}

    # First preference: entirely different provider family & active (not disabled)
    cross_family = [
        p
        for p in router.configured_providers()
        if p.provider_name != "mock"
        and not router.is_disabled(p.key)
        and p.provider_name not in resp_prefixes
    ]
    # Second preference: different model within another tier if needed (never same model as responder)
    diff_model = [
        p
        for p in router.configured_providers()
        if p.provider_name != "mock"
        and not router.is_disabled(p.key)
        and p.key not in resp_set
        and p.model_name not in resp_models
    ]
    ordered_judges = cross_family + [c for c in diff_model if c not in cross_family]
    if not ordered_judges:
        return None, None

    prompt = (
        "Score this voice Key Account Manager call transcript from 1 to 5 (floats or ints) on five criteria:\n"
        "1. relevance\n2. empathy\n3. clarity\n4. actionability\n5. honesty_uncertainty\n"
        "Return ONLY valid JSON: "
        '{"relevance": N, "empathy": N, "clarity": N, "actionability": N, "honesty_uncertainty": N}\n\n'
        + "\n".join(transcript)
    )

    for judge in ordered_judges:
        cache_key = hashlib.sha256(f"{judge.key}:{prompt}".encode("utf-8")).hexdigest()
        if cache_key in judge_cache:
            cached = judge_cache[cache_key]
            if cached.get("scores"):
                return cached["scores"], cached.get("model")

        msgs = [
            CanonicalMessage(role="system", content="You are an impartial QA evaluator. Respond with raw JSON only."),
            CanonicalMessage(role="user", content=prompt),
        ]
        for attempt in range(3):
            if attempt > 0:
                await asyncio.sleep(min(20.0, (2 ** (attempt + 1)) + random.uniform(0.2, 1.0)))
            try:
                chunks: list[str] = []
                async for ch in judge.stream_turn(messages=msgs, tools=[], temperature=0.0, max_tokens=200):
                    if ch.type == "token" and ch.text:
                        chunks.append(ch.text)
                raw = re.sub(r"```(?:json)?|```", "", "".join(chunks)).strip()
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    keys = ("relevance", "empathy", "clarity", "actionability", "honesty_uncertainty")
                    scores = {k: round(float(parsed[k]), 2) for k in keys if k in parsed}
                    if len(scores) == len(keys):
                        judge_cache[cache_key] = {"scores": scores, "model": judge.key}
                        _save_cache(judge_cache_path, judge_cache)
                        return scores, judge.key
                break
            except ProviderRateLimitOrTransientError as exc:
                if exc.reason_code == "forbidden":
                    router.mark_disabled(judge.key, reason="judge_403")
                    break
                continue
            except Exception:
                break
    return None, None


def _render_eval_section(
    *,
    section_kind: str,  # "Offline Mock" | "Live"
    trials: list[TrialMetrics],
    n: int,
    run_date_utc: str,
    responder_models: list[str],
    judge_models: list[str],
    note: str,
) -> str:
    tag = section_kind  # Explicitly "Offline Mock" or "Live" on EVERY heading & table!
    succeeded = [t for t in trials if t.succeeded]
    failed = [t for t in trials if not t.succeeded]

    total_calls = len(trials)
    succ_calls = len(succeeded)
    total_turns = sum(t.total_turns for t in succeeded)
    grounded_turns = sum(t.grounded_turns for t in succeeded)

    turn_ground_val = f"{round(100.0 * grounded_turns / total_turns, 1)}%" if total_turns else "not measured"
    turn_ground_det = f"{grounded_turns}/{total_turns} turns with 0 ungrounded numbers" if total_turns else "0 turns completed"

    total_nums = sum(t.total_number_tokens for t in succeeded)
    grounded_nums = sum(t.grounded_number_tokens for t in succeeded)
    num_ground_val = (
        f"{round(100.0 * grounded_nums / total_nums, 1)}%"
        if (total_turns and total_nums)
        else ("not measured" if not total_turns else "100.0%")
    )
    num_ground_det = f"{grounded_nums}/{total_nums} spoken numeric tokens traced to tools" if total_turns else "not measured"

    matched_tools = sum(t.expected_tools_matched for t in succeeded)
    expected_tools = sum(t.expected_tools_total for t in succeeded)
    tool_acc_val = f"{round(100.0 * matched_tools / expected_tools, 1)}%" if expected_tools else "not measured"
    tool_acc_det = f"{matched_tools}/{expected_tools} expected phase tools executed" if expected_tools else "not measured"

    completed_calls = sum(1 for t in succeeded if t.reached_summary_or_close)
    comp_val = f"{round(100.0 * completed_calls / total_calls, 1)}%" if succ_calls else "not measured"
    comp_det = f"{completed_calls}/{total_calls} attempted calls reached SUMMARY/CLOSE with card ({succ_calls}/{total_calls} completed without API failure)"

    all_words = [w for t in succeeded for w in t.word_counts]
    words_val = f"{round(sum(all_words) / len(all_words), 1)} / {max(all_words)}" if all_words else "not measured"

    calls_with_commits = sum(1 for t in succeeded if t.commitments_count >= 1)
    total_commits = sum(t.commitments_count for t in succeeded)
    commit_val = f"{round(100.0 * calls_with_commits / total_calls, 1)}% ({total_commits} total)" if succ_calls else "not measured"

    ft_vals = [x for t in succeeded for x in t.first_token_ms]
    fa_vals = [x for t in succeeded for x in t.first_audio_ms]
    ft_p50 = f"{round(_percentile(ft_vals, 50), 1)} ms" if ft_vals else "not measured"
    ft_p95 = f"{round(_percentile(ft_vals, 95), 1)} ms" if ft_vals else "not measured"
    fa_p50 = f"{round(_percentile(fa_vals, 50), 1)} ms" if fa_vals else "not measured"
    fa_p95 = f"{round(_percentile(fa_vals, 95), 1)} ms" if fa_vals else "not measured"

    scored_trials = [t.judge_scores for t in succeeded if t.judge_scores]
    if scored_trials:
        rubric_keys = ("relevance", "empathy", "clarity", "actionability", "honesty_uncertainty")
        means = {
            k: round(sum(s[k] for s in scored_trials) / len(scored_trials), 2)
            for k in rubric_keys
        }
        overall_judge = round(sum(means.values()) / len(means), 2)
        judge_summary = (
            f"{overall_judge} / 5.0 "
            f"(rel {means['relevance']}, emp {means['empathy']}, clr {means['clarity']}, "
            f"act {means['actionability']}, unc {means['honesty_uncertainty']}; n={len(scored_trials)}/{total_calls})"
        )
    else:
        judge_summary = "not measured"

    lines = [
        f"## {tag} Evaluation ({tag})",
        "",
        f"### Run Metadata ({tag})",
        f"- **Run Date (UTC):** {run_date_utc}",
        f"- **Evaluation Kind:** {tag}",
        f"- **Responder Model ID(s):** {', '.join(responder_models) if responder_models else 'not measured'}",
        f"- **Judge Model ID(s):** {', '.join(judge_models) if judge_models else 'not measured'}",
        f"- **N (trials per persona x scenario cell):** {n}",
        f"- **Calls Succeeded / Attempted:** {succ_calls} / {total_calls} ({len(failed)} failed)",
        f"- **Total Assistant Turns:** {total_turns}",
        f"- **Execution / Cache Note:** {note}",
        "",
        f"### Aggregate Measured Metrics ({tag})",
        "",
        f"| Metric ({tag}) | Measured Value | Detail |",
        "| --- | --- | --- |",
        f"| Grounding rate (turns) | {turn_ground_val} | {turn_ground_det} |",
        f"| Grounding rate (number tokens) | {num_ground_val} | {num_ground_det} |",
        f"| Expected tool-call accuracy | {tool_acc_val} | {tool_acc_det} |",
        f"| Phase completion rate | {comp_val} | {comp_det} |",
        f"| Words per turn (avg / max) | {words_val} | Voice word budget (`45` default, `55` DIAGNOSE/RECOMMEND, `82` SUMMARY) |",
        f"| Commitments captured | {commit_val} | {calls_with_commits}/{total_calls} calls captured >=1 commitment |",
        f"| LLM first-token latency (p50 / p95) | {ft_p50} / {ft_p95} | SSE `latency.llm_first_token_ms` ({len(ft_vals)} turns measured) |",
        f"| First-audio latency (p50 / p95) | {fa_p50} / {fa_p95} | Turn start -> first `sentence` + `edge-tts` MP3 ready ({len(fa_vals)} turns measured) |",
        f"| Cross-model LLM-as-judge score (1-5) | {judge_summary} | Distinct judge model (`relevance, empathy, clarity, actionability, honesty_uncertainty`) |",
        "",
        f"### Breakdown by Scenario ({tag})",
        "",
        f"| Scenario ({tag}) | Calls | Completion Rate | Tool Accuracy | Grounded Turns | Avg Words | Commitments |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for sc in SCENARIO_IDS:
        sub_all = [t for t in trials if t.scenario_id == sc]
        sub = [t for t in sub_all if t.succeeded]
        if not sub_all:
            continue
        if not sub:
            lines.append(f"| Scenario {sc} ({tag}) | 0/{len(sub_all)} | not measured | not measured | not measured | not measured | not measured |")
            continue
        sc_comp = round(100.0 * sum(1 for t in sub if t.reached_summary_or_close) / len(sub_all), 1)
        sc_m = sum(t.expected_tools_matched for t in sub)
        sc_e = sum(t.expected_tools_total for t in sub)
        sc_tool = f"{round(100.0 * sc_m / sc_e, 1)}%" if sc_e else "not measured"
        sc_gt = sum(t.grounded_turns for t in sub)
        sc_tt = sum(t.total_turns for t in sub)
        sc_gr = f"{round(100.0 * sc_gt / sc_tt, 1)}%" if sc_tt else "not measured"
        sc_words = [w for t in sub for w in t.word_counts]
        sc_aw = str(round(sum(sc_words) / len(sc_words), 1)) if sc_words else "not measured"
        sc_comm = sum(t.commitments_count for t in sub)
        lines.append(f"| Scenario {sc} ({tag}) | {len(sub)}/{len(sub_all)} | {sc_comp}% | {sc_tool} | {sc_gr} | {sc_aw} | {sc_comm} |")

    lines.extend(
        [
            "",
            f"### Breakdown by Persona ({tag})",
            "",
            f"| Persona ({tag}) | Calls | Completion Rate | Grounded Turns | Avg / Max Words | Commitments | Judge (1-5) |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for p in PERSONAS:
        sub_all = [t for t in trials if t.persona_id == p.persona_id]
        sub = [t for t in sub_all if t.succeeded]
        if not sub_all:
            continue
        if not sub:
            lines.append(f"| {p.persona_id} ({p.language}, {tag}) | 0/{len(sub_all)} | not measured | not measured | not measured | not measured | not measured |")
            continue
        p_comp = round(100.0 * sum(1 for t in sub if t.reached_summary_or_close) / len(sub_all), 1)
        p_gt = sum(t.grounded_turns for t in sub)
        p_tt = sum(t.total_turns for t in sub)
        p_gr = f"{round(100.0 * p_gt / p_tt, 1)}%" if p_tt else "not measured"
        p_words = [w for t in sub for w in t.word_counts]
        p_w_str = f"{round(sum(p_words) / len(p_words), 1)} / {max(p_words)}" if p_words else "not measured"
        p_comm = sum(t.commitments_count for t in sub)
        p_judged = [sum(t.judge_scores.values()) / len(t.judge_scores) for t in sub if t.judge_scores]
        p_j_str = f"{round(sum(p_judged) / len(p_judged), 2)} (n={len(p_judged)})" if p_judged else "not measured"
        lines.append(f"| {p.persona_id} ({p.language}, {tag}) | {len(sub)}/{len(sub_all)} | {p_comp}% | {p_gr} | {p_w_str} | {p_comm} | {p_j_str} |")

    return "\n".join(lines)


def _extract_existing_block(doc: str, begin_marker: str, end_marker: str) -> str | None:
    m = re.search(rf"{re.escape(begin_marker)}\s*(.*?)\s*{re.escape(end_marker)}", doc, re.DOTALL)
    return m.group(1).strip() if m else None


def _compose_document(*, live_block: str | None, offline_block: str | None) -> str:
    live_body = live_block or (
        "## Live Evaluation (Live)\n\n"
        "*No live evaluation has been recorded in this report yet (`make eval-live`). "
        "All live metrics remain `not measured` until executed.*"
    )
    offline_body = offline_block or (
        "## Offline Mock Evaluation (Offline Mock)\n\n"
        "*No offline evaluation recorded (`make eval`).*"
    )
    parts = [
        f"# {PROJECT_NAME} Evaluation Report",
        "",
        "> **Strict Separation Policy:** Tables below are explicitly labeled **`Live`** or **`Offline Mock`** and are never combined. Any metric from a failed call or unmeasured channel is reported literally as **`not measured`**.",
        "",
        _LIVE_BEGIN,
        live_body,
        _LIVE_END,
        "",
        "---",
        "",
        _OFFLINE_BEGIN,
        offline_body,
        _OFFLINE_END,
        "",
        "---",
        "",
        "## Causal-Recovery & Known Limitations",
        "- **Synthetic Causal-Recovery Scope:** All causal-recovery results (`200/200` top-driver recovery, `0.02 pp` mean absolute error in `tests/test_attribution_properties.py`) are measured strictly on **synthetic ground truth generated by the project's own scenario generator (`app/data/scenarios.py`)** and are **not claims about real-world marketplace causal accuracy**.",
        "- **Offline vs Live Separation:** Offline Mock rows run deterministically in-process via `MockLLMProvider` (`first_audio_ms` and judge score reported as `not measured`). Live rows exercise real external LLM providers, cross-model judging, and server-side `edge-tts` MP3 synthesis timing (`first_audio_ms`).",
        "- **Headless Microphone Gap:** Scripted seller personas simulate multi-turn interactions deterministically and do not capture live acoustic room noise, speaker echo, or browser `SpeechRecognition` STT errors; real browser microphone verification remains manual per [VOICE_TEST_CHECKLIST.md](VOICE_TEST_CHECKLIST.md).",
        "",
    ]
    return "\n".join(parts)


async def run_evaluation(
    *,
    mode: str,
    n: int,
    scenarios: tuple[str, ...],
    output_path: Path,
    max_calls: int | None = None,
) -> dict[str, Any]:
    settings = Settings()
    EVAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    call_cache_path = EVAL_CACHE_DIR / f"{mode}_calls_cache.json"
    judge_cache_path = EVAL_CACHE_DIR / "eval_cache.json"
    call_cache = _load_cache(call_cache_path)
    judge_cache = _load_cache(judge_cache_path)

    if mode == "offline":
        router = LLMRouter(providers=[MockLLMProvider()])
        tts_service: TTSService | None = None
        section_kind = "Offline Mock"
        note = "Offline mode (`MockLLMProvider`); disk cache & audio TTS disabled (`not measured`)"
        measure_audio = False
    else:
        os.environ["ALLOW_MOCK_LLM"] = "false"
        live_settings = Settings(allow_mock_llm=False, tts_cache_enabled=True)
        router = LLMRouter.from_settings(live_settings)
        if not router.configured_providers():
            raise RuntimeError("No live LLM API keys configured for --live evaluation.")
        await router.initialize_and_smoke_test(live_settings)
        tts_service = TTSService(live_settings)
        section_kind = "Live"
        note = f"Live API router with exponential backoff + disk cache ({call_cache_path.name}, {len(call_cache)} initial cached calls)"
        measure_audio = True

    orchestrator = AgentOrchestrator(router=router)
    store = SessionStore(
        ttl_seconds=max(settings.session_ttl_seconds, 7200),
        max_sessions=max(200, len(scenarios) * len(PERSONAS) * n + 20),
    )

    trials: list[TrialMetrics] = []
    cache_hits = 0
    call_counter = 0
    for trial_idx in range(1, n + 1):
        for sc in scenarios:
            for persona in PERSONAS:
                if max_calls is not None and call_counter >= max_calls:
                    break
                call_counter += 1
                tm, was_cached = await _run_single_trial(
                    orchestrator,
                    store,
                    tts_service,
                    sc,
                    persona,
                    trial_idx,
                    measure_audio=measure_audio,
                    live_mode=(mode == "live"),
                    call_cache=call_cache,
                    cache_path=call_cache_path,
                )
                if was_cached:
                    cache_hits += 1
                elif mode == "live":
                    # Gentle inter-call pacing on free-tier RPM/TPM limits
                    await asyncio.sleep(1.2)

                if mode == "live" and tm.succeeded:
                    scores, jmodel = await _judge_transcript_live(
                        router,
                        tm.provider_models,
                        tm.transcript_lines,
                        judge_cache,
                        judge_cache_path,
                    )
                    tm.judge_scores = scores
                    tm.judge_model = jmodel
                    if jmodel is not None and not tm.judge_scores:
                        tm.judge_model = None
                    if not was_cached:
                        await asyncio.sleep(0.8)

                if mode == "live":
                    print(
                        f"[eval-live {call_counter}/{len(scenarios) * len(PERSONAS) * n}] "
                        f"sc={sc} persona={persona.persona_id} trial={trial_idx} "
                        f"ok={tm.succeeded} turns={tm.total_turns} cached={was_cached} "
                        f"resp={tm.provider_models} judge={tm.judge_model}",
                        flush=True,
                    )

                trials.append(tm)
            if max_calls is not None and call_counter >= max_calls:
                break
        if max_calls is not None and call_counter >= max_calls:
            break

    responder_models = sorted({m for t in trials if t.succeeded for m in t.provider_models})
    judge_models = sorted({t.judge_model for t in trials if t.judge_model})
    run_date_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if mode == "live":
        note = f"Live API router + exponential backoff + disk cache ({cache_hits}/{len(trials)} call cache hits)"

    rendered_section = _render_eval_section(
        section_kind=section_kind,
        trials=trials,
        n=n,
        run_date_utc=run_date_utc,
        responder_models=responder_models,
        judge_models=judge_models,
        note=note,
    )

    existing_doc = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    existing_live = _extract_existing_block(existing_doc, _LIVE_BEGIN, _LIVE_END)
    existing_offline = _extract_existing_block(existing_doc, _OFFLINE_BEGIN, _OFFLINE_END)
    if existing_offline is None and existing_doc and "offline (hermetic MockLLMProvider)" in existing_doc:
        existing_offline = None  # Force refresh if running from pre-marker format

    if mode == "live":
        full_md = _compose_document(live_block=rendered_section, offline_block=existing_offline)
    else:
        full_md = _compose_document(live_block=existing_live, offline_block=rendered_section)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(full_md.replace("\r\n", "\n").encode("utf-8"))

    return {
        "total_calls": len(trials),
        "succeeded_calls": sum(1 for t in trials if t.succeeded),
        "failed_calls": sum(1 for t in trials if not t.succeeded),
        "mode": section_kind,
        "output_path": str(output_path),
        "responder_models": responder_models,
        "judge_models": judge_models,
        "first_audio_measured_turns": sum(len(t.first_audio_ms) for t in trials),
        "judged_calls": sum(1 for t in trials if t.judge_scores),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run VoiceKAM multi-persona evaluation across Scenarios A-D.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline", action="store_true", help="Use deterministic MockLLMProvider (default)")
    group.add_argument("--live", action="store_true", help="Run against configured live LLM providers")
    parser.add_argument("--n", type=int, default=1, help="Trials per (persona x scenario) cell (default: 1)")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIO_IDS), help="Scenarios to run (default: A B C D)")
    parser.add_argument("--max-calls", type=int, default=None, help="Optional global cap on total conversations")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output markdown path")
    args = parser.parse_args()

    mode = "live" if args.live else "offline"
    summary = asyncio.run(
        run_evaluation(
            mode=mode,
            n=max(1, args.n),
            scenarios=tuple(args.scenarios),
            output_path=args.output,
            max_calls=args.max_calls,
        )
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
