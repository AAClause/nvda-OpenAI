"""Message and content conversion helpers.

Bridges OpenAI's ``messages``/``input`` shape with Anthropic's content blocks
and the OpenAI Responses input format. Also handles inlining local files as
data URLs, decoding base64 audio payloads, etc.
"""
from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any, Optional

from ..consts import ContentType, Provider
from ._errors import APIError


# Mime types we treat as text for inlining purposes (so PDFs etc. stay binary).
_TEXT_MIME_EXACT = {
	"application/json",
	"application/xml",
	"application/javascript",
	"application/x-javascript",
	"application/sql",
}


def _is_text_media_type(media_type: str) -> bool:
	mt = (media_type or "").lower().strip()
	if not mt:
		return False
	if mt.startswith("text/"):
		return True
	return mt in _TEXT_MIME_EXACT


# ---------------------------------------------------------------------------
# Audio payload helpers (used by both chat-audio responses and TTS).
# ---------------------------------------------------------------------------

def _decode_audio_base64(value: Any) -> Optional[bytes]:
	"""Decode a base64 audio payload, optionally wrapped in a data: URL."""
	if not isinstance(value, str):
		return None
	data = value.strip()
	if not data:
		return None
	if data.startswith("data:") and "," in data:
		data = data.split(",", 1)[1].strip()
	try:
		return base64.b64decode(data, validate=False)
	except Exception:
		return None


def _extract_audio_bytes_from_json_payload(payload: Any) -> Optional[bytes]:
	"""Pull base64 audio out of common JSON TTS responses (Voxtral & friends)."""
	if not isinstance(payload, dict):
		return None
	for key in ("audio_data", "data"):
		audio = _decode_audio_base64(payload.get(key))
		if audio:
			return audio
	for container_key in ("audio", "output_audio"):
		container = payload.get(container_key)
		if not isinstance(container, dict):
			continue
		for key in ("audio_data", "data"):
			audio = _decode_audio_base64(container.get(key))
			if audio:
				return audio
	return None


# ---------------------------------------------------------------------------
# input_file detection / inlining (cross-provider PDF handling).
# ---------------------------------------------------------------------------

def _has_input_file_parts(messages: Any) -> bool:
	if not isinstance(messages, list):
		return False
	for msg in messages:
		if not isinstance(msg, dict):
			continue
		content = msg.get("content")
		if not isinstance(content, list):
			continue
		for part in content:
			if isinstance(part, dict) and part.get("type") == ContentType.INPUT_FILE:
				return True
	return False


def _file_path_to_data_url(path: str) -> Optional[str]:
	if not isinstance(path, str) or not path or not os.path.exists(path):
		return None
	try:
		with open(path, "rb") as f:
			raw = f.read()
	except OSError:
		return None
	mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
	b64 = base64.b64encode(raw).decode("utf-8")
	return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# Provider-aware document transformation (replaces blind inlining).
# ---------------------------------------------------------------------------
#
# Every provider's "OpenAI-compatible" chat completions endpoint accepts a
# different shape for non-image attachments (PDF/text/etc.). The translator
# below rewrites internal ``input_file`` parts produced by
# ``ConversationDialog.getFilesContent`` into whatever the target provider
# actually understands. When a provider does not support files at all on its
# chat endpoint, we fall back to inlining text-like documents as a text part
# and raise a clear error for binary documents.

def _read_local_file_bytes(file_path: str) -> Optional[bytes]:
	if not isinstance(file_path, str) or not file_path or not os.path.exists(file_path):
		return None
	try:
		with open(file_path, "rb") as f:
			return f.read()
	except OSError:
		return None


def _decode_data_url_to_bytes(data_url: str) -> tuple[bytes, str] | tuple[None, str]:
	"""Decode a ``data:<mime>;base64,<payload>`` URL. Returns (bytes, mime)."""
	if not isinstance(data_url, str) or not data_url.startswith("data:") or "," not in data_url:
		return (None, "")
	header, payload = data_url.split(",", 1)
	mime = header[5:].split(";")[0].strip() or "application/octet-stream"
	try:
		return (base64.b64decode(payload, validate=False), mime)
	except Exception:
		return (None, mime)


