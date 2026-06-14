"""Chat completion thread implementation."""
import base64
import json
import os
import re
import threading
import time
import uuid
import winsound
import wx

import addonHandler
import gui
from logHandler import log

from .apiclient import Choice, ChatCompletion, configure_client_for_provider
from .apiclient._think_tags import (
	_apply_think_chain_to_chunk,
	_flush_think_chain,
	_new_think_chain_states,
)
from .consts import (
	ContentType,
	Provider,
	ReasoningEffort,
	Role,
	SND_CHAT_RESPONSE_PENDING,
	SND_CHAT_RESPONSE_SENT,
	SND_PROGRESS,
	TEMP_DIR,
	TOP_P_MAX,
	TOP_P_MIN,
	TTS_DEFAULT_VOICE,
	stop_progress_sound,
)
from .history import HistoryBlock
from .mediastore import persist_local_file
from .recordthread import transcribe_audio_file
from .reasoningrequest import (
	apply_reasoning_disabled,
	apply_reasoning_enabled,
)
from .resultevent import ResultEvent

addonHandler.initTranslation()

# Speak streamed chunks this often even without newline/sentence punctuation (models often stream long clauses).
_STREAM_SPEECH_FLUSH_CHARS = 96

# Natural points to break streamed text into a spoken utterance:
#   - End-of-sentence punctuation (.!?…) or clause separators (:;), optionally
#     followed by a closing quote/bracket, when followed by whitespace.
#   - Any newline character.
# We match anywhere inside the buffer (not just the end), so a chunk that arrives
# as "Hello. How are you" still flushes "Hello. " and keeps "How are you" buffered.
_PHRASE_BOUNDARY_RE = re.compile(
	r"""
	(?: [.!?…:;] [\"'\)\]]* (?=[ \t\r\n]) )  # terminator + optional closer + whitespace
	| \n                                      # any newline
	""",
	re.VERBOSE,
)


def _last_phrase_boundary(buffer: str) -> int:
	"""Index of the last natural phrase break in ``buffer`` (split point), or -1."""
	last = -1
	for m in _PHRASE_BOUNDARY_RE.finditer(buffer):
		last = m.end()
	return last

# Providers that follow OpenAI's stream_options={include_usage:true} convention
# for surfacing usage on the final stream chunk.
_STREAM_USAGE_PROVIDERS = (
	Provider.OpenAI,
	Provider.CustomOpenAI,
	Provider.OpenRouter,
	Provider.MistralAI,
	Provider.DeepSeek,
	Provider.Google,
	Provider.Ollama,
)

# Providers that cap stop sequences at 4 (per OpenAI chat-completions docs);
# every other provider supports the more generous Anthropic 16-sequence cap.
_STOP_SEQUENCE_CAP_4_PROVIDERS = (Provider.OpenAI, Provider.CustomOpenAI)

# OpenRouter server tool: https://openrouter.ai/docs/guides/features/server-tools/web-search
_OPENROUTER_WEB_SEARCH_TOOL = {"type": "openrouter:web_search"}
# xAI built-in tools (Responses API): https://docs.x.ai/developers/tools/overview
_XAI_CODE_INTERPRETER_TOOL = {"type": "code_interpreter"}


def _set_builtin_tool(params: dict, tool: dict) -> None:
	"""Replace any existing tool with the same ``type`` and append ``tool``."""
	ttype = tool.get("type")
	tools = [
		t for t in (params.get("tools") or [])
		if not (isinstance(t, dict) and t.get("type") == ttype)
	]
	tools.append(dict(tool))
	params["tools"] = tools


def _append_tool(params: dict, tool: dict) -> None:
	tools = list(params.get("tools") or [])
	ttype = tool.get("type")
	if ttype and any(isinstance(t, dict) and t.get("type") == ttype for t in tools):
		return
	tools.append(dict(tool))
	params["tools"] = tools


def _apply_web_search_settings(params: dict, model, wnd, provider: str) -> None:
	"""Apply provider-native and/or OpenRouter universal web search to the request."""
	native_on = (
		model.supports_web_search
		and hasattr(wnd, "webSearchCheckBox")
		and wnd.webSearchCheckBox.IsChecked()
	)
	if native_on:
		if provider == Provider.Anthropic:
			params["web_search_options"] = {}
		elif provider == Provider.Google:
			# Google Search grounding uses the native generateContent API; see
			# apiclient._google and https://ai.google.dev/gemini-api/docs/google-search
			params["web_search_options"] = {}
		elif provider in (Provider.OpenAI, Provider.OpenRouter):
			# OpenRouter passes web_search_options to the upstream provider when supported.
			params["web_search_options"] = {}
		elif provider == Provider.xAI:
			from .xaitools import build_web_search_tool_from_wnd

			_set_builtin_tool(params, build_web_search_tool_from_wnd(wnd))

	or_cb = getattr(wnd, "openRouterWebSearchCheckBox", None)
	or_on = (
		getattr(model, "supports_openrouter_web_search", False)
		and or_cb is not None
		and or_cb.IsChecked()
	)
	if or_on and provider == Provider.OpenRouter:
		_append_tool(params, _OPENROUTER_WEB_SEARCH_TOOL)


def _apply_x_search_settings(params: dict, model, wnd, provider: str) -> None:
	"""Attach xAI ``x_search`` (X/Twitter) when the user enables it."""
	if provider != Provider.xAI:
		return
	if not getattr(model, "supports_x_search", False):
		return
	cb = getattr(wnd, "xSearchCheckBox", None)
	if cb is None or not cb.IsChecked():
		return
	from .xaitools import build_x_search_tool_from_wnd

	_set_builtin_tool(params, build_x_search_tool_from_wnd(wnd))


