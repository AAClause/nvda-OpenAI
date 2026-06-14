"""Conversation storage: save, load, list, rename, delete."""
import base64
import json
import os
import re
import shutil
import time
import uuid
from enum import StrEnum, auto

import addonHandler
from logHandler import log

from .consts import DATA_DIR, ensure_dir_exists
from .image_file import AttachmentFile
from .mediastore import persist_local_file
from .usage_ledger import (
	CONVERSATION_JSON_VERSION,
	conversation_json_version,
	deserialize_ledger,
	migrate_ledger_from_block_dicts,
	resolve_ledger_for_saved_data,
)

addonHandler.initTranslation()

CONVERSATIONS_DIR = os.path.join(DATA_DIR, "conversations")
ATTACHMENTS_DIR = os.path.join(CONVERSATIONS_DIR, "attachments")
INDEX_FILENAME = "index.json"
INDEX_PATH = os.path.join(CONVERSATIONS_DIR, INDEX_FILENAME)
DEFAULT_TITLE_LEN = 50
INDEX_VERSION = 1


def _atomic_write_json(path: str, data):
	tmp_path = path + ".tmp"
	with open(tmp_path, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=2, ensure_ascii=False)
	os.replace(tmp_path, path)


class ConversationFormat(StrEnum):
	@staticmethod
	def _generate_next_value_(name, start, count, last_values):
		mapping = {
			"GENERIC": "generic",
			"TOOL_GOOGLE_LYRIA_PRO": "tool/google/lyria-pro",
			"TOOL_MISTRAL_VOXTRAL_TTS": "tool/mistral/voxtral-tts",
			"TOOL_MISTRAL_OCR": "tool/mistral/ocr",
			"TOOL_MISTRAL_SPEECH_TO_TEXT": "tool/mistral/speech-to-text",
			"TOOL_OPENAI_TTS": "tool/openai/tts",
			"TOOL_OPENAI_TRANSCRIPTION": "tool/openai/transcription",
		}
		return mapping[name]

	GENERIC = auto()
	TOOL_GOOGLE_LYRIA_PRO = auto()
	TOOL_MISTRAL_VOXTRAL_TTS = auto()
	TOOL_MISTRAL_OCR = auto()
	TOOL_MISTRAL_SPEECH_TO_TEXT = auto()
	TOOL_OPENAI_TTS = auto()
	TOOL_OPENAI_TRANSCRIPTION = auto()


def normalize_conversation_format(value) -> ConversationFormat:
	if isinstance(value, ConversationFormat):
		return value
	if isinstance(value, str):
		for item in ConversationFormat:
			if item.value == value:
				return item
	return ConversationFormat.GENERIC


def _persist_path_maybe(path: str, category: str, prefix: str, fallback_ext: str) -> str:
	if not isinstance(path, str) or not path:
		return ""
	return persist_local_file(path, category, prefix=prefix, fallback_ext=fallback_ext)


