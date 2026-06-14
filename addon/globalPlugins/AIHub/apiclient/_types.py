"""Public data classes mirroring the openai-python types used by the addon.

Kept lightweight and dependency-free so the addon works with stock NVDA
without bundling the official openai package.
"""
from __future__ import annotations

from typing import Any, Optional


class ChoiceDelta:
	"""Streaming delta carrying incremental content/reasoning."""

	__slots__ = ("content", "reasoning")

	def __init__(self, content: Optional[str] = None, reasoning: Optional[str] = None):
		self.content = content or ""
		self.reasoning = reasoning or ""


class StreamChoice:
	"""One choice within a streaming chunk."""

	__slots__ = ("delta", "finish_reason")

	def __init__(self, delta: ChoiceDelta, finish_reason: Optional[str] = None):
		self.delta = delta
		self.finish_reason = finish_reason


class ChoiceMessage:
	"""Final assistant message returned by non-streaming responses."""

	__slots__ = ("content", "audio", "reasoning")

	def __init__(
		self,
		content: str = "",
		audio: Optional[dict] = None,
		reasoning: str = "",
	):
		self.content = content or ""
		# {"data": base64_str, "format": "wav"} when the model returns audio.
		self.audio = audio
		self.reasoning = reasoning or ""


class Choice:
	"""One choice within a non-streaming completion."""

	__slots__ = ("message", "index")

	def __init__(self, message: ChoiceMessage, index: int = 0):
		self.message = message
		self.index = index


class ChatCompletion:
	"""Aggregated non-streaming completion result."""

	__slots__ = ("choices", "usage", "response_id", "citations", "encrypted_reasoning")

	def __init__(
		self,
		choices: list,
		usage: Optional[dict] = None,
		response_id: str = "",
		citations: Optional[list] = None,
		encrypted_reasoning: Optional[list] = None,
	):
		self.choices = choices
		self.usage = usage or {}
		# xAI / OpenAI Responses API metadata (stateful follow-ups, source URLs).
		self.response_id = response_id or ""
		self.citations = list(citations or [])
		self.encrypted_reasoning = list(encrypted_reasoning or [])


class Transcription:
	"""Result of an audio transcription / translation call."""

	__slots__ = ("text", "payload", "response_format")

	def __init__(self, text: str, payload: Any = None, response_format: str = "json"):
		self.text = text or ""
		self.payload = payload
		self.response_format = response_format


class StreamEvent:
	"""Generic stream event yielded by the streaming generators.

	Mimics the shape of openai-python's ChatCompletionChunk so the
	chatcompletion consumer can use both interchangeably.
	"""

	__slots__ = ("choices", "usage", "error", "response_id", "citations", "encrypted_reasoning")

	def __init__(
		self,
		choices: Optional[list] = None,
		usage: Optional[dict] = None,
		error: Optional[dict] = None,
		response_id: str = "",
		citations: Optional[list] = None,
		encrypted_reasoning: Optional[list] = None,
	):
		self.choices = choices or []
		self.usage = usage or {}
		self.error = error
		self.response_id = response_id or ""
		self.citations = list(citations or [])
		self.encrypted_reasoning = list(encrypted_reasoning or [])


def build_stream_event(
	content: str = "",
	reasoning: str = "",
	finish_reason: Optional[str] = None,
	usage: Optional[dict] = None,
	error: Optional[dict] = None,
	response_id: str = "",
	citations: Optional[list] = None,
	encrypted_reasoning: Optional[list] = None,
) -> StreamEvent:
	"""Convenience constructor used by all stream parsers."""
	delta = ChoiceDelta(content, reasoning=reasoning)
	choice = StreamChoice(delta, finish_reason)
	return StreamEvent(
		choices=[choice],
		usage=usage or {},
		error=error,
		response_id=response_id,
		citations=citations,
		encrypted_reasoning=encrypted_reasoning,
	)
