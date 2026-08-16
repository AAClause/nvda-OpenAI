"""Provider-native prompt-cache request fields.

This is server-side prefix reuse, not a local cache. The add-on sends a stable
conversation key and, where the vendor requires it, an opt-in ``cache_control``.

Official references:
- OpenAI: https://developers.openai.com/api/docs/guides/prompt-caching
- Anthropic: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Mistral: https://docs.mistral.ai/studio-api/conversations/advanced/prompt-caching
- xAI: https://docs.x.ai/developers/advanced-api-usage/prompt-caching/maximizing-cache-hits
- OpenRouter: https://openrouter.ai/docs/guides/best-practices/prompt-caching
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from .consts import Provider

PROMPT_CACHE_TTL_5M = "5m"
PROMPT_CACHE_TTL_1H = "1h"
PROMPT_CACHE_TTL_VALUES = (PROMPT_CACHE_TTL_5M, PROMPT_CACHE_TTL_1H)

_PROMPT_CACHE_TTL_CONFIG_KEYS = {
	Provider.Anthropic: "Anthropic",
	Provider.OpenRouter: "OpenRouter",
}

_PROMPT_CACHE_KEY_PROVIDERS = frozenset({
	Provider.OpenAI,
	Provider.MistralAI,
	Provider.xAI,
	Provider.OpenRouter,
})


def _is_openrouter_anthropic_model(model_id: str) -> bool:
	mid = (model_id or "").lower()
	return mid.startswith("anthropic/") or "claude" in mid


def normalize_prompt_cache_ttl(value: Any) -> str:
	"""Return ``5m`` or ``1h``. Unknown values become the 5-minute default."""
	raw = str(value or "").strip().lower().replace(" ", "")
	if raw in ("1h", "1hour", "hour", "60m", "3600", "3600s"):
		return PROMPT_CACHE_TTL_1H
	return PROMPT_CACHE_TTL_5M


def cache_ttl_for_provider(conf: Any, provider: str) -> str:
	"""Configured Anthropic-style cache TTL for ``provider``, else 5 minutes."""
	key = _PROMPT_CACHE_TTL_CONFIG_KEYS.get(provider)
	if not key or conf is None:
		return PROMPT_CACHE_TTL_5M
	try:
		section = conf.get("promptCacheTtl")
	except Exception:
		section = None
	if section is None:
		return PROMPT_CACHE_TTL_5M
	try:
		return normalize_prompt_cache_ttl(section.get(key))
	except Exception:
		return PROMPT_CACHE_TTL_5M


def _cache_control(ttl: str) -> dict:
	# Always pin ttl. Anthropic's default is 5 minutes, but omitting the field
	# has drifted in the past; OpenRouter documents both "5m" and "1h".
	return {"type": "ephemeral", "ttl": normalize_prompt_cache_ttl(ttl)}


def stored_file_id(attachment: Any, provider: str) -> str:
	"""Return a previously uploaded file id for ``provider``, if any."""
	ids = getattr(attachment, "providerFileIds", None)
	if not isinstance(ids, dict) or not provider:
		return ""
	value = ids.get(provider)
	return value.strip() if isinstance(value, str) and value.strip() else ""


def remember_file_id(attachment: Any, provider: str, file_id: str) -> None:
	if not provider or not isinstance(file_id, str) or not file_id.strip():
		return
	ids = getattr(attachment, "providerFileIds", None)
	if not isinstance(ids, dict):
		ids = {}
		attachment.providerFileIds = ids
	ids[provider] = file_id.strip()


def store_file_ids_from_messages(wnd: Any, messages: Any, provider: str) -> None:
	"""Copy uploaded ``file_id`` values from request parts back onto attachments.

	OpenAI and xAI Responses insert a new file id on every upload. Reusing the
	same id keeps historical documents byte-identical in the prompt prefix.
	"""
	if not provider or not isinstance(messages, list):
		return
	by_path: dict[str, str] = {}
	for msg in messages:
		if not isinstance(msg, dict):
			continue
		content = msg.get("content")
		if not isinstance(content, list):
			continue
		for part in content:
			if not isinstance(part, dict) or part.get("type") != "input_file":
				continue
			file_id = part.get("file_id")
			file_path = part.get("file_path")
			if isinstance(file_id, str) and file_id.strip() and isinstance(file_path, str) and file_path:
				by_path[os.path.normcase(os.path.abspath(file_path))] = file_id.strip()
	if not by_path:
		return
	attachments: list[Any] = []
	current = getattr(wnd, "filesList", None) or []
	attachments.extend(current)
	block = getattr(wnd, "firstBlock", None)
	while block is not None:
		attachments.extend(getattr(block, "filesList", None) or [])
		block = getattr(block, "next", None)
	for att in attachments:
		path = getattr(att, "path", None)
		if not isinstance(path, str) or not path:
			continue
		try:
			key = os.path.normcase(os.path.abspath(path))
		except (OSError, ValueError, TypeError):
			continue
		fid = by_path.get(key)
		if fid:
			remember_file_id(att, provider, fid)


def clear_xai_state_after_history_splice(page: Any) -> None:
	"""Drop xAI server-chain ids after a non-append history edit.

	``previous_response_id`` and encrypted reasoning describe the unspliced
	server conversation. Replaying the local (edited) messages lets prefix
	caching start from the new history instead of a stale chain.
	"""
	block = getattr(page, "firstBlock", None)
	while block is not None:
		if hasattr(block, "xaiResponseId"):
			block.xaiResponseId = None
		if hasattr(block, "xaiEncryptedReasoning"):
			block.xaiEncryptedReasoning = None
		block = getattr(block, "next", None)


def apply_prompt_cache(
	params: dict,
	provider: str,
	model_id: str,
	cache_key: str,
	ttl: str = PROMPT_CACHE_TTL_5M,
) -> None:
	"""Mutate ``params`` with provider-native prompt-cache fields.

	Google, DeepSeek, Ollama, and Custom OpenAI are left unchanged: implicit or
	unknown backends, with no safe request field to send. Google TTL only exists
	on explicit ``cachedContents`` objects, which this add-on does not create.
	"""
	if not isinstance(params, dict):
		return
	key = (cache_key or "").strip()
	if not key:
		return
	if provider in _PROMPT_CACHE_KEY_PROVIDERS:
		params["prompt_cache_key"] = key
	if provider == Provider.OpenRouter:
		params["session_id"] = key
		if _is_openrouter_anthropic_model(model_id):
			params["cache_control"] = _cache_control(ttl)
	elif provider == Provider.Anthropic:
		params["cache_control"] = _cache_control(ttl)


def resolve_prompt_cache_key(page: Any) -> str:
	"""Return a stable cache key for ``page``, allocating one if needed.

	Prefers an already-assigned ``_promptCacheKey``, then the saved conversation
	id, then a fresh UUID so unsaved tabs still stick across turns.
	"""
	existing = getattr(page, "_promptCacheKey", None)
	if isinstance(existing, str) and existing.strip():
		return existing.strip()
	conv_id = getattr(page, "_conversationId", None)
	if isinstance(conv_id, str) and conv_id.strip():
		page._promptCacheKey = conv_id.strip()
		return page._promptCacheKey
	page._promptCacheKey = str(uuid.uuid4())
	return page._promptCacheKey