def _serialize_format_data(conv_format: ConversationFormat | str, data: dict | None) -> dict:
	conv_format = normalize_conversation_format(conv_format)
	raw = data if isinstance(data, dict) else {}
	if conv_format == ConversationFormat.TOOL_GOOGLE_LYRIA_PRO:
		return {
			"prompt": raw.get("prompt", ""),
			"negative_prompt": raw.get("negative_prompt", ""),
			"model": raw.get("model", ""),
			"requested_format": raw.get("requested_format", ""),
			"actual_format": raw.get("actual_format", ""),
			"audio_path": _persist_path_maybe(raw.get("audio_path", ""), "audio", "lyria", ".wav"),
			"options": raw.get("options", {}) if isinstance(raw.get("options"), dict) else {},
		}
	if conv_format == ConversationFormat.TOOL_MISTRAL_VOXTRAL_TTS:
		return {
			"input_text": raw.get("input_text", ""),
			"model": raw.get("model", ""),
			"voice_id": raw.get("voice_id", ""),
			"response_format": raw.get("response_format", ""),
			"stream": bool(raw.get("stream", False)),
			"ref_audio_path": _persist_path_maybe(raw.get("ref_audio_path", ""), "audio", "voxtral_ref", ".wav"),
			"audio_path": _persist_path_maybe(raw.get("audio_path", ""), "audio", "voxtral_tts", ".wav"),
		}
	if conv_format == ConversationFormat.TOOL_MISTRAL_OCR:
		return {
			"source": raw.get("source", ""),
			"model": raw.get("model", ""),
			"text_path": _persist_path_maybe(raw.get("text_path", ""), "documents", "mistral_ocr", ".txt"),
			"json_path": _persist_path_maybe(raw.get("json_path", ""), "documents", "mistral_ocr", ".json"),
			"text_preview": raw.get("text_preview", ""),
			"options": raw.get("options", {}) if isinstance(raw.get("options"), dict) else {},
		}
	if conv_format == ConversationFormat.TOOL_MISTRAL_SPEECH_TO_TEXT:
		return {
			"input_audio_path": _persist_path_maybe(raw.get("input_audio_path", ""), "audio", "mistral_stt_input", ".wav"),
			"model": raw.get("model", ""),
			"language": raw.get("language", ""),
			"diarize": bool(raw.get("diarize", False)),
			"context_bias": raw.get("context_bias", ""),
			"timestamp_granularities": raw.get("timestamp_granularities", ""),
			"temperature": raw.get("temperature", ""),
			"text_path": _persist_path_maybe(raw.get("text_path", ""), "documents", "mistral_stt", ".txt"),
			"raw_path": _persist_path_maybe(raw.get("raw_path", ""), "documents", "mistral_stt", ".json"),
		}
	if conv_format == ConversationFormat.TOOL_OPENAI_TTS:
		return {
			"input_text": raw.get("input_text", ""),
			"model": raw.get("model", ""),
			"voice": raw.get("voice", ""),
			"instructions": raw.get("instructions", ""),
			"response_format": raw.get("response_format", ""),
			"speed": raw.get("speed", ""),
			"stream_format": raw.get("stream_format", ""),
			"audio_path": _persist_path_maybe(raw.get("audio_path", ""), "audio", "openai_tts", ".mp3"),
		}
	if conv_format == ConversationFormat.TOOL_OPENAI_TRANSCRIPTION:
		return {
			"task": raw.get("task", "transcription"),
			"input_audio_path": _persist_path_maybe(raw.get("input_audio_path", ""), "audio", "openai_stt_input", ".wav"),
			"model": raw.get("model", ""),
			"language": raw.get("language", ""),
			"prompt": raw.get("prompt", ""),
			"response_format": raw.get("response_format", ""),
			"temperature": raw.get("temperature", ""),
			"timestamp_granularities": raw.get("timestamp_granularities", ""),
			"include": raw.get("include", ""),
			"chunking_strategy": raw.get("chunking_strategy", ""),
			"known_speaker_names": raw.get("known_speaker_names", ""),
			"known_speaker_references": raw.get("known_speaker_references", ""),
			"text_path": _persist_path_maybe(raw.get("text_path", ""), "documents", "openai_stt", ".txt"),
			"raw_path": _persist_path_maybe(raw.get("raw_path", ""), "documents", "openai_stt", ".json"),
		}
	return raw


def _deserialize_format_data(conv_format: ConversationFormat | str, data: dict | None) -> dict:
	# Paths are already persisted and portable; keep data as-is for now.
	return data if isinstance(data, dict) else {}


def _iter_format_data_paths(data):
	if isinstance(data, dict):
		for k, v in data.items():
			if isinstance(v, str) and ("path" in k.lower()):
				yield v
			else:
				yield from _iter_format_data_paths(v)
	elif isinstance(data, list):
		for item in data:
			yield from _iter_format_data_paths(item)


def _is_local_path(path: str) -> bool:
	return isinstance(path, str) and path and not path.startswith("http://") and not path.startswith("https://")


def _is_under_data_dir(path: str) -> bool:
	if not _is_local_path(path):
		return False
	try:
		abs_path = os.path.abspath(path)
		abs_data = os.path.abspath(DATA_DIR)
		return os.path.commonpath([abs_path, abs_data]) == abs_data
	except Exception:
		return False


def _collect_referenced_local_paths(data: dict, conv_id: str = "") -> set[str]:
	paths = set()
	blocks = data.get("blocks", [])
	if isinstance(blocks, list):
		for block in blocks:
			if not isinstance(block, dict):
				continue
			for item in block.get("pathList", []):
				if isinstance(item, dict):
					p = item.get("path", "")
				elif isinstance(item, str):
					p = item
				else:
					p = ""
				if _is_local_path(p):
					paths.add(p)
			for p in block.get("audioPathList", []):
				if _is_local_path(p):
					paths.add(p)
			audio_path = block.get("audioPath", "")
			if _is_local_path(audio_path):
				paths.add(audio_path)
	for item in data.get("draftPathList", []):
		if isinstance(item, dict):
			p = item.get("path", "")
		elif isinstance(item, str):
			p = item
		else:
			p = ""
		if _is_local_path(p):
			paths.add(p)
	for p in data.get("draftAudioPathList", []):
		if _is_local_path(p):
			paths.add(p)
	for p in _iter_format_data_paths(data.get("formatData")):
		if _is_local_path(p):
			paths.add(p)
	if conv_id:
		conv_dir = os.path.join(ATTACHMENTS_DIR, conv_id)
		if os.path.isdir(conv_dir):
			for root, _, files in os.walk(conv_dir):
				for name in files:
					paths.add(os.path.join(root, name))
	return paths


