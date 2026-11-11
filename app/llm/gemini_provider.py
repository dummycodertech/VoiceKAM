"""Google Gemini Free Tier provider using official google-genai SDK.

Preserves thought_signature across multi-turn tool calls so Gemini 3.x models
execute tool loops cleanly without 400 INVALID_ARGUMENT errors.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import Any, AsyncIterator

from google import genai
from google.genai import types

from app.llm.base import (
    CanonicalMessage,
    LLMProvider,
    LLMStreamChunk,
    ProviderRateLimitOrTransientError,
    ToolCallRequest,
)


def _openai_tools_to_gemini(tools: list[dict[str, Any]]) -> list[types.Tool]:
    decls: list[types.FunctionDeclaration] = []
    for t in tools:
        fn = t.get("function") or {}
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        decls.append(
            types.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters_json_schema=params,
            )
        )
    return [types.Tool(function_declarations=decls)] if decls else []


def _canonical_to_gemini(
    messages: list[CanonicalMessage],
) -> tuple[str | None, list[types.Content]]:
    import json

    system_parts: list[str] = []
    contents: list[types.Content] = []
    call_id_to_name: dict[str, str] = {}
    folded_call_ids: set[str] = set()

    for msg in messages:
        if msg.role == "system":
            if msg.content:
                system_parts.append(msg.content)
        elif msg.role == "user":
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=msg.content or " ")],
                )
            )
        elif msg.role == "assistant":
            has_tool_calls = bool(msg.tool_calls)
            has_gemini_sig = any(
                (tc.extra_metadata or {}).get("gemini_thought_signature")
                for tc in msg.tool_calls
            )
            if has_tool_calls and not has_gemini_sig:
                # Cross-provider tool call from Groq/OpenRouter lacks Gemini 3.x thought_signature.
                # Fold into plain text so mid-call failover to Gemini never fails with HTTP 400.
                summary_bits = []
                if msg.content:
                    summary_bits.append(msg.content)
                for tc in msg.tool_calls:
                    call_id_to_name[tc.id] = tc.name
                    folded_call_ids.add(tc.id)
                    summary_bits.append(f"[Called {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})]")
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=" ".join(summary_bits))],
                    )
                )
                continue

            parts: list[types.Part] = []
            if msg.content:
                parts.append(types.Part.from_text(text=msg.content))
            emitted_fc = False
            for tc in msg.tool_calls:
                call_id_to_name[tc.id] = tc.name
                sig_b64 = (tc.extra_metadata or {}).get("gemini_thought_signature")
                sig_bytes = base64.b64decode(sig_b64) if sig_b64 else None
                fc_part = types.Part.from_function_call(name=tc.name, args=tc.arguments)
                if sig_bytes:
                    fc_part.thought_signature = sig_bytes
                parts.append(fc_part)
                emitted_fc = True
            if parts:
                # Gemini requires every model(function_call) turn to come immediately after
                # a user turn (plain user or function_response user turn).
                if emitted_fc and (not contents or contents[-1].role != "user"):
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text="Continue assisting the seller.")],
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
        elif msg.role == "tool":
            fn_name = msg.tool_name or call_id_to_name.get(msg.tool_call_id or "", "tool")
            has_open_fc_turn = bool(
                contents
                and (
                    (
                        contents[-1].role == "model"
                        and contents[-1].parts
                        and any(p.function_call is not None for p in contents[-1].parts)
                    )
                    or (
                        contents[-1].role == "user"
                        and contents[-1].parts
                        and any(p.function_response is not None for p in contents[-1].parts)
                    )
                )
            )
            if (msg.tool_call_id and msg.tool_call_id in folded_call_ids) or not has_open_fc_turn:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=f"[Tool {fn_name} result: {msg.content}]")],
                    )
                )
                continue
            try:
                parsed = json.loads(msg.content) if msg.content else {}
                response_obj = parsed if isinstance(parsed, dict) else {"result": parsed}
            except Exception:
                response_obj = {"result": msg.content}
            fr_part = types.Part.from_function_response(name=fn_name, response=response_obj)
            # Group consecutive tool responses into a single user turn if previous turn was function_response
            if (
                contents
                and contents[-1].role == "user"
                and contents[-1].parts
                and any(p.function_response is not None for p in contents[-1].parts)
            ):
                contents[-1].parts.append(fr_part)
            else:
                contents.append(types.Content(role="user", parts=[fr_part]))

    system_instruction = "\n\n".join(system_parts) if system_parts else None
    if not contents:
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="Deliver your concise opening greeting for this call now.")],
            )
        )
    return system_instruction, contents


class GeminiProvider(LLMProvider):
    """Google Gemini Free Tier streaming provider."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-3.5-flash-lite",
        timeout_seconds: float = 12.0,
    ) -> None:
        self.provider_name = "gemini"
        self.model_name = model_name
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        if not self.is_configured():
            raise ProviderRateLimitOrTransientError("gemini API key is not configured", reason_code="forbidden")

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        system_instruction, contents = _canonical_to_gemini(messages)
        gemini_tools = _openai_tools_to_gemini(tools)

        # Gemini 3.x charges thinking tokens against max_output_tokens; use MINIMAL thinking
        # and generous output headroom so replies never truncate mid-number (spoken length is
        # enforced server-side via enforce_voice_length).
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=gemini_tools or None,
            temperature=temperature,
            max_output_tokens=max(max_tokens, 900),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_level=types.ThinkingLevel.MINIMAL,
            ),
        )

        collected_calls: list[ToolCallRequest] = []

        try:
            async with asyncio.timeout(self.timeout_seconds):
                stream = await client.aio.models.generate_content_stream(
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    for candidate in chunk.candidates or []:
                        content = candidate.content
                        if not content or not content.parts:
                            continue
                        for part in content.parts:
                            if getattr(part, "thought", False):
                                continue
                            if part.text:
                                yield LLMStreamChunk(
                                    type="token",
                                    text=part.text,
                                    provider=self.provider_name,
                                    model=self.model_name,
                                )
                            if part.function_call:
                                fc = part.function_call
                                extra: dict[str, Any] = {}
                                sig = getattr(part, "thought_signature", None)
                                if sig:
                                    extra["gemini_thought_signature"] = base64.b64encode(sig).decode("ascii")
                                collected_calls.append(
                                    ToolCallRequest(
                                        id=getattr(fc, "id", None) or f"gem_{uuid.uuid4().hex[:10]}",
                                        name=fc.name or "",
                                        arguments=dict(fc.args) if fc.args else {},
                                        extra_metadata=extra,
                                    )
                                )
        except asyncio.TimeoutError as exc:
            raise ProviderRateLimitOrTransientError(
                f"gemini ({self.model_name}) timed out after {self.timeout_seconds}s",
                reason_code="timeout",
            ) from exc
        except Exception as exc:
            msg = str(exc)
            lower = msg.lower()
            is_fb = "403" in msg or "permission_denied" in lower or "forbidden" in lower
            is_rl = not is_fb and ("429" in msg or "resource_exhausted" in lower or "quota" in lower)
            is_to = "timeout" in lower or "deadline_exceeded" in lower
            rc = "forbidden" if is_fb else ("rate_limited" if is_rl else ("timeout" if is_to else "server_error"))
            raise ProviderRateLimitOrTransientError(
                f"gemini ({self.model_name}) error: {msg[:250]}",
                status_code=403 if is_fb else (429 if is_rl else None),
                is_rate_limit=is_rl,
                is_forbidden=is_fb,
                reason_code=rc,
            ) from exc

        if collected_calls:
            yield LLMStreamChunk(
                type="tool_calls",
                tool_calls=collected_calls,
                provider=self.provider_name,
                model=self.model_name,
            )

        yield LLMStreamChunk(
            type="done",
            provider=self.provider_name,
            model=self.model_name,
        )

