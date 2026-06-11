"""Shared Anthropic thinking/effort capability helpers."""

from typing import Dict, Tuple


_DEFAULT_EFFORT = ("low", "medium", "high")

_PROFILES = (
	{
		"match": "claude-opus-4-7",
		"adaptive_only": True,
		"adaptive_supported": True,
		"adaptive_choice_visible": False,
		"effort_supported": True,
		"effort_levels": ("low", "medium", "high", "xhigh", "max"),
	},
	{
		"match": "claude-mythos",
		"adaptive_only": True,
		"adaptive_supported": True,
		"adaptive_choice_visible": False,
		"effort_supported": True,
		"effort_levels": ("low", "medium", "high", "max"),
	},
	{
		"match": "claude-fable",
		"adaptive_only": True,
		"adaptive_supported": True,
		"adaptive_choice_visible": False,
		"effort_supported": True,
		"effort_levels": ("low", "medium", "high", "max"),
	},
	{
		"match": "claude-opus-4-6",
		"adaptive_only": False,
		"adaptive_supported": True,
		"adaptive_choice_visible": True,
		"effort_supported": True,
		"effort_levels": ("low", "medium", "high", "max"),
	},
	{
		"match": "claude-sonnet-4-6",
		"adaptive_only": False,
		"adaptive_supported": True,
		"adaptive_choice_visible": True,
		"effort_supported": True,
		"effort_levels": ("low", "medium", "high", "max"),
	},
	{
		"match": "claude-opus-4-5",
		"adaptive_only": False,
		"adaptive_supported": False,
		"adaptive_choice_visible": False,
		"effort_supported": True,
		"effort_levels": _DEFAULT_EFFORT,
	},
)


def get_anthropic_thinking_profile(model_id: str) -> Dict[str, object]:
	"""Return normalized Anthropic thinking capabilities for a model id."""
	mid = (model_id or "").lower()
	for profile in _PROFILES:
		if profile["match"] in mid:
			return dict(profile)
	return {
		"match": "",
		"adaptive_only": False,
		"adaptive_supported": False,
		"adaptive_choice_visible": False,
		"effort_supported": True,
		"effort_levels": _DEFAULT_EFFORT,
	}


def normalize_effort(effort: str, allowed_efforts: Tuple[str, ...], default: str = "high") -> str:
	"""Normalize effort value to an allowed Anthropic effort level."""
	val = str(effort or default).strip().lower()
	if val == "minimal":
		val = "low"
	return val if val in set(allowed_efforts or ()) else default
