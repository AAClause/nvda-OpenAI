"""HTTP client and provider dispatch.

The ``OpenAIClient`` here mimics just enough of the openai-python ``OpenAI``
class for this addon to work without bundling the SDK. Provider-specific
quirks (Anthropic Messages API, OpenAI Responses, Voxtral TTS, etc.) are
handled by dispatching to focused helpers in the sibling modules.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, BinaryIO, Generator, Optional

from .. import apikeymanager
from ..anthropicthinking import get_anthropic_thinking_profile, normalize_effort, anthropic_reasoning_always_on
from ..consts import BASE_URLs, Provider

from ._content import (
	_convert_messages_to_anthropic,
	_extract_audio_bytes_from_json_payload,
	_has_input_file_parts,
	_messages_to_responses_input,
	_normalize_input_files_for_provider,
)
from ._errors import APIConnectionError, APIError, APIStatusError, _resolve_error_message
from ._http import (
	_AUDIO_MIME,
	_build_anthropic_headers,
	_build_headers,
	_create_opener,
	_open_bytes,
	_open_json,
	_open_streaming,
)
from ._google import create_gemini_generate_content_request
from ._parsers import parse_anthropic, parse_chat_completion, parse_gemini_generate_content, parse_responses
from ._streams import stream_anthropic, stream_chat_completions, stream_gemini_generate_content, stream_responses
from ._types import ChatCompletion, Transcription


class OpenAIClient:
	"""HTTP-based client for OpenAI-compatible APIs (and Anthropic).

	Replaces ``openai.OpenAI`` without depending on the openai SDK so the
	addon stays lightweight and ships only standard library code.
	"""

	def __init__(
		self,
		api_key: str,
		base_url: str = "https://api.openai.com/v1",
		organization: Optional[str] = None,
	):
		self.api_key = api_key
		self.base_url = base_url.rstrip("/")
		self.organization = organization
		self.provider: str = Provider.OpenAI
		self.account_id: Optional[str] = None
		self._opener = _create_opener()
		self.chat = _ChatCompletions(self)
		self.audio = _Audio(self)

	def clone(self) -> "OpenAIClient":
		"""Return a detached client copy for thread-safe per-request mutations."""
		other = OpenAIClient(
			api_key=self.api_key,
			base_url=self.base_url,
			organization=self.organization,
		)
		other.provider = getattr(self, "provider", Provider.OpenAI)
		other.account_id = getattr(self, "account_id", None)
		return other

	# ------------------------------------------------------------------
	# Chat completion dispatch.
	# ------------------------------------------------------------------

	def chat_completions_create(
		self,
		*,
		model: str,
		messages: list,
		stream: bool = False,
		**kwargs,
	) -> ChatCompletion | Generator:
		"""Create a chat completion. Returns ``ChatCompletion`` or a stream generator."""
		provider = getattr(self, "provider", Provider.OpenAI)
		has_input_files = _has_input_file_parts(messages)
		if provider == Provider.Anthropic:
			return self._anthropic_chat_completions_create(
				model=model, messages=messages, stream=stream, **kwargs
			)
		if provider == Provider.OpenAI and has_input_files:
			return self._responses_create(
				model=model, messages=messages, stream=stream, **kwargs
			)
		if provider == Provider.xAI:
			return self._responses_create(
				model=model, messages=messages, stream=stream, **kwargs
			)
		if provider == Provider.Google:
			return self._google_generate_content_create(
				model=model, messages=messages, stream=stream, **kwargs
			)
		if has_input_files:
			# Each provider expects a different on-the-wire shape for documents
			# on its chat-completions endpoint; the helper rewrites the
			# internal ``input_file`` parts in place (or raises a clear error
			# for binary docs on providers that don't support them).
			messages = _normalize_input_files_for_provider(messages, provider)
		body = self._build_chat_body(model, messages, stream, provider, kwargs)
		req = self._json_request("/chat/completions", body)
		if stream:
			resp = _open_streaming(self._opener, req, timeout=120)
			return stream_chat_completions(resp)
		data = _open_json(self._opener, req, timeout=120)
		return parse_chat_completion(data, provider=provider)

	def _build_chat_body(
		self,
		model: str,
		messages: list,
		stream: bool,
		provider: str,
		kwargs: dict,
	) -> dict:
		"""Build the JSON body for a Chat Completions request."""
		body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
		# Internal pseudo-params that must never appear on the wire.
		excluded = {
			"reasoning_enabled",
			"reasoning_disabled",
			"adaptive_thinking",
			"reasoning_effort",
			"extra_body",
			"think",
		}
		for k, v in kwargs.items():
			if k in excluded or v is None:
				continue
			body[k] = v
		# Reasoning effort uses different shapes depending on the provider.
		reasoning_effort = kwargs.get("reasoning_effort")
		if reasoning_effort is not None:
			if provider == Provider.OpenRouter:
				# Caller may have already supplied a full ``reasoning`` dict (e.g.
				# {effort: "none", exclude: true}); only convert reasoning_effort when absent.
				if "reasoning" not in body:
					body["reasoning"] = {"effort": reasoning_effort}
			else:
				body["reasoning_effort"] = reasoning_effort
		# OpenAI SDK's ``extra_body`` semantics: merge keys into the request root.
		extra_body = kwargs.get("extra_body")
		if isinstance(extra_body, dict):
			for k, v in extra_body.items():
				if v is not None:
					body[k] = v
		return body

	def _google_generate_content_create(
		self,
		*,
		model: str,
		messages: list,
		stream: bool = False,
		**kwargs,
	) -> ChatCompletion | Generator:
		"""Google chat via native ``generateContent`` (not OpenAI-compat)."""
		url, headers, body = create_gemini_generate_content_request(
			self.api_key, model, messages, stream, kwargs
		)
		data = json.dumps(body).encode("utf-8")
		req = urllib.request.Request(url, data=data, headers=headers, method="POST")
		if stream:
			resp = _open_streaming(self._opener, req, timeout=180)
			return stream_gemini_generate_content(resp)
		payload = _open_json(self._opener, req, timeout=180)
		return parse_gemini_generate_content(payload)

	def _json_request(
		self,
		path: str,
		body: Optional[dict],
		extra_headers: Optional[dict] = None,
	) -> urllib.request.Request:
		"""Build a JSON POST against ``self.base_url + path``."""
		url = f"{self.base_url}{path}"
		headers = _build_headers(self.api_key, self.organization)
		if extra_headers:
			headers.update(extra_headers)
		data = json.dumps(body).encode("utf-8") if body is not None else None
		return urllib.request.Request(url, data=data, headers=headers, method="POST")

	# ------------------------------------------------------------------
	# OpenAI Responses API path (used when input contains files).
	# ------------------------------------------------------------------

	def _responses_create(
		self,
		*,
		model: str,
		messages: list,
		stream: bool = False,
		**kwargs,
	) -> ChatCompletion | Generator:
		"""Make a request against ``/v1/responses`` (OpenAI file input, xAI built-in tools)."""
		provider = getattr(self, "provider", Provider.OpenAI)
		upload_file = None
		if provider == Provider.OpenAI:
			upload_file = lambda p: self._upload_openai_user_file(p, purpose="user_data")
		elif provider == Provider.xAI:
			upload_file = self._upload_xai_user_file
		input_payload = _messages_to_responses_input(
			messages,
			upload_file=upload_file,
		)
		if not input_payload:
			raise APIError("No valid messages for Responses API request.")
		body = self._build_responses_body(model, input_payload, stream, kwargs, provider=provider)
		req = self._json_request("/responses", body)
		if stream:
			resp = _open_streaming(self._opener, req, timeout=180)
			return stream_responses(resp)
		data = _open_json(self._opener, req, timeout=180)
		return parse_responses(data, provider=provider)

	def _openai_responses_create(
		self,
		*,
		model: str,
		messages: list,
		stream: bool = False,
		**kwargs,
	) -> ChatCompletion | Generator:
		"""Backward-compatible alias for OpenAI Responses routing."""
		return self._responses_create(
			model=model, messages=messages, stream=stream, **kwargs
		)

	def _build_responses_body(
		self,
		model: str,
		input_payload: list,
		stream: bool,
		kwargs: dict,
		provider: str = "",
	) -> dict:
		body: dict[str, Any] = {"model": model, "input": input_payload, "stream": stream}
		skip_keys = {
			"messages",
			"stream",
			"stream_options",
			"web_search_options",
			"reasoning_enabled",
			"reasoning_disabled",
			"adaptive_thinking",
			"extra_body",
			"think",
		}
		# xAI reasoning models reject these on the Responses API.
		if provider == Provider.xAI:
			skip_keys |= {"stop", "frequency_penalty", "presence_penalty"}
		for key, value in kwargs.items():
			if value is None or key in skip_keys:
				continue
			if key in ("max_tokens", "max_completion_tokens"):
				body["max_output_tokens"] = value
				continue
			if key == "reasoning_effort":
				body["reasoning"] = {"effort": value}
				continue
			body[key] = value
		return body

	def _upload_openai_user_file(self, file_path: str, purpose: str = "user_data") -> str:
		"""Upload a local file to OpenAI ``/v1/files`` and return its file id."""
		if not isinstance(file_path, str) or not file_path or not os.path.exists(file_path):
			raise APIError(f"Invalid file path: {file_path}")
		boundary, body, content_type = _build_file_upload_body(file_path, purpose)
		headers = _build_headers(self.api_key, self.organization)
		headers["Content-Type"] = content_type
		req = urllib.request.Request(
			f"{self.base_url}/files", data=body, headers=headers, method="POST",
		)
		try:
			with self._opener.open(req, timeout=180) as resp:
				raw = resp.read().decode("utf-8", errors="replace")
				if resp.status != 200:
					raise APIStatusError(
						_resolve_error_message(raw, resp.status),
						status_code=resp.status,
						response_body=raw,
					)
				payload = json.loads(raw) if raw else {}
				file_id = payload.get("id") if isinstance(payload, dict) else ""
				if not isinstance(file_id, str) or not file_id.strip():
					raise APIError("File upload succeeded but no file id was returned.")
				return file_id.strip()
		except urllib.error.HTTPError as e:
			text = e.read().decode("utf-8", errors="replace") if e.fp else ""
			raise APIStatusError(
				_resolve_error_message(text, e.code),
				status_code=e.code,
				response_body=text,
			)
		except (urllib.error.URLError, OSError, ConnectionError) as e:
			raise APIConnectionError(str(e)) from e

	def _upload_xai_user_file(self, file_path: str) -> str:
		"""Upload a local file to xAI ``/v1/files`` for Responses ``input_file`` parts."""
		return self._upload_openai_user_file(file_path, purpose="assistants")

	# ------------------------------------------------------------------
	# Anthropic Messages API path.
	# ------------------------------------------------------------------

	def _anthropic_chat_completions_create(
		self,
		*,
		model: str,
		messages: list,
		stream: bool = False,
		**kwargs,
	) -> ChatCompletion | Generator:
		"""Create a chat completion via the Anthropic Messages API."""
		system, anthropic_msgs = _convert_messages_to_anthropic(messages)
		if not anthropic_msgs:
			raise APIError("No valid messages for Anthropic")
		body = self._build_anthropic_body(model, anthropic_msgs, stream, system, kwargs)
		url = f"{self.base_url}/messages"
		headers = _build_anthropic_headers(self.api_key)
		data = json.dumps(body).encode("utf-8")
		req = urllib.request.Request(url, data=data, headers=headers, method="POST")
		if stream:
			resp = _open_streaming(self._opener, req, timeout=120)
			return stream_anthropic(resp)
		data_dict = _open_json(self._opener, req, timeout=120)
		return parse_anthropic(data_dict)

	def _build_anthropic_body(
		self,
		model: str,
		anthropic_msgs: list,
		stream: bool,
		system: Optional[str],
		kwargs: dict,
	) -> dict:
		body: dict[str, Any] = {
			"model": model,
			"max_tokens": kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 4096,
			"messages": anthropic_msgs,
			"stream": stream,
		}
		if system:
			body["system"] = system
		# Anthropic API: temperature and top_p are mutually exclusive.
		temp = kwargs.get("temperature")
		top_p = kwargs.get("top_p")
		if temp is not None:
			body["temperature"] = temp
		elif top_p is not None:
			body["top_p"] = top_p
		if kwargs.get("top_k") is not None:
			body["top_k"] = kwargs["top_k"]
		stop_seq = _normalize_stop_sequences(kwargs.get("stop"))
		if stop_seq:
			body["stop_sequences"] = stop_seq
		_apply_anthropic_thinking(body, model, kwargs)
		# Web search tool — web_search_20250305 is GA on all supported models.
		if kwargs.get("web_search_options") is not None:
			body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
		return body

	# ------------------------------------------------------------------
	# Audio API.
	# ------------------------------------------------------------------

	def audio_transcriptions_create(
		self,
		*,
		model: str = "whisper-1",
		file: BinaryIO,
		response_format: str = "json",
		**kwargs,
	) -> Transcription:
		return self._audio_text_create(
			endpoint="/audio/transcriptions",
			model=model,
			file=file,
			response_format=response_format,
			**kwargs,
		)

	def audio_translations_create(
		self,
		*,
		model: str = "whisper-1",
		file: BinaryIO,
		response_format: str = "json",
		**kwargs,
	) -> Transcription:
		return self._audio_text_create(
			endpoint="/audio/translations",
			model=model,
			file=file,
			response_format=response_format,
			**kwargs,
		)

	def _audio_text_create(
		self,
		*,
		endpoint: str,
		model: str,
		file: BinaryIO,
		response_format: str,
		**kwargs,
	) -> Transcription:
		"""Shared multipart handler for /audio/transcriptions and /audio/translations."""
		body, content_type = _build_audio_text_body(file, model, response_format, kwargs)
		headers = _build_headers(self.api_key, self.organization)
		headers["Content-Type"] = content_type
		url = f"{self.base_url}{endpoint}"
		req = urllib.request.Request(url, data=body, headers=headers, method="POST")
		raw, _ct = _open_bytes(self._opener, req, timeout=120)
		text = raw.decode("utf-8", errors="replace")
		if response_format in ("json", "verbose_json", "diarized_json"):
			result = json.loads(text) if text else {}
			content = result.get("text", "") if isinstance(result, dict) else str(result)
			return Transcription(content, payload=result, response_format=response_format)
		return Transcription(text, payload=text, response_format=response_format)

	def audio_speech_create(
		self,
		*,
		model: str,
		voice: str = "",
		input: str,
		response_format: str = "mp3",
		**kwargs,
	) -> bytes:
		"""Create speech from text. Returns raw audio bytes."""
		body: dict[str, Any] = {
			"model": model,
			"input": input,
			"response_format": response_format,
		}
		if voice:
			body["voice"] = voice
		for k, v in kwargs.items():
			if v is None:
				continue
			body[k] = v
		req = self._json_request("/audio/speech", body)
		raw, content_type = _open_bytes(self._opener, req, timeout=60)
		# Some providers (Voxtral) return JSON with base64 audio_data instead of binary.
		if "application/json" in content_type or "text/json" in content_type:
			try:
				payload = json.loads(raw.decode("utf-8", errors="replace"))
			except json.JSONDecodeError:
				return raw
			audio = _extract_audio_bytes_from_json_payload(payload)
			if audio:
				return audio
			raise APIStatusError(
				"No audio payload found in JSON TTS response.",
				status_code=200,
				response_body=str(payload),
			)
		return raw


# ---------------------------------------------------------------------------
# Standalone helper: Mistral Voxtral transcription (uses x-api-key, not Bearer).
# ---------------------------------------------------------------------------

def transcribe_audio_mistral(
	api_key: str,
	file_path: str,
	model: str = "voxtral-mini-latest",
	language: Optional[str] = None,
) -> Transcription:
	"""Transcribe audio via Mistral Voxtral.

	Mistral's transcription endpoint uses ``x-api-key`` auth (not Bearer) and
	multipart form data per the official documentation, so it cannot reuse the
	OpenAI-compatible client unchanged.
	"""
	if not api_key or not api_key.strip():
		raise ValueError("Mistral API key is required for transcription")
	url = "https://api.mistral.ai/v1/audio/transcriptions"
	boundary = uuid.uuid4().hex
	with open(file_path, "rb") as f:
		file_data = f.read()
	ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ".wav"
	filename = os.path.basename(file_path) or "audio.wav"
	mime = _AUDIO_MIME.get(ext, "audio/wav")
	parts = [
		(
			f'--{boundary}\r\n'
			f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
			f'Content-Type: {mime}\r\n\r\n'
		).encode("utf-8") + file_data,
		(
			f'\r\n--{boundary}\r\n'
			f'Content-Disposition: form-data; name="model"\r\n\r\n'
			f'{model}\r\n'
		).encode("utf-8"),
	]
	if language:
		parts.append(
			(
				f'\r\n--{boundary}\r\n'
				f'Content-Disposition: form-data; name="language"\r\n\r\n'
				f'{language}\r\n'
			).encode("utf-8")
		)
	parts.append(f'\r\n--{boundary}--\r\n'.encode("utf-8"))
	body = b"".join(parts)
	headers = {
		"x-api-key": api_key.strip(),
		"Content-Type": f"multipart/form-data; boundary={boundary}",
	}
	req = urllib.request.Request(url, data=body, headers=headers, method="POST")
	opener = _create_opener()
	data = _open_json(opener, req, timeout=300)
	return Transcription(data.get("text", ""))


# ---------------------------------------------------------------------------
# Provider configuration helper (called by every dialog before a request).
# ---------------------------------------------------------------------------

def configure_client_for_provider(
	client: OpenAIClient,
	provider,
	account_id: Optional[str] = None,
	clone: bool = False,
) -> OpenAIClient:
	"""Configure a client's base_url, api_key, and organization for the given provider.

	``provider`` accepts a ``Provider`` enum member or its string value.
	"""
	if clone and hasattr(client, "clone"):
		client = client.clone()
	manager = apikeymanager.get(provider)
	client.base_url = manager.get_base_url(account_id=account_id) or BASE_URLs[provider]
	api_key = manager.get_api_key(account_id=account_id)
	# Ollama doesn't require an API key; supply a placeholder so the Bearer header is set
	# (some Ollama-compatible reverse proxies reject empty Authorization headers).
	if provider == Provider.Ollama and not (api_key and str(api_key).strip()):
		api_key = "ollama"
	client.api_key = api_key
	client.organization = manager.get_organization_key(account_id=account_id)
	client.provider = provider
	client.account_id = account_id
	return client


# ---------------------------------------------------------------------------
# Multipart body builders (kept as module-level helpers to keep the client class slim).
# ---------------------------------------------------------------------------

def _build_file_upload_body(file_path: str, purpose: str) -> tuple[str, bytes, str]:
	import mimetypes
	boundary = uuid.uuid4().hex
	filename = os.path.basename(file_path)
	for ch in '\r\n"\\':
		filename = filename.replace(ch, "_")
	file_mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
	with open(file_path, "rb") as f:
		file_data = f.read()
	body = (
		f'--{boundary}\r\n'
		f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
		f'{purpose}\r\n'
		f'--{boundary}\r\n'
		f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
		f'Content-Type: {file_mime}\r\n\r\n'
	).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
	content_type = f"multipart/form-data; boundary={boundary}"
	return boundary, body, content_type


def _build_audio_text_body(
	file: BinaryIO,
	model: str,
	response_format: str,
	extra_fields: dict,
) -> tuple[bytes, str]:
	"""Build the multipart body for /audio/transcriptions or /audio/translations."""
	boundary = uuid.uuid4().hex
	file_data = file.read()
	file_name = os.path.basename(getattr(file, "name", "") or "audio.wav")
	ext = os.path.splitext(file_name)[1].lower() or ".wav"
	file_mime = _AUDIO_MIME.get(ext, "audio/wav")
	parts: list[bytes] = []

	def _add_field(name: str, value: Any) -> None:
		if value is None:
			return
		if isinstance(value, (list, tuple)):
			for item in value:
				_add_field(name, item)
			return
		if isinstance(value, dict):
			value = json.dumps(value, ensure_ascii=False)
		parts.append(
			(
				f'--{boundary}\r\n'
				f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
				f'{value}\r\n'
			).encode("utf-8")
		)

	_add_field("model", model)
	_add_field("response_format", response_format)
	for k, v in extra_fields.items():
		_add_field(k, v)
	file_part = (
		f'--{boundary}\r\n'
		f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
		f'Content-Type: {file_mime}\r\n\r\n'
	).encode("utf-8") + file_data + b"\r\n"
	body = b"".join(parts) + file_part + f"--{boundary}--\r\n".encode("utf-8")
	content_type = f"multipart/form-data; boundary={boundary}"
	return body, content_type


# ---------------------------------------------------------------------------
# Anthropic body helpers.
# ---------------------------------------------------------------------------

def _normalize_stop_sequences(stop_kw: Any) -> list[str]:
	"""Convert the OpenAI-style ``stop`` parameter to Anthropic's stop_sequences (max 16)."""
	if stop_kw is None:
		return []
	if isinstance(stop_kw, str) and stop_kw.strip():
		return [stop_kw.strip()][:16]
	if isinstance(stop_kw, list):
		return [s.strip() for s in stop_kw if isinstance(s, str) and s.strip()][:16]
	return []