def _collect_file_entries(data: dict) -> list[dict]:
	files = []
	seen = set()

	def _size_for(path: str):
		if not _is_local_path(path) or not os.path.exists(path):
			return None
		try:
			return int(os.path.getsize(path))
		except Exception:
			return None

	def _add(path: str, role: str, kind: str):
		if not isinstance(path, str) or not path:
			return
		key = (path, role, kind)
		if key in seen:
			return
		seen.add(key)
		files.append({
			"path": path,
			"role": role,
			"kind": kind,
			"size": _size_for(path),
		})

	blocks = data.get("blocks", [])
	if isinstance(blocks, list):
		for block in blocks:
			if not isinstance(block, dict):
				continue
			for item in block.get("pathList", []):
				if isinstance(item, dict):
					_add(item.get("path", ""), "input", "image")
				elif isinstance(item, str):
					_add(item, "input", "image")
			for p in block.get("audioPathList", []):
				_add(p, "input", "audio")
			_add(block.get("audioPath", ""), "output", "audio")

	for item in data.get("draftPathList", []):
		if isinstance(item, dict):
			_add(item.get("path", ""), "input", "draft-image")
		elif isinstance(item, str):
			_add(item, "input", "draft-image")
	for p in data.get("draftAudioPathList", []):
		_add(p, "input", "draft-audio")

	fmt = normalize_conversation_format(data.get("format", ConversationFormat.GENERIC.value))
	fmt_data = data.get("formatData", {})
	if isinstance(fmt_data, dict):
		if fmt == ConversationFormat.TOOL_MISTRAL_OCR:
			_add(fmt_data.get("source", ""), "input", "ocr-source")
			_add(fmt_data.get("text_path", ""), "output", "ocr-text")
			_add(fmt_data.get("json_path", ""), "output", "ocr-json")
		elif fmt == ConversationFormat.TOOL_MISTRAL_SPEECH_TO_TEXT:
			_add(fmt_data.get("input_audio_path", ""), "input", "mistral-stt-input")
			_add(fmt_data.get("text_path", ""), "output", "mistral-stt-text")
			_add(fmt_data.get("raw_path", ""), "output", "mistral-stt-raw")
		elif fmt == ConversationFormat.TOOL_GOOGLE_LYRIA_PRO:
			_add(fmt_data.get("audio_path", ""), "output", "lyria-audio")
		elif fmt == ConversationFormat.TOOL_MISTRAL_VOXTRAL_TTS:
			_add(fmt_data.get("ref_audio_path", ""), "input", "voxtral-ref-audio")
			_add(fmt_data.get("audio_path", ""), "output", "voxtral-audio")
		elif fmt == ConversationFormat.TOOL_OPENAI_TTS:
			_add(fmt_data.get("audio_path", ""), "output", "openai-tts-audio")
		elif fmt == ConversationFormat.TOOL_OPENAI_TRANSCRIPTION:
			_add(fmt_data.get("input_audio_path", ""), "input", "openai-stt-input")
			_add(fmt_data.get("text_path", ""), "output", "openai-stt-text")
			_add(fmt_data.get("raw_path", ""), "output", "openai-stt-raw")
	return files


def ensure_conversations_dir():
	ensure_dir_exists(DATA_DIR)
	ensure_dir_exists(CONVERSATIONS_DIR)


def get_default_title(first_message: str) -> str:
	"""Use start of first message as default conversation title."""
	if not first_message or not first_message.strip():
		# Translators: Text in conversation metadata/properties shown to the user.
		return _("Untitled conversation")
	text = first_message.strip()
	# Collapse whitespace and newlines
	text = re.sub(r"\s+", " ", text)
	if len(text) <= DEFAULT_TITLE_LEN:
		return text
	return text[:DEFAULT_TITLE_LEN].rstrip() + "…"


