"""Shared helpers for message/conversation properties views."""

import re

import addonHandler
from html import escape

addonHandler.initTranslation()


def _normalize_reasoning_for_properties(raw: str) -> str:
	"""Unwrap literal thinking markers stored on HistoryBlock reasoning text.

	Providers may emit ``<think>``, ``<thinking>``, or ``<thought>``
	wrappers inside the reasoning channel (or legacy saves may still contain
	them). Message properties used to wrap the whole body in similar markers
	too, which duplicated them."""
	t = (raw or "").strip()
	if not t:
		return ""
	patterns = (
		re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL | re.IGNORECASE),
		re.compile(r"<thought>\s*(.*?)\s*</thought>", re.DOTALL | re.IGNORECASE),
		re.compile(r"<thinking>\s*(.*?)\s*</thinking>", re.DOTALL | re.IGNORECASE),
	)
	prev = None
	while prev != t:
		prev = t
		for pat in patterns:
			t = pat.sub(lambda m: (m.group(1) or "").strip(), t)
	t = re.sub(r"\n{3,}", "\n\n", t).strip()
	return t


def _to_int(value):
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _usage_triplet(usage):
	"""Return normalized (input, output, total) usage counts with fallbacks."""
	if not isinstance(usage, dict):
		return 0, 0, 0
	input_tokens = _to_int(usage.get("input_tokens")) or _to_int(usage.get("prompt_tokens"))
	output_tokens = _to_int(usage.get("output_tokens")) or _to_int(usage.get("completion_tokens"))
	total_tokens = _to_int(usage.get("total_tokens"))
	if total_tokens == 0 and (input_tokens or output_tokens):
		total_tokens = input_tokens + output_tokens
	return input_tokens, output_tokens, total_tokens


def aggregate_blocks_usage(blocks, unknown_model_label):
	"""Aggregate usage totals and model counts from iterable of blocks."""
	total_input = total_output = total_tokens = 0
	total_reasoning = total_cached = total_cache_write = 0
	total_input_audio = total_output_audio = 0
	total_cost = 0.0
	has_cost = False
	model_counts = {}

	for block in blocks:
		model_name = getattr(block, "model", "") or unknown_model_label
		model_counts[model_name] = model_counts.get(model_name, 0) + 1
		usage = getattr(block, "usage", {}) or {}
		if not isinstance(usage, dict):
			continue
		input_tokens, output_tokens, total_for_block = _usage_triplet(usage)
		total_input += input_tokens
		total_output += output_tokens
		total_tokens += total_for_block
		total_reasoning += _to_int(usage.get("reasoning_tokens"))
		total_cached += _to_int(usage.get("cached_input_tokens"))
		total_cache_write += _to_int(usage.get("cache_creation_input_tokens"))
		total_input_audio += _to_int(usage.get("input_audio_tokens"))
		total_output_audio += _to_int(usage.get("output_audio_tokens"))
		cost = usage.get("cost")
		if isinstance(cost, (int, float)):
			total_cost += float(cost)
			has_cost = True

	return {
		"total_input": total_input,
		"total_output": total_output,
		"total_tokens": total_tokens,
		"total_reasoning": total_reasoning,
		"total_cached": total_cached,
		"total_cache_write": total_cache_write,
		"total_input_audio": total_input_audio,
		"total_output_audio": total_output_audio,
		"total_cost": total_cost,
		"has_cost": has_cost,
		"model_counts": model_counts,
	}


def format_token_usage_lines(usage, include_unavailable=True):
	"""Return user-facing token usage lines for a usage dict."""
	if not isinstance(usage, dict) or not usage:
		# Translators: Text in message/conversation properties output.
		return [_("Token usage: unavailable")] if include_unavailable else []
	input_tokens, output_tokens, total_tokens = _usage_triplet(usage)
	reasoning = _to_int(usage.get("reasoning_tokens"))
	cached = _to_int(usage.get("cached_input_tokens"))
	cache_write = _to_int(usage.get("cache_creation_input_tokens"))
	input_audio = _to_int(usage.get("input_audio_tokens"))
	output_audio = _to_int(usage.get("output_audio_tokens"))
	cost = usage.get("cost")
	has_any_usage = any((
		input_tokens,
		output_tokens,
		total_tokens,
		reasoning,
		cached,
		cache_write,
		input_audio,
		output_audio,
		isinstance(cost, (int, float)),
	))
	if not has_any_usage:
		# Translators: Text in message/conversation properties output.
		return [_("Token usage: unavailable")] if include_unavailable else []
	lines = [
		# Translators: Text in message/conversation properties output.
		_("Input tokens: %d") % input_tokens,
		# Translators: Text in message/conversation properties output.
		_("Output tokens: %d") % output_tokens,
		# Translators: Text in message/conversation properties output.
		_("Total tokens: %d") % total_tokens,
	]
	if reasoning:
		# Translators: Text in message/conversation properties output.
		lines.append(_("Reasoning tokens: %d") % reasoning)
	if cached:
		# Translators: Text in message/conversation properties output.
		lines.append(_("Cached input tokens: %d") % cached)
	if cache_write:
		# Translators: Text in message/conversation properties output.
		lines.append(_("Cache write tokens: %d") % cache_write)
	if input_audio:
		# Translators: Text in message/conversation properties output.
		lines.append(_("Input audio tokens: %d") % input_audio)
	if output_audio:
		# Translators: Text in message/conversation properties output.
		lines.append(_("Output audio tokens: %d") % output_audio)
	if isinstance(cost, (int, float)):
		# Translators: Text in message/conversation properties output.
		lines.append(_("Cost: $%.6f") % float(cost))
	return lines