def _apply_anthropic_thinking(body: dict, model: str, kwargs: dict) -> None:
	"""Mutate ``body`` to enable Anthropic extended thinking when requested.

	See https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
	Opus 4.7+ and Fable/Mythos reject ``thinking.type: enabled``; those models
	must use ``thinking.type: adaptive`` with ``output_config.effort``.
	"""
	if kwargs.get("reasoning_disabled"):
		# Omitting ``thinking`` also disables extended thinking; explicit disabled
		# matches the Messages API docs for models that accept it.
		if not anthropic_reasoning_always_on(model):
			body["thinking"] = {"type": "disabled"}
		return
	if not kwargs.get("reasoning_enabled"):
		return
	caps = get_anthropic_thinking_profile(model)
	use_adaptive = bool(
		caps.get("adaptive_only")
		or (caps.get("adaptive_supported") and kwargs.get("adaptive_thinking", True))
	)
	if use_adaptive:
		body["thinking"] = {"type": "adaptive"}
	else:
		body["thinking"] = {"type": "enabled", "budget_tokens": 10000}
	# Opus 4.7+/Fable/Mythos default to omitted thinking text; request summarized
	# output so history can display <think>...</think>.
	body["thinking"]["display"] = "summarized"
	if caps.get("effort_supported"):
		effort = normalize_effort(
			kwargs.get("reasoning_effort", "high"),
			tuple(caps.get("effort_levels") or ()),
			default="high",
		)
		body["output_config"] = {"effort": effort}