def _block_to_dict(block) -> dict:
	"""Serialize a HistoryBlock to JSON-safe dict."""
	d = {
		"prompt": block.prompt or "",
		"responseText": block.responseText or "",
		"reasoningText": getattr(block, "reasoningText", "") or "",
		"model": getattr(block, "model", "") or "",
		"temperature": getattr(block, "temperature", 0),
		"topP": getattr(block, "topP", 0),
		"seed": getattr(block, "seed", None),
		"topK": getattr(block, "topK", None),
		"stopText": getattr(block, "stopText", "") or "",
		"frequencyPenalty": getattr(block, "frequencyPenalty", None),
		"presencePenalty": getattr(block, "presencePenalty", None),
		"maxTokens": getattr(block, "maxTokens", 0),
		"system": getattr(block, "system", "") or "",
	}
	usage = getattr(block, "usage", None)
	if isinstance(usage, dict) and usage:
		d["usage"] = usage
	timing = getattr(block, "timing", None)
	if isinstance(timing, dict) and timing:
		d["timing"] = timing
	# JSON key is intentionally still ``pathList`` to keep older saved
	# conversations forward-readable; the in-code attribute is ``filesList``.
	files_list = getattr(block, "filesList", None)
	if files_list:
		d["pathList"] = [
			{
				"path": persist_local_file(
					(getattr(att, "path", att) if hasattr(att, "path") else att),
					"images",
					prefix="image",
					fallback_ext=".png",
				),
				"name": getattr(att, "name", ""),
			}
			for att in files_list
		]
	else:
		d["pathList"] = []
	audio_list = getattr(block, "audioPathList", None)
	if audio_list:
		d["audioPathList"] = [
			persist_local_file(
				(p if isinstance(p, str) else getattr(p, "path", str(p))),
				"audio",
				prefix="audio",
				fallback_ext=".wav",
			)
			for p in audio_list
		]
	else:
		d["audioPathList"] = []
	audio_path = getattr(block, "audioPath", None)
	if isinstance(audio_path, str) and audio_path:
		d["audioPath"] = persist_local_file(audio_path, "audio", prefix="chat_audio", fallback_ext=".wav")
	transcripts = getattr(block, "audioTranscriptList", None)
	d["audioTranscriptList"] = list(transcripts) if transcripts else []
	block_id = getattr(block, "uid", None)
	if isinstance(block_id, str) and block_id:
		d["id"] = block_id
	return d


def _dict_to_img(item, conv_id: str, block_idx: int, img_idx: int):
	"""Deserialize one attachment dict. Restores base64 to persistent file; URLs used as-is.

	Returns an :class:`AttachmentFile` (image or document) or ``None``.
	"""
	if isinstance(item, str):
		path, name, b64 = item, "", None
	elif isinstance(item, dict):
		path = item.get("path", "")
		name = item.get("name", "")
		b64 = item.get("base64")
	else:
		return None
	# Restore from embedded base64
	if b64:
		ensure_dir_exists(ATTACHMENTS_DIR)
		conv_dir = os.path.join(ATTACHMENTS_DIR, conv_id)
		ensure_dir_exists(conv_dir)
		ext = item.get("ext", ".png") if isinstance(item, dict) else ".png"
		if not ext.startswith("."):
			ext = "." + ext
		stored_path = os.path.join(conv_dir, f"img_{block_idx}_{img_idx}{ext}")
		try:
			data = base64.b64decode(b64)
			with open(stored_path, "wb") as f:
				f.write(data)
			return AttachmentFile(stored_path, name=name or None)
		except Exception as err:
			log.warning(f"conversations: could not restore image {stored_path}: {err}")
			return None
	if not path:
		return None
	# URL or existing path
	if path.startswith("http://") or path.startswith("https://") or os.path.exists(path):
		try:
			return AttachmentFile(path, name=name or None)
		except Exception as err:
			log.warning(f"conversations: skipped image {path}: {err}")
	return None