def build_message_properties_html(block, unknown_model_label):
	"""Build structured HTML for message properties."""

	def _fmt(value):
		return escape(str(value))

	def _li(label, value):
		return f"<li><strong>{_fmt(label)}:</strong> {_fmt(value)}</li>"

	def _to_float(value):
		try:
			return float(value)
		except (TypeError, ValueError):
			return None

	def _to_int(value):
		try:
			return int(value or 0)
		except (TypeError, ValueError):
			return 0

	model_name = getattr(block, "model", "") or unknown_model_label
	prompt_chars = len(getattr(block, "prompt", "") or "")
	response_chars = len(getattr(block, "responseText", "") or "")
	files_list = getattr(block, "filesList", None) or []
	audio_list = getattr(block, "audioPathList", None) or []
	usage = getattr(block, "usage", {}) or {}
	timing = getattr(block, "timing", {}) or {}
	reasoning_text = _normalize_reasoning_for_properties(getattr(block, "reasoningText", "") or "")
	html = [
		# Translators: Text in message/conversation properties output.
		f"<h1>{_fmt(_('Message properties'))}</h1>",
		# Translators: Text in message/conversation properties output.
		f"<h2>{_fmt(_('Overview'))}</h2>",
		"<ul>",
		# Translators: Text in message/conversation properties output.
		_li(_("Model"), model_name),
		# Translators: Text in message/conversation properties output.
		_li(_("Prompt characters"), prompt_chars),
		# Translators: Text in message/conversation properties output.
		_li(_("Response characters"), response_chars),
		# Translators: Text in message/conversation properties output.
		_li(_("Files attached"), len(files_list)),
		# Translators: Text in message/conversation properties output.
		_li(_("Audio files attached"), len(audio_list)),
	]
	if getattr(block, "maxTokens", 0):
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Max tokens"), int(block.maxTokens)))
	if getattr(block, "temperature", None) is not None:
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Temperature"), block.temperature))
	if getattr(block, "topP", None) is not None:
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Top P"), block.topP))
	if getattr(block, "seed", None) is not None:
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Seed"), block.seed))
	if getattr(block, "topK", None) is not None:
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Top K"), block.topK))
	st_txt = (getattr(block, "stopText", "") or "").strip()
	if st_txt:
		if len(st_txt) > 200:
			st_txt = st_txt[:200] + "…"
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Stop sequences"), st_txt))
	if getattr(block, "frequencyPenalty", None) is not None:
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Frequency penalty"), block.frequencyPenalty))
	if getattr(block, "presencePenalty", None) is not None:
		# Translators: Text in message/conversation properties output.
		html.append(_li(_("Presence penalty"), block.presencePenalty))
	html.append("</ul>")

	# Translators: Text in message/conversation properties output.
	html.extend([f"<h2>{_fmt(_('Token usage'))}</h2>", "<ul>"])
	for line in format_token_usage_lines(usage, include_unavailable=True):
		if ":" in line:
			label, value = line.split(":", 1)
			html.append(_li(label.strip(), value.strip()))
		else:
			html.append(f"<li>{_fmt(line)}</li>")
	html.append("</ul>")

	timing_items = []
	elapsed = _to_float(timing.get("elapsedSec"))
	if isinstance(elapsed, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Elapsed time'), f"{elapsed:.2f}s"))
	request_sent = _to_float(timing.get("timeToRequestSentSec"))
	if isinstance(request_sent, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Time to request sent'), f"{request_sent:.2f}s"))
	first_token = _to_float(timing.get("timeToFirstTokenSec"))
	if isinstance(first_token, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Time to first token'), f"{first_token:.2f}s"))
	req_to_end = _to_float(timing.get("timeFromRequestSentToEndSec"))
	if isinstance(req_to_end, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Request sent to end'), f"{req_to_end:.2f}s"))
	gen_span = _to_float(timing.get("generationDurationSec"))
	if isinstance(gen_span, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Generation duration'), f"{gen_span:.2f}s"))
	output_tok_s = _to_float(timing.get("outputTokensPerSec"))
	if isinstance(output_tok_s, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Mean output speed'), f"{output_tok_s:.2f} tok/s"))
	total_tok_s = _to_float(timing.get("totalTokensPerSec"))
	if isinstance(total_tok_s, float):
		# Translators: Text in message/conversation properties output.
		timing_items.append((_('Mean total speed'), f"{total_tok_s:.2f} tok/s"))
	if not timing_items and not isinstance(elapsed, float):
		elapsed_fallback = _to_float(timing.get("elapsedSec"))
		if isinstance(elapsed_fallback, float) and elapsed_fallback > 0:
			output_tokens = _to_int(usage.get("output_tokens")) or _to_int(usage.get("completion_tokens"))
			total_tokens = _to_int(usage.get("total_tokens"))
			if output_tokens:
				# Translators: Text in message/conversation properties output.
				timing_items.append((_('Mean output speed'), f"{(output_tokens / elapsed_fallback):.2f} tok/s"))
			if total_tokens:
				# Translators: Text in message/conversation properties output.
				timing_items.append((_('Mean total speed'), f"{(total_tokens / elapsed_fallback):.2f} tok/s"))
	if timing_items:
		# Translators: Text in message/conversation properties output.
		html.extend([f"<h2>{_fmt(_('Timing and throughput'))}</h2>", "<ul>"])
		for label, value in timing_items:
			html.append(_li(label, value))
		html.append("</ul>")
	if reasoning_text:
		html.extend([
			# Translators: Text in message/conversation properties output.
			f"<h2>{_fmt(_('Reasoning text'))}</h2>",
			f"<pre>{escape(reasoning_text)}</pre>",
		])

	return "".join(html)
