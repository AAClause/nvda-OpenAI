"""Provider-agnostic normalization of usage/billing payloads.

Each provider exposes token counts under different keys. We normalize them
to a single shape so the rest of the addon (history/cost display) does not
have to know which provider produced the data.
"""
from __future__ import annotations

from typing import Any


def _to_int(value: Any) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _first_int(container: Any, *keys: str) -> int:
	"""First non-zero integer from ``container[key]`` for the given keys."""
	if not isinstance(container, dict):
		return 0
	for key in keys:
		value = _to_int(container.get(key))
		if value:
			return value
	return 0


def _sum_int(container: Any, *keys: str) -> int:
	"""Sum integer values from ``container`` for the given keys."""
	if not isinstance(container, dict):
		return 0
	total = 0
	for key in keys:
		total += _to_int(container.get(key))
	return total


def _sum_modality_token_counts(items: Any) -> int:
	"""Sum ``tokenCount`` entries from Gemini-style modality breakdown lists."""
	if not isinstance(items, list):
		return 0
	total = 0
	for item in items:
		if not isinstance(item, dict):
			continue
		total += _to_int(item.get("tokenCount") or item.get("token_count"))
	return total


def uncached_input_tokens(input_tokens: int, cached_read: int, cache_write: int) -> int:
	"""Tokens billed at the regular prompt rate.

	OpenAI, Gemini, DeepSeek, and Mistral report cache read/write as a subset of
	``input_tokens``. Anthropic reports the three fields as a disjoint partition
	(``input_tokens`` is only the uncached tail), so subtracting would drop the
	uncached tokens when a cache write is larger than that tail.
	"""
	input_tokens = max(0, _to_int(input_tokens))
	cache_tokens = max(0, _to_int(cached_read)) + max(0, _to_int(cache_write))
	if cache_tokens > input_tokens:
		return input_tokens
	return max(0, input_tokens - cache_tokens)


def _has_any_usage_signal(raw_usage: Any) -> bool:
	"""True when a usage payload contains at least one concrete usage signal."""
	if not isinstance(raw_usage, dict) or not raw_usage:
		return False
	primary = (
		"input_tokens",
		"output_tokens",
		"total_tokens",
		"prompt_tokens",
		"completion_tokens",
		"reasoning_tokens",
		"cached_input_tokens",
		"cache_creation_input_tokens",
		"cache_read_input_tokens",
		"cache_write_tokens",
		"prompt_cache_hit_tokens",
		"prompt_cache_miss_tokens",
		"cachedContentTokenCount",
		"cached_content_token_count",
		"total_cached_tokens",
		"cacheTokensDetails",
		"cache_tokens_details",
		"input_audio_tokens",
		"output_audio_tokens",
		"input_token_count",
		"output_token_count",
		"total_token_count",
		"prompt_token_count",
		"promptTokenCount",
		"candidatesTokenCount",
		"totalTokenCount",
	)
	if any(k in raw_usage for k in primary):
		return True
	for nested_key in (
		"prompt_tokens_details",
		"completion_tokens_details",
		"input_tokens_details",
		"output_tokens_details",
		"cache_creation",
	):
		nested = raw_usage.get(nested_key)
		if isinstance(nested, dict) and nested:
			return True
	return False