def _dict_to_block(d: dict, conv_id: str = "", block_idx: int = 0):
	"""Deserialize a block dict. conv_id required for restoring embedded images."""
	from .history import HistoryBlock
	block = HistoryBlock()
	block.prompt = d.get("prompt", "")
	block.responseText = d.get("responseText", "")
	block.reasoningText = d.get("reasoningText", "")
	block.model = d.get("model", "")
	block.temperature = d.get("temperature", 0)
	block.topP = d.get("topP", 0)
	block.seed = d.get("seed")
	block.topK = d.get("topK")
	block.stopText = d.get("stopText", "") or ""
	block.frequencyPenalty = d.get("frequencyPenalty")
	block.presencePenalty = d.get("presencePenalty")
	block.maxTokens = d.get("maxTokens", 0)
	block.system = d.get("system", "")
	block.usage = d.get("usage") if isinstance(d.get("usage"), dict) else {}
	block.timing = d.get("timing") if isinstance(d.get("timing"), dict) else {}
	block.responseTerminated = True
	block.displayHeader = False
	# JSON key is still the legacy ``pathList`` for forward-compat with older
	# saved conversations; map it onto the new ``filesList`` attribute.
	path_list = d.get("pathList", [])
	block.filesList = []
	for i, item in enumerate(path_list):
		img = _dict_to_img(item, conv_id, block_idx, i) if conv_id and isinstance(item, dict) else None
		if img is None and isinstance(item, dict):
			path = item.get("path", "")
			name = item.get("name", "")
			if path and (path.startswith("http://") or path.startswith("https://") or os.path.exists(path)):
				try:
					img = AttachmentFile(path, name=name or None)
				except Exception as err:
					log.warning(f"conversations: skipped image {path}: {err}")
		elif img is None and isinstance(item, str) and item:
			try:
				img = AttachmentFile(item)
			except Exception as err:
				log.warning(f"conversations: skipped image {item}: {err}")
		if img is not None:
			block.filesList.append(img)
	audio_list = d.get("audioPathList", [])
	block.audioPathList = [p for p in audio_list if p and isinstance(p, str)]
	audio_path = d.get("audioPath")
	block.audioPath = audio_path if isinstance(audio_path, str) else None
	block.audioTranscriptList = d.get("audioTranscriptList") or []
	block_id = d.get("id") or d.get("uid")
	block.uid = block_id if isinstance(block_id, str) and block_id else str(uuid.uuid4())
	return block


def _read_index():
	if not os.path.exists(INDEX_PATH):
		return {"version": INDEX_VERSION, "entries": []}
	try:
		with open(INDEX_PATH, "r", encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data.get("entries"), list):
			data["entries"] = []
		return data
	except Exception as err:
		log.error(f"conversations: read index: {err}", exc_info=True)
		return {"version": INDEX_VERSION, "entries": []}


def _write_index(data: dict):
	ensure_conversations_dir()
	try:
		_atomic_write_json(INDEX_PATH, data)
	except Exception as err:
		log.error(f"conversations: write index: {err}", exc_info=True)
		raise


def list_conversations() -> list:
	"""Return list of conversation metadata dicts, newest first."""
	idx = _read_index()
	entries = idx.get("entries", [])
	# Sort by updated desc
	entries = sorted(entries, key=lambda e: e.get("updated", 0), reverse=True)
	return entries