# ---------------------------------------------------------------------------
# Compatibility shims mimicking the openai-python client.X.Y.create() chain.
# ---------------------------------------------------------------------------

class _ChatCompletions:
	"""Mimics ``client.chat`` / ``client.chat.completions``."""

	def __init__(self, client: OpenAIClient):
		self._client = client
		self.completions = self  # so ``client.chat.completions.create(...)`` works.

	def create(self, **kwargs):
		return self._client.chat_completions_create(**kwargs)


class _AudioTranscriptions:
	def __init__(self, client: OpenAIClient):
		self._client = client

	def create(self, **kwargs):
		return self._client.audio_transcriptions_create(**kwargs)


class _AudioTranslations:
	def __init__(self, client: OpenAIClient):
		self._client = client

	def create(self, **kwargs):
		return self._client.audio_translations_create(**kwargs)


class _TTSResponse:
	"""Wrapper exposing ``stream_to_file`` to match the openai-python TTS shape."""

	def __init__(self, data: bytes):
		self._data = data

	def stream_to_file(self, path: str) -> None:
		with open(path, "wb") as f:
			f.write(self._data)


class _AudioSpeech:
	def __init__(self, client: OpenAIClient):
		self._client = client

	def create(self, **kwargs) -> _TTSResponse:
		return _TTSResponse(self._client.audio_speech_create(**kwargs))


class _Audio:
	def __init__(self, client: OpenAIClient):
		self.transcriptions = _AudioTranscriptions(client)
		self.translations = _AudioTranslations(client)
		self.speech = _AudioSpeech(client)
		self._client = client
