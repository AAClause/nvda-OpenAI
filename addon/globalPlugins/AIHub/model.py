from datetime import date
import addonHandler
from logHandler import log
from . import apikeymanager
from .apiclient import APIConnectionError, APIStatusError, http_request_json
from .anthropicthinking import anthropic_thinking_default_on, get_anthropic_thinking_profile
from .reasoningrequest import (
	catalog_effort_values,
	catalog_reasoning_meta,
	deepseek_thinking_defaults_on,
	detect_reasoning_mandatory,
	openai_default_effort,
	openai_default_enabled,
	openai_fallback_efforts,
	openai_reasoning_model,
	supports_reasoning_disable as _supports_reasoning_disable,
	xai_default_effort,
	xai_default_enabled,
	xai_fallback_efforts,
	xai_reasoning_mandatory,
)
from .consts import Provider, REASONING_EFFORT_NONE, ReasoningEffort

addonHandler.initTranslation()

_models = {}


def _reasoning_effort_labels() -> dict[str, str]:
	return {
		# Translators: Text in model labels and capability descriptions.
		ReasoningEffort.MINIMAL.value: _("Minimal"),
		# Translators: Text in model labels and capability descriptions.
		ReasoningEffort.LOW.value: _("Low"),
		# Translators: Text in model labels and capability descriptions.
		ReasoningEffort.MEDIUM.value: _("Medium"),
		# Translators: Text in model labels and capability descriptions.
		ReasoningEffort.HIGH.value: _("High"),
		# Translators: Text in model labels and capability descriptions.
		ReasoningEffort.XHIGH.value: _("Extra high"),
		# Translators: Text in model labels and capability descriptions.
		ReasoningEffort.MAX.value: _("Maximum"),
	}


