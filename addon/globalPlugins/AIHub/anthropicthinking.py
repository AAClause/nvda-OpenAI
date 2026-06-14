"""Anthropic extended/adaptive thinking capability profiles.

Profiles follow the official Claude API docs:
https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
https://platform.claude.com/docs/en/build-with-claude/extended-thinking

Order matters: more specific ``match`` strings must appear before broader ones
(e.g. ``claude-mythos-preview`` before ``claude-mythos``).
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

_DEFAULT_EFFORT = ("low", "medium", "high")

# Fields every profile supplies after normalization.
_PROFILE_DEFAULTS: Dict[str, object] = {
	"match": "",
	"adaptive_only": False,
	"adaptive_supported": False,
	"adaptive_choice_visible": False,
	"reasoning_always_on": False,
	"effort_supported": True,
	"effort_levels": _DEFAULT_EFFORT,
}

# (match substring, profile overrides) — first match wins.
_PROFILES: Tuple[Tuple[str, Dict[str, object]], ...] = (
	# Adaptive-only: manual ``thinking.type: enabled`` returns 400.
	(
		"claude-opus-4-8",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"effort_levels": ("low", "medium", "high", "xhigh", "max"),
		},
	),
	(
		"claude-opus-4-7",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"effort_levels": ("low", "medium", "high", "xhigh", "max"),
		},
	),
	# Thinking always on; adaptive is the only mode.
	(
		"claude-fable-5",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	(
		"claude-mythos-preview",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	(
		"claude-mythos-5",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	# Broader mythos/fable ids (e.g. dated snapshots) after exact 5.x ids.
	(
		"claude-mythos",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	(
		"claude-fable",
		{
			"adaptive_only": True,
			"adaptive_supported": True,
			"reasoning_always_on": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	# Adaptive recommended; manual budget_tokens still accepted (deprecated).
	(
		"claude-opus-4-6",
		{
			"adaptive_supported": True,
			"adaptive_choice_visible": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	(
		"claude-sonnet-4-6",
		{
			"adaptive_supported": True,
			"adaptive_choice_visible": True,
			"effort_levels": ("low", "medium", "high", "max"),
		},
	),
	(
		"claude-opus-4-5",
		{
			"effort_levels": _DEFAULT_EFFORT,
		},
	),
)


def _normalize_profile(raw: Dict[str, object]) -> Dict[str, object]:
	out = dict(_PROFILE_DEFAULTS)
	out.update(raw)
	return out


def get_anthropic_thinking_profile(model_id: str) -> Dict[str, object]:
	"""Return normalized Anthropic thinking capabilities for a model id."""
	mid = (model_id or "").lower()
	for match, overrides in _PROFILES:
		if match in mid:
			profile = _normalize_profile(overrides)
			profile["match"] = match
			return profile
	return dict(_PROFILE_DEFAULTS)


def anthropic_reasoning_always_on(model_id: str) -> bool:
	"""True when the API does not allow turning extended thinking off."""
	return bool(get_anthropic_thinking_profile(model_id).get("reasoning_always_on"))


def normalize_effort(effort: str, allowed_efforts: Iterable[str], default: str = "high") -> str:
	"""Normalize effort value to an allowed Anthropic effort level."""
	allowed = set(allowed_efforts or ())
	val = str(effort or default).strip().lower()
	if val == "minimal":
		val = "low"
	return val if val in allowed else default
