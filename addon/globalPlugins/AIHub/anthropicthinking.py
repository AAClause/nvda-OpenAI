"""Anthropic extended/adaptive thinking capability profiles.

Profiles follow the official Claude API docs:
https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
https://platform.claude.com/docs/en/build-with-claude/effort
https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5

Order matters: more specific ``match`` strings must appear before broader ones
(e.g. ``claude-mythos-preview`` before ``claude-mythos``, ``claude-sonnet-5``
before ``claude-sonnet-4-6``).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

_DEFAULT_EFFORT = ("low", "medium", "high")
_EFFORT_WITH_XHIGH = ("low", "medium", "high", "xhigh", "max")
_EFFORT_WITH_MAX = ("low", "medium", "high", "max")

# Claude Sonnet 5: adaptive only, thinking on by default, rejects ``thinking.type: enabled``.
# https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting
_SONNET_5_PROFILE: Dict[str, object] = {
	"adaptive_only": True,
	"adaptive_supported": True,
	"effort_supported": True,
	"effort_levels": _EFFORT_WITH_XHIGH,
	"thinking_default_on": True,
	"thinking_display_omitted_default": True,
	"fixed_sampling": True,
}

# Fields every profile supplies after normalization.
# ``manual_thinking_supported`` defaults True for legacy ids not in the table.
# Modern adaptive models set ``adaptive_choice_visible`` or ``adaptive_only``,
# which clears ``manual_thinking_supported`` during normalization.
_PROFILE_DEFAULTS: Dict[str, object] = {
	"match": "",
	"adaptive_only": False,
	"adaptive_supported": False,
	"adaptive_choice_visible": False,
	"reasoning_always_on": False,
	"manual_thinking_supported": True,
	"effort_supported": False,
	"effort_levels": (),
	"thinking_default_on": False,
	"thinking_display_omitted_default": False,
	# Anthropic Messages API rejects explicit temperature/top_p/top_k (omit entirely).
	"fixed_sampling": False,
}

# (match substring, profile overrides) — first match wins.
_PROFILES: Tuple[Tuple[str, Dict[str, object]], ...] = (
	(
		"claude-opus-5",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_default_on": True,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-opus-4-8",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-opus-4-7",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-fable-5",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-mythos-preview",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_MAX,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-mythos-5",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-mythos",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	(
		"claude-fable",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_XHIGH,
			"thinking_display_omitted_default": True,
			"fixed_sampling": True,
		},
	),
	("claude-sonnet-5", dict(_SONNET_5_PROFILE)),
	(
		"claude-opus-4-6",
		{
			"adaptive_supported": True,
			"adaptive_choice_visible": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_MAX,
		},
	),
	(
		"claude-sonnet-4-6",
		{
			"adaptive_supported": True,
			"adaptive_choice_visible": True,
			"effort_supported": True,
			"effort_levels": _EFFORT_WITH_MAX,
		},
	),
	(
		"claude-opus-4-5",
		{
			"effort_supported": True,
			"effort_levels": _DEFAULT_EFFORT,
		},
	),
	("claude-sonnet-4-5", {}),
)


def _normalize_profile(raw: Dict[str, object]) -> Dict[str, object]:
	out = dict(_PROFILE_DEFAULTS)
	out.update(raw)
	if out.get("adaptive_only") or out.get("adaptive_choice_visible"):
		out["manual_thinking_supported"] = False
	return out


def get_anthropic_thinking_profile(model_id: str) -> Dict[str, object]:
	"""Return normalized Anthropic thinking capabilities for a model id."""
	mid = (model_id or "").lower()
	for match, overrides in _PROFILES:
		if match in mid:
			profile = _normalize_profile(overrides)
			profile["match"] = match
			return profile
	# Fallback for Sonnet 5 snapshots not yet listed in ``_PROFILES``.
	if "claude-sonnet-5" in mid:
		profile = _normalize_profile(dict(_SONNET_5_PROFILE))
		profile["match"] = "claude-sonnet-5"
		return profile
	return dict(_PROFILE_DEFAULTS)


def anthropic_reasoning_always_on(model_id: str) -> bool:
	"""True when the API does not allow turning extended thinking off."""
	return bool(get_anthropic_thinking_profile(model_id).get("reasoning_always_on"))


def anthropic_thinking_default_on(model_id: str) -> bool:
	"""True when the API enables thinking unless the request disables it."""
	profile = get_anthropic_thinking_profile(model_id)
	return bool(profile.get("reasoning_always_on") or profile.get("thinking_default_on"))


def anthropic_fixed_sampling_model(model_id: str) -> bool:
	"""True when Anthropic rejects explicit temperature/top_p/top_k (must omit).

	Sonnet 5 and Opus 4.7+ per official docs; also Fable/Mythos adaptive-only models.
	"""
	return bool(get_anthropic_thinking_profile(model_id).get("fixed_sampling"))


def anthropic_use_adaptive_thinking(model_id: str, *, adaptive_thinking: bool | None) -> bool:
	"""True when the Messages API body must use ``thinking.type: adaptive``."""
	profile = get_anthropic_thinking_profile(model_id)
	if profile.get("adaptive_only"):
		return True
	if adaptive_thinking is True:
		return True
	return not bool(profile.get("manual_thinking_supported", True))


def resolve_anthropic_reasoning_request(
	model_id: str,
	mode: str,
	default_effort: str,
	*,
	effort_value: str | None = None,
) -> Dict[str, Any]:
	"""Map UI reasoning combo selection to CompletionThread pseudo-params.

	Returns a dict with ``reasoning_enabled`` plus optional ``adaptive_thinking``
	and ``reasoning_effort``. See official docs for mode mapping:

	- **Adaptive-choice models** (Opus/Sonnet 4.6): effort uses ``adaptive`` +
	  ``output_config.effort``; pure Adaptive omits effort.
	- **Adaptive-only models** (Opus 5, Sonnet 5, Opus 4.7+/Fable/Mythos): always
	  ``adaptive`` + effort. Never ``thinking.type: enabled``.
	- **Legacy** (Opus 4.5, Sonnet 4.5, …): manual ``budget_tokens`` + optional effort.
	"""
	profile = get_anthropic_thinking_profile(model_id)
	effort = effort_value
	out: Dict[str, Any] = {"reasoning_enabled": True}

	if mode == "adaptive":
		out["adaptive_thinking"] = True
		return out

	if profile.get("adaptive_choice_visible") and mode == "enabled":
		out["adaptive_thinking"] = True
		if effort:
			out["reasoning_effort"] = effort
		return out

	if profile.get("adaptive_only"):
		out["adaptive_thinking"] = True
		if effort:
			out["reasoning_effort"] = effort
		return out

	out["adaptive_thinking"] = False
	if effort:
		out["reasoning_effort"] = effort
	return out


def normalize_effort(effort: str, allowed_efforts: Iterable[str], default: str = "high") -> str:
	"""Normalize effort value to an allowed Anthropic effort level."""
	allowed = set(allowed_efforts or ())
	val = str(effort or default).strip().lower()
	if val == "minimal":
		val = "low"
	return val if val in allowed else default
