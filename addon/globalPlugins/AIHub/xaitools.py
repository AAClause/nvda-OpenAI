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

# Built-in tool types executed on xAI servers (not OpenAI-style function tools).
XAI_BUILTIN_TOOL_TYPES = frozenset({
	"web_search",
	"x_search",
	"code_interpreter",
	"collections_search",
})

_XAI_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def xai_builtin_tools_requested(tools: list | None) -> bool:
	"""True when ``tools`` includes a built-in xAI server-side tool."""
	for tool in tools or []:
		if not isinstance(tool, dict):
			continue
		ttype = str(tool.get("type", "")).lower()
		if ttype in XAI_BUILTIN_TOOL_TYPES:
			return True
	return False


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