class Model:

	def __init__(
		self,
		provider: str,
		id_: str,
		description: str = '',
		contextWindow: int = 32768,
		maxOutputToken: int = -1,
		maxTemperature: float = 2.0,
		defaultTemperature: float = 1.0,
		vision: bool = False,
		preview: bool = False,
		supportedParameters: list = None,
		name: str = '',
		extraInfo=None,
		reasoning: bool = False,
		reasoningMandatory: bool = False,
		audioInput: bool = False,
		audioOutput: bool = False,
		created: int = 0,
		parameterConflicts: list = None,
		defaultParameters: dict | None = None,
		**kwargs
	):
		self.provider = provider
		self.created = created
		self.id = id_
		self.name = name or id_
		self.description = description
		self.contextWindow = contextWindow
		self.maxOutputToken = maxOutputToken
		self.maxTemperature = maxTemperature
		self.defaultTemperature = defaultTemperature
		self.vision = vision
		self.audioInput = audioInput
		self.audioOutput = audioOutput
		self.preview = preview
		self.supportedParameters = supportedParameters or []
		self.extraInfo = extraInfo or {}
		self.reasoning = reasoning
		self.reasoningMandatory = bool(reasoningMandatory)
		# Groups of mutually exclusive params, e.g. [["temperature", "top_p"]] from JSON
		self.parameterConflicts = parameterConflicts if isinstance(parameterConflicts, list) else []
		# SigmaNight ``default_parameters``: null means omit from API; numeric values are UI defaults.
		self.defaultParameters = defaultParameters if isinstance(defaultParameters, dict) else {}

	def _supported_param_set(self) -> set[str]:
		return {
			p.lower()
			for p in (self.supportedParameters or [])
			if isinstance(p, str)
		}

	def allows_request_parameter(self, name: str) -> bool:
		"""True when SigmaNight ``supported_parameters`` lists this API parameter.

		``default_parameters`` with ``null`` documents fixed/omitted params when
		they are absent from ``supported_parameters`` (Sonnet 5, Opus 4.7+). When
		a param is both supported and null-default (Sonnet 4.6), it stays tunable.

		Anthropic's API version 2026-10-21 (the one the add-on sends) rejects
		temperature/top_p/top_k on every model, whatever the metadata lists.
		"""
		key = (name or "").lower()
		if key not in self._supported_param_set():
			return False
		if self.provider == Provider.Anthropic and key in ("temperature", "top_p", "top_k"):
			return False
		return True

	def metadata_default_for(self, name: str, fallback=None):
		"""Return a default from ``default_parameters``, or ``fallback`` when unset/null."""
		key = (name or "").lower()
		if key not in self.defaultParameters:
			return fallback
		val = self.defaultParameters[key]
		return fallback if val is None else val

	@property
	def supports_web_search(self):
		"""True when model metadata declares provider-native web search.

		SigmaNight ``pricing.web_search`` is the per-search price of the provider's
		own search tool, so a non-zero value means the model supports it (OpenAI
		Responses ``web_search``, Anthropic ``web_search``, Gemini Google Search
		grounding, xAI ``web_search``).
		"""
		params = self._supported_param_set()
		if params & {"web_search_options", "web_search", "google_search"}:
			return True
		if self.provider not in _NATIVE_WEB_SEARCH_PROVIDERS:
			return False
		pricing = self.extraInfo.get("pricing") if isinstance(self.extraInfo, dict) else None
		if not isinstance(pricing, dict):
			return False
		try:
			return float(pricing.get("web_search") or 0) > 0
		except (TypeError, ValueError):
			return False

	@property
	def expiration_date(self) -> date | None:
		"""Catalog ``expiration_date`` (the provider's retirement date), or ``None``."""
		raw = self.extraInfo.get("expiration_date") if isinstance(self.extraInfo, dict) else None
		if not isinstance(raw, str) or not raw.strip():
			return None
		try:
			return date.fromisoformat(raw.strip()[:10])
		except ValueError:
			return None

	@property
	def is_retired(self) -> bool:
		"""True once the catalog retirement date has passed."""
		expires = self.expiration_date
		return bool(expires and expires <= date.today())

	@property
	def knowledge_cutoff(self) -> str:
		"""Catalog ``knowledge_cutoff`` as published (e.g. ``Jun 2026``), or ``""``."""
		raw = self.extraInfo.get("knowledge_cutoff") if isinstance(self.extraInfo, dict) else None
		return raw.strip() if isinstance(raw, str) else ""

	@property
	def supports_x_search(self):
		"""True when xAI Responses API ``x_search`` (X/Twitter) is available for this model."""
		return self.supports_xai_builtin_tools

	@property
	def supports_xai_builtin_tools(self):
		"""True when xAI Responses API built-in server-side tools are available."""
		if self.provider != Provider.xAI:
			return False
		return "tools" in self._supported_param_set()

	@property
	def supports_code_interpreter(self):
		"""True when xAI ``code_interpreter`` built-in tool is available."""
		return self.supports_xai_builtin_tools

	@property
	def supports_collections_search(self):
		"""True when xAI collections search (Responses ``file_search``) is available."""
		return self.supports_xai_builtin_tools

	@property
	def supports_openrouter_web_search(self):
		"""True when OpenRouter can attach the openrouter:web_search server tool (tool-calling models)."""
		if self.provider != Provider.OpenRouter:
			return False
		params = self._supported_param_set()
		return "tools" in params or "tool_choice" in params

	@property
	def reasoning_mandatory(self):
		"""True when model metadata marks reasoning as required."""
		if bool(getattr(self, "reasoningMandatory", False)):
			return True
		extra = self.extraInfo if isinstance(self.extraInfo, dict) else {}
		if extra.get("reasoning_mandatory") is True:
			return True
		meta = catalog_reasoning_meta(extra)
		if meta and "mandatory" in meta:
			return bool(meta["mandatory"])
		return False

	@property
	def catalog_reasoning(self) -> dict:
		return catalog_reasoning_meta(self.extraInfo)

	@property
	def supports_reasoning_disable(self) -> bool:
		"""True when the API accepts an explicit reasoning-off signal for this model."""
		return _supports_reasoning_disable(
			self.provider,
			self.id,
			self._supported_param_set(),
			reasoning=bool(self.reasoning),
			reasoning_mandatory=bool(self.reasoning_mandatory),
			extra_info=self.extraInfo if isinstance(self.extraInfo, dict) else {},
		)

	@property
	def reasoning_always_on(self) -> bool:
		"""True when the user cannot turn reasoning off (mandatory or no disable API)."""
		return bool(self.reasoning and (self.reasoning_mandatory or not self.supports_reasoning_disable))

	@property
	def supports_adaptive_thinking(self):
		"""True if model supports Anthropic adaptive thinking."""
		if self.provider != Provider.Anthropic:
			return False
		profile = get_anthropic_thinking_profile(self.id)
		return bool(profile.get("adaptive_supported"))

	@property
	def adaptive_choice_visible(self):
		"""True if user can choose adaptive vs manual for this Anthropic model."""
		if self.provider != Provider.Anthropic:
			return False
		profile = get_anthropic_thinking_profile(self.id)
		return bool(profile.get("adaptive_choice_visible"))

	@property
	def thinking_budget_supported(self):
		if not self.reasoning:
			return False
		if self.provider == Provider.Anthropic:
			profile = get_anthropic_thinking_profile(self.id)
			if profile.get("adaptive_only") or profile.get("adaptive_choice_visible"):
				return False
			meta = self.catalog_reasoning
			if meta:
				return bool(meta.get("supports_max_tokens"))
			return True
		return bool(self.catalog_reasoning.get("supports_max_tokens"))

	@property
	def default_reasoning_effort(self) -> str | None:
		opts = {v for v, _ in self.reasoning_effort_options}
		effort = self.catalog_reasoning.get("default_effort")
		if isinstance(effort, str):
			val = effort.strip().lower()
			if val != REASONING_EFFORT_NONE and val in opts:
				return val
		if self.provider == Provider.DeepSeek and ReasoningEffort.HIGH.value in opts:
			return ReasoningEffort.HIGH.value
		if self.provider == Provider.Anthropic and ReasoningEffort.HIGH.value in opts:
			return ReasoningEffort.HIGH.value
		if self.provider == Provider.xAI:
			val = xai_default_effort(self.id)
			return val if val in opts else None
		if self.provider in (Provider.OpenAI, Provider.CustomOpenAI):
			val = openai_default_effort(self.id)
			return val if val in opts else None
		return None

	@property
	def default_reasoning_enabled(self) -> bool:
		if self.reasoning_always_on:
			return True
		meta = self.catalog_reasoning
		if meta:
			if str(meta.get("default_effort") or "").strip().lower() == REASONING_EFFORT_NONE:
				return False
			if "default_enabled" in meta:
				return bool(meta["default_enabled"])
			effort = meta.get("default_effort")
			if isinstance(effort, str) and effort.strip():
				return True
		if self.provider == Provider.DeepSeek:
			return deepseek_thinking_defaults_on(self.id)
		if self.provider == Provider.Anthropic:
			return anthropic_thinking_default_on(self.id)
		if self.provider == Provider.xAI:
			return xai_default_enabled(self.id)
		if self.provider in (Provider.OpenAI, Provider.CustomOpenAI):
			return openai_default_enabled(self.id)
		return False

	def _known_default_reasoning_enabled(self) -> bool | None:
		"""API default on/off when documented; ``None`` if unknown."""
		if not self.reasoning:
			return None
		if self.reasoning_always_on:
			return True
		meta = self.catalog_reasoning
		if meta:
			if str(meta.get("default_effort") or "").strip().lower() == REASONING_EFFORT_NONE:
				return False
			if "default_enabled" in meta:
				return bool(meta["default_enabled"])
			effort = meta.get("default_effort")
			if isinstance(effort, str) and effort.strip() and effort.strip().lower() != REASONING_EFFORT_NONE:
				return True
		if self.provider == Provider.DeepSeek:
			return deepseek_thinking_defaults_on(self.id)
		if self.provider == Provider.Anthropic:
			return anthropic_thinking_default_on(self.id)
		if self.provider == Provider.xAI:
			mid = (self.id or "").lower()
			if xai_reasoning_mandatory(self.id) or "grok-4.3" in mid:
				return xai_default_enabled(self.id)
			return None
		if self.provider in (Provider.OpenAI, Provider.CustomOpenAI):
			if openai_reasoning_model(self.id):
				return openai_default_enabled(self.id)
			return None
		return None

	@property
	def api_default_reasoning_option(self) -> tuple[str, str | None] | None:
		"""Combo ``(mode, effort)`` matching the provider API default, if known."""
		if not self.reasoning:
			return None
		known_on = self._known_default_reasoning_enabled()
		effort = self.default_reasoning_effort
		if known_on is False:
			return ("disabled", None)
		if known_on is True:
			if effort:
				return ("enabled", effort)
			if not self.reasoning_effort_options:
				return ("enabled", None)
			return None
		if effort:
			return ("enabled", effort)
		return None

	@property
	def reasoning_effort_options(self):
		"""Tuple of (value, label) for effort dropdown, or () if no configurable effort."""
		if not self.reasoning:
			return ()
		meta = self.catalog_reasoning
		if meta:
			levels = catalog_effort_values(meta)
			if not levels:
				return ()
		else:
			levels = self._fallback_effort_values()
		if not levels:
			return ()
		labels = _reasoning_effort_labels()
		return tuple((lv, labels.get(lv, lv.title())) for lv in levels)

	def _fallback_effort_values(self) -> tuple[str, ...]:
		if self.provider == Provider.Anthropic:
			profile = get_anthropic_thinking_profile(self.id)
			if not profile.get("effort_supported"):
				return ()
			return tuple(profile.get("effort_levels") or ())
		if self.provider == Provider.DeepSeek:
			return (
				ReasoningEffort.LOW.value,
				ReasoningEffort.HIGH.value,
				ReasoningEffort.MAX.value,
			)
		if self.provider == Provider.Ollama:
			return (
				ReasoningEffort.LOW.value,
				ReasoningEffort.MEDIUM.value,
				ReasoningEffort.HIGH.value,
			)
		if self.provider == Provider.xAI:
			return xai_fallback_efforts(self.id)
		if self.provider in (Provider.OpenAI, Provider.CustomOpenAI):
			return openai_fallback_efforts(self.id)
		return ()

	def __repr__(self):
		return (
			f"Model(id={self.id}, name={self.name}, description={self.description}, "
			f"contextWindow={self.contextWindow}, maxOutputToken={self.maxOutputToken}, "
			f"maxTemperature={self.maxTemperature}, defaultTemperature={self.defaultTemperature})"
		)

	def __str__(self):
		name = self.name
		id_ = self.id
		contextWindow = self.contextWindow
		maxOutputToken = self.maxOutputToken
		s = name + " ["
		l = [
			# Translators: Text in model labels and capability descriptions.
			_("provider: {provider}").format(provider=self.provider),
		]
		if id_ != name:
			# Translators: Text in model labels and capability descriptions.
			l.append(_("ID: {id}").format(id=id_))
		if contextWindow > 0:
			# Translators: Text in model labels and capability descriptions.
			l.append(_("context window: {contextWindow}").format(contextWindow=contextWindow))
		if maxOutputToken > 0:
			# Translators: Text in model labels and capability descriptions.
			l.append(_("max output tokens: {maxOutputToken}").format(maxOutputToken=maxOutputToken))
		s += ". ".join(l)
		s += ']'
		return s

	def __hash__(self):
		return hash((self.provider, self.id))


