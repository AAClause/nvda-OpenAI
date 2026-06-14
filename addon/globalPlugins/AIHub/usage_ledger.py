"""Append-only API usage ledger for accurate session billing.

Each billable API call appends one entry. Deleting a history block removes it from
the visible thread but not from the ledger, so session spend stays correct.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import addonHandler

addonHandler.initTranslation()

# Conversation JSON files without ``version`` are treated as legacy (v1).
CONVERSATION_JSON_VERSION = 2

USAGE_KIND_COMPLETION = "completion"
USAGE_KIND_ABORTED = "aborted"


def _to_int(value) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _to_float(value) -> float:
	try:
		return float(value or 0.0)
	except (TypeError, ValueError):
		return 0.0


def _usage_triplet(usage: dict) -> tuple[int, int, int]:
	if not isinstance(usage, dict):
		return 0, 0, 0
	input_tokens = _to_int(usage.get("input_tokens")) or _to_int(usage.get("prompt_tokens"))
	output_tokens = _to_int(usage.get("output_tokens")) or _to_int(usage.get("completion_tokens"))
	total_tokens = _to_int(usage.get("total_tokens"))
	if total_tokens == 0 and (input_tokens or output_tokens):
		total_tokens = input_tokens + output_tokens
	return input_tokens, output_tokens, total_tokens


def has_usage_signal(usage: Any) -> bool:
	if not isinstance(usage, dict) or not usage:
		return False
	input_tokens, output_tokens, total_tokens = _usage_triplet(usage)
	if input_tokens or output_tokens or total_tokens:
		return True
	for key in (
		"reasoning_tokens",
		"cached_input_tokens",
		"cache_creation_input_tokens",
		"input_audio_tokens",
		"output_audio_tokens",
	):
		if _to_int(usage.get(key)):
			return True
	return isinstance(usage.get("cost"), (int, float))


def normalize_persisted_usage(usage: Any) -> dict:
	"""Normalize usage into the slim shape stored on blocks and ledger entries."""
	if not isinstance(usage, dict) or not usage:
		return {}
	normalized = {
		"input_tokens": _to_int(usage.get("input_tokens")) or _to_int(usage.get("prompt_tokens")),
		"output_tokens": _to_int(usage.get("output_tokens")) or _to_int(usage.get("completion_tokens")),
		"total_tokens": _to_int(usage.get("total_tokens")),
		"prompt_tokens": _to_int(usage.get("prompt_tokens")),
		"completion_tokens": _to_int(usage.get("completion_tokens")),
		"reasoning_tokens": _to_int(usage.get("reasoning_tokens")),
		"cached_input_tokens": _to_int(usage.get("cached_input_tokens")),
		"cache_creation_input_tokens": _to_int(usage.get("cache_creation_input_tokens")),
		"input_audio_tokens": _to_int(usage.get("input_audio_tokens")),
		"output_audio_tokens": _to_int(usage.get("output_audio_tokens")),
	}
	if normalized["total_tokens"] <= 0:
		in_tok = normalized["input_tokens"]
		out_tok = normalized["output_tokens"]
		if in_tok or out_tok:
			normalized["total_tokens"] = in_tok + out_tok
	if "cost" in usage:
		try:
			normalized["cost"] = float(usage.get("cost"))
		except (TypeError, ValueError):
			pass
	return normalized if has_usage_signal(normalized) else {}


def ensure_block_uid(block) -> str:
	uid = getattr(block, "uid", None)
	if not uid:
		uid = str(uuid.uuid4())
		block.uid = uid
	return uid


def _block_usage(block_or_dict) -> dict:
	if isinstance(block_or_dict, dict):
		usage = block_or_dict.get("usage")
	else:
		usage = getattr(block_or_dict, "usage", None)
	return normalize_persisted_usage(usage) if isinstance(usage, dict) else {}


def _block_model(block_or_dict, unknown_model_label: str) -> str:
	if isinstance(block_or_dict, dict):
		return block_or_dict.get("model") or unknown_model_label
	return getattr(block_or_dict, "model", "") or unknown_model_label


def aggregate_usage_dicts(
	usage_items: list,
	*,
	unknown_model_label: str,
	model_from_entry=None,
) -> dict:
	"""Sum token/cost totals from an iterable of usage dicts or ledger entries."""
	total_input = total_output = total_tokens = 0
	total_reasoning = total_cached = total_cache_write = 0
	total_input_audio = total_output_audio = 0
	total_cost = 0.0
	has_cost = False
	model_counts: dict[str, int] = {}
	usage_count = 0

	for item in usage_items:
		if isinstance(item, dict) and "usage" in item and model_from_entry is not None:
			usage = normalize_persisted_usage(item.get("usage"))
			model_name = item.get("model") or unknown_model_label
		else:
			usage = normalize_persisted_usage(item)
			model_name = unknown_model_label
		if not has_usage_signal(usage):
			continue
		usage_count += 1
		model_counts[model_name] = model_counts.get(model_name, 0) + 1
		input_tokens, output_tokens, total_for_item = _usage_triplet(usage)
		total_input += input_tokens
		total_output += output_tokens
		total_tokens += total_for_item
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
		"usage_count": usage_count,
	}


def aggregate_blocks_usage(blocks, unknown_model_label: str) -> dict:
	"""Aggregate usage for blocks still in the active thread."""
	items = []
	for block in blocks:
		usage = _block_usage(block)
		if not has_usage_signal(usage):
			continue
		items.append({"usage": usage, "model": _block_model(block, unknown_model_label)})
	return aggregate_usage_dicts(items, unknown_model_label=unknown_model_label, model_from_entry=True)


def aggregate_block_dicts_usage(block_dicts, unknown_model_label: str) -> dict:
	"""Aggregate usage from saved block dicts without deserializing attachments."""
	items = []
	for bd in block_dicts:
		if not isinstance(bd, dict):
			continue
		usage = normalize_persisted_usage(bd.get("usage"))
		if not has_usage_signal(usage):
			continue
		items.append({"usage": usage, "model": bd.get("model") or unknown_model_label})
	return aggregate_usage_dicts(items, unknown_model_label=unknown_model_label, model_from_entry=True)


def aggregate_ledger_usage(ledger, unknown_model_label: str) -> dict:
	"""Aggregate usage across all ledger entries (session / API spend)."""
	if not isinstance(ledger, list):
		return aggregate_usage_dicts([], unknown_model_label=unknown_model_label)
	entries = [e for e in ledger if isinstance(e, dict)]
	return aggregate_usage_dicts(entries, unknown_model_label=unknown_model_label, model_from_entry=True)


def aggregate_conversation_usage(*, blocks, ledger, unknown_model_label: str) -> dict:
	"""Return thread (active blocks) and session (ledger) usage aggregates."""
	thread = aggregate_blocks_usage(blocks, unknown_model_label)
	session = aggregate_ledger_usage(ledger, unknown_model_label)
	return {
		"thread": thread,
		"session": session,
		"ledger_entries": len(ledger) if isinstance(ledger, list) else 0,
	}


def ledger_entry_to_dict(
	entry_id: str,
	*,
	at: float,
	model: str,
	kind: str,
	usage: dict,
	block_id: str | None = None,
	migrated: bool = False,
) -> dict:
	payload = {
		"id": entry_id,
		"at": at,
		"model": model or "",
		"kind": kind,
		"usage": normalize_persisted_usage(usage),
	}
	if block_id:
		payload["blockId"] = block_id
	if migrated:
		payload["migrated"] = True
	return payload


def append_usage_event(
	ledger: list,
	*,
	usage: dict,
	model: str,
	kind: str = USAGE_KIND_COMPLETION,
	block_id: str | None = None,
	at: float | None = None,
) -> dict | None:
	"""Append one billable API event. Returns the new entry or None if usage is empty."""
	normalized = normalize_persisted_usage(usage)
	if not has_usage_signal(normalized):
		return None
	if not isinstance(ledger, list):
		raise TypeError("ledger must be a list")
	entry = ledger_entry_to_dict(
		str(uuid.uuid4()),
		at=at if at is not None else time.time(),
		model=model or "",
		kind=kind,
		usage=normalized,
		block_id=block_id,
	)
	ledger.append(entry)
	return entry


def migrate_ledger_from_blocks(blocks) -> list:
	"""Build a ledger from legacy per-block usage (v1 conversations)."""
	entries = []
	for block in blocks:
		usage = _block_usage(block)
		if not has_usage_signal(usage):
			continue
		block_id = ensure_block_uid(block)
		timing = getattr(block, "timing", None) or {}
		at = timing.get("finishedAt") or timing.get("startedAt") or time.time()
		try:
			at = float(at)
		except (TypeError, ValueError):
			at = time.time()
		entries.append(
			ledger_entry_to_dict(
				str(uuid.uuid4()),
				at=at,
				model=_block_model(block, ""),
				kind=USAGE_KIND_COMPLETION,
				usage=usage,
				block_id=block_id,
				migrated=True,
			)
		)
	return entries


def migrate_ledger_from_block_dicts(block_dicts: list) -> list:
	"""Build a ledger from saved block dicts (properties / load without HistoryBlock)."""
	entries = []
	for bd in block_dicts:
		if not isinstance(bd, dict):
			continue
		usage = normalize_persisted_usage(bd.get("usage"))
		if not has_usage_signal(usage):
			continue
		block_id = bd.get("id") or bd.get("uid") or ""
		entries.append(
			ledger_entry_to_dict(
				str(uuid.uuid4()),
				at=time.time(),
				model=bd.get("model") or "",
				kind=USAGE_KIND_COMPLETION,
				usage=usage,
				block_id=block_id or None,
				migrated=True,
			)
		)
	return entries


def deserialize_ledger(data) -> list:
	if not isinstance(data, list):
		return []
	ledger = []
	for item in data:
		if not isinstance(item, dict):
			continue
		usage = normalize_persisted_usage(item.get("usage"))
		if not has_usage_signal(usage):
			continue
		entry = {
			"id": item.get("id") or str(uuid.uuid4()),
			"at": _to_float(item.get("at")) or time.time(),
			"model": item.get("model") or "",
			"kind": item.get("kind") or USAGE_KIND_COMPLETION,
			"usage": usage,
		}
		block_id = item.get("blockId")
		if isinstance(block_id, str) and block_id:
			entry["blockId"] = block_id
		if item.get("migrated"):
			entry["migrated"] = True
		ledger.append(entry)
	return ledger


def resolve_ledger_for_saved_data(data: dict) -> list:
	"""Return usage ledger from saved JSON, migrating from blocks when needed."""
	raw = data.get("usageLedger")
	if isinstance(raw, list) and raw:
		return deserialize_ledger(raw)
	blocks = data.get("blocks", [])
	if not isinstance(blocks, list):
		blocks = []
	return migrate_ledger_from_block_dicts(blocks)


def conversation_json_version(data: dict) -> int:
	try:
		return int(data.get("version", 1) or 1)
	except (TypeError, ValueError):
		return 1


def format_usage_metric_lines(agg: dict, *, prefix: str = "") -> list[str]:
	"""Return translated token/cost lines for one aggregate dict."""
	lines = []
	label_prefix = f"{prefix} " if prefix else ""

	def _line(fmt, value):
		return (label_prefix + fmt) % value

	# Translators: Token usage line in conversation properties (prefix may be "Session" or "Active thread").
	lines.append(_line(_("Billed input tokens: %d"), agg["total_input"]))
	# Translators: Token usage line in conversation properties.
	lines.append(_line(_("Billed output tokens: %d"), agg["total_output"]))
	# Translators: Token usage line in conversation properties.
	lines.append(_line(_("Billed total tokens: %d"), agg["total_tokens"]))
	if agg.get("total_reasoning"):
		# Translators: Token usage line in conversation properties.
		lines.append(_line(_("Reasoning tokens: %d"), agg["total_reasoning"]))
	if agg.get("total_cached"):
		# Translators: Token usage line in conversation properties.
		lines.append(_line(_("Cached input tokens: %d"), agg["total_cached"]))
	if agg.get("total_cache_write"):
		# Translators: Token usage line in conversation properties.
		lines.append(_line(_("Cache write tokens: %d"), agg["total_cache_write"]))
	if agg.get("total_input_audio"):
		# Translators: Token usage line in conversation properties.
		lines.append(_line(_("Input audio tokens: %d"), agg["total_input_audio"]))
	if agg.get("total_output_audio"):
		# Translators: Token usage line in conversation properties.
		lines.append(_line(_("Output audio tokens: %d"), agg["total_output_audio"]))
	if agg.get("has_cost"):
		# Translators: Cost line in conversation properties.
		lines.append(_line(_("API spend: $%.6f"), agg["total_cost"]))
	return lines


def build_conversation_usage_lines(
	*,
	blocks,
	ledger,
	unknown_model_label: str,
	message_count: int | None = None,
) -> list[str]:
	"""Build user-facing conversation usage summary lines."""
	combined = aggregate_conversation_usage(
		blocks=blocks,
		ledger=ledger,
		unknown_model_label=unknown_model_label,
	)
	session = combined["session"]
	thread = combined["thread"]
	lines = []
	if message_count is not None:
		# Translators: Message count in conversation properties.
		lines.append(_("Messages: %d") % message_count)
	if combined["ledger_entries"]:
		# Translators: API call count in conversation properties.
		lines.append(_("API calls recorded: %d") % combined["ledger_entries"])
		lines.append("")
		# Translators: Section heading — cumulative spend across all API calls including deleted turns.
		lines.append(_("Session (all API calls)"))
		lines.extend(format_usage_metric_lines(session))
		if thread.get("usage_count"):
			lines.append("")
			# Translators: Section heading — spend for messages still visible in history.
			lines.append(_("Active thread (remaining messages)"))
			lines.extend(format_usage_metric_lines(thread))
	elif thread.get("usage_count"):
		lines.extend(format_usage_metric_lines(thread))
	else:
		# Translators: Shown when no usage data exists for a conversation.
		lines.append(_("Token usage: unavailable"))
	return lines
