"""xAI Responses API SSE parser (event-driven, per official lifecycle).

Official references:
- Reasoning summaries: https://docs.x.ai/developers/model-capabilities/text/reasoning
- Response structure: https://docs.x.ai/developers/model-capabilities/text/comparison
- Event lifecycle (reasoning item → message item → output_text deltas):
  OpenAI Responses API streaming field guide (Tables B & C).

Lifecycle for reasoning + assistant text:
1. ``response.output_item.added`` (``type=reasoning``)
2. ``response.reasoning_summary_text.delta`` … ``response.output_item.done`` (reasoning)
3. ``response.output_item.added`` (``type=message``)
4. ``response.output_text.delta`` … ``response.output_item.done`` (message)
5. ``response.completed``
"""
from __future__ import annotations

from typing import Iterator

from ._parsers import (
	_extract_responses_encrypted_reasoning,
	_extract_xai_assistant_text,
	_extract_xai_reasoning_summaries,
	_xai_final_response_text,
)
from ._sse import DONE, iter_sse_events
from ._think_tags import _extract_reasoning_text
from ._types import StreamEvent, build_stream_event
from ._usage import _normalize_usage


class _XaiResponsesStreamParser:
	"""Maps Responses SSE events to thinking vs answer channels using item types."""

	def __init__(self) -> None:
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
		if not item_id:
			return not self._assistant_message_started
		return self._item_types.get(item_id, "reasoning") == "reasoning"

	def feed(self, evt_type: str, data: dict) -> list[StreamEvent]:
		if evt_type == "response.output_item.added":
			return self._on_output_item_added(data.get("item") or {})
		if evt_type == "response.output_item.done":
			return self._on_output_item_done(data.get("item") or {})
		if evt_type == "response.reasoning_summary_text.delta":
			return self._on_reasoning_summary_delta(data)
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
			return [build_stream_event(finish_reason="error", error=_xai_resp_error_payload(data))]
		return []

	def _on_output_item_added(self, item: dict) -> list[StreamEvent]:
		if not isinstance(item, dict):
			return []
		item_type = str(item.get("type", "")).lower()
		item_id = self._register_item(item)
		if item_type == "message":
			self._assistant_message_started = True
			return []
		if item_type == "reasoning":
			if item_id and item_id in self._streamed_reasoning_ids:
				return []
			reasoning = _extract_xai_reasoning_summaries([item])
			if reasoning and item_id:
				self._streamed_reasoning_ids.add(item_id)
				return [build_stream_event(reasoning=reasoning)]
		return []

	def _on_output_item_done(self, item: dict) -> list[StreamEvent]:
		if not isinstance(item, dict):
			return []
		item_type = str(item.get("type", "")).lower()
		item_id = self._register_item(item)
		if item_type == "reasoning":
			if item_id and item_id in self._streamed_reasoning_ids:
				return []
			reasoning = _extract_xai_reasoning_summaries([item])
			if reasoning:
				if item_id:
					self._streamed_reasoning_ids.add(item_id)
				return [build_stream_event(reasoning=reasoning)]
			return []
		if item_type == "message":
			self._assistant_message_started = True
			if item_id and item_id in self._streamed_message_ids:
				return []
			text = _extract_xai_assistant_text([item])
			if text:
				return [build_stream_event(content=text)]
		return []

	def _on_reasoning_summary_delta(self, data: dict) -> list[StreamEvent]:
		if self._assistant_message_started:
			return []
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and not self._is_reasoning_item(item_id.strip()):
			return []
		delta = data.get("delta")
		text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
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
			return [build_stream_event(content=text)]
		return []

	def _on_refusal_delta(self, data: dict) -> list[StreamEvent]:
		self._assistant_message_started = True
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip():
			self._streamed_message_ids.add(item_id.strip())
		delta = data.get("delta")
		text = delta if isinstance(delta, str) else _extract_reasoning_text(delta)
		if text:
			return [build_stream_event(content=text)]
		return []

	def _on_output_text_done(self, data: dict) -> list[StreamEvent]:
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and item_id.strip() in self._streamed_message_ids:
			return []
		text = data.get("text")
		if isinstance(text, str) and text:
			self._assistant_message_started = True
			return [build_stream_event(content=text)]
		return []

	def _on_refusal_done(self, data: dict) -> list[StreamEvent]:
		item_id = data.get("item_id")
		if isinstance(item_id, str) and item_id.strip() and item_id.strip() in self._streamed_message_ids:
			return []
		text = data.get("refusal")
		if isinstance(text, str) and text:
			self._assistant_message_started = True
			return [build_stream_event(content=text)]
		return []

	def _on_completed(self, data: dict) -> list[StreamEvent]:
		usage = _normalize_usage(_xai_resp_completed_usage(data))
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
		reconcile_reasoning = _extract_xai_reasoning_summaries(resp_obj.get("output") or [])
		reconcile_content = _xai_final_response_text(resp_obj)
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


def _xai_resp_completed_usage(data: dict):
	resp_obj = data.get("response")
	if isinstance(resp_obj, dict):
		usage = resp_obj.get("usage")
		if isinstance(usage, dict):
			return usage
	return data.get("usage")


def _xai_resp_error_payload(data: dict) -> dict:
	resp_obj = data.get("response")
	if isinstance(resp_obj, dict) and isinstance(resp_obj.get("error"), dict):
		return resp_obj["error"]
	if isinstance(data.get("error"), dict):
		return data["error"]
	return {"message": str(data.get("message") or "Stream failed")}


def _xai_safe_close(resp) -> None:
	try:
		resp.close()
	except Exception:
		pass


def stream_xai_responses(resp) -> Iterator[StreamEvent]:
	"""Parse xAI ``/v1/responses`` SSE using the documented item lifecycle."""
	parser = _XaiResponsesStreamParser()
	try:
		for data in iter_sse_events(resp):
			if data is DONE:
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
		_xai_safe_close(resp)
