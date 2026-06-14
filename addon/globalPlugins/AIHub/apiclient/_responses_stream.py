"""OpenAI / xAI Responses API SSE parser (event-driven item lifecycle).

Official references:
- OpenAI reasoning + interleaved thinking: https://developers.openai.com/api/docs/guides/reasoning
- xAI reasoning summaries: https://docs.x.ai/developers/model-capabilities/text/reasoning
- Responses streaming events: OpenAI Responses API streaming reference

Lifecycle for reasoning + assistant text:
1. ``response.output_item.added`` (``type=reasoning``)
2. Reasoning deltas … ``response.output_item.done`` (reasoning)
3. ``response.output_item.added`` (``type=message``)
4. ``response.output_text.delta`` … ``response.output_item.done`` (message)
5. ``response.completed`` (reconcile final ``output``)
"""
from __future__ import annotations

from typing import Iterator

from ._parsers import (
	_extract_responses_assistant_text,
	_extract_responses_encrypted_reasoning,
	_extract_responses_reasoning_from_item,
	_extract_responses_reasoning_from_output,
	_merge_reasoning,
	_responses_final_assistant_text,
)
from ._sse import DONE, iter_sse_events
from ._think_tags import (
	_apply_think_chain_to_chunk,
	_extract_reasoning_text,
	_flush_think_chain,
	_new_think_chain_states,
	_split_ollama_think_inline,
)
from ._types import StreamEvent, build_stream_event
from ._usage import _normalize_usage

_REASONING_DELTA_EVENTS = frozenset({
	"response.reasoning_summary_text.delta",
	"response.reasoning_text.delta",
	"response.reasoning.delta",
})
_REASONING_DONE_EVENTS = frozenset({
	"response.reasoning_text.done",
	"response.reasoning_summary_text.done",
	"response.reasoning.done",
})