def get_conversation_properties(conv_id: str) -> dict | None:
	"""Return lightweight properties from saved JSON without restoring attachments."""
	path = get_conversation_path(conv_id)
	if not os.path.exists(path):
		return None
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
	except Exception as err:
		log.error(f"conversations: properties {conv_id}: {err}", exc_info=True)
		return None

	blocks = data.get("blocks", [])
	if not isinstance(blocks, list):
		blocks = []
	ledger = resolve_ledger_for_saved_data(data)
	session_agg = None
	thread_agg = None
	try:
		from .usage_ledger import aggregate_block_dicts_usage, aggregate_ledger_usage

		session_agg = aggregate_ledger_usage(ledger, _("unknown"))
		thread_agg = aggregate_block_dicts_usage(blocks, _("unknown"))
	except Exception:
		session_agg = None
		thread_agg = None

	# Legacy scalar totals (session when ledger exists, else sum of block dicts).
	total_input = total_output = total_tokens = 0
	total_reasoning = total_cached = 0
	total_cache_write = total_input_audio = total_output_audio = 0
	total_cost = 0.0
	has_cost = False
	usage_message_count = 0
	model_counts = {}
	if session_agg and session_agg.get("usage_count"):
		total_input = session_agg["total_input"]
		total_output = session_agg["total_output"]
		total_tokens = session_agg["total_tokens"]
		total_reasoning = session_agg["total_reasoning"]
		total_cached = session_agg["total_cached"]
		total_cache_write = session_agg["total_cache_write"]
		total_input_audio = session_agg["total_input_audio"]
		total_output_audio = session_agg["total_output_audio"]
		total_cost = session_agg["total_cost"]
		has_cost = session_agg["has_cost"]
		usage_message_count = session_agg["usage_count"]
		model_counts = dict(session_agg.get("model_counts") or {})
	else:
		for block in blocks:
			if not isinstance(block, dict):
				continue
			# Translators: Text in conversation metadata/properties shown to the user.
			model_name = block.get("model") or _("unknown")
			model_counts[model_name] = model_counts.get(model_name, 0) + 1
			usage = block.get("usage")
			if not isinstance(usage, dict):
				continue
			usage_message_count += 1
			try:
				input_tokens = int(usage.get("input_tokens", 0) or 0)
				output_tokens = int(usage.get("output_tokens", 0) or 0)
				if input_tokens == 0:
					input_tokens = int(usage.get("prompt_tokens", 0) or 0)
				if output_tokens == 0:
					output_tokens = int(usage.get("completion_tokens", 0) or 0)
				total_input += input_tokens
				total_output += output_tokens
				total_for_block = int(usage.get("total_tokens", 0) or 0)
				if total_for_block == 0 and (input_tokens or output_tokens):
					total_for_block = input_tokens + output_tokens
				total_tokens += total_for_block
				total_reasoning += int(usage.get("reasoning_tokens", 0) or 0)
				total_cached += int(usage.get("cached_input_tokens", 0) or 0)
				total_cache_write += int(usage.get("cache_creation_input_tokens", 0) or 0)
				total_input_audio += int(usage.get("input_audio_tokens", 0) or 0)
				total_output_audio += int(usage.get("output_audio_tokens", 0) or 0)
			except (TypeError, ValueError):
				pass
			cost = usage.get("cost")
			if isinstance(cost, (int, float)):
				total_cost += float(cost)
				has_cost = True

	thread_cost = thread_agg.get("total_cost", 0.0) if thread_agg else total_cost
	thread_has_cost = bool(thread_agg.get("has_cost")) if thread_agg else has_cost

	return {
		"id": data.get("id", conv_id),
		# Translators: Text in conversation metadata/properties shown to the user.
		"name": data.get("name", _("Untitled conversation")),
		"created": data.get("created", 0),
		"updated": data.get("updated", 0),
		"messages": len(blocks),
		"system_len": len(data.get("system", "") or ""),
		"draft_len": len(data.get("draftPrompt", "") or ""),
		"total_input": total_input,
		"total_output": total_output,
		"total_tokens": total_tokens,
		"total_reasoning": total_reasoning,
		"total_cached": total_cached,
		"total_cache_write": total_cache_write,
		"total_input_audio": total_input_audio,
		"total_output_audio": total_output_audio,
		"total_cost": total_cost,
		"has_cost": has_cost,
		"thread_total_input": int(thread_agg.get("total_input", 0)) if thread_agg else total_input,
		"thread_total_output": int(thread_agg.get("total_output", 0)) if thread_agg else total_output,
		"thread_total_tokens": int(thread_agg.get("total_tokens", 0)) if thread_agg else total_tokens,
		"thread_total_cost": thread_cost,
		"thread_has_cost": thread_has_cost,
		"ledger_entries": len(ledger),
		"file_version": conversation_json_version(data),
		"has_usage": bool(usage_message_count),
		"usage_message_count": usage_message_count,
		"model_counts": model_counts,
		"format": normalize_conversation_format(data.get("format", ConversationFormat.GENERIC.value)).value,
		"files": _collect_file_entries(data),
	}


def get_conversation_path(conv_id: str) -> str:
	return os.path.join(CONVERSATIONS_DIR, f"{conv_id}.json")


HUB_SESSION_JSON = os.path.join(DATA_DIR, "hub_session.json")


def write_hub_session_snapshot(*, tabs: list) -> None:
	"""Write hub_session.json as version 2 (tabs only). Uses atomic replace."""
	_atomic_write_json(HUB_SESSION_JSON, {"version": 2, "tabs": tabs})


def remove_hub_session_file() -> None:
	"""Remove hub_session.json when session persistence is disabled."""
	if not os.path.isfile(HUB_SESSION_JSON):
		return
	try:
		os.remove(HUB_SESSION_JSON)
	except OSError as err:
		log.debug("remove hub session file: %s", err)


def conversation_file_exists(conv_id: str) -> bool:
	if not conv_id or not isinstance(conv_id, str):
		return False
	return os.path.isfile(get_conversation_path(conv_id))


def prune_hub_session_references(removed_ids) -> None:
	"""Remove tab entries from hub_session.json when those conversations were deleted."""
	if not removed_ids:
		return
	removed = {str(x) for x in removed_ids if x}
	if not removed:
		return
	if not os.path.isfile(HUB_SESSION_JSON):
		return
	try:
		with open(HUB_SESSION_JSON, "r", encoding="utf-8") as f:
			snap = json.load(f)
	except Exception as err:
		log.debug("prune hub session: read failed: %s", err)
		return
	tabs = snap.get("tabs")
	if not isinstance(tabs, list) or not tabs:
		return
	new_tabs = []
	changed = False
	for entry in tabs:
		cid = ""
		if isinstance(entry, dict):
			cid = entry.get("id") or ""
		elif isinstance(entry, str):
			cid = entry
		if cid in removed:
			changed = True
			continue
		new_tabs.append(entry if isinstance(entry, dict) else {"id": cid})
	if not changed:
		return
	if not new_tabs:
		try:
			os.remove(HUB_SESSION_JSON)
		except OSError as err:
			log.debug("prune hub session: remove empty file: %s", err)
		return
	try:
		write_hub_session_snapshot(tabs=new_tabs)
	except Exception as err:
		log.warning("prune hub session: write failed: %s", err)