# Providers whose native web search the request builder knows how to attach
# (see chatparams._apply_web_search_settings). OpenRouter has its own path.
_NATIVE_WEB_SEARCH_PROVIDERS = (Provider.OpenAI, Provider.Anthropic, Provider.Google, Provider.xAI)


PROVIDER_URL = {
	Provider.MistralAI: "https://raw.githubusercontent.com/SigmaNight/model-metadata/refs/heads/master/data/mistralai.json",
	Provider.OpenAI: "https://raw.githubusercontent.com/SigmaNight/model-metadata/refs/heads/master/data/openai.json",
	Provider.DeepSeek: "https://raw.githubusercontent.com/SigmaNight/model-metadata/refs/heads/master/data/deepseek.json",
	Provider.CustomOpenAI: "",
	Provider.Ollama: "",
	Provider.OpenRouter: "https://openrouter.ai/api/v1/models",
	Provider.Anthropic: "https://raw.githubusercontent.com/SigmaNight/model-metadata/refs/heads/master/data/anthropic.json",
	Provider.xAI: "https://raw.githubusercontent.com/SigmaNight/model-metadata/refs/heads/master/data/x-ai.json",
	Provider.Google: "https://raw.githubusercontent.com/SigmaNight/model-metadata/refs/heads/master/data/google.json",
}