class _ResponsesStreamParser:
	"""Maps Responses SSE events to thinking vs answer channels using item types."""

	def __init__(
		self,
		*,
		interleaved_reasoning: bool = False,
		strip_inline_think_tags: bool = False,
	) -> None:
		self._interleaved_reasoning = interleaved_reasoning
		self._summaries_only = not interleaved_reasoning
		self._think_states = _new_think_chain_states() if strip_inline_think_tags else None
		self._item_types: dict[str, str] = {}
		self._assistant_message_started = False
		self._streamed_reasoning_ids: set[str] = set()
		self._streamed_message_ids: set[str] = set()

	def _register_item(self, item: dict) -> str:
		item_id = item.get("id")
		item_type = str(item.get("type", "")).lower()
		if isinstance(item_id, str) and item_id.strip():
			self._item_types[item_id.strip()] = item_type
			return item_id.strip()
		return ""

	def _is_reasoning_item(self, item_id: str | None) -> bool:
		if isinstance(item_id, str) and item_id.strip():
			item_id = item_id.strip()
			item_type = self._item_types.get(item_id, "")
			if item_type == "message":
				return False
			if item_type == "reasoning" or "reasoning" in item_type:
				return True
			if item_type:
				return False
		if self._interleaved_reasoning:
			return True
		return not self._assistant_message_started

	def _reasoning_from_item(self, item: dict) -> str:
		return _extract_responses_reasoning_from_item(
			item,
			summaries_only=self._summaries_only,
		)

	def _emit_content_events(self, text: str) -> list[StreamEvent]:
		if not text:
			return []
		if self._think_states:
			text, think_from_tags = _apply_think_chain_to_chunk(text, self._think_states)
			events: list[StreamEvent] = []
			if think_from_tags:
				events.append(build_stream_event(reasoning=think_from_tags))
			if text:
				events.append(build_stream_event(content=text))
			return events
		return [build_stream_event(content=text)]

	def flush(self) -> list[StreamEvent]:
		if not self._think_states:
			return []
		flushed_content, flushed_reasoning = _flush_think_chain(self._think_states)
		events: list[StreamEvent] = []
		if flushed_reasoning:
			events.append(build_stream_event(reasoning=flushed_reasoning))
		if flushed_content:
			events.extend(self._emit_content_events(flushed_content))
		return events

	def feed(self, evt_type: str, data: dict) -> list[StreamEvent]:
		if evt_type == "response.output_item.added":
			return self._on_output_item_added(data.get("item") or {})
		if evt_type == "response.output_item.done":
			return self._on_output_item_done(data.get("item") or {})
		if evt_type in _REASONING_DELTA_EVENTS:
			return self._on_reasoning_delta(data)
		if evt_type in _REASONING_DONE_EVENTS:
			return self._on_reasoning_done(data)
		if evt_type == "response.output_text.delta":
			return self._on_output_text_delta(data)
		if evt_type == "response.refusal.delta":
			return self._on_refusal_delta(data)
		if evt_type == "response.output_text.done":
			return self._on_output_text_done(data)
		if evt_type == "response.refusal.done":
			return self._on_refusal_done(data)
		if evt_type == "response.completed":
			return self._on_completed(data)
		if evt_type in ("response.failed", "error"):
			return [build_stream_event(finish_reason="error", error=_responses_resp_error_payload(data))]
		return []

	def _on_output_item_added(self, item: dict) -> list[StreamEvent]:
		if not isinstance(item, dict):
			return []
		item_type = str(item.get("type", "")).lower()
		item_id = self._register_item(item)
		if item_type == "message":
			self._assistant_message_started = True
			return []
		if item_type == "reasoning" or "reasoning" in item_type:
			if item_id and item_id in self._streamed_reasoning_ids:
				return []
			reasoning = self._reasoning_from_item(item)
			if reasoning and item_id:
				self._streamed_reasoning_ids.add(item_id)
			if reasoning:
				return [build_stream_event(reasoning=reasoning)]
		return []

	def _on_output_item_done(self, item: dict) -> list[StreamEvent]:
		if not isinstance(item, dict):
			return []
		item_type = str(item.get("type", "")).lower()
		item_id = self._register_item(item)
		if item_type == "reasoning" or "reasoning" in item_type:
			if item_id and item_id in self._streamed_reasoning_ids:
				return []
			reasoning = self._reasoning_from_item(item)
			if reasoning:
				if item_id:
					self._streamed_reasoning_ids.add(item_id)
				return [build_stream_event(reasoning=reasoning)]
			return []
		if item_type == "message":
			self._assistant_message_started = True
			if item_id and item_id in self._streamed_message_ids:
				return []
			text = _extract_responses_assistant_text([item])
			if text:
				return self._emit_content_events(text)
		return []

	def _on_reasoning_delta(self, data: dict) -> list[StreamEvent]:
		if not self._interleaved_reasoning and self._assistant_message_started:
			return []
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and not self._is_reasoning_item(item_id.strip()):
			return []
		delta = data.get("delta")
		text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
		if not text:
			text = _extract_reasoning_text(data)
		if not text:
			return []
		if isinstance(item_id, str) and item_id.strip():
			self._streamed_reasoning_ids.add(item_id.strip())
		return [build_stream_event(reasoning=text)]

	def _on_reasoning_done(self, data: dict) -> list[StreamEvent]:
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and item_id.strip() in self._streamed_reasoning_ids:
			return []
		text = data.get("text")
		if not isinstance(text, str) or not text:
			text = _extract_reasoning_text(data)
		if not text:
			return []
		if isinstance(item_id, str) and item_id.strip():
			self._streamed_reasoning_ids.add(item_id.strip())
		return [build_stream_event(reasoning=text)]

	def _on_output_text_delta(self, data: dict) -> list[StreamEvent]:
		self._assistant_message_started = True
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip():
			self._streamed_message_ids.add(item_id.strip())
		delta = data.get("delta")
		text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
		if text:
			return self._emit_content_events(text)
		return []

	def _on_refusal_delta(self, data: dict) -> list[StreamEvent]:
		self._assistant_message_started = True
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip():
			self._streamed_message_ids.add(item_id.strip())
		delta = data.get("delta")
		text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
		if text:
			return self._emit_content_events(text)
		return []

	def _on_output_text_done(self, data: dict) -> list[StreamEvent]:
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and item_id.strip() in self._streamed_message_ids:
			return []
		text = data.get("text")
		if isinstance(text, str) and text:
			self._assistant_message_started = True
			return self._emit_content_events(text)
		return []

	def _on_refusal_done(self, data: dict) -> list[StreamEvent]:
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and item_id.strip() in self._streamed_message_ids:
			return []
		text = data.get("refusal")
		if isinstance(text, str) and text:
			self._assistant_message_started = True
			return self._emit_content_events(text)
		return []

	def _on_completed(self, data: dict) -> list[StreamEvent]:
		usage = _normalize_usage(_responses_resp_completed_usage(data))
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
		output = resp_obj.get("output") or []
		reconcile_reasoning = _extract_responses_reasoning_from_output(
			output,
			summaries_only=self._summaries_only,
		)
		reconcile_content = _responses_final_assistant_text(resp_obj)
		if reconcile_content and self._interleaved_reasoning:
			visible, think_inline, _ = _split_ollama_think_inline(reconcile_content, in_think=False)
			reconcile_content = visible.strip()
			if think_inline:
				reconcile_reasoning = _merge_reasoning(
					reconcile_reasoning,
					think_inline,
					separator="\n",
				).strip()
		kwargs = {
			"finish_reason": "stop",
			"response_id": response_id,
			"citations": citations,
			"encrypted_reasoning": encrypted_reasoning,
			"reconcile_reasoning": reconcile_reasoning,
			"reconcile_content": reconcile_content,
		}
		if usage:
			kwargs["usage"] = usage
		return [build_stream_event(**kwargs)]


