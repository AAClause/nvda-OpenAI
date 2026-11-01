"""Inline thinking-tag handling for streaming and non-streaming output.

Models embed chain-of-thought inside the assistant ``content`` string using
different XML-like wrappers, for example:

* ``<think>`` — Anthropic-style / many open-weight models
* ``<thinking>`` — common alias
* ``<thought>`` — Google Gemma and some Gemini OpenAI-compatible outputs

We split those regions out into the reasoning channel so they can be shown in
the dedicated thinking segment (with ``<think>`` framing in the UI)
or hidden when the user turns thinking off, and they are not spoken as the
answer text.

For providers that expose structured reasoning only on a separate delta field
(``delta.reasoning``, Anthropic content blocks, OpenAI ``reasoning_text`` events,
…), the stream layer may skip inline parsing once that channel is active so we
do not mis-split model output that merely mentions ``<`` characters.
"""
from __future__ import annotations

from typing import Any


# Both elements of every pair MUST be non-empty: an empty tag would make
# str.find("", i) return i and ``i = j + len("")`` would never advance,
# producing an infinite loop in ``_split_think_pair_inline``.
_THINK_TAG_PAIRS: tuple[tuple[str, str], ...] = tuple(
	(o, c) for (o, c) in (
		("<think>", "</think>"),
		("<thinking>", "</thinking>"),
		("<thought>", "</thought>"),
	)
	if o and c
)


# ---------------------------------------------------------------------------
# Reasoning-text extraction (used by both streaming and non-streaming parsers).
# ---------------------------------------------------------------------------

def _extract_reasoning_text(value: Any) -> str:
	"""Best-effort extractor for reasoning/thinking payloads of arbitrary shape."""
	if isinstance(value, str):
		return value
	if isinstance(value, list):
		parts = []
		for item in value:
			txt = _extract_reasoning_text(item)
			if txt:
				parts.append(txt)
		return "\n".join(parts).strip()
	if isinstance(value, dict):
		for key in ("text", "content", "reasoning", "thinking", "thought", "summary"):
			txt = _extract_reasoning_text(value.get(key))
			if txt:
				return txt
	return ""


def _split_text_and_reasoning_from_parts(parts: Any) -> tuple[str, str]:
	"""Split a multimodal-style ``content`` array into (text, reasoning) strings."""
	text_chunks = []
	reasoning_chunks = []
	if not isinstance(parts, list):
		return "", ""
	for part in parts:
		if not isinstance(part, dict):
			continue
		part_type = str(part.get("type", "")).lower()
		part_text = _extract_reasoning_text(part)
		if not part_text:
			continue
		if (
			"reasoning" in part_type
			or "thinking" in part_type
			or part_type in ("thought", "thought_summary")
		):
			reasoning_chunks.append(part_text)
		else:
			text_chunks.append(part_text)
	return "\n".join(text_chunks).strip(), "\n".join(reasoning_chunks).strip()


# ---------------------------------------------------------------------------
# Inline (non-streaming) think-tag parsing.
# ---------------------------------------------------------------------------

def _split_think_pair_inline(
	text: str,
	in_think: bool,
	open_tag: str,
	close_tag: str,
) -> tuple[str, str, bool]:
	"""Strip ONE tag pair from a fully-known string. Case-insensitive."""
	if not text or not open_tag or not close_tag:
		# Empty tags would deadlock because str.find("", i) always returns i.
		return (text or ""), "", in_think
	resp_parts: list[str] = []
	reasoning_parts: list[str] = []
	lower = text.lower()
	ot_l = open_tag.lower()
	ct_l = close_tag.lower()
	n = len(text)
	i = 0
	while i < n:
		if in_think:
			j = lower.find(ct_l, i)
			if j < 0:
				reasoning_parts.append(text[i:])
				break
			reasoning_parts.append(text[i:j])
			i = j + len(close_tag)
			in_think = False
		else:
			j = lower.find(ot_l, i)
			if j < 0:
				resp_parts.append(text[i:])
				break
			resp_parts.append(text[i:j])
			i = j + len(open_tag)
			in_think = True
	return "".join(resp_parts), "".join(reasoning_parts), in_think


