"""Reusable transcription helpers: provider resolution, text extraction."""

import addonHandler
from . import apikeymanager
from .consts import (
	BASE_URLs,
	Provider,
	TranscriptionProvider,
	TRANSCRIPTION_PROVIDERS,
)

addonHandler.initTranslation()


def get_transcription_provider(conf: dict) -> str:
	"""Resolve transcription provider from config. Backward compat with whisper.cpp.enabled.

	Returns the ``str`` value of a ``TranscriptionProvider`` member.
	"""
	if conf.get("whisper.cpp", {}).get("enabled", False):
		return TranscriptionProvider.WHISPER_CPP.value
	val = conf.get("transcriptionProvider", TranscriptionProvider.OPENAI.value)
	if isinstance(val, str):
		val_norm = val.lower().replace("-", "_")
		if val_norm in (TranscriptionProvider.CUSTOM_OPENAI.value, "customopenai", "custom_openai"):
			return TranscriptionProvider.CUSTOM_OPENAI.value
		if val_norm in (TranscriptionProvider.MISTRAL.value, "mistralai"):
			return TranscriptionProvider.MISTRAL.value
		if val_norm == TranscriptionProvider.OPENAI.value:
			return TranscriptionProvider.OPENAI.value
		if val_norm in (TranscriptionProvider.WHISPER_CPP.value, "whisper_cpp", "whispercpp"):
			return TranscriptionProvider.WHISPER_CPP.value
	return val


def list_transcription_providers() -> tuple[str, ...]:
	"""Return the list of all supported transcription provider identifiers."""
	return TRANSCRIPTION_PROVIDERS


def get_transcription_provider_choices() -> list[tuple[str, str]]:
	"""Return (provider_id, display_label) tuples for UI controls."""
	return [
		# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — local whisper.cpp option.
		(TranscriptionProvider.WHISPER_CPP.value, _("whisper.cpp (local)")),
		# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — OpenAI Whisper option.
		(TranscriptionProvider.OPENAI.value, _("OpenAI Whisper")),
		# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — Mistral Voxtral option.
		(TranscriptionProvider.MISTRAL.value, _("Mistral Voxtral")),
		# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — Custom OpenAI option.
		(TranscriptionProvider.CUSTOM_OPENAI.value, _("Custom OpenAI")),
	]


def resolve_transcription_endpoint(conf: dict, provider: str | None = None) -> dict:
	"""Resolve endpoint parameters (provider, account_id, base_url, api_key, model) for transcription.

	Accepts either the full AIHub config dict or the 'audio' section dict.
	"""
	audio_conf = conf.get("audio", conf) if isinstance(conf, dict) else {}
	resolved_provider = provider or get_transcription_provider(audio_conf)

	if resolved_provider == TranscriptionProvider.WHISPER_CPP.value:
		whisper_host = audio_conf.get("whisper.cpp", {}).get("host", "http://127.0.0.1:8081")
		return {
			"provider": TranscriptionProvider.WHISPER_CPP.value,
			"api_provider": None,
			"account_id": None,
			"base_url": whisper_host,
			"api_key": None,
			"model": None,
		}

	if resolved_provider == TranscriptionProvider.MISTRAL.value:
		manager = apikeymanager.get(Provider.MistralAI)
		account_id = audio_conf.get("mistralTranscriptionAccountId", "").strip()
		if account_id and not manager.isReady(account_id=account_id):
			account_id = ""
		if not account_id:
			account_id = manager.get_active_account_id() or ""
		api_key = manager.get_api_key(account_id=account_id) if account_id else manager.get_api_key()
		base_url = BASE_URLs.get(Provider.MistralAI, "https://api.mistral.ai/v1")
		model = audio_conf.get("voxtralModel", "voxtral-mini-latest")
		return {
			"provider": TranscriptionProvider.MISTRAL.value,
			"api_provider": Provider.MistralAI,
			"account_id": account_id,
			"base_url": base_url,
			"api_key": api_key,
			"model": model,
		}

	if resolved_provider == TranscriptionProvider.CUSTOM_OPENAI.value:
		manager = apikeymanager.get(Provider.CustomOpenAI)
		account_id = audio_conf.get("customOpenaiTranscriptionAccountId", "").strip()
		if account_id and not manager.isReady(account_id=account_id):
			account_id = ""
		if not account_id:
			account_id = manager.get_active_account_id() or ""
		base_url = manager.get_base_url(account_id=account_id) if account_id else manager.get_base_url()
		api_key = manager.get_api_key(account_id=account_id) if account_id else manager.get_api_key()
		model = audio_conf.get("customOpenaiTranscriptionModel", "").strip() or audio_conf.get("whisperModel", "whisper-1")
		return {
			"provider": TranscriptionProvider.CUSTOM_OPENAI.value,
			"api_provider": Provider.CustomOpenAI,
			"account_id": account_id,
			"base_url": base_url,
			"api_key": api_key,
			"model": model,
		}

	# Default: OpenAI
	manager = apikeymanager.get(Provider.OpenAI)
	account_id = audio_conf.get("openaiTranscriptionAccountId", "").strip()
	if account_id and not manager.isReady(account_id=account_id):
		account_id = ""
	if not account_id:
		account_id = manager.get_active_account_id() or ""
	base_url = manager.get_base_url(account_id=account_id) if account_id else manager.get_base_url()
	base_url = base_url or BASE_URLs.get(Provider.OpenAI, "https://api.openai.com/v1")
	api_key = manager.get_api_key(account_id=account_id) if account_id else manager.get_api_key()
	model = audio_conf.get("whisperModel", "whisper-1")
	return {
		"provider": TranscriptionProvider.OPENAI.value,
		"api_provider": Provider.OpenAI,
		"account_id": account_id,
		"base_url": base_url,
		"api_key": api_key,
		"model": model,
	}


def get_transcription_text(transcription) -> str:
	"""Extract text from Transcription, WhisperTranscription, or str. Returns empty string if None/invalid."""
	if transcription is None:
		return ""
	if isinstance(transcription, str):
		return transcription
	return getattr(transcription, "text", None) or ""
