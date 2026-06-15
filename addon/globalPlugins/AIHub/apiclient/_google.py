"""Native Gemini ``generateContent`` API helpers.

All Google chat completions use the native Gemini API (not the OpenAI-compatible
``/v1beta/openai/chat/completions`` endpoint), which supports inline PDFs,
Google Search grounding, thinking config, and audio I/O.

Official references:
- https://ai.google.dev/gemini-api/docs/google-search
- https://discuss.ai.google.dev/t/does-openai-api-support-google-search-grounding/107542
"""
from __future__ import annotations

import base64
import urllib.parse
from typing import Any, Optional

from ..consts import ContentType, Role
from ._content import _decode_data_url_to_bytes, _input_file_to_data_url, _is_text_media_type
from ._errors import APIError
from ._http import _USER_AGENT

GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

_AUDIO_FORMAT_MIME = {
	"wav": "audio/wav",
	"mp3": "audio/mpeg",
	"mpeg": "audio/mpeg",
	"flac": "audio/flac",
	"ogg": "audio/ogg",
	"webm": "audio/webm",
}

# OpenAI TTS voice names map to Gemini prebuilt voices when audio output is requested.
_OPENAI_TO_GEMINI_VOICE = {
	"nova": "Kore",
	"shimmer": "Aoede",
	"echo": "Charon",
	"fable": "Fenrir",
	"onyx": "Orus",
	"alloy": "Puck",
}


def build_google_native_headers(api_key: str) -> dict[str, str]:
	return {
		"Content-Type": "application/json",
		"x-goog-api-key": api_key,
		"User-Agent": _USER_AGENT,
	}


def google_grounding_tool_for_model(model_id: str) -> dict[str, Any]:
	"""Return the native Gemini Google Search tool descriptor for *model_id*."""
	mid = (model_id or "").lower()
	if "gemini-1" in mid or mid.startswith("gemini-pro") or "ultra" in mid:
		return {"google_search_retrieval": {}}
	return {"google_search": {}}


def _gemini_text_part(text: str) -> dict[str, str]:
	return {"text": text}


def _gemini_inline_data_part(mime_type: str, data_b64: str) -> dict[str, Any]:
	return {"inline_data": {"mime_type": mime_type, "data": data_b64}}


def _gemini_part_from_image_url(part: dict) -> Optional[dict]:
	img = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
	url = (img.get("url") or "").strip()
	if not url:
		return None
	raw, mime = _decode_data_url_to_bytes(url)
	if raw is None:
		return None
	data_b64 = base64.b64encode(raw).decode("ascii")
	if not mime or not mime.startswith("image/"):
		mime = "image/jpeg"
	return _gemini_inline_data_part(mime, data_b64)


def _gemini_part_from_input_audio(part: dict) -> Optional[dict]:
	audio = part.get("input_audio") if isinstance(part.get("input_audio"), dict) else {}
	data_b64 = audio.get("data")
	if not data_b64:
		return None
	fmt = (audio.get("format") or "wav").lower().strip()
	mime = _AUDIO_FORMAT_MIME.get(fmt, "audio/wav")
	return _gemini_inline_data_part(mime, data_b64)


def _gemini_part_from_input_file(part: dict) -> Optional[dict]:
	data_url, filename, mime = _input_file_to_data_url(part)
	if data_url:
		raw, resolved_mime = _decode_data_url_to_bytes(data_url)
		mime = resolved_mime or mime
		if raw is None:
			return None
		if _is_text_media_type(mime):
			try:
				text = raw.decode("utf-8", errors="replace")
			except Exception:
				return None
			label = filename or "attached document"
			return _gemini_text_part(f"[{label}]\n{text}")
		return _gemini_inline_data_part(mime, base64.b64encode(raw).decode("ascii"))
	file_url = part.get("file_url")
	if isinstance(file_url, str) and file_url.strip().startswith("http"):
		# Native generateContent expects inline bytes; remote URLs are out of scope here.
		raise APIError(
			f"Gemini generateContent does not support remote file URLs ('{filename or file_url}'). "
			"Attach a local file instead."
		)
	return None


def _openai_content_to_gemini_parts(content: Any) -> list[dict]:
	if isinstance(content, str):
		return [_gemini_text_part(content)] if content else []
	if not isinstance(content, list):
		text = str(content) if content else ""
		return [_gemini_text_part(text)] if text else []
	parts: list[dict] = []
	for part in content:
		if not isinstance(part, dict):
			continue
		typ = part.get("type", "")
		if typ == ContentType.TEXT:
			text = part.get("text", "")
			if text:
				parts.append(_gemini_text_part(text))
		elif typ == ContentType.IMAGE_URL:
			gp = _gemini_part_from_image_url(part)
			if gp:
				parts.append(gp)
		elif typ == ContentType.INPUT_FILE:
			gp = _gemini_part_from_input_file(part)
			if gp:
				parts.append(gp)
		elif typ == ContentType.INPUT_AUDIO:
			gp = _gemini_part_from_input_audio(part)
			if gp:
				parts.append(gp)
	return parts