def _input_file_to_text_part(part: dict) -> Optional[dict]:
	"""Try to read the file referenced by ``part`` and return a text content part.

	Returns ``None`` for binary documents (PDF, DOCX, ...) — the caller is
	expected to raise an actionable error for those.
	"""
	filename = part.get("filename") or ""
	file_path = part.get("file_path")
	file_data = part.get("file_data")
	file_url = part.get("file_url")
	raw: Optional[bytes] = None
	mime = ""
	if isinstance(file_path, str) and file_path:
		raw = _read_local_file_bytes(file_path)
		if not filename:
			filename = os.path.basename(file_path)
		mime = mimetypes.guess_type(file_path)[0] or ""
	elif isinstance(file_data, str) and file_data:
		raw, mime = _decode_data_url_to_bytes(file_data)
	elif isinstance(file_url, str) and file_url:
		# We don't fetch arbitrary URLs here for safety; let the provider
		# fail the request and surface the error via the normal path.
		return None
	if raw is None:
		return None
	if not _is_text_media_type(mime):
		return None
	try:
		text = raw.decode("utf-8", errors="replace")
	except Exception:
		return None
	label = filename or "attached document"
	return {
		"type": ContentType.TEXT,
		"text": f"[{label}]\n{text}",
	}


def _input_file_to_data_url(part: dict) -> tuple[Optional[str], str, str]:
	"""Resolve an ``input_file`` part to ``(data_url, filename, mime)``.

	``data_url`` may be ``None`` when only an external URL is available — the
	caller will use it directly. ``mime`` is the resolved media type (best
	effort, ``application/octet-stream`` if unknown).
	"""
	filename = part.get("filename") or ""
	file_data = part.get("file_data")
	file_url = part.get("file_url")
	file_path = part.get("file_path")
	if isinstance(file_data, str) and file_data:
		_, mime = _decode_data_url_to_bytes(file_data)
		if not mime and filename:
			mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
		return (file_data, filename, mime or "application/octet-stream")
	if isinstance(file_path, str) and file_path:
		raw = _read_local_file_bytes(file_path)
		if raw is None:
			return (None, filename, "application/octet-stream")
		mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
		b64 = base64.b64encode(raw).decode("utf-8")
		if not filename:
			filename = os.path.basename(file_path)
		return (f"data:{mime};base64,{b64}", filename, mime)
	if isinstance(file_url, str) and file_url:
		# Caller handles plain URLs without re-encoding.
		mime = mimetypes.guess_type(filename or file_url)[0] or "application/octet-stream"
		return (None, filename, mime)
	return (None, filename, "application/octet-stream")


def _convert_input_file_for_openrouter(part: dict) -> dict:
	"""OpenRouter chat-completions PDF/file shape: ``{"type": "file", "file": {...}}``.

	Per https://openrouter.ai/docs/guides/overview/multimodal/pdfs the ``file``
	object accepts ``filename`` and ``file_data`` (a base64 data URL or a plain
	HTTPS URL).
	"""
	data_url, filename, _mime = _input_file_to_data_url(part)
	file_url = part.get("file_url")
	# Prefer external URL when available — avoids re-uploading large PDFs.
	file_value = data_url
	if file_value is None and isinstance(file_url, str) and file_url:
		file_value = file_url
	if file_value is None:
		raise APIError(_unreadable_file_message(filename or "<unknown>"))
	out: dict[str, Any] = {"type": "file", "file": {"file_data": file_value}}
	if filename:
		out["file"]["filename"] = filename
	return out


def _convert_input_file_for_mistral(part: dict) -> dict:
	"""Mistral chat-completions ``document_url`` shape (URL or data URI)."""
	data_url, filename, _mime = _input_file_to_data_url(part)
	file_url = part.get("file_url")
	url_value = file_url if (isinstance(file_url, str) and file_url) else data_url
	if not url_value:
		raise APIError(_unreadable_file_message(filename or "<unknown>"))
	return {"type": "document_url", "document_url": url_value}


def _unreadable_file_message(filename: str) -> str:
	return (
		f"Unable to read attached file '{filename}'. The file may be missing, "
		"unreadable, or located behind a private URL the addon cannot fetch."
	)


def _binary_doc_unsupported_message(provider: str, filename: str) -> str:
	"""Human-readable error for binary documents that a provider cannot accept."""
	label = filename or "<unknown file>"
	return (
		f"Provider '{provider}' does not support binary document attachments "
		f"like '{label}' on its chat completions endpoint. Use OpenAI, "
		"Anthropic, OpenRouter, Mistral, or xAI for native PDF/file support, "
		"or convert the file to plain text."
	)


