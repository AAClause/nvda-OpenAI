"""Provider-native reasoning request shaping and capability detection.

Each provider documents different defaults; when thinking is optional we default
the UI off and send an explicit disable signal so users are not billed for
reasoning tokens they did not request.

Official references:
- Anthropic adaptive thinking: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
- OpenAI reasoning: https://developers.openai.com/api/docs/guides/reasoning
- Gemini OpenAI compat: https://ai.google.dev/gemini-api/docs/openai
- DeepSeek thinking: https://api-docs.deepseek.com/guides/thinking_mode
- OpenRouter reasoning: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- Mistral reasoning: https://docs.mistral.ai/studio-api/conversations/reasoning
- xAI reasoning: https://docs.x.ai/developers/model-capabilities/text/reasoning
- Ollama OpenAI compat: https://docs.ollama.com/api/openai-compatibility (reasoning_effort)
"""

from __future__ import annotations

from typing import Any

from .anthropicthinking import anthropic_reasoning_always_on
from .consts import Provider, ReasoningEffort

# Providers whose chat-completions body accepts top-level ``reasoning_effort``.
_REASONING_EFFORT_BODY_PROVIDERS = frozenset({
	Provider.OpenAI,
	Provider.CustomOpenAI,
	Provider.MistralAI,
	Provider.Google,
	Provider.xAI,
	Provider.Ollama,
	Provider.DeepSeek,
})


def _mid(model_id: str) -> str:
	return (model_id or "").lower()


def google_reasoning_mandatory(model_id: str) -> bool:
	"""Gemini 2.5 Pro and Gemini 3 families cannot disable thinking."""
	mid = _mid(model_id)
	return "gemini-2.5-pro" in mid or "gemini-3" in mid


def deepseek_reasoning_mandatory(model_id: str) -> bool:
	mid = _mid(model_id)
	return "reasoner" in mid


def deepseek_thinking_defaults_on(model_id: str) -> bool:
	"""DeepSeek V4 thinking mode defaults to enabled; must send disabled explicitly."""
	mid = _mid(model_id)
	if deepseek_reasoning_mandatory(mid):
		return True
	return mid.startswith("deepseek-v4") or mid == "deepseek-chat"


def ollama_reasoning_always_on(model_id: str) -> bool:
	"""GPT-OSS on Ollama only accepts think levels, not full off."""
	return "gpt-oss" in _mid(model_id)


def xai_supports_reasoning_effort(model_id: str) -> bool:
	mid = _mid(model_id)
	return "grok-4.3" in mid or "grok-3-mini" in mid


def xai_reasoning_mandatory(model_id: str) -> bool:
	"""grok-3-mini only documents low/high — no ``none`` disable."""
	return "grok-3-mini" in _mid(model_id)


def mistral_supports_reasoning_effort(model_id: str) -> bool:
	mid = _mid(model_id)
	return "mistral-small" in mid or "mistral-medium-3-5" in mid or "mistral-medium-3.5" in mid


def mistral_reasoning_mandatory(model_id: str) -> bool:
	"""Native magistral reasoning models always think."""
	return "magistral" in _mid(model_id)


def openai_reasoning_model(model_id: str) -> bool:
	mid = _mid(model_id)
	for prefix in ("o1", "o3", "o4", "gpt-5", "gpt-oss"):
		if mid.startswith(prefix) or f"/{prefix}" in mid:
			return True
	return False


def detect_reasoning_mandatory(provider: str, model_id: str, extra_info: dict) -> bool:
	"""True when the upstream API always applies reasoning/thinking."""
	extra = extra_info if isinstance(extra_info, dict) else {}
	if extra.get("reasoning_mandatory") is True:
		return True
	if provider == Provider.Anthropic:
		return anthropic_reasoning_always_on(model_id)
	if provider == Provider.Google:
		return google_reasoning_mandatory(model_id)
	if provider == Provider.DeepSeek:
		return deepseek_reasoning_mandatory(model_id)
	if provider == Provider.Ollama:
		return ollama_reasoning_always_on(model_id)
	if provider == Provider.xAI:
		return xai_reasoning_mandatory(model_id)
	if provider == Provider.MistralAI:
		return mistral_reasoning_mandatory(model_id)
	return False