def messages_to_gemini_contents(messages: list) -> tuple[Optional[dict], list[dict]]:
	"""Convert OpenAI-style messages to Gemini ``systemInstruction`` + ``contents``."""
	system_chunks: list[str] = []
	contents: list[dict] = []
	if not isinstance(messages, list):
		return None, contents
	for msg in messages:
		if not isinstance(msg, dict):
			continue
		role = str(msg.get("role") or Role.USER).lower()
		parts = _openai_content_to_gemini_parts(msg.get("content"))
		if not parts:
			continue
		if role in (Role.SYSTEM, Role.DEVELOPER):
			for p in parts:
				text = p.get("text")
				if text:
					system_chunks.append(text)
			continue
		gemini_role = "model" if role == Role.ASSISTANT else "user"
		if contents and contents[-1].get("role") == gemini_role:
			contents[-1]["parts"].extend(parts)
		else:
			contents.append({"role": gemini_role, "parts": parts})
	system_instruction = None
	if system_chunks:
		system_instruction = {"parts": [_gemini_text_part("\n\n".join(system_chunks))]}
	return system_instruction, contents


def _gemini_voice_name(audio_cfg: dict) -> str:
	voice = (audio_cfg.get("voice") or "").strip()
	if voice in _OPENAI_TO_GEMINI_VOICE:
		return _OPENAI_TO_GEMINI_VOICE[voice]
	return voice or "Kore"


def _apply_gemini_audio_output(gen: dict[str, Any], kwargs: dict) -> None:
	modalities = kwargs.get("modalities") or []
	if not isinstance(modalities, list):
		modalities = []
	audio_cfg = kwargs.get("audio") if isinstance(kwargs.get("audio"), dict) else {}
	if "audio" not in modalities and not audio_cfg:
		return
	gen["responseModalities"] = ["AUDIO"]
	gen["speechConfig"] = {
		"voiceConfig": {
			"prebuiltVoiceConfig": {"voiceName": _gemini_voice_name(audio_cfg)},
		},
	}


def _apply_gemini_generation_config(body: dict[str, Any], model_id: str, kwargs: dict) -> None:
	gen: dict[str, Any] = {}
	max_out = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
	if max_out is not None:
		try:
			val = int(max_out)
			if val > 0:
				gen["maxOutputTokens"] = val
		except (TypeError, ValueError):
			pass
	if kwargs.get("temperature") is not None:
		gen["temperature"] = kwargs["temperature"]
	if kwargs.get("top_p") is not None:
		gen["topP"] = kwargs["top_p"]
	if kwargs.get("top_k") is not None:
		gen["topK"] = kwargs["top_k"]
	if kwargs.get("seed") is not None:
		try:
			gen["seed"] = int(kwargs["seed"])
		except (TypeError, ValueError):
			pass
	stop = kwargs.get("stop")
	if isinstance(stop, str) and stop.strip():
		gen["stopSequences"] = [stop.strip()]
	elif isinstance(stop, list):
		seq = [s.strip() for s in stop if isinstance(s, str) and s.strip()]
		if seq:
			gen["stopSequences"] = seq[:16]
	reasoning_effort = kwargs.get("reasoning_effort")
	if reasoning_effort:
		mid = (model_id or "").lower()
		if reasoning_effort == "none":
			if "gemini-2.5" in mid and "pro" not in mid:
				gen["thinkingConfig"] = {"thinkingBudget": 0}
		elif "gemini-3" in mid:
			# Gemini 3.x uses thinkingLevel (minimal/low/medium/high). The Pro
			# variants don't support "minimal", so clamp it up to "low" there.
			level = reasoning_effort
			if level == "minimal" and "pro" in mid:
				level = "low"
			gen["thinkingConfig"] = {"thinkingLevel": level, "includeThoughts": True}
		elif "gemini-2.5" in mid:
			budget_map = {"low": 1024, "medium": 8192, "high": 24576}
			gen["thinkingConfig"] = {
				"thinkingBudget": budget_map.get(reasoning_effort, 8192),
				"includeThoughts": True,
			}
	_apply_gemini_audio_output(gen, kwargs)
	if gen:
		body["generationConfig"] = gen


def build_gemini_generate_content_body(model_id: str, messages: list, kwargs: dict) -> dict[str, Any]:
	"""Build a native ``generateContent`` JSON body."""
	system_instruction, contents = messages_to_gemini_contents(messages)
	if not contents:
		raise APIError("No valid messages for Gemini generateContent request.")
	body: dict[str, Any] = {"contents": contents}
	if system_instruction:
		body["systemInstruction"] = system_instruction
	if kwargs.get("web_search_options") is not None:
		body["tools"] = [google_grounding_tool_for_model(model_id)]
	_apply_gemini_generation_config(body, model_id, kwargs)
	return body


def gemini_model_url(model_id: str, *, stream: bool) -> str:
	encoded = urllib.parse.quote(model_id, safe="")
	action = "streamGenerateContent" if stream else "generateContent"
	suffix = "?alt=sse" if stream else ""
	return f"{GEMINI_API_ROOT}/models/{encoded}:{action}{suffix}"


def create_gemini_generate_content_request(
	api_key: str,
	model_id: str,
	messages: list,
	stream: bool,
	kwargs: dict,
) -> tuple[str, dict[str, str], dict[str, Any]]:
	url = gemini_model_url(model_id, stream=stream)
	headers = build_google_native_headers(api_key)
	body = build_gemini_generate_content_body(model_id, messages, kwargs)
	return url, headers, body
