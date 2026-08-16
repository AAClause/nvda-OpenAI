"""Provider-native reasoning request shaping and catalog capability detection.

Official references:
- Anthropic: https://platform.claude.com/docs/en/build-with-claude/effort
- OpenAI: https://developers.openai.com/api/docs/guides/reasoning
- Gemini: https://ai.google.dev/gemini-api/docs/thinking
- DeepSeek: https://api-docs.deepseek.com/guides/thinking_mode
- OpenRouter: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- Mistral: https://docs.mistral.ai/capabilities/reasoning
- xAI: https://docs.x.ai/developers/model-capabilities/text/reasoning
- Ollama: https://docs.ollama.com/api/openai-compatibility
"""

from __future__ import annotations

from typing import Any

from .anthropicthinking import (
	anthropic_reasoning_always_on,
	resolve_anthropic_reasoning_request,
)
from .consts import GATEWAY_EFFORT_ORDER, Provider, REASONING_EFFORT_NONE, ReasoningEffort

_REASONING_EFFORT_BODY_PROVIDERS = frozenset({
	Provider.OpenAI,
	Provider.CustomOpenAI,
	Provider.MistralAI,
	Provider.Google,
	Provider.Ollama,
	Provider.DeepSeek,
	Provider.xAI,
})
_GATEWAY_EFFORT_SET = frozenset(GATEWAY_EFFORT_ORDER)


def _mid(model_id: str) -> str:
	return (model_id or "").lower()


def catalog_reasoning_meta(extra_info) -> dict:
	"""Nested catalog ``reasoning`` object, or ``{}`` when absent."""
	extra = extra_info if isinstance(extra_info, dict) else {}
	meta = extra.get("reasoning")
	return meta if isinstance(meta, dict) else {}


def catalog_effort_values(meta: dict) -> tuple[str, ...] | None:
	"""Ordered effort values excluding ``none``.

	``None`` means the catalog omitted ``supported_efforts`` (no effort selector).
	JSON ``null`` means all gateway values.
	"""
	if not meta or "supported_efforts" not in meta:
		return None
	raw = meta.get("supported_efforts")
	if raw is None:
		return GATEWAY_EFFORT_ORDER
	if not isinstance(raw, list):
		return None
	seen: set[str] = set()
	for item in raw:
		if not isinstance(item, str):
			continue
		val = item.strip().lower()
		if val == REASONING_EFFORT_NONE or val not in _GATEWAY_EFFORT_SET:
			continue
		seen.add(val)
	return tuple(e for e in GATEWAY_EFFORT_ORDER if e in seen)


def deepseek_reasoning_mandatory(model_id: str) -> bool:
	return "reasoner" in _mid(model_id)


def deepseek_thinking_defaults_on(model_id: str) -> bool:
	mid = _mid(model_id)
	if deepseek_reasoning_mandatory(mid):
		return True
	return mid.startswith("deepseek-v4") or mid == "deepseek-chat"


def ollama_reasoning_always_on(model_id: str) -> bool:
	return "gpt-oss" in _mid(model_id)


def openai_reasoning_model(model_id: str) -> bool:
	mid = _mid(model_id)
	for prefix in ("o1", "o3", "o4", "gpt-5", "gpt-oss"):
		if mid.startswith(prefix) or f"/{prefix}" in mid:
			return True
	return False


def openai_fallback_efforts(model_id: str) -> tuple[str, ...]:
	"""Official GPT-5.x / o-series effort lists when catalog metadata is absent.

	https://developers.openai.com/api/docs/guides/reasoning
	https://developers.openai.com/api/docs/models/gpt-5.4
	https://developers.openai.com/api/docs/models/gpt-5.5
	https://developers.openai.com/api/docs/models/gpt-5.6-sol
	"""
	mid = _mid(model_id)
	if "gpt-5.6" in mid:
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
			ReasoningEffort.XHIGH.value,
			ReasoningEffort.MAX.value,
		)
	if "gpt-5.5" in mid or "gpt-5.4" in mid:
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
			ReasoningEffort.XHIGH.value,
		)
	if "gpt-5" in mid:
		return (
			ReasoningEffort.MINIMAL.value,
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
		)
	if openai_reasoning_model(mid):
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
		)
	return ()


def openai_default_effort(model_id: str) -> str | None:
	mid = _mid(model_id)
	if "gpt-5.4" in mid:
		return None
	if "gpt-5.6" in mid or "gpt-5.5" in mid or "gpt-5" in mid:
		return ReasoningEffort.MEDIUM.value
	if openai_reasoning_model(mid):
		return ReasoningEffort.MEDIUM.value
	return None


def openai_default_enabled(model_id: str) -> bool:
	mid = _mid(model_id)
	if "gpt-5.4" in mid:
		return False
	return openai_reasoning_model(mid)


def xai_fallback_efforts(model_id: str) -> tuple[str, ...]:
	"""Official Grok 4.x effort lists when catalog metadata is absent.

	https://docs.x.ai/developers/model-capabilities/text/reasoning
	"""
	mid = _mid(model_id)
	if "multi-agent" in mid:
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
			ReasoningEffort.XHIGH.value,
		)
	if "grok-4.20" in mid:
		return ()
	if "grok-4.6" in mid:
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
			ReasoningEffort.XHIGH.value,
		)
	if "grok-4.5" in mid:
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
		)
	if "grok-4.3" in mid:
		return (
			ReasoningEffort.LOW.value,
			ReasoningEffort.MEDIUM.value,
			ReasoningEffort.HIGH.value,
		)
	return ()