def _ollama_native_base(base_url: str) -> str:
	base = (base_url or "").rstrip("/")
	if base.lower().endswith("/v1"):
		base = base[:-3]
	return base


def _fetch_ollama_tags(base_url: str, headers: dict) -> dict:
	"""Fetch Ollama native /api/tags metadata indexed by model name."""
	native_base = _ollama_native_base(base_url)
	if not native_base:
		return {}
	try:
		payload = http_request_json(native_base + "/api/tags", headers=headers, timeout=15)
	except Exception:
		log.debug("Ollama /api/tags failed for %s", native_base, exc_info=True)
		return {}
	models = payload.get("models", []) if isinstance(payload, dict) else []
	index = {}
	if not isinstance(models, list):
		return index
	for item in models:
		if not isinstance(item, dict):
			continue
		name = item.get("model") or item.get("name")
		if isinstance(name, str) and name:
			index[name] = item
	return index


def _fetch_ollama_show(native_base: str, name: str, headers: dict) -> dict:
	"""``POST /api/show`` for one model, or ``{}`` on failure."""
	try:
		payload = http_request_json(
			native_base + "/api/show", method="POST", headers=headers, json_body={"model": name}, timeout=10
		)
	except Exception:
		log.debug("Ollama /api/show failed for %s", name, exc_info=True)
		return {}
	return payload if isinstance(payload, dict) else {}