def _split_ollama_think_inline(text: str, in_think: bool = False) -> tuple[str, str, bool]:
	"""Apply every known tag pair to a fully-known string (used by non-streaming parsers)."""
	visible = text or ""
	reasoning_all: list[str] = []
	for open_tag, close_tag in _THINK_TAG_PAIRS:
		if not open_tag or not close_tag:
			continue
		visible, chunk, _flag = _split_think_pair_inline(visible, False, open_tag, close_tag)
		if chunk:
			reasoning_all.append(chunk)
	return visible, "".join(reasoning_all), in_think


# ---------------------------------------------------------------------------
# Streaming think-tag parsing.
# ---------------------------------------------------------------------------

def _max_partial_suffix_match(buf: str, tag: str) -> int:
	"""Largest k such that ``buf[-k:]`` is a prefix of ``tag`` (case-insensitive).

	Used to size the streaming carry: only the bytes that COULD still become
	part of a tag once the next chunk arrives need to be held back.
	"""
	if not tag or not buf:
		return 0
	tag_l = tag.lower()
	# A full-tag match should already have been processed, never carried.
	max_k = min(len(buf), len(tag) - 1)
	buf_l = buf.lower()
	for k in range(max_k, 0, -1):
		if buf_l.endswith(tag_l[:k]):
			return k
	return 0


def _split_think_pair_stream(
	text: str,
	state: dict,
	open_tag: str,
	close_tag: str,
) -> tuple[str, str]:
	"""Incremental parser for one tag pair across chunked SSE text.

	Holds back only the suffix that COULD be a partial prefix of the relevant
	tag, so plain content (no ``<`` near the end) is forwarded immediately.
	First-token latency stays near zero for providers that never emit tags.
	"""
	if not open_tag or not close_tag:
		return text or "", ""
	if not isinstance(state, dict):
		state = {}
	carry = state.get("carry", "") or ""
	in_think = bool(state.get("in_think", False))
	buf = carry + (text or "")
	lower = buf.lower()
	resp_parts: list[str] = []
	reasoning_parts: list[str] = []
	i = 0
	while True:
		# The tag to look for changes as soon as one is found, and so does
		# the partial tag to hold back at the end of the chunk.
		tag = close_tag if in_think else open_tag
		out = reasoning_parts if in_think else resp_parts
		j = lower.find(tag.lower(), i)
		if j < 0:
			end = len(buf) - _max_partial_suffix_match(buf[i:], tag)
			out.append(buf[i:end])
			state["carry"] = buf[end:]
			break
		out.append(buf[i:j])
		i = j + len(tag)
		in_think = not in_think
	state["in_think"] = in_think
	return "".join(resp_parts), "".join(reasoning_parts)


def _flush_think_chain(states: list) -> tuple[str, str]:
	"""Flush all per-pair carries at the end of the stream.

	Tag pairs are applied IN ORDER; the visible output of pair N becomes the
	input of pair N+1. The accumulated reasoning of every pair is concatenated.
	"""
	if not states:
		return "", ""
	reasoning_parts: list[str] = []
	visible_acc = ""
	for idx, (open_tag, close_tag) in enumerate(_THINK_TAG_PAIRS):
		if idx >= len(states):
			break
		s = states[idx]
		buf = (s.get("carry") or "") + visible_acc
		s["carry"] = ""
		if not open_tag or not close_tag:
			s["in_think"] = bool(s.get("in_think", False))
			visible_acc = buf
			continue
		v, r, in_t = _split_think_pair_inline(buf, s.get("in_think", False), open_tag, close_tag)
		s["in_think"] = in_t
		visible_acc = v
		if r:
			reasoning_parts.append(r)
	return visible_acc, "".join(reasoning_parts)


def _apply_think_chain_to_chunk(content: str, states: list) -> tuple[str, str]:
	"""Run one streamed chunk through every tag-pair parser in sequence."""
	if not content or not states:
		return content or "", ""
	out = content
	reasoning_frags: list[str] = []
	for idx, (open_tag, close_tag) in enumerate(_THINK_TAG_PAIRS):
		if idx >= len(states):
			break
		if not open_tag or not close_tag:
			continue
		out, frag = _split_think_pair_stream(out, states[idx], open_tag, close_tag)
		if frag:
			reasoning_frags.append(frag)
	return out, "".join(reasoning_frags)


def _new_think_chain_states() -> list[dict]:
	"""Fresh per-stream carry state (one dict per tag pair)."""
	return [{"carry": "", "in_think": False} for _ in _THINK_TAG_PAIRS]
