"""Streaming generators, one per supported wire protocol.

We implement three distinct stream parsers because the on-the-wire shapes
differ enough that a generic parser would be a maintenance liability:

* ``stream_chat_completions``: OpenAI Chat Completions / Mistral / OpenRouter /
  DeepSeek / Ollama (OpenAI-compat).
* ``stream_gemini_generate_content``: native Gemini ``streamGenerateContent``.
* ``stream_responses``: OpenAI Responses API (event-typed SSE).
* ``stream_anthropic``: Anthropic Messages API.

Every generator yields ``StreamEvent`` instances with a stable shape so the
chatcompletion consumer thread can iterate them uniformly.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from ._parsers import _extract_responses_encrypted_reasoning
from ._sse import DONE, iter_sse_events
from ._think_tags import (
	_apply_think_chain_to_chunk,
	_extract_reasoning_text,
	_flush_think_chain,
	_new_think_chain_states,
	_split_text_and_reasoning_from_parts,
)
from ._types import StreamEvent, build_stream_event
from ._usage import _merge_usage, _normalize_usage, _normalize_usage_from_payload


# ---------------------------------------------------------------------------
# OpenAI Chat Completions and OpenAI-compatible providers.
# ---------------------------------------------------------------------------

def stream_chat_completions(resp) -> Iterator[StreamEvent]:
	"""Parse a Chat Completions SSE stream and yield ``StreamEvent``s.

	Token order is preserved: each event yields whatever content/reasoning was
	in the corresponding SSE delta, in arrival order. The think-tag stripper
	holds back at most a handful of bytes so first-token latency stays low.

	Inline stripping for ``<think>`` / ``<thinking>`` / ``<thought>`` is run on
	every content chunk regardless of whether the provider also emits structured
	reasoning. This is required because Gemini/Gemma split the inline wrapper
	across many chunks (open tag in chunk A, body in chunk B, closing tag in
	chunk C); a per-chunk gate that only fires when ``<thought`` happens to be
	in the current chunk would let the body and closing tag leak into the
	visible answer once structured reasoning has been seen. DeepSeek/OpenAI do
	not embed XML wrappers in their content stream, so always-on stripping is a
	no-op for them.
	"""
	think_states = _new_think_chain_states()
	# Tracked only to know when to flush the held-back carry into the first
	# content chunk that follows a reasoning-only delta (so we never reorder
	# tokens or drop a few held-back bytes when reasoning starts).
	structured_reasoning_seen = False
	try:
		for data in iter_sse_events(resp):
			if data is DONE:
				flushed_content, flushed_reasoning = _flush_think_chain(think_states)
				if flushed_content or flushed_reasoning:
					yield build_stream_event(flushed_content, flushed_reasoning)
				return
			if not isinstance(data, dict):
				continue

			usage = _payload_usage(data)
			content, reasoning, finish, error = _parse_chat_completion_chunk(data)

			# Always run inline tag stripping. The per-pair state machine carries
			# at most a partial-tag suffix between chunks so a tag split across
			# chunk boundaries is still recognized. Reasoning extracted from
			# inline tags is appended to the structured ``reasoning`` channel
			# (deduplicated) so the UI receives one coherent reasoning stream.
			if think_states and content:
				content, think_from_tags = _apply_think_chain_to_chunk(content, think_states)
				if think_from_tags:
					reasoning = _merge_reasoning(reasoning, think_from_tags)

			# When structured reasoning first appears, flush any held-back content
			# carry so no characters are lost from the visible answer.
			if not structured_reasoning_seen and (reasoning or "").strip():
				structured_reasoning_seen = True
				flushed_content, flushed_reasoning = _flush_think_chain(think_states)
				if flushed_content:
					content = flushed_content + (content or "")
				if flushed_reasoning:
					reasoning = _merge_reasoning(reasoning, flushed_reasoning)

			if not (content or reasoning or usage or finish or error):
				# Heartbeat / role-only chunk; skip to reduce consumer wakeups.
				continue
			yield build_stream_event(content or "", reasoning or "", finish, usage, error)
		# Connection ended without [DONE]: flush whatever carry remains.
		flushed_content, flushed_reasoning = _flush_think_chain(think_states)
		if flushed_content or flushed_reasoning:
			yield build_stream_event(flushed_content, flushed_reasoning)
	finally:
		_safe_close(resp)


def _merge_reasoning(base: str, addition: str) -> str:
	"""Append ``addition`` to ``base`` while avoiding obvious duplication.

	The structured reasoning channel and the inline-tag stripper can both
	contribute to the reasoning text (Gemma sometimes ships a short summary in
	a ``thought`` delta plus the full chain inside ``<thought>...</thought>``).
	Empty fragments are skipped, and an addition that already appears verbatim
	in the base is dropped so the user does not see the same paragraph twice.
	"""
	if not addition:
		return base or ""
	if not base:
		return addition
	if addition in base:
		return base
	return base + addition


def _payload_usage(data: dict) -> dict:
	"""Resolve the usage dict from either the top-level or a nested ``response`` field."""
	usage = _normalize_usage_from_payload(data)
	if usage:
		return usage
	nested = data.get("response")
	if isinstance(nested, dict):
		return _normalize_usage_from_payload(nested)
	return {}


def _parse_chat_completion_chunk(data: dict) -> tuple[str, str, Optional[str], Optional[dict]]:
	"""Extract (content, reasoning, finish_reason, error) from one chat-completions chunk.

	Handles the OpenRouter mid-stream error shape (top-level ``error`` key with
	``finish_reason: "error"``) so the caller can surface it to the user.
	"""
	error = _chunk_error(data)
	choices = data.get("choices") or []
	content = ""
	reasoning = ""
	finish: Optional[str] = None
	if choices:
		c = choices[0] if isinstance(choices, list) else None
		if isinstance(c, dict):
			content, reasoning = _parse_chat_delta(c.get("delta") or {})
			finish = c.get("finish_reason")
			if finish is None and isinstance(c.get("delta"), dict):
				finish = c["delta"].get("finish_reason")
	if finish is None:
		finish = data.get("finish_reason")
	return content, reasoning, finish, error


def _chunk_error(data: dict) -> Optional[dict]:
	"""Return the OpenRouter-style mid-stream error payload, or None."""
	err = data.get("error")
	if isinstance(err, dict):
		return err
	if isinstance(err, str) and err:
		return {"message": err}
	return None


def _parse_chat_delta(delta: Any) -> tuple[str, str]:
	"""Extract (content, reasoning) from one chat-completions delta object."""
	if isinstance(delta, str):
		return delta, ""
	if not isinstance(delta, dict):
		return "", ""
	content_val = delta.get("content")
	if isinstance(content_val, list):
		content, reasoning = _split_text_and_reasoning_from_parts(content_val)
	elif isinstance(content_val, str):
		content, reasoning = content_val, ""
	else:
		content, reasoning = "", ""
	if not reasoning:
		for key in ("reasoning", "reasoning_content", "thinking", "thinking_content", "thought"):
			text = _extract_reasoning_text(delta.get(key))
			if text:
				reasoning = text
				break
	if not reasoning:
		details = delta.get("reasoning_details")
		if isinstance(details, list):
			reasoning = _extract_reasoning_text(details)
	# Refusals stream in delta.refusal alongside (or instead of) content.
	refusal = delta.get("refusal")
	if isinstance(refusal, str) and refusal:
		content = (content or "") + refusal
	if content is not None and not isinstance(content, str):
		content = str(content)
	return content or "", reasoning or ""


# ---------------------------------------------------------------------------
# OpenAI Responses API.
# ---------------------------------------------------------------------------

def stream_responses(resp, provider: str = "") -> Iterator[StreamEvent]:
	"""Parse an OpenAI Responses API SSE stream (OpenAI file-input path).

	xAI traffic uses ``stream_xai_responses`` in ``_xai_responses_stream.py``.
	"""
	think_states = _new_think_chain_states()
	try:
		for data in iter_sse_events(resp):
			if data is DONE:
				flushed_content, flushed_reasoning = _flush_think_chain(think_states)
				if flushed_content or flushed_reasoning:
					yield build_stream_event(flushed_content, flushed_reasoning)
				return
			if not isinstance(data, dict):
				continue
			evt_type = str(data.get("type", "")).lower()
			if not evt_type:
				continue

			if evt_type == "response.output_text.delta" or evt_type == "response.refusal.delta":
				delta = data.get("delta")
				text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
				if not text:
					continue
				text, think_from_tags = _apply_think_chain_to_chunk(text, think_states)
				if think_from_tags:
					yield build_stream_event(reasoning=think_from_tags)
				if text:
					yield build_stream_event(content=text)
				continue

			if evt_type in (
				"response.reasoning_text.delta",
				"response.reasoning_summary_text.delta",
			):
				delta = data.get("delta")
				text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
				if text:
					yield build_stream_event(reasoning=text)
				continue

			if evt_type == "response.output_item.added":
				item = data.get("item") or {}
				text_from_parts, reasoning_from_parts = _split_text_and_reasoning_from_parts(
					item.get("content")
				)
				if text_from_parts or reasoning_from_parts:
					yield build_stream_event(text_from_parts, reasoning_from_parts)
				continue

			if evt_type == "response.completed":
				usage = _normalize_usage(_resp_completed_usage(data))
				resp_obj = data.get("response") if isinstance(data.get("response"), dict) else {}
				response_id = ""
				rid = resp_obj.get("id")
				if isinstance(rid, str) and rid.strip():
					response_id = rid.strip()
				citations: list[str] = []
				raw_citations = resp_obj.get("citations")
				if isinstance(raw_citations, list):
					for item in raw_citations:
						if isinstance(item, str) and item.strip():
							citations.append(item.strip())
				encrypted_reasoning = _extract_responses_encrypted_reasoning(resp_obj)
				if usage:
					yield build_stream_event(
						usage=usage,
						finish_reason="stop",
						response_id=response_id,
						citations=citations,
						encrypted_reasoning=encrypted_reasoning,
					)
				else:
					yield build_stream_event(
						finish_reason="stop",
						response_id=response_id,
						citations=citations,
						encrypted_reasoning=encrypted_reasoning,
					)
				return

			if evt_type in ("response.failed", "error"):
				err = _resp_error_payload(data)
				yield build_stream_event(finish_reason="error", error=err)
				return
	finally:
		_safe_close(resp)


def _resp_completed_usage(data: dict) -> Any:
	"""Locate the usage dict inside a ``response.completed`` event."""
	resp_obj = data.get("response")
	if isinstance(resp_obj, dict):
		usage = resp_obj.get("usage")
		if isinstance(usage, dict):
			return usage
	return data.get("usage")


def _resp_error_payload(data: dict) -> dict:
	resp_obj = data.get("response")
	if isinstance(resp_obj, dict) and isinstance(resp_obj.get("error"), dict):
		return resp_obj["error"]
	if isinstance(data.get("error"), dict):
		return data["error"]
	return {"message": str(data.get("message") or "Stream failed")}


# ---------------------------------------------------------------------------
# Anthropic Messages API.
# ---------------------------------------------------------------------------

def stream_anthropic(resp) -> Iterator[StreamEvent]:
	"""Parse an Anthropic Messages SSE stream into the addon's StreamEvent shape."""
	usage_acc: dict = {}
	try:
		for data in iter_sse_events(resp):
			if data is DONE:
				return
			if not isinstance(data, dict):
				continue
			evt_type = data.get("type")

			# Track usage across message_start (input_tokens) and message_delta
			# (cumulative output_tokens) — neither chunk alone has the full picture.
			if evt_type == "message_start":
				msg = data.get("message") or {}
				if isinstance(msg, dict):
					usage_acc = _merge_usage(usage_acc, _normalize_usage(msg.get("usage")))
				continue

			if evt_type == "message_delta":
				usage_acc = _merge_usage(usage_acc, _normalize_usage(data.get("usage")))
				finish = data.get("delta", {}).get("stop_reason") if isinstance(data.get("delta"), dict) else None
				if usage_acc:
					yield build_stream_event(usage=dict(usage_acc), finish_reason=finish or None)
				continue

			if evt_type == "content_block_delta":
				delta = data.get("delta") or {}
				d_type = delta.get("type")
				if d_type == "text_delta":
					text = delta.get("text", "") or ""
					if text:
						yield build_stream_event(content=text)
				elif d_type in ("thinking_delta", "reasoning_delta"):
					th = delta.get("thinking")
					if not isinstance(th, str) or not th:
						th = delta.get("text") or ""
					reasoning = th if isinstance(th, str) else _extract_reasoning_text(delta)
					if reasoning:
						yield build_stream_event(reasoning=reasoning)
				# Ignore signature_delta and input_json_delta — chat use case has no use for them.
				continue

			if evt_type == "message_stop":
				if usage_acc:
					yield build_stream_event(usage=dict(usage_acc), finish_reason="stop")
				else:
					yield build_stream_event(finish_reason="stop")
				return

			if evt_type == "error":
				err = data.get("error")
				err_payload = err if isinstance(err, dict) else {"message": str(err or "Stream failed")}
				yield build_stream_event(finish_reason="error", error=err_payload)
				return
			# message_start / content_block_start / content_block_stop / ping are not
			# relevant to the consumer; they're skipped here.
	finally:
		_safe_close(resp)


def stream_gemini_generate_content(resp) -> Iterator[StreamEvent]:
	"""Parse a native Gemini ``streamGenerateContent`` SSE stream."""
	from ._parsers import _gemini_parts_text

	latest_usage: dict = {}
	try:
		for data in iter_sse_events(resp):
			if data is DONE:
				if latest_usage:
					yield build_stream_event(usage=dict(latest_usage), finish_reason="stop")
				return
			if not isinstance(data, dict):
				continue
			usage = _normalize_usage_from_payload(data)
			if usage:
				latest_usage = _merge_usage(latest_usage, usage)
			content, reasoning = _gemini_parts_text(data)
			finish = None
			for candidate in data.get("candidates") or []:
				if isinstance(candidate, dict) and candidate.get("finishReason"):
					finish = str(candidate.get("finishReason")).lower()
			if content or reasoning:
				yield build_stream_event(content=content, reasoning=reasoning)
			if finish and finish not in ("unspecified", "unknown"):
				if latest_usage:
					yield build_stream_event(usage=dict(latest_usage), finish_reason=finish)
				return
	finally:
		_safe_close(resp)


def _safe_close(resp) -> None:
	try:
		resp.close()
	except Exception:
		pass