def load_conversation(conv_id: str) -> dict | None:
	"""Load conversation by id. Returns dict with keys: id, name, system, blocks, model, draftPrompt, accountKey, uiState."""
	path = get_conversation_path(conv_id)
	if not os.path.exists(path):
		return None
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
		blocks_data = data.get("blocks", [])
		blocks = []
		for idx, bd in enumerate(blocks_data):
			blocks.append(_dict_to_block(bd, conv_id=conv_id, block_idx=idx))
		raw_ui = data.get("uiState")
		ui_state = raw_ui if isinstance(raw_ui, dict) else {}
		ledger = resolve_ledger_for_saved_data(data)
		file_version = conversation_json_version(data)
		return {
			"id": data.get("id", conv_id),
			# Translators: Text in conversation metadata/properties shown to the user.
			"name": data.get("name", _("Untitled conversation")),
			"system": data.get("system", ""),
			"blocks": blocks,
			"usageLedger": ledger,
			"fileVersion": file_version,
			"model": data.get("model", ""),
			"accountKey": data.get("accountKey", ""),
			"uiState": ui_state,
			"draftPrompt": data.get("draftPrompt", ""),
			"draftPathList": data.get("draftPathList", []),
			"draftAudioPathList": data.get("draftAudioPathList", []),
			"format": normalize_conversation_format(data.get("format", ConversationFormat.GENERIC.value)).value,
			"formatData": _deserialize_format_data(
				normalize_conversation_format(data.get("format", ConversationFormat.GENERIC.value)),
				data.get("formatData"),
			),
		}
	except Exception as err:
		log.error(f"conversations: load {conv_id}: {err}", exc_info=True)
		return None


def save_conversation(
	blocks,
	system: str = "",
	model: str = "",
	name: str = None,
	conv_id: str = None,
	draftPrompt: str = "",
	draftPathList=None,
	draftAudioPathList=None,
	conversation_format: ConversationFormat | str = ConversationFormat.GENERIC,
	format_data: dict | None = None,
	account_key: str = "",
	ui_state: dict | None = None,
	usage_ledger=None,
) -> str:
	"""
	Save conversation. Returns conversation id.
	If conv_id given and exists, updates. Otherwise creates new.
	"""
	ensure_conversations_dir()
	draftPathList = draftPathList or []
	draftAudioPathList = draftAudioPathList or []
	serialized_draft_paths = []
	for img in draftPathList:
		path = getattr(img, "path", img) if hasattr(img, "path") else img
		if not isinstance(path, str) or not path:
			continue
		serialized_draft_paths.append({
			"path": persist_local_file(path, "images", prefix="image", fallback_ext=".png"),
			"name": getattr(img, "name", "") if hasattr(img, "name") else "",
		})
	serialized_draft_audio = []
	for p in draftAudioPathList:
		path = p if isinstance(p, str) else getattr(p, "path", str(p))
		if isinstance(path, str) and path:
			serialized_draft_audio.append(
				persist_local_file(path, "audio", prefix="audio", fallback_ext=".wav")
			)
	now = int(time.time())
	normalized_format = normalize_conversation_format(conversation_format)
	ui_payload = ui_state if isinstance(ui_state, dict) else {}
	ledger_payload = deserialize_ledger(usage_ledger) if usage_ledger is not None else None
	path = get_conversation_path(conv_id) if conv_id else ""
	if conv_id and os.path.exists(path):
		try:
			with open(path, "r", encoding="utf-8") as f:
				existing = json.load(f)
		except Exception:
			existing = {}
		if ledger_payload is None:
			ledger_payload = resolve_ledger_for_saved_data(existing)
		existing["version"] = CONVERSATION_JSON_VERSION
		existing["updated"] = now
		if name is not None:
			existing["name"] = name
		existing["system"] = system
		existing["model"] = model
		existing["accountKey"] = account_key or ""
		existing["uiState"] = ui_payload
		existing["draftPrompt"] = draftPrompt or ""
		existing["draftPathList"] = serialized_draft_paths
		existing["draftAudioPathList"] = serialized_draft_audio
		existing["format"] = normalized_format.value
		existing["formatData"] = _serialize_format_data(normalized_format, format_data)
		existing["blocks"] = [_block_to_dict(b) for b in blocks]
		existing["usageLedger"] = ledger_payload
	else:
		conv_id = conv_id or str(uuid.uuid4())
		if ledger_payload is None:
			ledger_payload = migrate_ledger_from_block_dicts([_block_to_dict(b) for b in blocks])
		if name is None and blocks:
			first = blocks[0]
			prompt = getattr(first, "prompt", "") or ""
			tlist = getattr(first, "audioTranscriptList", None)
			if not prompt and tlist and any(t for t in tlist):
				prompt = "\n".join(t for t in tlist if t).strip()
			name = get_default_title(prompt)
		existing = {
			"version": CONVERSATION_JSON_VERSION,
			"id": conv_id,
			# Translators: Text in conversation metadata/properties shown to the user.
			"name": name or _("Untitled conversation"),
			"created": now,
			"updated": now,
			"system": system,
			"model": model,
			"accountKey": account_key or "",
			"uiState": ui_payload,
			"draftPrompt": draftPrompt or "",
			"draftPathList": serialized_draft_paths,
			"draftAudioPathList": serialized_draft_audio,
			"format": normalized_format.value,
			"formatData": _serialize_format_data(normalized_format, format_data),
			"blocks": [_block_to_dict(b) for b in blocks],
			"usageLedger": ledger_payload,
		}
	path = get_conversation_path(conv_id)
	try:
		_atomic_write_json(path, existing)
	except Exception as err:
		log.error(f"conversations: save {conv_id}: {err}", exc_info=True)
		raise
	# Update index
	idx = _read_index()
	entries = {e["id"]: e for e in idx.get("entries", [])}
	entries[conv_id] = {
		"id": conv_id,
		# Translators: Text in conversation metadata/properties shown to the user.
		"name": existing.get("name", _("Untitled conversation")),
		"created": existing.get("created", now),
		"updated": now,
		"format": normalize_conversation_format(existing.get("format", ConversationFormat.GENERIC.value)).value,
	}
	idx["entries"] = list(entries.values())
	_write_index(idx)
	return conv_id