def _fetch_ollama_show_index(base_url: str, names, headers: dict) -> dict:
	"""``/api/show`` payloads for ``names``, fetched in parallel and indexed by name."""
	native_base = _ollama_native_base(base_url)
	names = [n for n in names if isinstance(n, str) and n]
	if not native_base or not names:
		return {}
	from concurrent.futures import ThreadPoolExecutor

	with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
		payloads = pool.map(lambda n: _fetch_ollama_show(native_base, n, headers), names)
		return dict(zip(names, payloads, strict=True))


def ollama_catalog_fields(show: dict) -> dict:
	"""Translate ``/api/show`` into the catalog fields the model parser understands.

	``capabilities`` gives vision and tools; ``thinking`` gives the levels the
	model accepts (named levels and/or booleans) and its default:
	https://docs.ollama.com/capabilities/thinking
	"""
	if not isinstance(show, dict):
		return {}
	caps = {str(c).lower() for c in show.get("capabilities") or [] if isinstance(c, str)}
	fields: dict = {}
	if "vision" in caps:
		fields["input_modalities"] = ["text", "image"]
	if "tools" in caps:
		fields["supported_parameters"] = ["tools"]
	thinking = show.get("thinking")
	values = thinking.get("values") if isinstance(thinking, dict) else None
	if isinstance(values, list) and values:
		if values == [False]:
			return fields
		levels = [v.strip().lower() for v in values if isinstance(v, str) and v.strip()]
		meta: dict = {"mandatory": False not in values, "supports_max_tokens": False}
		if levels:
			meta["supported_efforts"] = levels + (["none"] if False in values else [])
		default = thinking.get("default")
		if isinstance(default, str) and default.strip():
			meta["default_effort"] = default.strip().lower()
			meta["default_enabled"] = True
		elif isinstance(default, bool):
			meta["default_enabled"] = default
		fields["reasoning"] = meta
	elif "thinking" in caps:
		# Thinking without level metadata: simple on/off.
		fields["reasoning"] = {"mandatory": False, "supports_max_tokens": False}
	return fields


def _models_from_ollama_tags(tags_index: dict, show_index: dict | None = None) -> list:
	"""Build OpenAI-like model list from Ollama native tags payload."""
	models = []
	for name, tag in tags_index.items():
		if not isinstance(tag, dict):
			continue
		details = tag.get("details", {}) if isinstance(tag.get("details"), dict) else {}
		description_parts = []
		for key in ("family", "parameter_size", "quantization_level"):
			val = details.get(key)
			if val:
				description_parts.append(str(val))
		show_fields = ollama_catalog_fields((show_index or {}).get(name) or {})
		model = {
			"id": name,
			"name": tag.get("name", name),
			"description": " / ".join(description_parts),
			"architecture": {
				"input_modalities": show_fields.get("input_modalities", ["text"]),
				"output_modalities": ["text"],
			},
			"supported_parameters": [
				"temperature",
				"top_p",
				"top_k",
				"stop",
				"max_tokens",
				*show_fields.get("supported_parameters", []),
			],
			"created": 0,
			"ollama": {
				"size_bytes": tag.get("size"),
				"digest": tag.get("digest"),
				"modified_at": tag.get("modified_at"),
				"details": details,
			},
		}
		if "reasoning" in show_fields:
			model["reasoning"] = show_fields["reasoning"]
		models.append(model)
	return models