def _normalize_input_files_for_provider(messages: list, provider: str) -> list:
	"""Rewrite ``input_file`` content parts into the shape ``provider`` expects.

	The internal representation produced by the conversation dialog is the
	OpenAI Responses ``input_file`` part. Most providers don't accept that
	shape on their Chat Completions endpoint, so this function rewrites each
	occurrence to the appropriate provider-specific format, inlines text-like
	documents as text, or raises ``APIError`` with a clear message when the
	provider cannot accept the document at all.

	The caller is responsible for routing requests with files to the correct
	endpoint when a provider has a separate Files/Responses API (handled by
	the dispatch in :class:`OpenAIClient`).
	"""
	if not isinstance(messages, list):
		return messages
	converted: list[Any] = []
	for msg in messages:
		if not isinstance(msg, dict):
			converted.append(msg)
			continue
		content = msg.get("content")
		if not isinstance(content, list):
			converted.append(msg)
			continue
		new_parts: list[dict] = []
		for part in content:
			if not isinstance(part, dict) or part.get("type") != ContentType.INPUT_FILE:
				new_parts.append(part)
				continue
			rewritten = _rewrite_input_file_part(part, provider)
			# A single ``input_file`` may expand to multiple parts (e.g. label
			# text + inlined content) — accept either a list or a single dict.
			if isinstance(rewritten, list):
				new_parts.extend(rewritten)
			elif rewritten is not None:
				new_parts.append(rewritten)
		new_msg = dict(msg)
		new_msg["content"] = new_parts
		converted.append(new_msg)
	return converted


def _rewrite_input_file_part(part: dict, provider: str) -> dict | list[dict]:
	"""Provider-aware conversion of a single ``input_file`` content part."""
	if provider == Provider.OpenRouter:
		return _convert_input_file_for_openrouter(part)
	if provider == Provider.MistralAI:
		return _convert_input_file_for_mistral(part)
	# Generic fallback: try inlining as text; binary docs raise a clear error.
	text_part = _input_file_to_text_part(part)
	if text_part is not None:
		return text_part
	filename = part.get("filename") or ""
	if not filename:
		fp = part.get("file_path")
		if isinstance(fp, str) and fp:
			filename = os.path.basename(fp)
	raise APIError(_binary_doc_unsupported_message(str(provider), filename))


# ---------------------------------------------------------------------------
# OpenAI -> Anthropic message conversion.
# ---------------------------------------------------------------------------

def _decode_text_payload(data: str, media_type: str) -> str:
	"""Decode a base64 text-like payload, honouring an optional data: URL header."""
	if not isinstance(data, str) or not data.strip():
		return ""
	raw_data = data.strip()
	mt = media_type
	if raw_data.startswith("data:") and "," in raw_data:
		header, b64_data = raw_data.split(",", 1)
		raw_data = b64_data
		if ";" in header:
			mt = header[5:].split(";")[0].strip() or media_type
	if not _is_text_media_type(mt):
		return ""
	try:
		decoded = base64.b64decode(raw_data, validate=False)
		return decoded.decode("utf-8", errors="replace")
	except Exception:
		return ""


def _anthropic_text_block(text: str) -> dict:
	return {"type": "text", "text": text}


def _anthropic_image_block_from_data_url(url: str) -> Optional[dict]:
	if not url.startswith("data:"):
		return None
	try:
		header, data = url.split(",", 1)
	except (ValueError, IndexError):
		return None
	mt = "image/png"
	if "image/" in header:
		mt = header.split("image/")[-1].split(";")[0].strip()
		mt = f"image/{mt}" if not mt.startswith("image/") else mt
	return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}


def _anthropic_doc_block_from_input_file(part: dict) -> Optional[dict | list]:
	"""Convert an OpenAI ``input_file`` part to one or more Anthropic blocks.

	Returns ``None`` when the file cannot be encoded. May return a list when
	a text file is inlined as a text block instead of a binary document block.
	"""
	source: Optional[dict] = None
	filename = part.get("filename", "") or ""
	file_id = part.get("file_id")
	file_url = part.get("file_url")
	file_path = part.get("file_path")
	file_data = part.get("file_data")
	extracted_text = ""

	if isinstance(file_id, str) and file_id.strip():
		source = {"type": "file", "file_id": file_id.strip()}
	elif isinstance(file_url, str) and file_url.strip():
		source = {"type": "url", "url": file_url.strip()}
	elif isinstance(file_data, str) and file_data.strip():
		data = file_data.strip()
		media_type = "application/pdf"
		if data.startswith("data:") and "," in data:
			header, b64_data = data.split(",", 1)
			if ";" in header:
				media_type = header[5:].split(";")[0].strip() or media_type
			data = b64_data
		if _is_text_media_type(media_type):
			extracted_text = _decode_text_payload(data, media_type)
		else:
			source = {"type": "base64", "media_type": media_type, "data": data}
	elif isinstance(file_path, str) and os.path.exists(file_path):
		try:
			with open(file_path, "rb") as f:
				raw = f.read()
		except OSError:
			return None
		media_type = mimetypes.guess_type(file_path)[0] or "application/pdf"
		if _is_text_media_type(media_type):
			extracted_text = raw.decode("utf-8", errors="replace")
		else:
			source = {
				"type": "base64",
				"media_type": media_type,
				"data": base64.b64encode(raw).decode("utf-8"),
			}
		if not filename:
			filename = os.path.basename(file_path)

	if extracted_text:
		title = filename or "Attached document"
		return _anthropic_text_block(f"[{title}]\n{extracted_text}")
	if source:
		block = {"type": "document", "source": source}
		if filename:
			block["title"] = filename
		return block
	return None