def _normalize_usage(usage: Any) -> dict:
	"""Normalize a single ``usage`` dict (OpenAI, Anthropic, etc.) into the addon's shape."""
	if not isinstance(usage, dict):
		return {}
	if not _has_any_usage_signal(usage):
		return {}

	prompt_tokens_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
	completion_tokens_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
	output_tokens_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
	input_tokens_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}

	prompt_tokens = _first_int(usage, "prompt_tokens", "prompt_token_count", "promptTokenCount")
	if not prompt_tokens:
		prompt_tokens = _sum_int(usage, "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
	completion_tokens = _first_int(
		usage, "completion_tokens", "candidates_token_count", "candidatesTokenCount"
	)
	input_tokens = _first_int(usage, "input_tokens", "input_token_count") or prompt_tokens
	output_tokens = _first_int(usage, "output_tokens", "output_token_count") or completion_tokens

	reasoning_tokens = (
		_to_int(usage.get("reasoning_tokens"))
		or _to_int(completion_tokens_details.get("reasoning_tokens"))
		or _to_int(output_tokens_details.get("reasoning_tokens"))
		or _to_int(output_tokens_details.get("thinking_tokens"))
		or _to_int(usage.get("thinking_tokens"))
	)

	cache_creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
	cached_input_tokens = (
		_to_int(prompt_tokens_details.get("cached_tokens"))
		or _to_int(input_tokens_details.get("cached_tokens"))
		or _to_int(prompt_tokens_details.get("cache_read_tokens"))
		or _to_int(input_tokens_details.get("cache_read_tokens"))
		or _to_int(usage.get("cached_input_tokens"))
		or _to_int(usage.get("cache_read_input_tokens"))
		or _to_int(usage.get("cache_read_tokens"))
		or _to_int(usage.get("prompt_cache_hit_tokens"))  # DeepSeek
		or _to_int(usage.get("cachedContentTokenCount"))  # Gemini generateContent
		or _to_int(usage.get("cached_content_token_count"))
		or _to_int(usage.get("total_cached_tokens"))
		or _sum_modality_token_counts(usage.get("cacheTokensDetails"))
		or _sum_modality_token_counts(usage.get("cache_tokens_details"))
	)
	cache_creation_input_tokens = (
		_to_int(usage.get("cache_creation_input_tokens"))
		or _sum_int(cache_creation, "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
		or _to_int(prompt_tokens_details.get("cache_write_tokens"))
		or _to_int(input_tokens_details.get("cache_write_tokens"))
		or _to_int(usage.get("cache_write_tokens"))
	)

	# Anthropic reports cache read/write disjoint from input_tokens (the uncached
	# tail). Fold them in so display, totals, and OpenAI-style pricing agree.
	cache_extra = cached_input_tokens + cache_creation_input_tokens
	if cache_extra > input_tokens:
		input_tokens += cache_extra
		if prompt_tokens < input_tokens:
			prompt_tokens = input_tokens

	total_tokens = _first_int(usage, "total_tokens", "total_token_count", "totalTokenCount")
	if input_tokens or output_tokens:
		total_tokens = max(total_tokens, input_tokens + output_tokens)

	input_audio_tokens = (
		_to_int(prompt_tokens_details.get("audio_tokens"))
		or _to_int(input_tokens_details.get("audio_tokens"))
		or _to_int(usage.get("prompt_audio_tokens"))
		or _to_int(usage.get("input_audio_tokens"))
	)
	output_audio_tokens = (
		_to_int(completion_tokens_details.get("audio_tokens"))
		or _to_int(output_tokens_details.get("audio_tokens"))
		or _to_int(usage.get("completion_audio_tokens"))
		or _to_int(usage.get("output_audio_tokens"))
	)

	normalized = {
		"input_tokens": input_tokens,
		"output_tokens": output_tokens,
		"total_tokens": total_tokens,
		"prompt_tokens": prompt_tokens,
		"completion_tokens": completion_tokens,
		"reasoning_tokens": reasoning_tokens,
		"cached_input_tokens": cached_input_tokens,
		"cache_creation_input_tokens": cache_creation_input_tokens,
		"input_audio_tokens": input_audio_tokens,
		"output_audio_tokens": output_audio_tokens,
	}
	cost = usage.get("cost")
	if isinstance(cost, (int, float)):
		normalized["cost"] = float(cost)
	return normalized


def _normalize_usage_from_payload(payload: Any) -> dict:
	"""Try the standard ``usage`` field first, then fall back to provider-specific shapes."""
	if not isinstance(payload, dict):
		return {}
	usage = _normalize_usage(payload.get("usage"))
	if usage:
		return usage
	nested = payload.get("response")
	if isinstance(nested, dict):
		usage = _normalize_usage(nested.get("usage"))
		if usage:
			return usage
	msg = payload.get("message")
	if isinstance(msg, dict):
		usage = _normalize_usage(msg.get("usage"))
		if usage:
			return usage
	# Google native schema (Gemini/Vertex).
	usage_meta = payload.get("usage_metadata")
	if not isinstance(usage_meta, dict):
		usage_meta = payload.get("usageMetadata")
	if isinstance(usage_meta, dict):
		usage = _normalize_usage(usage_meta)
		if usage:
			return usage

	# Ollama native counters (also seen in some compatibility payloads).
	prompt_tokens = _to_int(payload.get("prompt_eval_count")) or _to_int(payload.get("prompt_tokens"))
	completion_tokens = _to_int(payload.get("eval_count")) or _to_int(payload.get("completion_tokens"))
	if prompt_tokens == 0 and completion_tokens == 0:
		return {}
	return {
		"input_tokens": prompt_tokens,
		"output_tokens": completion_tokens,
		"total_tokens": prompt_tokens + completion_tokens,
		"prompt_tokens": prompt_tokens,
		"completion_tokens": completion_tokens,
		"reasoning_tokens": 0,
		"cached_input_tokens": 0,
		"cache_creation_input_tokens": 0,
		"input_audio_tokens": 0,
		"output_audio_tokens": 0,
	}


def _merge_usage(base: dict, update: dict) -> dict:
	"""Merge two normalized usage dicts, preferring non-zero values from ``update``.

	Used by Anthropic streaming where ``message_start.usage`` provides input_tokens
	and ``message_delta.usage`` provides cumulative output_tokens — neither chunk
	alone has the full picture.
	"""
	if not base:
		return dict(update or {})
	if not update:
		return dict(base)
	merged = dict(base)
	for key, value in update.items():
		if key == "cost":
			if isinstance(value, (int, float)):
				merged["cost"] = float(value)
			continue
		try:
			ivalue = int(value or 0)
		except (TypeError, ValueError):
			continue
		if ivalue:
			merged[key] = ivalue
	# A later Anthropic chunk may repeat uncached input_tokens without cache
	# fields. Fold disjoint cache counts back into the inclusive prompt total.
	cached = _to_int(merged.get("cached_input_tokens"))
	cache_write = _to_int(merged.get("cache_creation_input_tokens"))
	in_tok = _to_int(merged.get("input_tokens")) or _to_int(merged.get("prompt_tokens"))
	cache_extra = cached + cache_write
	if cache_extra > in_tok:
		in_tok += cache_extra
		merged["input_tokens"] = in_tok
		if _to_int(merged.get("prompt_tokens")) < in_tok:
			merged["prompt_tokens"] = in_tok
	out_tok = _to_int(merged.get("output_tokens")) or _to_int(merged.get("completion_tokens"))
	if in_tok or out_tok:
		merged["total_tokens"] = max(_to_int(merged.get("total_tokens")), in_tok + out_tok)
	return merged