def _detect_reasoning_mandatory(provider: str, model_id: str, extra_info: dict) -> bool:
	return detect_reasoning_mandatory(provider, model_id, extra_info)


def _parse_model_obj(provider: str, model: dict) -> Model:
	"""Parse a single model dict from SigmaNight or OpenRouter format."""
	# Support both SigmaNight (models) and OpenRouter (data) structures
	# SigmaNight: context_length and max_completion_tokens in top_provider
	# OpenRouter: may have context_length at top level or in different structure
	top_provider = model.get("top_provider") or {}
	arch = model.get("architecture") or {}

	context_length = model.get("context_length")
	if context_length is None:
		context_length = top_provider.get("context_length", 32768)
	try:
		context_length = int(context_length)
	except (TypeError, ValueError):
		context_length = 32768

	max_completion = top_provider.get("max_completion_tokens")
	if max_completion is None:
		max_completion = model.get("max_completion_tokens", -1)
	try:
		max_completion = int(max_completion) if max_completion is not None else -1
	except (TypeError, ValueError):
		max_completion = -1

	# Modalities: architecture first, then top-level (OpenRouter/SigmaNight/other formats)
	modality = arch.get("modality", "") if isinstance(arch, dict) else ""
	input_mods = (arch.get("input_modalities") if isinstance(arch, dict) else None) or model.get("input_modalities") or []
	output_mods = (arch.get("output_modalities") if isinstance(arch, dict) else None) or model.get("output_modalities") or []
	if not isinstance(input_mods, list):
		input_mods = []
	if not isinstance(output_mods, list):
		output_mods = []
	input_mods = [str(m).lower() for m in input_mods]
	output_mods = [str(m).lower() for m in output_mods]
	if modality:
		mod_parts = modality.lower().replace(" ", "").split("->")
		input_part = mod_parts[0] if len(mod_parts) > 0 else ""
		output_part = mod_parts[1] if len(mod_parts) > 1 else ""
		if "image" in input_part and "image" not in input_mods:
			input_mods.append("image")
		if "audio" in input_part and "audio" not in input_mods:
			input_mods.append("audio")
		if "audio" in output_part and "audio" not in output_mods:
			output_mods.append("audio")

	vision = "image" in input_mods
	audio_input = "audio" in input_mods
	audio_output = "audio" in output_mods

	supported = model.get("supported_parameters")
	if not isinstance(supported, list):
		supported = []

	model_id = model.get("id", "")
	catalog_reasoning = model.get("reasoning")
	has_catalog = isinstance(catalog_reasoning, dict)
	reasoning = has_catalog or "reasoning" in supported or "include_reasoning" in supported
	if not reasoning:
		if provider == Provider.DeepSeek and deepseek_thinking_defaults_on(model_id):
			reasoning = True
		elif provider in (Provider.OpenAI, Provider.CustomOpenAI) and openai_reasoning_model(model_id):
			reasoning = True
		elif provider == Provider.xAI and "non-reasoning" not in (model_id or "").lower() and (
			xai_reasoning_mandatory(model_id)
			or bool(xai_fallback_efforts(model_id))
			or "grok-4.20" in (model_id or "").lower()
		):
			reasoning = True
		elif provider == Provider.Anthropic:
			profile = get_anthropic_thinking_profile(model_id)
			if profile.get("adaptive_supported") or profile.get("effort_supported") or profile.get("thinking_default_on"):
				reasoning = True

	exclude_keys_pre = {"id", "name", "description", "context_length", "top_provider", "parameter_conflicts", "default_parameters"}
	extra_info_pre = {k: v for k, v in model.items() if k not in exclude_keys_pre}
	reasoning_mandatory = _detect_reasoning_mandatory(provider, model_id, extra_info_pre) if reasoning else False

	# Use only parameters from supported_parameters; no defaults or provider overrides
	created = model.get("created", 0)
	try:
		created = int(created) if created is not None else 0
	except (TypeError, ValueError):
		created = 0

	exclude_keys = {"id", "name", "description", "context_length", "top_provider", "parameter_conflicts", "default_parameters"}
	extra_info = {k: v for k, v in model.items() if k not in exclude_keys}

	# Optional: groups of mutually exclusive params from model JSON, e.g. [["temperature", "top_p"]]
	param_conflicts = model.get("parameter_conflicts")
	if isinstance(param_conflicts, list):
		param_conflicts = [g for g in param_conflicts if isinstance(g, list)]
	else:
		param_conflicts = []

	raw_defaults = model.get("default_parameters")
	default_parameters: dict[str, object] = {}
	if isinstance(raw_defaults, dict):
		for key, val in raw_defaults.items():
			if isinstance(key, str):
				default_parameters[key.lower()] = val
	default_temperature = 0.7
	temp_default = default_parameters.get("temperature")
	if isinstance(temp_default, (int, float)):
		default_temperature = float(temp_default)

	return Model(
		provider=provider,
		id_=model.get("id", ""),
		name=model.get("name", model.get("id", "")),
		description=model.get("description", ""),
		contextWindow=context_length,
		maxOutputToken=max_completion,
		maxTemperature=2.0,
		defaultTemperature=default_temperature,
		vision=vision,
		preview="-preview" in model.get("id", ""),
		supportedParameters=supported,
		extraInfo=extra_info,
		reasoning=reasoning,
		reasoningMandatory=reasoning_mandatory,
		audioInput=audio_input,
		audioOutput=audio_output,
		created=created,
		parameterConflicts=param_conflicts,
		defaultParameters=default_parameters,
	)