def _convert_content_to_anthropic(content) -> str | list:
	"""Convert an OpenAI-format ``content`` value to Anthropic content blocks."""
	if isinstance(content, str):
		return content
	if not isinstance(content, list):
		return str(content) if content else ""
	blocks: list[dict] = []
	for part in content:
		if not isinstance(part, dict):
			continue
		typ = part.get("type", "")
		if typ == "text":
			text = part.get("text", "")
			if text:
				blocks.append(_anthropic_text_block(text))
		elif typ == "image_url":
			img = part.get("image_url") or {}
			url = img.get("url", "") or ""
			block = _anthropic_image_block_from_data_url(url)
			if block:
				blocks.append(block)
		elif typ == "input_file":
			block = _anthropic_doc_block_from_input_file(part)
			if isinstance(block, list):
				blocks.extend(block)
			elif block:
				blocks.append(block)
	return blocks if blocks else ""


def _convert_messages_to_anthropic(messages: list) -> tuple[Optional[str], list]:
	"""Convert OpenAI messages to Anthropic format. Returns ``(system, messages)``."""
	system: Optional[str] = None
	anthropic_msgs: list[dict] = []
	for m in messages:
		if not isinstance(m, dict):
			continue
		role = (m.get("role") or "").lower()
		content = m.get("content", "")
		if role == "system":
			if isinstance(content, str):
				system = content
			elif isinstance(content, list):
				text_parts = [
					p.get("text", "") for p in content
					if isinstance(p, dict) and p.get("type") == "text"
				]
				system = "\n".join(text_parts) if text_parts else None
			else:
				system = str(content) if content else None
			continue
		if role not in ("user", "assistant"):
			continue
		conv = _convert_content_to_anthropic(content)
		if conv:
			anthropic_msgs.append({"role": role, "content": conv})
	return (system, anthropic_msgs)


# ---------------------------------------------------------------------------
# OpenAI Responses input conversion.
# ---------------------------------------------------------------------------

def _convert_part_to_responses(
	part: dict,
	upload_file: Optional[callable] = None,
) -> Optional[dict]:
	"""Convert one OpenAI Chat Completions content part to a Responses input part."""
	part_type = part.get("type")
	if part_type == "text":
		text = part.get("text", "")
		return {"type": "input_text", "text": text} if text else None
	if part_type == "image_url":
		image = part.get("image_url") or {}
		url = image.get("url")
		if isinstance(url, str) and url:
			return {"type": "input_image", "image_url": url}
		return None
	if part_type == "input_audio":
		audio = part.get("input_audio") or {}
		data = audio.get("data")
		fmt = audio.get("format")
		if data:
			return {"type": "input_audio", "input_audio": {"data": data, "format": fmt or "wav"}}
		return None
	if part_type == "input_file":
		file_id = part.get("file_id")
		if isinstance(file_id, str) and file_id.strip():
			return {"type": "input_file", "file_id": file_id.strip()}
		file_url = part.get("file_url")
		if isinstance(file_url, str) and file_url.strip():
			return {"type": "input_file", "file_url": file_url.strip()}
		file_data = part.get("file_data")
		if isinstance(file_data, str) and file_data.strip():
			file_part = {"type": "input_file", "file_data": file_data.strip()}
			if part.get("filename"):
				file_part["filename"] = part.get("filename")
			return file_part
		file_path = part.get("file_path")
		if isinstance(file_path, str) and file_path and upload_file is not None:
			file_id_uploaded = upload_file(file_path)
			return {"type": "input_file", "file_id": file_id_uploaded}
	return None


def _messages_to_responses_input(
	messages: list,
	upload_file: Optional[callable] = None,
) -> list:
	"""Convert chat-completion-style messages into the OpenAI ``/v1/responses`` input shape."""
	if not isinstance(messages, list):
		return []
	output: list[dict] = []
	for msg in messages:
		if not isinstance(msg, dict):
			continue
		role = (msg.get("role") or "user").lower()
		content = msg.get("content", "")
		if isinstance(content, str):
			text = content.strip()
			if not text:
				continue
			output.append({"role": role, "content": [{"type": "input_text", "text": text}]})
			continue
		if not isinstance(content, list):
			continue
		parts: list[dict] = []
		for part in content:
			if not isinstance(part, dict):
				continue
			converted = _convert_part_to_responses(part, upload_file=upload_file)
			if converted:
				parts.append(converted)
		if parts:
			output.append({"role": role, "content": parts})
	return output