def supports_reasoning_disable(
	provider: str,
	model_id: str,
	supported_params: set[str],
	*,
	reasoning: bool,
	reasoning_mandatory: bool,
) -> bool:
	"""True when we can send an explicit reasoning-off signal for this model."""
	if not reasoning or reasoning_mandatory:
		return False
	if provider == Provider.Anthropic:
		return True
	if provider == Provider.Ollama:
		return not ollama_reasoning_always_on(model_id)
	if provider == Provider.DeepSeek:
		if reasoning_mandatory:
			return False
		return deepseek_thinking_defaults_on(model_id) or bool(
			reasoning and supported_params & {"thinking", "reasoning"}
		)
	if provider == Provider.Google:
		return not google_reasoning_mandatory(model_id)
	if provider == Provider.xAI:
		return xai_supports_reasoning_effort(model_id) and not xai_reasoning_mandatory(model_id)
	if provider == Provider.MistralAI:
		return mistral_supports_reasoning_effort(model_id)
	if provider == Provider.OpenRouter:
		return "reasoning_effort" in supported_params
	if provider in (Provider.OpenAI, Provider.CustomOpenAI):
		return "reasoning_effort" in supported_params or openai_reasoning_model(model_id)
	return "reasoning_effort" in supported_params


def reasoning_effort_body_providers() -> frozenset[str]:
	return _REASONING_EFFORT_BODY_PROVIDERS


def _deepseek_effort(effort: str) -> str:
	# https://api-docs.deepseek.com/guides/thinking_mode — only high/max are native.
	if effort in ("high", "max"):
		return effort
	return "high"


def _ollama_effort(effort: str) -> str:
	# Ollama OpenAI-compat maps high/medium/low; minimal -> low.
	if effort in ("high", "medium", "low"):
		return effort
	if effort == ReasoningEffort.MINIMAL.value:
		return "low"
	return "medium"


def _mistral_effort(effort: str) -> str:
	# Adjustable Mistral models only document high vs none (none = off via checkbox).
	return "high"


def apply_reasoning_enabled(
	params: dict[str, Any],
	model,
	provider: str,
	effort: str,
	conf: dict,
) -> None:
	"""Send the provider-native reasoning-on signal."""
	if provider == Provider.Anthropic:
		params["reasoning_enabled"] = True
		params["reasoning_effort"] = effort
		params["adaptive_thinking"] = conf.get("adaptiveThinking", True)
		return
	if provider == Provider.Ollama:
		# OpenAI-compat /v1/chat/completions ignores ``think``; use reasoning_effort.
		params["reasoning_effort"] = _ollama_effort(effort)
		return
	if provider == Provider.OpenRouter:
		params["reasoning"] = {"enabled": True, "effort": effort}
		return
	if provider == Provider.DeepSeek:
		if not getattr(model, "reasoning_mandatory", False):
			params["thinking"] = {"type": "enabled"}
		if "reasoning_effort" in model._supported_param_set():
			params["reasoning_effort"] = _deepseek_effort(effort)
		return
	if provider == Provider.MistralAI and mistral_supports_reasoning_effort(model.id):
		params["reasoning_effort"] = _mistral_effort(effort)
		return
	if getattr(model, "reasoning_mandatory", False):
		return
	if provider in _REASONING_EFFORT_BODY_PROVIDERS:
		params["reasoning_effort"] = effort


def apply_reasoning_disabled(params: dict[str, Any], model, provider: str) -> None:
	"""Send the provider-native reasoning-off signal when supported."""
	if not getattr(model, "reasoning", False):
		return
	if not getattr(model, "supports_reasoning_disable", False):
		return
	if provider == Provider.Anthropic:
		return
	if provider == Provider.OpenRouter:
		params["reasoning"] = {"effort": "none"}
		return
	if provider == Provider.DeepSeek:
		params["thinking"] = {"type": "disabled"}
		return
	if provider == Provider.Ollama:
		params["reasoning_effort"] = "none"
		return
	if provider == Provider.MistralAI and mistral_supports_reasoning_effort(model.id):
		params["reasoning_effort"] = "none"
		return
	if provider in _REASONING_EFFORT_BODY_PROVIDERS:
		params["reasoning_effort"] = "none"