def rename_conversation(conv_id: str, new_name: str) -> bool:
	path = get_conversation_path(conv_id)
	if not os.path.exists(path):
		return False
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
		# Translators: Text in conversation metadata/properties shown to the user.
		data["name"] = new_name.strip() or _("Untitled conversation")
		data["updated"] = int(time.time())
		_atomic_write_json(path, data)
		idx = _read_index()
		for e in idx.get("entries", []):
			if e.get("id") == conv_id:
				e["name"] = data["name"]
				e["updated"] = data["updated"]
				break
		_write_index(idx)
		return True
	except Exception as err:
		log.error(f"conversations: rename {conv_id}: {err}", exc_info=True)
		return False


def delete_conversation(conv_id: str) -> bool:
	path = get_conversation_path(conv_id)
	if not os.path.exists(path):
		return False
	try:
		try:
			with open(path, "r", encoding="utf-8") as f:
				data = json.load(f)
		except Exception:
			data = {}
		to_delete_candidates = _collect_referenced_local_paths(data, conv_id=conv_id)
		other_refs = set()
		if to_delete_candidates and os.path.isdir(CONVERSATIONS_DIR):
			for name in os.listdir(CONVERSATIONS_DIR):
				if not name.endswith(".json"):
					continue
				other_path = os.path.join(CONVERSATIONS_DIR, name)
				if os.path.abspath(other_path) == os.path.abspath(path):
					continue
				try:
					with open(other_path, "r", encoding="utf-8") as f:
						other_data = json.load(f)
					other_conv_id = str(other_data.get("id", ""))
					other_refs |= _collect_referenced_local_paths(other_data, conv_id=other_conv_id)
				except Exception:
					continue
		os.remove(path)
		idx = _read_index()
		idx["entries"] = [e for e in idx.get("entries", []) if e.get("id") != conv_id]
		_write_index(idx)
		for p in to_delete_candidates:
			if p in other_refs:
				continue
			if not _is_under_data_dir(p):
				continue
			try:
				if os.path.isfile(p):
					os.remove(p)
			except Exception:
				pass
		conv_attach_dir = os.path.join(ATTACHMENTS_DIR, conv_id)
		if os.path.isdir(conv_attach_dir):
			try:
				shutil.rmtree(conv_attach_dir, ignore_errors=True)
			except Exception:
				pass
		return True
	except Exception as err:
		log.error(f"conversations: delete {conv_id}: {err}", exc_info=True)
		return False