def clearModelCache(provider=None):
	"""Clear cached models. If provider is None, clear all providers."""
	global _models
	if provider is None:
		_models.clear()
		_unreachable_until.clear()
	else:
		for cache in (_models, _unreachable_until):
			keys = [k for k in cache.keys() if k == provider or (isinstance(k, str) and k.startswith(provider + ":"))]
			for key in keys:
				del cache[key]


# Providers whose model list lives at a per-account base URL rather than a
# fixed PROVIDER_URL. Mirrors apikeymanager._USER_ENDPOINT_PROVIDERS.
_DYNAMIC_BASE_URL_PROVIDERS = (Provider.CustomOpenAI, Provider.Ollama)

# Model lists come from the network: the SigmaNight catalog / OpenRouter for
# fixed providers, the account's own server for Ollama and custom endpoints
# (often LAN hosts unreachable away from that network). Check name resolution
# + TCP connect quickly before any request, and remember failures briefly so
# browsing accounts does not retry them.
_ENDPOINT_PROBE_TIMEOUT = 1.5
_UNREACHABLE_RETRY_SECONDS = 60
_unreachable_until: dict = {}


def _endpoint_reachable(base_url: str) -> bool:
	"""True when ``base_url``'s host resolves and accepts a TCP connection within the probe timeout."""
	import socket
	import threading
	import urllib.parse

	parts = urllib.parse.urlsplit(base_url or "")
	host = parts.hostname
	if not host:
		return False
	port = parts.port or (443 if parts.scheme == "https" else 80)
	result = []

	def _probe():
		try:
			# getaddrinfo has no timeout of its own: the thread join bounds it.
			with socket.create_connection((host, port), timeout=_ENDPOINT_PROBE_TIMEOUT):
				result.append(True)
		except OSError:
			pass

	worker = threading.Thread(target=_probe, daemon=True)
	worker.start()
	worker.join(_ENDPOINT_PROBE_TIMEOUT + 0.5)
	return bool(result)


def _check_endpoint(cache_key: str, base_url: str) -> bool:
	"""Probe the host serving a model list, caching failures for a short while."""
	import time

	if _unreachable_until.get(cache_key, 0) > time.monotonic():
		return False
	if _endpoint_reachable(base_url):
		_unreachable_until.pop(cache_key, None)
		return True
	_unreachable_until[cache_key] = time.monotonic() + _UNREACHABLE_RETRY_SECONDS
	log.info("OpenAI addon: endpoint %s unreachable; skipping model list for %ss", base_url, _UNREACHABLE_RETRY_SECONDS)
	return False


def _models_cache_key(provider, account_id: str = None) -> str:
	return provider if provider not in _DYNAMIC_BASE_URL_PROVIDERS else f"{provider}:{account_id or '__active__'}"


def getCachedModels(provider, account_id: str = None):
	"""Models already loaded for ``provider``/``account_id``, or ``None`` (never blocks)."""
	return _models.get(_models_cache_key(provider, account_id))