def _apply_code_interpreter_settings(params: dict, model, wnd, provider: str) -> None:
	"""Attach xAI ``code_interpreter`` when enabled."""
	if provider != Provider.xAI:
		return
	if not getattr(model, "supports_code_interpreter", False):
		return
	cb = getattr(wnd, "codeInterpreterCheckBox", None)
	if cb is None or not cb.IsChecked():
		return
	_set_builtin_tool(params, _XAI_CODE_INTERPRETER_TOOL)


def _apply_collections_search_settings(params: dict, model, wnd, provider: str) -> None:
	"""Attach xAI ``collections_search`` when enabled and collection ids are set."""
	if provider != Provider.xAI:
		return
	if not getattr(model, "supports_collections_search", False):
		return
	cb = getattr(wnd, "collectionsSearchCheckBox", None)
	if cb is None or not cb.IsChecked():
		return
	from .xaitools import build_collections_search_tool_from_wnd

	tool = build_collections_search_tool_from_wnd(wnd)
	if tool is not None:
		_set_builtin_tool(params, tool)


def _apply_xai_include_settings(
	params: dict,
	model,
	wnd,
	provider: str,
	reasoning_enabled: bool,
) -> None:
	"""Set xAI Responses ``include`` only for encrypted reasoning.

	Inline citations are returned by default on the Responses API; requesting
	``inline_citations`` via ``include`` is rejected by xAI (``Argument not
	supported: include``).
	"""
	if provider != Provider.xAI:
		return
	from .xaitools import xai_encrypted_reasoning_requested

	if xai_encrypted_reasoning_requested(wnd, reasoning_enabled):
		params["include"] = ["reasoning.encrypted_content"]
	else:
		params.pop("include", None)


def _messages_for_xai_responses(wnd, messages: list, is_regenerate: bool) -> list:
	"""Use ``previous_response_id`` state: send only the new user turn when possible."""
	if is_regenerate:
		return messages
	anchor = getattr(wnd, "lastBlock", None)
	prev_id = getattr(anchor, "xaiResponseId", None) if anchor is not None else None
	if not isinstance(prev_id, str) or not prev_id.strip():
		return messages
	system_msgs = [
		m for m in messages
		if isinstance(m, dict) and str(m.get("role", "")).lower() == "system"
	]
	user_msgs = [
		m for m in messages
		if isinstance(m, dict) and str(m.get("role", "")).lower() == "user"
	]
	if not user_msgs:
		return messages
	return system_msgs + [user_msgs[-1]]


def _apply_xai_previous_response_id(params: dict, wnd, is_regenerate: bool) -> None:
	if is_regenerate:
		return
	anchor = getattr(wnd, "lastBlock", None)
	prev_id = getattr(anchor, "xaiResponseId", None) if anchor is not None else None
	if isinstance(prev_id, str) and prev_id.strip():
		params["previous_response_id"] = prev_id.strip()


def _format_xai_citations_footer(citations: list, response_text: str) -> str:
	"""Append a sources list when the API returned citations not already inline."""
	if not citations:
		return ""
	text = response_text or ""
	unique = []
	seen: set[str] = set()
	for url in citations:
		if not isinstance(url, str) or not url.strip():
			continue
		u = url.strip()
		if u in seen:
			continue
		seen.add(u)
		if u in text:
			continue
		unique.append(u)
	if not unique:
		return ""
	lines = ["", "---", _("Sources:")]
	for idx, url in enumerate(unique, start=1):
		lines.append(f"{idx}. {url}")
	return "\n".join(lines)


def _apply_xai_response_metadata(block, response, response_text: str = "") -> str:
	"""Store xAI Responses metadata on ``block``; return optional footer text."""
	rid = getattr(response, "response_id", None)
	if isinstance(rid, str) and rid.strip():
		block.xaiResponseId = rid.strip()
	encrypted = getattr(response, "encrypted_reasoning", None)
	if isinstance(encrypted, list) and encrypted:
		block.xaiEncryptedReasoning = list(encrypted)
	citations = getattr(response, "citations", None) or []
	if citations:
		block.citations = list(citations)
		return _format_xai_citations_footer(citations, response_text)
	return ""


def _strip_markdown_for_speech(fragment: str) -> str:
	"""Remove common markdown syntax so streamed TTS does not read *, #, etc. Chunks may be incomplete."""
	if not fragment:
		return ""
	t = fragment
	t = re.sub(r"(^|\n)[ \t]*#{1,6}(?:[ \t]+|$)", r"\1", t)
	t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
	t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
	for _ in range(4):
		t = t.replace("***", "").replace("**", "")
	while "__" in t:
		t = t.replace("__", "")
	while "```" in t:
		t = t.replace("```", "")
	t = t.replace("`", "")
	t = re.sub(r"(?<!\*)\*(?!\*)", "", t)
	t = re.sub(r"\s+", " ", t).strip()
	return t


def _parse_stop_sequences(text: str, *, provider) -> list[str]:
	"""Non-empty lines from multiline stop UI; cap follows common provider limits (OpenAI chat: 4)."""
	seq = []
	for line in (text or "").replace("\r\n", "\n").split("\n"):
		s = line.strip()
		if s:
			seq.append(s)
	cap = 4 if provider in _STOP_SEQUENCE_CAP_4_PROVIDERS else 16
	return seq[:cap]