def _responses_resp_completed_usage(data: dict):
	resp_obj = data.get("response")
	if isinstance(resp_obj, dict):
		usage = resp_obj.get("usage")
		if isinstance(usage, dict):
			return usage
	return data.get("usage")


def _responses_resp_error_payload(data: dict) -> dict:
	resp_obj = data.get("response")
	if isinstance(resp_obj, dict) and isinstance(resp_obj.get("error"), dict):
		return resp_obj["error"]
	if isinstance(data.get("error"), dict):
		return data["error"]
	return {"message": str(data.get("message") or "Stream failed")}


def _responses_safe_close(resp) -> None:
	try:
		resp.close()
	except Exception:
		pass


def stream_responses_api(
	resp,
	*,
	interleaved_reasoning: bool = False,
	strip_inline_think_tags: bool = False,
) -> Iterator[StreamEvent]:
	"""Parse ``/v1/responses`` SSE using the documented output-item lifecycle."""
	parser = _ResponsesStreamParser(
		interleaved_reasoning=interleaved_reasoning,
		strip_inline_think_tags=strip_inline_think_tags,
	)
	try:
		for data in iter_sse_events(resp):
			if data is DONE:
				for event in parser.flush():
					yield event
				return
			if not isinstance(data, dict):
				continue
			evt_type = str(data.get("type", "")).lower()
			if not evt_type:
				continue
			for event in parser.feed(evt_type, data):
				yield event
			if evt_type in ("response.completed", "response.failed", "error"):
				return
	finally:
		_responses_safe_close(resp)


def stream_xai_responses(resp) -> Iterator[StreamEvent]:
	"""Parse xAI ``/v1/responses`` SSE (reasoning summaries before answer)."""
	return stream_responses_api(
		resp,
		interleaved_reasoning=False,
		strip_inline_think_tags=False,
	)