def getModels(provider, account_id: str = None) -> list:
	"""Fetch and parse model list for provider. Supports both 'models' and 'data' keys.
	Returns empty list on network or parse errors; logs failures."""
	if provider is None or provider not in PROVIDER_URL:
		raise ValueError("Unknown provider %s" % provider)
	global _models
	cache_key = _models_cache_key(provider, account_id)
	if cache_key in _models:
		return _models[cache_key]
	if provider in _DYNAMIC_BASE_URL_PROVIDERS:
		list_url = apikeymanager.get(provider).get_base_url(account_id=account_id)
	else:
		list_url = PROVIDER_URL[provider]
	if not list_url or not _check_endpoint(cache_key, list_url):
		return []

	if provider == Provider.Ollama:
		manager = apikeymanager.get(provider)
		base_url = manager.get_base_url(account_id=account_id)
		if not base_url:
			return []
		headers = {"User-Agent": "Mozilla/5.0 (compatible; NVDA-OpenAI-Addon/1.0)"}
		# Prefer official native endpoint, then fallback to OpenAI-compatible /v1/models.
		ollama_tags = _fetch_ollama_tags(base_url, headers)
		if ollama_tags:
			show_index = _fetch_ollama_show_index(base_url, list(ollama_tags), headers)
			model_list = _models_from_ollama_tags(ollama_tags, show_index)
			data = {"models": model_list}
		else:
			url = base_url.rstrip("/") + "/models"
			try:
				data = http_request_json(url, headers=headers, timeout=30)
			except (APIStatusError, APIConnectionError) as e:
				log.warning("OpenAI addon: failed to fetch models for %s: %s", provider, e)
				return []
			except Exception as e:
				log.warning("OpenAI addon: error fetching models for %s: %s", provider, e)
				return []
	elif provider == Provider.CustomOpenAI:
		manager = apikeymanager.get(provider)
		base_url = manager.get_base_url(account_id=account_id)
		if not base_url:
			return []
		url = base_url.rstrip("/") + "/models"
		headers = {"User-Agent": "Mozilla/5.0 (compatible; NVDA-OpenAI-Addon/1.0)"}
		api_key = manager.get_api_key(account_id=account_id)
		if api_key:
			headers["Authorization"] = f"Bearer {api_key}"
		ollama_tags = {}
	else:
		url = PROVIDER_URL[provider]
		headers = {"User-Agent": "Mozilla/5.0 (compatible; NVDA-OpenAI-Addon/1.0)"}
		ollama_tags = {}
	if provider != Provider.Ollama:
		try:
			data = http_request_json(url, headers=headers, timeout=30)
		except (APIStatusError, APIConnectionError) as e:
			log.warning("OpenAI addon: failed to fetch models for %s: %s", provider, e)
			return []
		except ValueError as e:
			log.warning("OpenAI addon: invalid JSON for %s models: %s", provider, e)
			return []
		except Exception as e:
			log.warning("OpenAI addon: error fetching models for %s: %s", provider, e)
			return []

	# SigmaNight uses "models", OpenRouter uses "data"
	model_list = data.get("models") or data.get("data") or []
	if not isinstance(model_list, list):
		model_list = []

	models = []
	for m in model_list:
		if not isinstance(m, dict):
			continue
		model_id = m.get("id")
		if not model_id:
			continue
		if provider == Provider.Ollama:
			tag = ollama_tags.get(model_id) or ollama_tags.get(model_id + ":latest") or {}
			details = tag.get("details", {}) if isinstance(tag, dict) else {}
			model_obj = dict(m)
			if isinstance(tag, dict):
				model_obj.setdefault("name", tag.get("name", model_id))
				model_obj.setdefault("description", "")
				parts = []
				if details.get("family"):
					parts.append(str(details.get("family")))
				if details.get("parameter_size"):
					parts.append(str(details.get("parameter_size")))
				if details.get("quantization_level"):
					parts.append(str(details.get("quantization_level")))
				if parts and not model_obj.get("description"):
					model_obj["description"] = " / ".join(parts)
				model_obj["ollama"] = {
					"size_bytes": tag.get("size"),
					"digest": tag.get("digest"),
					"modified_at": tag.get("modified_at"),
					"details": details,
				}
			m = model_obj
		try:
			models.append(_parse_model_obj(provider, m))
		except Exception:
			log.debug("Ignored error", exc_info=True)
			continue

	models.sort(key=lambda m: m.created, reverse=True)
	_models[cache_key] = models
	return models
