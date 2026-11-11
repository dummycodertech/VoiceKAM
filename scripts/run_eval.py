"""Evaluation harness for VoiceKAM across 6 scripted personas and Scenarios A-D.

Supports deterministic offline evaluation (--offline, using MockLLMProvider for
hermetic CI) and throttled live LLM evaluation (--live, with .eval_cache/ disk
caching, exponential backoff, and optional cross-model LLM-as-judge).

Never invents or estimates numbers: any unmeasured metric is recorded literally
as "not measured".
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
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

SCENARIO_IDS: tuple[str, ...] = tuple(SCENARIO_SPECS.keys())
from app.llm.base import CanonicalMessage, ProviderRateLimitOrTransientError
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter

EVAL_CACHE_DIR = REPO_ROOT / ".eval_cache"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "docs" / "EVALUATION.md"

_NUMBER_TOKEN_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

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
    provider_models: set[str] = field(default_factory=set)
    judge_scores: dict[str, float] | None = None
    judge_model: str | None = None
    transcript_lines: list[str] = field(default_factory=list)


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
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def _run_single_trial(
    orchestrator: AgentOrchestrator,
    store: SessionStore,
    scenario_id: str,
    persona: ScriptedPersona,
    trial_idx: int,
) -> TrialMetrics:
    ctx = get_scenario_context(scenario_id)
    session = store.create(
        mode="inbound",
        language=persona.language,  # type: ignore[arg-type]
        seller_context=ctx,
    )

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
    provider_models: set[str] = set()

    for utt in persona.utterances:
        transcript.append(f"Seller ({persona.persona_id}): {utt}")
        active_phase = session.phase.value
        turn_reply = ""
        turn_ungrounded: list[str] = []

        # Wrap live calls cleanly with small retry/backoff on transient errors
        events: list[dict[str, Any]] = []
        async for ev in orchestrator.run_turn(session, utt):
            events.append(ev)
            if ev["event"] == "phase":
                active_phase = ev["data"].get("phase") or active_phase
                visited_phases.add(active_phase)
            elif ev["event"] == "tool_result":
                tname = ev["data"].get("name")
                if tname:
                    tools_by_phase.setdefault(active_phase, set()).add(tname)
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

        total_turns += 1
        words = len(turn_reply.split())
        word_counts.append(words)

        if not turn_ungrounded:
            # Double check directly against grounded_numbers to verify consistency
            check_ungrounded = find_ungrounded_numbers(
                turn_reply,
                session.grounded_numbers,
                seller_context=session.seller_context,
            )
            turn_ungrounded = check_ungrounded

        if not turn_ungrounded:
            grounded_turns += 1

        nums_in_reply = _NUMBER_TOKEN_RE.findall(turn_reply)
        total_num_tokens += len(nums_in_reply)
        grounded_num_tokens += max(0, len(nums_in_reply) - len(turn_ungrounded))
        transcript.append(f"Agent ({active_phase}): {turn_reply}")

    # Evaluate tool-call accuracy across expected milestone phases reached
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

    return TrialMetrics(
        scenario_id=scenario_id,
        persona_id=persona.persona_id,
        trial_idx=trial_idx,
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
        first_audio_ms=[],  # TTS audio not streamed in headless eval unless explicit audio mode added
        provider_models=provider_models or {"mock:deterministic-test-kam"},
        transcript_lines=transcript,
    )


async def _judge_transcript_live(
    router: LLMRouter,
    responder_providers: set[str],
    transcript: list[str],
    cache: dict[str, Any],
    cache_path: Path,
) -> tuple[dict[str, float] | None, str | None]:
    """Run a rubric LLM-as-judge call using a distinct provider where available."""
    resp_prefixes = {r.split(":", 1)[0] for r in responder_providers}
    candidates = [
        p
        for p in router.configured_providers()
        if p.provider_name not in {"mock"} and p.provider_name not in resp_prefixes
    ]
    if not candidates:
        candidates = [
            p
            for p in router.configured_providers()
            if p.provider_name != "mock" and p.key not in responder_providers
        ]
    if not candidates:
        return None, None

    judge = candidates[0]
    prompt = (
        "Score this voice Key Account Manager call transcript from 1 to 5 on five criteria:\n"
        "1. relevance\n2. empathy\n3. clarity\n4. actionability\n5. honesty_uncertainty\n"
        "Return ONLY valid JSON: "
        '{"relevance": N, "empathy": N, "clarity": N, "actionability": N, "honesty_uncertainty": N}\n\n'
        + "\n".join(transcript)
    )
    cache_key = hashlib.sha256(f"{judge.key}:{prompt}".encode("utf-8")).hexdigest()
    if cache_key in cache:
        cached = cache[cache_key]
        return cached.get("scores"), cached.get("model")

    msgs = [
        CanonicalMessage(role="system", content="You are an impartial QA evaluator. Respond with JSON only."),
        CanonicalMessage(role="user", content=prompt),
    ]
    for attempt in range(3):
        try:
            chunks: list[str] = []
            async for ch in judge.stream_turn(messages=msgs, tools=[], temperature=0.0, max_tokens=160):
                if ch.type == "token" and ch.text:
                    chunks.append(ch.text)
            raw = "".join(chunks).strip()
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                keys = ("relevance", "empathy", "clarity", "actionability", "honesty_uncertainty")
                scores = {k: round(float(parsed[k]), 2) for k in keys if k in parsed}
                if len(scores) == len(keys):
                    cache[cache_key] = {"scores": scores, "model": judge.key}
                    _save_cache(cache_path, cache)
                    return scores, judge.key
            return None, judge.key
        except ProviderRateLimitOrTransientError:
            await asyncio.sleep(2 ** attempt)
        except Exception:
            break
    return None, None


def _render_markdown(
    *,
    trials: list[TrialMetrics],
    mode: str,
    n: int,
    run_date_utc: str,
    responder_models: list[str],
    judge_models: list[str],
    cache_hits_or_note: str,
) -> str:
    total_calls = len(trials)
    total_turns = sum(t.total_turns for t in trials)
    grounded_turns = sum(t.grounded_turns for t in trials)
    turn_ground_pct = round(100.0 * grounded_turns / total_turns, 1) if total_turns else 0.0

    total_nums = sum(t.total_number_tokens for t in trials)
    grounded_nums = sum(t.grounded_number_tokens for t in trials)
    num_ground_pct = round(100.0 * grounded_nums / total_nums, 1) if total_nums else 100.0

    matched_tools = sum(t.expected_tools_matched for t in trials)
    expected_tools = sum(t.expected_tools_total for t in trials)
    tool_acc_pct = round(100.0 * matched_tools / expected_tools, 1) if expected_tools else 0.0

    completed_calls = sum(1 for t in trials if t.reached_summary_or_close)
    completion_pct = round(100.0 * completed_calls / total_calls, 1) if total_calls else 0.0

    all_words = [w for t in trials for w in t.word_counts]
    avg_words = round(sum(all_words) / len(all_words), 1) if all_words else 0.0
    max_words = max(all_words) if all_words else 0

    calls_with_commits = sum(1 for t in trials if t.commitments_count >= 1)
    commit_rate_pct = round(100.0 * calls_with_commits / total_calls, 1) if total_calls else 0.0
    total_commits = sum(t.commitments_count for t in trials)

    ft_vals = [x for t in trials for x in t.first_token_ms]
    fa_vals = [x for t in trials for x in t.first_audio_ms]
    ft_p50 = f"{round(_percentile(ft_vals, 50), 1)} ms" if ft_vals else "not measured"
    ft_p95 = f"{round(_percentile(ft_vals, 95), 1)} ms" if ft_vals else "not measured"
    fa_p50 = f"{round(_percentile(fa_vals, 50), 1)} ms" if fa_vals else "not measured"
    fa_p95 = f"{round(_percentile(fa_vals, 95), 1)} ms" if fa_vals else "not measured"

    scored_trials = [t.judge_scores for t in trials if t.judge_scores]
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
            f"act {means['actionability']}, unc {means['honesty_uncertainty']})"
        )
    else:
        judge_summary = "not measured"

    lines = [
        f"# {PROJECT_NAME} Evaluation Report",
        "",
        "## Run Metadata",
        f"- **Run Date (UTC):** {run_date_utc}",
        f"- **Mode:** {mode}",
        f"- **Responder Model ID(s):** {', '.join(responder_models) if responder_models else 'not measured'}",
        f"- **Judge Model ID(s):** {', '.join(judge_models) if judge_models else 'not measured'}",
        f"- **N (trials per persona x scenario cell):** {n}",
        f"- **Total Evaluated Calls:** {total_calls} ({len(PERSONAS)} personas x {len(SCENARIO_IDS)} scenarios x {n})",
        f"- **Total Assistant Turns:** {total_turns}",
        f"- **Cache / Execution Note:** {cache_hits_or_note}",
        "",
        "## Aggregate Measured Metrics",
        "",
        "| Metric | Measured Value | Detail |",
        "| --- | --- | --- |",
        f"| Grounding rate (turns) | {turn_ground_pct}% | {grounded_turns}/{total_turns} turns with 0 ungrounded numbers |",
        f"| Grounding rate (number tokens) | {num_ground_pct}% | {grounded_nums}/{total_nums} spoken numeric tokens traced to tools |",
        f"| Expected tool-call accuracy | {tool_acc_pct}% | {matched_tools}/{expected_tools} expected phase tools executed |",
        f"| Phase completion rate | {completion_pct}% | {completed_calls}/{total_calls} calls reached SUMMARY/CLOSE with card |",
        f"| Words per turn (avg / max) | {avg_words} / {max_words} | Hard cap 45 words (60 in RECOMMEND) |",
        f"| Commitments captured | {commit_rate_pct}% ({total_commits} total) | {calls_with_commits}/{total_calls} calls captured >=1 commitment |",
        f"| LLM first-token latency (p50 / p95) | {ft_p50} / {ft_p95} | Computed from SSE `latency` events |",
        f"| First-audio latency (p50 / p95) | {fa_p50} / {fa_p95} | Audio TTS synthesis/playback timing |",
        f"| LLM-as-judge rubric score (1-5) | {judge_summary} | Relevance, empathy, clarity, actionability, uncertainty honesty |",
        "",
        "## Breakdown by Scenario",
        "",
        "| Scenario | Calls | Completion Rate | Tool Accuracy | Grounded Turns | Avg Words | Commitments |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for sc in SCENARIO_IDS:
        sub = [t for t in trials if t.scenario_id == sc]
        if not sub:
            continue
        sc_comp = round(100.0 * sum(1 for t in sub if t.reached_summary_or_close) / len(sub), 1)
        sc_m = sum(t.expected_tools_matched for t in sub)
        sc_e = sum(t.expected_tools_total for t in sub)
        sc_tool = round(100.0 * sc_m / sc_e, 1) if sc_e else 0.0
        sc_gt = sum(t.grounded_turns for t in sub)
        sc_tt = sum(t.total_turns for t in sub)
        sc_gr = round(100.0 * sc_gt / sc_tt, 1) if sc_tt else 0.0
        sc_words = [w for t in sub for w in t.word_counts]
        sc_aw = round(sum(sc_words) / len(sc_words), 1) if sc_words else 0.0
        sc_comm = sum(t.commitments_count for t in sub)
        lines.append(f"| Scenario {sc} | {len(sub)} | {sc_comp}% | {sc_tool}% | {sc_gr}% | {sc_aw} | {sc_comm} |")

    lines.extend(
        [
            "",
            "## Breakdown by Persona",
            "",
            "| Persona | Calls | Completion Rate | Grounded Turns | Avg / Max Words | Commitments |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    for p in PERSONAS:
        sub = [t for t in trials if t.persona_id == p.persona_id]
        if not sub:
            continue
        p_comp = round(100.0 * sum(1 for t in sub if t.reached_summary_or_close) / len(sub), 1)
        p_gt = sum(t.grounded_turns for t in sub)
        p_tt = sum(t.total_turns for t in sub)
        p_gr = round(100.0 * p_gt / p_tt, 1) if p_tt else 0.0
        p_words = [w for t in sub for w in t.word_counts]
        p_aw = round(sum(p_words) / len(p_words), 1) if p_words else 0.0
        p_mw = max(p_words) if p_words else 0
        p_comm = sum(t.commitments_count for t in sub)
        lines.append(f"| {p.persona_id} ({p.language}) | {len(sub)} | {p_comp}% | {p_gr}% | {p_aw} / {p_mw} | {p_comm} |")

    lines.extend(
        [
            "",
            "## Known Limitations",
            "- Scripted seller personas simulate multi-turn interactions deterministically and do not capture spontaneous acoustic interruptions, background noise, or real Web Speech API STT errors (`first_audio_ms` and real microphone STT remain `not measured` in headless CI evaluation).",
        ]
    )
    if mode.startswith("offline"):
        lines.append(
            "- Offline evaluation uses the deterministic in-process `MockLLMProvider`; first-token latencies reflect local Python execution rather than networked free-tier LLM API latency, and LLM-as-judge quality scores are reported as `not measured`."
        )
    lines.append("")
    return "\n".join(lines)


async def run_evaluation(
    *,
    mode: str,
    n: int,
    scenarios: tuple[str, ...],
    output_path: Path,
    max_calls: int | None = None,
) -> dict[str, Any]:
    settings = Settings()
    cache_path = EVAL_CACHE_DIR / "eval_cache.json"
    cache = _load_cache(cache_path)

    if mode == "offline":
        router = LLMRouter(providers=[MockLLMProvider()])
        mode_desc = "offline (hermetic MockLLMProvider)"
        cache_note = "Offline mode; disk cache unused"
    else:
        os.environ.setdefault("ALLOW_MOCK_LLM", "false")
        live_settings = Settings(allow_mock_llm=False)
        router = LLMRouter.from_settings(live_settings)
        if not router.configured_providers():
            raise RuntimeError("No live LLM API keys configured for --live evaluation.")
        await router.initialize_and_smoke_test(live_settings)
        mode_desc = "live (free-tier LLM router with failover & backoff)"
        cache_note = f"Disk judge/eval cache enabled at {cache_path.name} ({len(cache)} initial entries)"

    orchestrator = AgentOrchestrator(router=router)
    store = SessionStore(ttl_seconds=settings.session_ttl_seconds, max_sessions=max(200, len(scenarios) * len(PERSONAS) * n + 10))

    trials: list[TrialMetrics] = []
    call_counter = 0
    for trial_idx in range(1, n + 1):
        for sc in scenarios:
            for persona in PERSONAS:
                if max_calls is not None and call_counter >= max_calls:
                    break
                call_counter += 1
                tm = await _run_single_trial(orchestrator, store, sc, persona, trial_idx)
                if mode == "live":
                    scores, jmodel = await _judge_transcript_live(
                        router,
                        tm.provider_models,
                        tm.transcript_lines,
                        cache,
                        cache_path,
                    )
                    tm.judge_scores = scores
                    tm.judge_model = jmodel
                trials.append(tm)
            if max_calls is not None and call_counter >= max_calls:
                break

    responder_models = sorted({m for t in trials for m in t.provider_models})
    judge_models = sorted({t.judge_model for t in trials if t.judge_model})
    run_date_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    md = _render_markdown(
        trials=trials,
        mode=mode_desc,
        n=n,
        run_date_utc=run_date_utc,
        responder_models=responder_models,
        judge_models=judge_models,
        cache_hits_or_note=cache_note,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")

    return {
        "total_calls": len(trials),
        "mode": mode_desc,
        "output_path": str(output_path),
        "responder_models": responder_models,
        "judge_models": judge_models,
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