def xai_reasoning_mandatory(model_id: str) -> bool:
	mid = _mid(model_id)
	return "grok-4.6" in mid or "grok-4.5" in mid


def xai_default_effort(model_id: str) -> str | None:
	mid = _mid(model_id)
	if "grok-4.6" in mid or "grok-4.5" in mid:
		return ReasoningEffort.HIGH.value
	if "grok-4.3" in mid:
		return ReasoningEffort.LOW.value
	return None


def xai_default_enabled(model_id: str) -> bool:
	if xai_reasoning_mandatory(model_id):
		return True
	return "grok-4.3" in _mid(model_id)


def detect_reasoning_mandatory(provider: str, model_id: str, extra_info: dict) -> bool:
	extra = extra_info if isinstance(extra_info, dict) else {}
	if extra.get("reasoning_mandatory") is True:
		return True
	meta = catalog_reasoning_meta(extra)
	if meta and "mandatory" in meta:
		return bool(meta["mandatory"])
	if provider == Provider.Anthropic:
		return anthropic_reasoning_always_on(model_id)
	if provider == Provider.xAI:
		return xai_reasoning_mandatory(model_id)
	if provider == Provider.DeepSeek:
		return deepseek_reasoning_mandatory(model_id)
	if provider == Provider.Ollama:
		return ollama_reasoning_always_on(model_id)
	return False


def supports_reasoning_disable(
	provider: str,
	model_id: str,
	supported_params: set[str],
	*,
	reasoning: bool,
	reasoning_mandatory: bool,
	extra_info: dict | None = None,
) -> bool:
	if not reasoning or reasoning_mandatory:
		return False
	meta = catalog_reasoning_meta(extra_info)
	if meta:
		if meta.get("mandatory") is True:
			return False
		if provider == Provider.xAI and "supported_efforts" not in meta:
			return False
		return True
	if provider == Provider.Anthropic:
		return True
	if provider == Provider.xAI:
		if xai_reasoning_mandatory(model_id):
			return False
		if "grok-4.20" in _mid(model_id) and "multi-agent" not in _mid(model_id):
			return False
		return "grok-4.3" in _mid(model_id) or "reasoning_effort" in supported_params
	if provider == Provider.Ollama:
		return not ollama_reasoning_always_on(model_id)
	if provider == Provider.DeepSeek:
		return deepseek_thinking_defaults_on(model_id) or bool(
			supported_params & {"thinking", "reasoning"}
		)
	if provider in (Provider.OpenAI, Provider.CustomOpenAI):
		return "reasoning_effort" in supported_params or openai_reasoning_model(model_id)
	return "reasoning_effort" in supported_params or "reasoning" in supported_params


def _deepseek_effort(effort: str) -> str:
	if effort in ("low", "high", "max"):
		return effort
	if effort in ("medium", "xhigh"):
		return "high"
	return "high"


def _ollama_effort(effort: str) -> str:
	if effort in ("high", "medium", "low"):
		return effort
	if effort == ReasoningEffort.MINIMAL.value:
		return "low"
	return "medium"


def apply_reasoning_enabled(
	params: dict[str, Any],
	model,
	provider: str,
	effort: str,
	conf: dict,
	*,
	reasoning_selection: tuple[str, str | None, str] | None = None,
) -> None:
	if provider == Provider.Anthropic:
		mode = "enabled"
		effort_value = None
		if reasoning_selection:
			mode, effort_value, _label = reasoning_selection
		elif conf.get("adaptiveThinking") and getattr(model, "adaptive_choice_visible", False):
			mode = "adaptive"
		params.update(
			resolve_anthropic_reasoning_request(
				model.id, mode, effort, effort_value=effort_value
			)
		)
		return
	effort_use = reasoning_selection[1] if reasoning_selection is not None else effort
	if provider == Provider.Ollama:
		params["reasoning_effort"] = _ollama_effort(effort_use)
		return
	if provider == Provider.OpenRouter:
		body: dict[str, Any] = {"enabled": True}
		if effort_use:
			body["effort"] = effort_use
		params["reasoning"] = body
		return
	if provider == Provider.DeepSeek:
		if not getattr(model, "reasoning_mandatory", False):
			params["thinking"] = {"type": "enabled"}
		if "reasoning_effort" in model._supported_param_set() or effort_use:
			params["reasoning_effort"] = _deepseek_effort(effort_use)
		return
	if provider == Provider.MistralAI:
		params["reasoning_effort"] = effort_use or "high"
		return
	if provider == Provider.xAI:
		if effort_use:
			params["reasoning_effort"] = effort_use
		return
	if getattr(model, "reasoning_mandatory", False) and not effort_use:
		return
	if provider in _REASONING_EFFORT_BODY_PROVIDERS and effort_use:
		params["reasoning_effort"] = effort_use


def apply_reasoning_disabled(params: dict[str, Any], model, provider: str) -> None:
	if not getattr(model, "reasoning", False):
		return
	if not getattr(model, "supports_reasoning_disable", False):
		return
	if provider == Provider.Anthropic:
		params["reasoning_disabled"] = True
		return
	if provider == Provider.OpenRouter:
		params["reasoning"] = {"effort": REASONING_EFFORT_NONE}
		return
	if provider == Provider.DeepSeek:
		params["thinking"] = {"type": "disabled"}
		return
	if provider in _REASONING_EFFORT_BODY_PROVIDERS:
		params["reasoning_effort"] = REASONING_EFFORT_NONE
