"""xAI built-in server-side tools (Responses API).

Official references:
- Overview: https://docs.x.ai/developers/tools/overview
- Web search: https://docs.x.ai/developers/tools/web-search
- X search: https://docs.x.ai/developers/tools/x-search
- Code interpreter: https://docs.x.ai/developers/tools/code-execution
- Collections search: https://docs.x.ai/developers/tools/collections-search
- Reasoning / encrypted content: https://docs.x.ai/developers/model-capabilities/text/generate-text
"""

from __future__ import annotations

import re
from typing import Any

_XAI_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_token_list(
	raw: str,
	*,
	max_items: int | None = None,
	strip_at_prefix: bool = False,
) -> list[str]:
	if not isinstance(raw, str) or not raw.strip():
		return []
	items: list[str] = []
	seen: set[str] = set()
	for part in re.split(r"[\s,;]+", raw.strip()):
		token = part.strip()
		if strip_at_prefix and token.startswith("@"):
			token = token[1:]
		if not token or token in seen:
			continue
		seen.add(token)
		items.append(token)
		if max_items is not None and len(items) >= max_items:
			break
	return items


def parse_xai_collection_ids(raw: str) -> list[str]:
	"""Parse comma/space-separated xAI collection ids from user input."""
	return _parse_token_list(raw)


def parse_xai_domain_list(raw: str) -> list[str]:
	"""Parse up to 5 domain names for web search filters."""
	return _parse_token_list(raw, max_items=5)


def parse_xai_handle_list(raw: str) -> list[str]:
	"""Parse up to 20 X handles (``@`` optional)."""
	return _parse_token_list(raw, max_items=20, strip_at_prefix=True)


def parse_xai_iso_date(raw: str) -> str | None:
	"""Return ``YYYY-MM-DD`` when valid, else ``None``."""
	if not isinstance(raw, str):
		return None
	value = raw.strip()
	if not value or not _XAI_ISO_DATE_RE.match(value):
		return None
	return value


def _wnd_checked(wnd, attr: str) -> bool:
	cb = getattr(wnd, attr, None)
	return cb is not None and cb.IsChecked()


def _wnd_text(wnd, attr: str) -> str:
	ctrl = getattr(wnd, attr, None)
	if ctrl is None:
		return ""
	try:
		return ctrl.GetValue()
	except Exception:
		return ""


def _wnd_spin_value(wnd, attr: str, default: int = 0) -> int:
	ctrl = getattr(wnd, attr, None)
	if ctrl is None:
		return default
	try:
		return int(ctrl.GetValue())
	except (TypeError, ValueError):
		return default


def build_web_search_tool_from_wnd(wnd) -> dict[str, Any]:
	"""Build ``web_search`` tool dict with optional filters from conversation chrome."""
	tool: dict[str, Any] = {"type": "web_search"}
	allowed = parse_xai_domain_list(_wnd_text(wnd, "xaiWebAllowedDomainsTextCtrl"))
	excluded = parse_xai_domain_list(_wnd_text(wnd, "xaiWebExcludedDomainsTextCtrl"))
	if allowed:
		tool["allowed_domains"] = allowed
	elif excluded:
		tool["excluded_domains"] = excluded
	if _wnd_checked(wnd, "xaiWebImageSearchCheckBox"):
		tool["enable_image_search"] = True
	if _wnd_checked(wnd, "xaiWebImageUnderstandingCheckBox"):
		tool["enable_image_understanding"] = True
	return tool


def build_x_search_tool_from_wnd(wnd) -> dict[str, Any]:
	"""Build ``x_search`` tool dict with optional filters from conversation chrome."""
	tool: dict[str, Any] = {"type": "x_search"}
	allowed = parse_xai_handle_list(_wnd_text(wnd, "xaiXAllowedHandlesTextCtrl"))
	excluded = parse_xai_handle_list(_wnd_text(wnd, "xaiXExcludedHandlesTextCtrl"))
	if allowed:
		tool["allowed_x_handles"] = allowed
	elif excluded:
		tool["excluded_x_handles"] = excluded
	from_date = parse_xai_iso_date(_wnd_text(wnd, "xaiXFromDateTextCtrl"))
	to_date = parse_xai_iso_date(_wnd_text(wnd, "xaiXToDateTextCtrl"))
	if from_date:
		tool["from_date"] = from_date
	if to_date:
		tool["to_date"] = to_date
	if _wnd_checked(wnd, "xaiXImageUnderstandingCheckBox"):
		tool["enable_image_understanding"] = True
	if _wnd_checked(wnd, "xaiXVideoUnderstandingCheckBox"):
		tool["enable_video_understanding"] = True
	return tool


def build_collections_search_tool_from_wnd(wnd) -> dict[str, Any] | None:
	"""Build ``collections_search`` tool dict when ids are configured."""
	collection_ids = parse_xai_collection_ids(_wnd_text(wnd, "xaiCollectionIdsTextCtrl"))
	if not collection_ids:
		return None
	tool: dict[str, Any] = {
		"type": "collections_search",
		"collection_ids": collection_ids,
	}
	max_results = _wnd_spin_value(wnd, "xaiCollectionsMaxResultsSpinCtrl", default=0)
	if max_results > 0:
		tool["max_num_results"] = max_results
	return tool


def xai_encrypted_reasoning_requested(wnd, reasoning_enabled: bool) -> bool:
	"""True when the user wants encrypted reasoning content in the Responses payload."""
	if not reasoning_enabled:
		return False
	cb = getattr(wnd, "xaiEncryptedReasoningCheckBox", None)
	return cb is not None and cb.IsChecked()


def collect_xai_encrypted_reasoning_input(
	wnd,
	is_regenerate: bool,
	use_previous_response_id: bool,
) -> list[dict]:
	"""Build Responses ``input`` reasoning items from stored block metadata.

	Skipped when ``previous_response_id`` is used (server retains reasoning state).
	"""
	if use_previous_response_id:
		return []
	first = getattr(wnd, "firstBlock", None)
	if first is None:
		return []
	items: list[dict] = []
	block = first
	stop = getattr(wnd, "lastBlock", None)
	while block is not None:
		if is_regenerate and block is stop:
			break
		for entry in getattr(block, "xaiEncryptedReasoning", None) or []:
			if not isinstance(entry, dict):
				continue
			encrypted = entry.get("encrypted_content")
			if not isinstance(encrypted, str) or not encrypted.strip():
				continue
			item: dict[str, Any] = {
				"type": entry.get("type") or "reasoning",
				"encrypted_content": encrypted.strip(),
			}
			rid = entry.get("id")
			if isinstance(rid, str) and rid.strip():
				item["id"] = rid.strip()
			items.append(item)
		if block is stop:
			break
		block = block.next
	return items