def _params_for_error_log(params):
	"""Subset of chat completion params suitable for NVDA logs (no prompt / message bodies)."""
	if not isinstance(params, dict):
		return params
	out = {k: v for k, v in params.items() if k != "messages"}
	msgs = params.get("messages")
	if isinstance(msgs, list):
		out["messages"] = "<%d message(s) omitted>" % len(msgs)
	else:
		out["messages"] = "<omitted>"
	return out


class CompletionThread(threading.Thread):
	def __init__(self, notifyWindow):
		threading.Thread.__init__(self, daemon=True)
		self._notifyWindow = notifyWindow
		self._wantAbort = False
		self.lastTime = int(time.time())

	def _log_timing(self, debug, label, elapsed):
		if debug and elapsed is not None:
			log.info("OpenAI [timing] %s: %.2fs", label, elapsed)

	def _configureReasoning(self, params: dict, model, conf, checkbox_enabled: bool) -> bool:
		"""Add reasoning-related params for the given provider/model.

		Returns True when reasoning is being requested (so the caller can
		choose ``max_completion_tokens`` over ``max_tokens``).

		Honoring the "Reasoning enabled" checkbox is provider-specific: most
		APIs default to reasoning ON when the model supports it, so simply
		omitting params would still bill us for reasoning tokens. We send the
		appropriate disable signal whenever the official API exposes one.
		See ``reasoningrequest`` for per-provider rules and doc links.
		"""
		model_supports_reasoning = bool(getattr(model, "reasoning", False))
		if getattr(model, "reasoning_always_on", False):
			checkbox_enabled = True
		use_reasoning = model_supports_reasoning and checkbox_enabled
		provider = model.provider
		effort = conf.get("reasoningEffort", ReasoningEffort.MEDIUM.value)
		if use_reasoning:
			apply_reasoning_enabled(params, model, provider, effort, conf)
		else:
			apply_reasoning_disabled(params, model, provider)
		return use_reasoning

	def _usage_for_block(self, usage):
		"""Normalize usage dict into the persisted HistoryBlock usage shape."""
		if not isinstance(usage, dict) or not usage:
			return None
		def _to_int(value):
			try:
				return int(value or 0)
			except (TypeError, ValueError):
				return 0
		normalized = {
			"input_tokens": _to_int(usage.get("input_tokens")),
			"output_tokens": _to_int(usage.get("output_tokens")),
			"total_tokens": _to_int(usage.get("total_tokens")),
			"prompt_tokens": _to_int(usage.get("prompt_tokens")),
			"completion_tokens": _to_int(usage.get("completion_tokens")),
			"reasoning_tokens": _to_int(usage.get("reasoning_tokens")),
			"cached_input_tokens": _to_int(usage.get("cached_input_tokens")),
			"cache_creation_input_tokens": _to_int(usage.get("cache_creation_input_tokens")),
			"input_audio_tokens": _to_int(usage.get("input_audio_tokens")),
			"output_audio_tokens": _to_int(usage.get("output_audio_tokens")),
		}
		if "cost" in usage:
			try:
				normalized["cost"] = float(usage.get("cost"))
			except (TypeError, ValueError):
				pass
		if normalized.get("total_tokens", 0) <= 0:
			in_tok = normalized.get("input_tokens", 0) or normalized.get("prompt_tokens", 0)
			out_tok = normalized.get("output_tokens", 0) or normalized.get("completion_tokens", 0)
			if in_tok or out_tok:
				normalized["total_tokens"] = int(in_tok) + int(out_tok)
		return normalized

	def _log_usage_debug(self, debug, source, raw_usage, normalized_usage):
		"""Debug-only snapshot of raw and normalized usage payloads."""
		if not debug:
			return
		try:
			log.info(
				"OpenAI [usage] %s raw=%s normalized=%s",
				source,
				json.dumps(raw_usage or {}, ensure_ascii=False, sort_keys=True),
				json.dumps(normalized_usage or {}, ensure_ascii=False, sort_keys=True),
			)
		except Exception:
			log.info("OpenAI [usage] %s raw=%r normalized=%r", source, raw_usage, normalized_usage)

	def _set_block_usage_from_response(self, block, response, debug=False):
		"""Copy normalized usage fields from response to HistoryBlock."""
		raw_usage = getattr(response, "usage", None)
		normalized = self._usage_for_block(raw_usage)
		if normalized:
			block.usage = normalized
		self._log_usage_debug(debug, "non-stream response", raw_usage, normalized)

	def _apply_pricing_if_missing(self, block, model):
		usage = getattr(block, "usage", None)
		if not isinstance(usage, dict):
			return
		if isinstance(usage.get("cost"), (int, float)):
			return
		pricing = {}
		if getattr(model, "extraInfo", None) and isinstance(model.extraInfo, dict):
			pricing = model.extraInfo.get("pricing", {})
		if not isinstance(pricing, dict) or not pricing:
			return
		def _to_float(value):
			try:
				return float(value or 0.0)
			except (TypeError, ValueError):
				return 0.0
		def _to_int(value):
			try:
				return int(value or 0)
			except (TypeError, ValueError):
				return 0
		input_tokens = _to_int(usage.get("input_tokens"))
		output_tokens = _to_int(usage.get("output_tokens"))
		cached_read_tokens = _to_int(usage.get("cached_input_tokens"))
		cache_write_tokens = _to_int(usage.get("cache_creation_input_tokens"))
		audio_tokens = _to_int(usage.get("input_audio_tokens")) + _to_int(usage.get("output_audio_tokens"))
		regular_input_tokens = max(0, input_tokens - cached_read_tokens - cache_write_tokens)
		prompt_rate = _to_float(pricing.get("prompt"))
		completion_rate = _to_float(pricing.get("completion"))
		cache_read_rate = _to_float(pricing.get("input_cache_read"))
		cache_write_rate = _to_float(pricing.get("input_cache_write"))
		audio_rate = _to_float(pricing.get("audio"))
		request_rate = _to_float(pricing.get("request"))
		cost = (
			(regular_input_tokens * prompt_rate)
			+ (output_tokens * completion_rate)
			+ (cached_read_tokens * cache_read_rate)
			+ (cache_write_tokens * cache_write_rate)
			+ (audio_tokens * audio_rate)
			+ request_rate
		)
		if cost > 0:
			usage["cost"] = float(cost)

	def _record_usage_in_ledger(self, wnd, block, model, *, kind):
		"""Append this API call to the tab's append-only usage ledger."""
		from .usage_ledger import append_usage_event, ensure_block_uid

		page = getattr(wnd, "_worker_page", None)
		if page is None:
			return
		ledger = getattr(page, "usageLedger", None)
		if not isinstance(ledger, list):
			page.usageLedger = []
			ledger = page.usageLedger
		usage = getattr(block, "usage", None)
		if not isinstance(usage, dict):
			return
		finished_at = None
		timing = getattr(block, "timing", None)
		if isinstance(timing, dict):
			finished_at = timing.get("finishedAt")
		append_usage_event(
			ledger,
			usage=usage,
			model=getattr(model, "id", "") or getattr(block, "model", ""),
			kind=kind,
			block_id=ensure_block_uid(block),
			at=finished_at,
		)

	def _maybe_build_document_support_error(self, err, provider, document_count):
		"""Return a clearer provider-specific document failure message when possible."""
		if document_count <= 0:
			return None
		base_message = str(getattr(err, "message", "") or err or "")
		if not base_message:
			return None
		lower = base_message.lower()
		indicators = (
			"input_file",
			"document",
			"pdf",
			"unsupported",
			"not support",
			"invalid type",
			"invalid_request_error",
			"media_type",
			"file_id",
			"file_data",
		)
		if not any(flag in lower for flag in indicators):
			return None
		if provider == Provider.Anthropic:
			# Translators: Text in chat completion status and error messages.
			hint = _(
				"Anthropic accepts PDF as native document blocks and inlines plain-text files. "
				"Other formats (.docx, .xlsx, .csv, …) must be converted to PDF or plain text."
			)
		elif provider == Provider.Google:
			# Translators: Text in chat completion status and error messages.
			hint = _(
				"Google accepts images and PDFs as native inline attachments on the Gemini API. "
				"Plain-text files are inlined automatically."
			)
		elif provider == Provider.OpenRouter:
			# Translators: Text in chat completion status and error messages.
			hint = _(
				"OpenRouter parses PDFs into the underlying model. "
				"If this file was rejected, the underlying model may not support documents — try another model."
			)
		elif provider == Provider.MistralAI:
			# Translators: Text in chat completion status and error messages.
			hint = _(
				"Mistral expects documents via the document_url shape (URL or data URI). "
				"For OCR of scanned files, use the Mistral OCR tool."
			)
		elif provider == Provider.OpenAI:
			# Translators: Text in chat completion status and error messages.
			hint = _(
				"The selected OpenAI model rejected this document. "
				"Try another OpenAI model with file-input support or use a supported document format."
			)
		else:
			# Translators: Text in chat completion status and error messages.
			hint = _(
				"The selected provider does not natively accept binary documents on its chat endpoint. "
				"Plain-text files are inlined automatically; for PDFs use OpenAI, Anthropic, OpenRouter, Mistral, or xAI."
			)
		return f"{base_message}\n\n{hint}"

	def run(self):
		wnd = self._notifyWindow
		client = wnd.client
		conf = wnd.conf
		data = wnd.data
		page = getattr(wnd, "_worker_page", None)
		regenerate_block = getattr(page, "_regenerateBlock", None) if page else None
		is_regenerate = regenerate_block is not None
		if is_regenerate and page is not None:
			page._regenerateBlock = None

		if is_regenerate:
			block = regenerate_block
			system = (getattr(block, "system", "") or wnd.systemTextCtrl.GetValue()).strip()
			block.system = system
			prompt = (block.prompt or "").strip()
			block.responseText = ""
			block.reasoningText = ""
			block.responseTerminated = False
			block.displayHeader = False
			block.lastLen = 0
			block.lastReasoningLen = 0
			block.usage = None
			block.timing = {"startedAt": time.time()}
		else:
			block = HistoryBlock()
			system = wnd.systemTextCtrl.GetValue().strip()
			block.system = system
			prompt = getattr(wnd, "_askPromptOverride", None)
			if prompt is not None:
				delattr(wnd, "_askPromptOverride")
			else:
				prompt = wnd.promptTextCtrl.GetValue().strip()
			block.prompt = prompt
			block.timing = {"startedAt": time.time()}
		model = wnd.getCurrentModel()
		block.model = model.id
		stream = wnd.streamModeCheckBox.IsChecked()
		conf["stream"] = stream
		debug = wnd.debugModeCheckBox.IsChecked()
		conf["debug"] = debug
		t0 = time.perf_counter()
		maxTokens = wnd.maxTokensSpinCtrl.GetValue()
		block.maxTokens = maxTokens
		data["maxTokens_%s" % model.id] = maxTokens
		temperature = 1
		topP = 1
		_adv_fn = getattr(wnd, "_effective_advanced_mode", None)
		_advanced_on = _adv_fn() if callable(_adv_fn) else False
		if _advanced_on:
			temperature = wnd.temperatureSpinCtrl.GetValue() / 100
			data["temperature_%s" % model.id] = wnd.temperatureSpinCtrl.GetValue()
			topP = wnd.topPSpinCtrl.GetValue() / 100
			conf["topP"] = wnd.topPSpinCtrl.GetValue()
		block.temperature = temperature
		block.topP = topP
		if not is_regenerate:
			block.filesList = wnd.filesList.copy()
			block.audioPathList = wnd.audioPathList.copy()
		else:
			if block.filesList is None:
				block.filesList = []
			if block.audioPathList is None:
				block.audioPathList = []

		current_audio_transcripts = None
		audio_source = block.audioPathList if is_regenerate else wnd.audioPathList
		if audio_source:
			# Translators: Text in chat completion status and error messages.
			wnd.message(_("Transcribing audio..."))
			try:
				t_transcribe_start = time.perf_counter()
				transcripts = []
				for path in audio_source:
					path_str = path if isinstance(path, str) else getattr(path, "path", str(path))
					t = transcribe_audio_file(path_str, wnd.conf["audio"], wnd.client)
					transcripts.append((t or "").strip())
				self._log_timing(debug, "transcription", time.perf_counter() - t_transcribe_start)
				block.audioTranscriptList = transcripts
				current_audio_transcripts = transcripts
				combined_txt = "\n".join(t for t in transcripts if t).strip()
				if combined_txt and not is_regenerate:
					try:
						wx.CallAfter(wnd._merge_audio_transcripts_into_prompt, combined_txt)
					except Exception:
						pass
				if not prompt and any(t for t in transcripts):
					block.prompt = "\n".join(t for t in transcripts if t).strip()
					prompt = block.prompt
			except Exception as err:
				log.error(f"Transcription error: {err}", exc_info=True)
				stop_progress_sound()
				wx.PostEvent(self._notifyWindow, ResultEvent(err))
				return

		if not 0 <= temperature <= model.maxTemperature * 100:
			# Translators: Text in chat completion status and error messages.
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid temperature")))
			return
		if not TOP_P_MIN <= topP <= TOP_P_MAX:
			# Translators: Text in chat completion status and error messages.
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid top P")))
			return
		t_build_start = time.perf_counter()
		wnd._historyUntilBlock = regenerate_block if is_regenerate else None
		try:
			messages = self._getMessages(system, prompt, current_audio_transcripts)
		finally:
			wnd._historyUntilBlock = None
		self._log_timing(debug, "build messages (incl. history)", time.perf_counter() - t_build_start)
		nbImages = 0
		nbAudio = 0
		nbDocuments = 0
		for message in messages:
			if message["role"] == Role.USER and not isinstance(message["content"], str):
				for c in message["content"]:
					ctype = c.get("type")
					if ctype == ContentType.IMAGE_URL:
						nbImages += 1
					elif ctype == ContentType.INPUT_AUDIO:
						nbAudio += 1
					elif ctype == ContentType.INPUT_FILE:
						nbDocuments += 1
		# Translators: Text in chat completion status and error messages.
		msg = _("Uploading %s, please wait...") % ", ".join(
			# Translators: Text in chat completion status and error messages.
			([_("%d image(s)") % nbImages] if nbImages else []) +
			# Translators: Text in chat completion status and error messages.
			([_("%d audio file(s)") % nbAudio] if nbAudio else []) +
			# Translators: Text in chat completion status and error messages.
			([_("%d document(s)") % nbDocuments] if nbDocuments else [])
		# Translators: Text in chat completion status and error messages.
		) if nbImages or nbAudio or nbDocuments else _("Please wait...")
		conf["modelVision" if nbImages else "model"] = model.id
		wnd.message(msg)
		if conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		account = wnd.getCurrentAccount() if hasattr(wnd, "getCurrentAccount") else None
		account_id = account.get("id") if account and account.get("provider") == model.provider else None
		client = configure_client_for_provider(client, model.provider, account_id=account_id, clone=True)
		audio_output = getattr(model, "audioOutput", False)
		use_stream = stream and not audio_output
		params = {
			"model": model.id,
			"messages": messages,
			"stream": use_stream
		}
		if use_stream and model.provider in _STREAM_USAGE_PROVIDERS:
			params["stream_options"] = {"include_usage": True}
		if audio_output or nbAudio > 0:
			voice = conf.get("TTSVoice") or TTS_DEFAULT_VOICE
			params["modalities"] = ["text", "audio"]
			params["audio"] = {"voice": voice, "format": "wav"}
		params_to_add = []
		if "temperature" in model.supportedParameters:
			params_to_add.append(("temperature", temperature))
		if "top_p" in model.supportedParameters:
			params_to_add.append(("top_p", topP))
		if _advanced_on and "top_k" in model.supportedParameters and hasattr(wnd, "advancedTopKSpinCtrl"):
			_tk = wnd.advancedTopKSpinCtrl.GetValue()
			if _tk > 0:
				params_to_add.append(("top_k", int(_tk)))
		conflicts = getattr(model, "parameterConflicts", []) or []
		for group in conflicts:
			group_set = set(group)
			candidates = [(k, v) for k, v in params_to_add if k in group_set]
			if len(candidates) > 1:
				order = ("temperature", "top_p", "top_k")
				chosen = next((c for p in order for c in candidates if c[0] == p), candidates[0])
				params_to_add = [(k, v) for k, v in params_to_add if (k, v) == chosen or k not in group_set]
		for k, v in params_to_add:
			params[k] = v
			if k == "top_k":
				data["top_k_%s" % model.id] = v
		if _advanced_on:
			if "seed" in model.supportedParameters and hasattr(wnd, "advancedSeedSpinCtrl"):
				sv = wnd.advancedSeedSpinCtrl.GetValue()
				if sv >= 0:
					params["seed"] = int(sv)
					data["seed_%s" % model.id] = int(sv)
			if "stop" in model.supportedParameters and hasattr(wnd, "advancedStopTextCtrl"):
				stops = _parse_stop_sequences(
					wnd.advancedStopTextCtrl.GetValue(),
					provider=model.provider,
				)
				if stops:
					params["stop"] = stops
					data["stop_%s" % model.id] = wnd.advancedStopTextCtrl.GetValue()
			if "frequency_penalty" in model.supportedParameters and hasattr(wnd, "advancedFreqPenaltySpinCtrl"):
				fp = wnd.advancedFreqPenaltySpinCtrl.GetValue() / 100.0
				params["frequency_penalty"] = fp
				data["frequency_penalty_%s" % model.id] = wnd.advancedFreqPenaltySpinCtrl.GetValue()
			if "presence_penalty" in model.supportedParameters and hasattr(wnd, "advancedPresPenaltySpinCtrl"):
				pp = wnd.advancedPresPenaltySpinCtrl.GetValue() / 100.0
				params["presence_penalty"] = pp
				data["presence_penalty_%s" % model.id] = wnd.advancedPresPenaltySpinCtrl.GetValue()
		reasoningEnabled = wnd.reasoningModeCheckBox.IsChecked()
		useReasoning = self._configureReasoning(params, model, conf, reasoningEnabled)
		if maxTokens > 0:
			params["max_completion_tokens" if useReasoning else "max_tokens"] = maxTokens
		# Resolve the provider once up-front: it's needed both for provider-specific
		# request shaping below AND for the error path further down (so it must be
		# defined regardless of which optional branches fire).
		provider = model.provider
		_apply_web_search_settings(params, model, wnd, provider)
		_apply_x_search_settings(params, model, wnd, provider)
		_apply_code_interpreter_settings(params, model, wnd, provider)
		_apply_collections_search_settings(params, model, wnd, provider)
		if provider == Provider.xAI:
			_apply_xai_include_settings(params, model, wnd, provider, useReasoning)
			_apply_xai_previous_response_id(params, wnd, is_regenerate)
			from .xaitools import collect_xai_encrypted_reasoning_input

			use_prev = bool(params.get("previous_response_id"))
			enc_input = collect_xai_encrypted_reasoning_input(wnd, is_regenerate, use_prev)
			if enc_input:
				params["xai_encrypted_reasoning_input"] = enc_input
			messages = _messages_for_xai_responses(wnd, messages, is_regenerate)
			params["messages"] = messages
		if debug:
			log.info("Client base URL: %s", client.base_url)
			log.info("OpenAI [timing] Messages in request: %d", len(messages))
			if nbImages:
				log.info("%d images", nbImages)
			log.info(json.dumps(_params_for_error_log(params), indent=2, ensure_ascii=False))
		try:
			t_api_start = time.perf_counter()
			block.timing["requestSentAt"] = time.time()
			response = client.chat.completions.create(**params)
			self._log_timing(debug, "API call", time.perf_counter() - t_api_start)
			block.timing["responseReceivedAt"] = time.time()
			if conf["chatFeedback"]["sndResponseSent"]:
				winsound.PlaySound(SND_CHAT_RESPONSE_SENT, winsound.SND_ASYNC)
		except Exception as err:
			log.error("Error when calling the API for model %s: %s", model.id, err, exc_info=True)
			log.error("Parameters used (messages omitted): %s", _params_for_error_log(params))
			stop_progress_sound()
			doc_error = self._maybe_build_document_support_error(err, provider, nbDocuments)
			wx.PostEvent(self._notifyWindow, ResultEvent(doc_error if doc_error else err))
			return
		if not is_regenerate:
			if wnd.lastBlock is None:
				wnd.firstBlock = wnd.lastBlock = block
			else:
				wnd.lastBlock.next = block
				block.previous = wnd.lastBlock
				wnd.lastBlock = block
			wnd.previousPrompt = wnd.promptTextCtrl.GetValue()
			wnd.promptTextCtrl.Clear()
		else:
			wnd.lastBlock = block
			if wnd.firstBlock is None:
				wnd.firstBlock = block
		try:
			t_resp_start = time.perf_counter()
			if use_stream:
				self._responseWithStream(response, block, debug)
			else:
				self._responseWithoutStream(response, block, debug)
			self._apply_pricing_if_missing(block, model)
			from .usage_ledger import USAGE_KIND_ABORTED, USAGE_KIND_COMPLETION
			aborted = self._wantAbort or wnd.stopRequest.is_set()
			self._record_usage_in_ledger(
				wnd,
				block,
				model,
				kind=USAGE_KIND_ABORTED if aborted else USAGE_KIND_COMPLETION,
			)
			self._log_timing(debug, "response processing", time.perf_counter() - t_resp_start)
		except Exception as err:
			log.error("Error processing response for model %s: %s", model.id, err, exc_info=True)
			stop_progress_sound()
			wx.PostEvent(self._notifyWindow, ResultEvent(err))
			return
		total = time.perf_counter() - t0
		block.timing["finishedAt"] = time.time()
		block.timing["elapsedSec"] = round(total, 3)
		self._finalize_timing_metrics(block)
		self._log_timing(debug, "total", total)
		if debug and total > 10:
			log.info("OpenAI [timing] Request took %.1fs. If 'history' is dominant, reduce conversation length.", total)
		wnd.filesList.clear()
		wnd.audioPathList.clear()
		wx.PostEvent(self._notifyWindow, ResultEvent())

	def _getMessages(self, system=None, prompt=None, current_audio_transcripts=None):
		wnd = self._notifyWindow
		debug = wnd.conf.get("debug", False)
		messages = []
		if system:
			messages.append({"role": Role.SYSTEM, "content": system})
		until_block = getattr(wnd, "_historyUntilBlock", None)
		t_hist = time.perf_counter()
		if until_block is not None:
			wnd.getMessages(messages, until_block=until_block)
			self._log_timing(debug, "  history (prior blocks)", time.perf_counter() - t_hist)
			return messages
		wnd.getMessages(messages)
		self._log_timing(debug, "  history (prior blocks)", time.perf_counter() - t_hist)
		t_cur = time.perf_counter()
		content_parts = []
		if prompt:
			content_parts.append({"type": ContentType.TEXT, "text": prompt})
		if wnd.filesList:
			content_parts.extend(wnd.getFilesContent(prompt=None))
		if wnd.audioPathList:
			if current_audio_transcripts and any(t for t in current_audio_transcripts):
				for t in current_audio_transcripts:
					if t:
						content_parts.append({"type": ContentType.TEXT, "text": t})
			else:
				content_parts.extend(wnd.getAudioContent(prompt=None))
		self._log_timing(debug, "  current message (images/audio)", time.perf_counter() - t_cur)
		if content_parts:
			messages.append({"role": Role.USER, "content": content_parts})
		elif prompt:
			messages.append({"role": Role.USER, "content": prompt})
		return messages

	def abort(self):
		self._wantAbort = True

	def stop(self):
		"""Same as ``abort``; matches ``RecordThread.stop`` for shared shutdown code."""
		self.abort()

	def _finalize_timing_metrics(self, block):
		timing = getattr(block, "timing", None)
		if not isinstance(timing, dict):
			return

		def _as_float(value):
			try:
				return float(value)
			except (TypeError, ValueError):
				return None

		started = _as_float(timing.get("startedAt"))
		request_sent = _as_float(timing.get("requestSentAt"))
		first_token = _as_float(timing.get("firstTokenAt"))
		finished = _as_float(timing.get("finishedAt"))
		if request_sent is not None and started is not None and request_sent >= started:
			timing["timeToRequestSentSec"] = round(request_sent - started, 3)
		if first_token is not None and request_sent is not None and first_token >= request_sent:
			timing["timeToFirstTokenSec"] = round(first_token - request_sent, 3)
		if finished is not None and request_sent is not None and finished >= request_sent:
			timing["timeFromRequestSentToEndSec"] = round(finished - request_sent, 3)
		if finished is not None and first_token is not None and finished >= first_token:
			generation_sec = finished - first_token
			timing["generationDurationSec"] = round(generation_sec, 3)
			usage = getattr(block, "usage", None)
			if isinstance(usage, dict) and generation_sec > 0:
				def _to_int(value):
					try:
						return int(value or 0)
					except (TypeError, ValueError):
						return 0
				output_tokens = _to_int(usage.get("output_tokens")) or _to_int(usage.get("completion_tokens"))
				total_tokens = _to_int(usage.get("total_tokens"))
				if output_tokens > 0:
					timing["outputTokensPerSec"] = round(output_tokens / generation_sec, 3)
				if total_tokens > 0:
					timing["totalTokensPerSec"] = round(total_tokens / generation_sec, 3)

	def _responseWithStream(self, response, block, debug=False):
		wnd = self._notifyWindow
		speechBuffer = ""
		latest_usage = None
		first_speech_emitted = False
		think_states = _new_think_chain_states()

		def _emit_speech(text: str) -> bool:
			"""Speak ``text`` if streaming speech is currently allowed.

			Silently no-ops (returns False) when the dialog isn't focused or the
			text reduces to nothing after markdown stripping. Sets
			``first_speech_emitted`` only on a real spoken utterance.
			"""
			nonlocal first_speech_emitted
			if not text:
				return False
			if not (hasattr(wnd, "canAutoReadStreamingResponse") and wnd.canAutoReadStreamingResponse()):
				return False
			speech_plain = _strip_markdown_for_speech(text)
			if not speech_plain:
				return False
			wnd.message(speech_plain, speechOnly=True, onPromptFieldOnly=False)
			first_speech_emitted = True
			return True

		def _decide_speech_cut() -> int:
			"""Return the buffer index up to which we should speak now, or 0 if not yet.

			Priority:
			  1. Last completed phrase / newline anywhere in the buffer.
			  2. Hard length cap reached -> cut at the last whitespace so we don't
			     split a word.
			  3. First-token early flush -> cut at the last whitespace (or speak
			     the whole short buffer once it's at least ``early_min_chars``).
			"""
			cut = _last_phrase_boundary(speechBuffer)
			if cut > 0:
				return cut
			if len(speechBuffer) >= _STREAM_SPEECH_FLUSH_CHARS:
				ws = max(speechBuffer.rfind(" "), speechBuffer.rfind("\t"))
				if ws > 0:
					return ws + 1
			if not first_speech_emitted and speechBuffer.strip():
				ws = max(speechBuffer.rfind(" "), speechBuffer.rfind("\n"))
				if ws > 0:
					return ws + 1
				if len(speechBuffer) >= 16:
					return len(speechBuffer)
			return 0

		for event in response:
			if time.time() - self.lastTime > 4:
				self.lastTime = int(time.time())
				if wnd.conf["chatFeedback"]["sndResponsePending"]:
					winsound.PlaySound(SND_CHAT_RESPONSE_PENDING, winsound.SND_ASYNC)
			if self._wantAbort or wnd.stopRequest.is_set():
				break
			usage = getattr(event, "usage", None)
			if isinstance(usage, dict) and usage:
				latest_usage = usage
			rid = getattr(event, "response_id", None)
			if isinstance(rid, str) and rid.strip():
				block.xaiResponseId = rid.strip()
			event_citations = getattr(event, "citations", None)
			if event_citations:
				block.citations = list(event_citations)
			event_encrypted = getattr(event, "encrypted_reasoning", None)
			if isinstance(event_encrypted, list) and event_encrypted:
				block.xaiEncryptedReasoning = list(event_encrypted)
			choices = getattr(event, "choices", None)
			if not choices:
				continue
			choice = choices[0]
			delta = getattr(choice, "delta", None)
			reasoning_chunk = getattr(delta, "reasoning", "") if delta else ""
			content_chunk = getattr(delta, "content", "") if delta else ""
			# Always update reasoning before content within a single event so the order in
			# which they appear in HistoryBlock matches what the server sent.
			if reasoning_chunk:
				if "firstTokenAt" not in block.timing:
					block.timing["firstTokenAt"] = time.time()
				block.reasoningText += reasoning_chunk
			if content_chunk:
				if think_states:
					content_chunk, think_from_tags = _apply_think_chain_to_chunk(content_chunk, think_states)
					if think_from_tags:
						block.reasoningText += think_from_tags
				if not content_chunk:
					continue
				if "firstTokenAt" not in block.timing:
					block.timing["firstTokenAt"] = time.time()
				speechBuffer += content_chunk
				block.responseText += content_chunk
				cut = _decide_speech_cut()
				if cut > 0:
					to_speak = speechBuffer[:cut]
					speechBuffer = speechBuffer[cut:]
					_emit_speech(to_speak)
			if getattr(choice, "finish_reason", None):
				break
		flushed_content, flushed_reasoning = _flush_think_chain(think_states)
		if flushed_reasoning:
			block.reasoningText += flushed_reasoning
		if flushed_content:
			block.responseText += flushed_content
			speechBuffer += flushed_content
		if speechBuffer:
			_emit_speech(speechBuffer)
			speechBuffer = ""
		if isinstance(latest_usage, dict) and latest_usage:
			normalized = self._usage_for_block(latest_usage)
			if normalized:
				block.usage = normalized
			self._log_usage_debug(debug, "stream final chunk", latest_usage, normalized)
		citations_footer = _format_xai_citations_footer(
			getattr(block, "citations", None) or [],
			block.responseText or "",
		)
		if citations_footer:
			block.responseText += citations_footer
			wnd.updateResponse(block, citations_footer)
		block.responseTerminated = True

	def _responseWithoutStream(self, response, block, debug=False):
		wnd = self._notifyWindow
		text = ""
		played_audio = False
		self._set_block_usage_from_response(block, response, debug=debug)
		if "firstTokenAt" not in block.timing:
			first_token_at = block.timing.get("responseReceivedAt")
			if not isinstance(first_token_at, (int, float)):
				first_token_at = time.time()
			block.timing["firstTokenAt"] = float(first_token_at)
		if isinstance(response, (Choice, ChatCompletion)):
			for choice in response.choices:
				if self._wantAbort:
					break
				msg = choice.message
				text += msg.content or ""
				block.reasoningText += getattr(msg, "reasoning", "") or ""
				audio = getattr(msg, "audio", None)
				if isinstance(audio, dict) and audio.get("data"):
					transcript = audio.get("transcript") or ""
					if transcript and not text:
						text = transcript
					try:
						data = base64.b64decode(audio["data"])
						path = os.path.join(TEMP_DIR, f"audio_response_{uuid.uuid4().hex}.wav")
						with open(path, "wb") as f:
							f.write(data)
						path = persist_local_file(path, "audio", prefix="chat_audio", fallback_ext=".wav")
						block.audioPath = path
						import gui
						wx.CallAfter(wnd._playBlockAudio, path)
						played_audio = True
					except Exception as e:
						log.error("Failed to save/play audio response: %s", e, exc_info=True)
						wx.CallAfter(
							lambda: gui.messageBox(
								# Translators: Error message when the chat response included audio but NVDA could not play or save it (details in the log).
								_("An error occurred playing the audio response. More information is in the NVDA log."),
								# Translators: Title of the error message box for chat-completion failures in AI-Hub.
								_("OpenAI Error"),
								wx.OK | wx.ICON_ERROR
							)
						)
		else:
			raise TypeError(f"Invalid response type: {type(response)}")
		citations_footer = _apply_xai_response_metadata(block, response, text)
		block.responseText += text
		if citations_footer:
			block.responseText += citations_footer
			text += citations_footer
		if not played_audio and text:
			if hasattr(wnd, "canAutoReadStreamingResponse") and wnd.canAutoReadStreamingResponse():
				speech_plain = _strip_markdown_for_speech(text)
				if speech_plain:
					wnd.message(speech_plain, speechOnly=True, onPromptFieldOnly=False)
		block.responseTerminated = True


