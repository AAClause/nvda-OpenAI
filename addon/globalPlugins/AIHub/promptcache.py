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

_ANTHROPIC_CACHE_CONTROL = {"type": "ephemeral"}

_PROMPT_CACHE_KEY_PROVIDERS = frozenset({
	Provider.OpenAI,
	Provider.MistralAI,
	Provider.xAI,
	Provider.OpenRouter,
})


def _is_openrouter_anthropic_model(model_id: str) -> bool:
	mid = (model_id or "").lower()
	return mid.startswith("anthropic/") or "claude" in mid


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


def apply_prompt_cache(params: dict, provider: str, model_id: str, cache_key: str) -> None:
	"""Mutate ``params`` with provider-native prompt-cache fields.

	Google, DeepSeek, Ollama, and Custom OpenAI are left unchanged: implicit or
	unknown backends, with no safe request field to send.
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
			params["cache_control"] = dict(_ANTHROPIC_CACHE_CONTROL)
	elif provider == Provider.Anthropic:
		params["cache_control"] = dict(_ANTHROPIC_CACHE_CONTROL)


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
