import base64
import ctypes
import datetime
import json
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
import winsound
import gui
import wx

import addonHandler
import api
import braille
import config
import queueHandler
import speech
import ui
from logHandler import log

from . import apikeymanager
from . import conversations
from .audiohandlers import AudioHandlersMixin
from .chatcompletion import CompletionThread
from .historyhandlers import HistoryHandlersMixin
from .filehandlers import FileHandlersMixin
from .modelhandlers_core import ModelHandlersMixin
from .consts import (
	ADDON_DIR, ADDON_LIBS_DIR, DATA_DIR, LIBS_BASE, TEMP_DIR, cleanup_temp_dir, stop_progress_sound,
	ensure_temp_dir,
	ContentType,
	Provider,
	Role,
	TranscriptionProvider,
	TOP_P_MIN, TOP_P_MAX,
	DEFAULT_SYSTEM_PROMPT,
	AUDIO_EXT_TO_FORMAT,
	SND_CHAT_RESPONSE_RECEIVED, SND_PROGRESS,
	REASONING_EFFORT_OPTIONS, DEFAULT_REASONING_EFFORT,
	UI_DIALOG_BORDER_PX,
	UI_SECTION_SPACING_PX,
)
from .history import HistoryBlock, TextSegment
from .imagehelper import encode_image, get_image_dimensions, resize_image
from .image_file import AttachmentFile, AttachmentFileTypes, get_display_size, URL_PATTERN
from .recordthread import RecordThread, WhisperTranscription, AudioInputResult
from .thread_shutdown import stop_worker_thread
from .resultevent import ResultEvent, EVT_RESULT_ID
from .transcription import get_transcription_provider
from .toolsmenu import show_tools_menu
from .attachment_ui import AttachmentListUIMixin
from .conversation_session_panel import ConversationSessionPanel

sys.path.insert(0, LIBS_BASE)
from markdown_it import MarkdownIt
sys.path.remove(LIBS_BASE)

from .apiclient import (
	APIConnectionError,
	APIStatusError,
	Choice,
	ChatCompletion,
	Transcription,
	configure_client_for_provider,
)

addonHandler.initTranslation()

DATA_JSON_FP = os.path.join(DATA_DIR, "data.json")

# WinUser.h GA_ROOT — root top-level for GetAncestor(GetForegroundWindow(), …).
_GA_ROOT = 2

addToSession = None
activeChatDlg = None
_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable("table")

def EVT_RESULT(win, func):
	win.Connect(-1, -1, EVT_RESULT_ID, func)


def copyToClipAsHTML(html_content):
	html_data_object = wx.HTMLDataObject()
	html_data_object.SetHTML(html_content)
	if wx.TheClipboard.Open():
		wx.TheClipboard.Clear()
		wx.TheClipboard.SetData(html_data_object)
		wx.TheClipboard.Close()
	else:
		raise RuntimeError("Unable to open the clipboard")


def render_markdown_html(text: str) -> str:
	return _MARKDOWN_RENDERER.render(text or "")


def _labeled_control_row(parent, sizer, label_text, ctrl, proportion=0):
	lbl = wx.StaticText(parent, label=label_text)
	try:
		ctrl.MoveAfterInTabOrder(lbl)
	except Exception:
		pass
	sizer.Add(lbl, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
	sizer.Add(ctrl, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
	return lbl


class ConversationNotebook(wx.Notebook):
	"""Notebook hosting parallel conversation sessions."""


class ConversationDialog(ModelHandlersMixin, AttachmentListUIMixin, FileHandlersMixin, AudioHandlersMixin, HistoryHandlersMixin, wx.Dialog):
	"""NVDA AI-Hub conversation window (parallel sessions in a notebook)."""
	_THINK_HISTORY_OPEN = "<think>\n"
	_THINK_HISTORY_CLOSE = "\n</think>\n"

	def _onNotebookPageChanging(self, evt):
		try:
			old = evt.GetOldSelection()
			if old >= 0 and getattr(self, "notebook", None):
				old_page = self.notebook.GetPage(old)
				self._captureEphemeralToPage(old_page)
				self._captureConversationChromeToPage(old_page)
		except Exception:
			pass
		evt.Skip()

	def _onNotebookPageChanged(self, evt):
		self._cached_messages_hwnd = None
		page = self.get_active_page()
		self._hydrateLazySessionTabIfNeeded(page)
		if self.conf.get("saveSystem"):
			page.systemTextCtrl.ChangeValue(getattr(page, "conversationSystemText", "") or "")
		self._syncWindowTitleFromActiveTab()
		wx.CallAfter(self._syncSharedChromeForActiveTab)
		evt.Skip()

	def _onConversationChromeEdited(self, evt):
		if getattr(self, "_sync_suppress_tab_capture", False):
			evt.Skip()
			return
		try:
			self._captureConversationChromeToPage(self.get_active_page())
		except Exception:
			pass
		evt.Skip()

	def _onSystemTextEdited(self, evt):
		if getattr(self, "_sync_suppress_tab_capture", False):
			evt.Skip()
			return
		try:
			self.get_active_page().conversationSystemText = self.systemTextCtrl.GetValue()
		except Exception:
			pass
		evt.Skip()

	def _merge_audio_transcripts_into_prompt(self, combined: str):
		try:
			text = (combined or "").strip()
			if not text:
				return
			page = getattr(self, "_dictation_page", None) or self.get_active_page()
			self._insert_transcription_on_page(page, text)
		except Exception:
			log.exception("merge audio transcripts into prompt")

	def _prompt_ctrl_has_focus(self):
		"""True when the active tab's prompt field owns keyboard focus."""
		try:
			focused = wx.Window.FindFocus()
		except Exception:
			return False
		page = self._page_from_control(focused)
		return page is not None and focused is page.promptTextCtrl

	def _insert_transcription_on_page(self, page, text):
		"""Insert dictated text into a tab prompt and focus it."""
		if not page:
			return
		text = (text or "").strip()
		if not text:
			return
		prompt = page.promptTextCtrl
		cur = prompt.GetValue().strip()
		if cur:
			prompt.AppendText("\n" + text)
		else:
			prompt.SetValue(text)
		idx = self._notebook_page_index(page)
		if idx >= 0 and self.notebook.GetSelection() != idx:
			self.notebook.SetSelection(idx)
			self._syncSharedChromeForActiveTab()
		prompt.SetFocus()
		prompt.SetInsertionPointEnd()

	def _collect_blocks_from_page(self, page):
		blocks = []
		b = page.firstBlock
		while b:
			blocks.append(b)
			b = b.next
		return blocks

	def _model_id_from_block_chain(self, page):
		"""First non-empty model id on the block chain (tail toward head, then forward)."""
		b = page.lastBlock
		while b:
			mid = (getattr(b, "model", None) or "").strip()
			if mid:
				return mid
			b = getattr(b, "previous", None)
		b = page.firstBlock
		while b:
			mid = (getattr(b, "model", None) or "").strip()
			if mid:
				return mid
			b = getattr(b, "next", None)
		return ""

	def _conversation_record_for_page(self, page):
		cid = getattr(page, "_conversationId", None)
		if not cid:
			return None
		try:
			return conversations.load_conversation(cid)
		except Exception:
			return None

	def _chrome_hints_for_page(self, page):
		"""Resolve model id and account key for shared chrome: blocks, tab hints, then one disk read."""
		model_id = self._model_id_from_block_chain(page)
		if not model_id:
			model_id = (getattr(page, "conversationModelHint", None) or "").strip()
		account_key = (getattr(page, "conversationAccountKey", None) or "").strip()
		rec = None
		if not model_id or not account_key:
			rec = self._conversation_record_for_page(page)
		if rec:
			if not model_id:
				model_id = (rec.get("model") or "").strip()
			if not account_key:
				account_key = (rec.get("accountKey") or "").strip()
		return model_id, account_key

	def _model_id_for_page_persist(self, page):
		return self._model_id_from_block_chain(page) or (getattr(page, "conversationModelHint", None) or "").strip()

	def _default_title_for_new_conversation(self, blocks, draft_stripped):
		if blocks:
			first = blocks[0]
			prompt = getattr(first, "prompt", "") or ""
			tlist = getattr(first, "audioTranscriptList", None)
			if not prompt and tlist and any(t for t in tlist):
				prompt = "\n".join(t for t in tlist if t).strip()
		else:
			prompt = draft_stripped
		return conversations.get_default_title(prompt)

	def _page_has_restorable_content(self, page, *, blocks=None, draft_prompt=None):
		"""A page should be restored only when it has message history or a non-empty draft."""
		if blocks is None:
			blocks = self._collect_blocks_from_page(page)
		if draft_prompt is None:
			draft_prompt = page.promptTextCtrl.GetValue()
		return bool(blocks) or bool((draft_prompt or "").strip())

	def _storage_kwargs_for_page(self, page, *, autosave_force=False, for_autosave=False):
		"""
		Build arguments for conversations.save_conversation, or None if nothing should be written.
		Persist allows draft-only tabs; periodic autosave skips draft-only unless autosave_force (manual save).
		A tab is persisted only when it has message history or a non-empty draft prompt.
		"""
		if getattr(page, "ephemeral", False):
			return None
		blocks = self._collect_blocks_from_page(page)
		draft_prompt = page.promptTextCtrl.GetValue()
		draft_stripped = draft_prompt.strip()
		if not self._page_has_restorable_content(page, blocks=blocks, draft_prompt=draft_prompt):
			return None
		if not blocks and for_autosave and not autosave_force:
			return None
		if self.conf.get("saveSystem"):
			system = (getattr(page, "conversationSystemText", None) or "").strip()
		else:
			system = ""
		model_id = self._model_id_for_page_persist(page)
		account_key = (getattr(page, "conversationAccountKey", None) or "").strip()
		ui_state = getattr(page, "conversationUiState", None)
		if not isinstance(ui_state, dict):
			ui_state = {}
		name = None
		if not page._conversationId:
			name = self._default_title_for_new_conversation(blocks, draft_stripped)
		return {
			"blocks": blocks,
			"system": system,
			"model": model_id,
			"name": name,
			"conv_id": page._conversationId,
			"draftPrompt": draft_prompt,
			"draftPathList": page.filesList,
			"draftAudioPathList": page.audioPathList,
			"account_key": account_key,
			"ui_state": ui_state,
			"usage_ledger": list(getattr(page, "usageLedger", None) or []),
		}

	def _saveConversationFromKw(self, kw):
		return conversations.save_conversation(
			kw["blocks"],
			system=kw["system"],
			model=kw["model"],
			name=kw["name"],
			conv_id=kw["conv_id"],
			draftPrompt=kw["draftPrompt"],
			draftPathList=kw["draftPathList"],
			draftAudioPathList=kw["draftAudioPathList"],
			account_key=kw["account_key"],
			ui_state=kw["ui_state"],
			usage_ledger=kw.get("usage_ledger"),
		)

	def _captureConversationChromeToPage(self, page):
		"""Snapshot shared chrome + system prompt into a tab (call before saving or switching away)."""
		if not page:
			return
		acc = self.getCurrentAccount()
		page.conversationAccountKey = acc["key"] if acc else ""
		model = self.getCurrentModel()
		page.conversationModelHint = model.id if model else ""
		page.conversationSystemText = page.systemTextCtrl.GetValue()
		st = {}
		if model:
			try:
				st["maxTokens"] = self.maxTokensSpinCtrl.GetValue()
			except Exception:
				pass
			if self.reasoningModeCheckBox.IsShown():
				st["reasoningMode"] = self.reasoningModeCheckBox.IsChecked()
			opts = getattr(self, "_reasoningEffortOptions", ())
			idx = self.reasoningEffortChoice.GetSelection()
			if opts and 0 <= idx < len(opts):
				st["reasoningEffort"] = opts[idx][0]
			if self.adaptiveThinkingCheckBox.IsShown():
				st["adaptiveThinking"] = self.adaptiveThinkingCheckBox.IsChecked()
			if self.webSearchCheckBox.IsShown():
				st["webSearch"] = self.webSearchCheckBox.IsChecked()
			or_cb = getattr(self, "openRouterWebSearchCheckBox", None)
			if or_cb is not None and or_cb.IsShown():
				st["openRouterWebSearch"] = or_cb.IsChecked()
			try:
				st["advancedSampling"] = self.advancedSamplingCheckBox.IsChecked()
			except Exception:
				pass
			if hasattr(self, "streamModeCheckBox"):
				st["stream"] = self.streamModeCheckBox.IsChecked()
			if hasattr(self, "debugModeCheckBox"):
				st["debug"] = self.debugModeCheckBox.IsChecked()
			if self._effective_advanced_mode():
				if "temperature" in model.supportedParameters and hasattr(self, "temperatureSpinCtrl"):
					st["temperature"] = self.temperatureSpinCtrl.GetValue()
				if "top_p" in model.supportedParameters and hasattr(self, "topPSpinCtrl"):
					st["topP"] = self.topPSpinCtrl.GetValue()
				if hasattr(self, "advancedSeedSpinCtrl"):
					st["advancedSeed"] = self.advancedSeedSpinCtrl.GetValue()
				if hasattr(self, "advancedTopKSpinCtrl"):
					st["advancedTopK"] = self.advancedTopKSpinCtrl.GetValue()
				if hasattr(self, "advancedStopTextCtrl"):
					st["advancedStopText"] = self.advancedStopTextCtrl.GetValue()
				if hasattr(self, "advancedFreqPenaltySpinCtrl"):
					st["advancedFreqPenalty"] = self.advancedFreqPenaltySpinCtrl.GetValue()
				if hasattr(self, "advancedPresPenaltySpinCtrl"):
					st["advancedPresPenalty"] = self.advancedPresPenaltySpinCtrl.GetValue()
		page.conversationUiState = st

	def _applyConversationUiStateToChrome(self, page):
		"""Apply tab-stored model options after account/model selection and base onModelChange."""
		st = getattr(page, "conversationUiState", None)
		if not isinstance(st, dict) or not st:
			return
		if "advancedSampling" in st:
			try:
				self.advancedSamplingCheckBox.SetValue(bool(st["advancedSampling"]))
			except Exception:
				pass
			self._update_advanced_controls_visibility()
		model = self.getCurrentModel()
		if "reasoningMode" in st and self.reasoningModeCheckBox.IsShown():
			self.reasoningModeCheckBox.SetValue(bool(st["reasoningMode"]))
		try:
			self.onModelChange(None)
		except Exception:
			pass
		model = self.getCurrentModel()
		if model and model.supports_web_search and "webSearch" in st:
			self.webSearchCheckBox.SetValue(bool(st["webSearch"]))
		or_cb = getattr(self, "openRouterWebSearchCheckBox", None)
		if or_cb is not None and model and model.supports_openrouter_web_search and "openRouterWebSearch" in st:
			or_cb.SetValue(bool(st["openRouterWebSearch"]))
		if "maxTokens" in st:
			try:
				self.maxTokensSpinCtrl.SetValue(int(st["maxTokens"]))
			except Exception:
				pass
		opts = getattr(self, "_reasoningEffortOptions", ())
		if opts and "reasoningEffort" in st:
			want = st["reasoningEffort"]
			idx = next((i for i, (v, _) in enumerate(opts) if v == want), None)
			if idx is not None:
				self.reasoningEffortChoice.SetSelection(idx)
				self.conf["reasoningEffort"] = want
		if self.adaptiveThinkingCheckBox.IsShown() and "adaptiveThinking" in st:
			v = bool(st["adaptiveThinking"])
			self.adaptiveThinkingCheckBox.SetValue(v)
			self.conf["adaptiveThinking"] = v
		if self._effective_advanced_mode() and model:
			if "temperature" in model.supportedParameters and "temperature" in st and hasattr(self, "temperatureSpinCtrl"):
				try:
					self.temperatureSpinCtrl.SetValue(int(st["temperature"]))
				except Exception:
					pass
			if "top_p" in model.supportedParameters and "topP" in st and hasattr(self, "topPSpinCtrl"):
				try:
					self.topPSpinCtrl.SetValue(int(st["topP"]))
				except Exception:
					pass
			for key, attr in (
				("advancedSeed", "advancedSeedSpinCtrl"),
				("advancedTopK", "advancedTopKSpinCtrl"),
				("advancedFreqPenalty", "advancedFreqPenaltySpinCtrl"),
				("advancedPresPenalty", "advancedPresPenaltySpinCtrl"),
			):
				if key in st and hasattr(self, attr):
					try:
						getattr(self, attr).SetValue(int(st[key]))
					except Exception:
						pass
			if "advancedStopText" in st and hasattr(self, "advancedStopTextCtrl"):
				try:
					self.advancedStopTextCtrl.SetValue(str(st["advancedStopText"] or ""))
				except Exception:
					pass
		if hasattr(self, "streamModeCheckBox") and "stream" in st:
			v = bool(st["stream"])
			self.streamModeCheckBox.SetValue(v)
			self.conf["stream"] = v
		if hasattr(self, "debugModeCheckBox") and "debug" in st:
			v = bool(st["debug"])
			self.debugModeCheckBox.SetValue(v)
			self.conf["debug"] = v

	def _syncSharedChromeForActiveTab(self):
		"""Refresh shared account/model/options controls for the selected conversation tab."""
		if not getattr(self, "notebook", None) or self.notebook.GetPageCount() <= 0:
			return
		page = self.get_active_page()
		self._sync_suppress_tab_capture = True
		try:
			mid, acc_key = self._chrome_hints_for_page(page)
			if acc_key:
				self._refreshAccountsList(account_to_select=acc_key)
			else:
				self._refreshAccountsList()
			self._reload_models_for_current_account(model_to_select=mid or None)
			try:
				self.onModelChange(None)
			except Exception as err:
				log.debug(f"syncSharedChromeForActiveTab: {err}", exc_info=True)
			self._applyConversationUiStateToChrome(page)
		finally:
			self._sync_suppress_tab_capture = False
		self._syncEphemeralChromeFromPage(page)
		try:
			self.Layout()
		except Exception:
			pass

	def _syncWindowTitleFromActiveTab(self):
		"""Window title: "<tab label> (n/N) – Conversation" so users hear which tab is active."""
		if not getattr(self, "notebook", None) or self.notebook.GetPageCount() <= 0:
			# Translators: Fallback title when no conversation tab exists.
			self.SetTitle(_("Conversation"))
			return
		idx = self.notebook.GetSelection()
		n = self.notebook.GetPageCount()
		if idx < 0:
			idx = 0
		# Translators: Fallback active-tab label used in the window title.
		label = self.notebook.GetPageText(idx).strip() or _("Conversation")
		# Translators: Tab position in the notebook (e.g. second of three tabs).
		pos = _("({cur}/{tot})").format(cur=idx + 1, tot=n)
		# Translators: Final conversation window title pattern.
		self.SetTitle(f"{label} {pos} – {_('Conversation')}")

	def _deriveTabTitleFromAttachments(self, files) -> str:
		"""Derive a notebook-tab label from the first attachment in the active tab.

		Reuses the user-visible name shown in the Files list (e.g.
		``"Screenshot 12:34:56"`` for NVDA+E, ``"<obj name> (Navigator Object …)"``
		for NVDA+O, or just the file basename for picker/drag-drop). If there
		are several attachments, the extra count is appended.
		"""
		if not files:
			return ""
		primary = (getattr(files[0], "name", "") or "").strip()
		if not primary:
			try:
				primary = os.path.basename(getattr(files[0], "path", "") or "").strip()
			except Exception:
				primary = ""
		if not primary:
			return ""
		if len(primary) > 50:
			primary = primary[:47].rstrip() + "…"
		extra = len(files) - 1
		if extra > 0:
			# Translators: Tab title when first attachment name is followed by more attachments.
			return _("{base} (+{n})").format(base=primary, n=extra)
		return primary

	def _retitleEmptyTabFromAttachments(self):
		"""Rename the active tab from its placeholder label to the first attachment's name.

		Only fires when the tab is brand-new (no saved conversation id, no
		message history, label is the default placeholder). Anything the user
		has explicitly renamed is left alone, and a tab that already has a
		conversation/messages keeps its existing title.
		"""
		page = self.get_active_page()
		if not page:
			return
		if getattr(page, "_conversationId", None):
			return
		if getattr(page, "firstBlock", None):
			return
		files = getattr(page, "filesList", None) or []
		if not files:
			return
		title = self._deriveTabTitleFromAttachments(files)
		if not title:
			return
		idx = self._notebook_page_index(page)
		if idx < 0:
			return
		current = (self.notebook.GetPageText(idx) or "").strip()
		# Untranslated defaults are listed too because users may run the addon
		# in a locale other than the one in which the tab was first created.
		# Translators: Built-in placeholder tab labels considered "untitled".
		placeholders = {
			# Translators: Notebook tab caption treated as an automatic placeholder until the conversation gets a real title.
			_("Untitled conversation"),
			# Translators: Generic notebook tab caption still treated as «no real title yet» when syncing the window title from the model.
			_("Conversation"),
			# Translators: Placeholder caption for a brand new conversation tab.
			_("New conversation"),
			"Untitled conversation",
			"Conversation",
			"New conversation",
		}
		if current and current not in placeholders:
			return
		self.notebook.SetPageText(idx, title)
		if self.notebook.GetSelection() == idx:
			self._syncWindowTitleFromActiveTab()

	def get_active_page(self):
		idx = self.notebook.GetSelection()
		if idx < 0:
			idx = 0
		return self.notebook.GetPage(idx)

	def _conversation_scope(self):
		rs = getattr(self, "_result_snapshot_page", None)
		if rs is not None:
			return rs
		wp = getattr(self, "_worker_page", None)
		if wp is not None and getattr(wp, "worker", None):
			return wp
		return self.get_active_page()

	def _page_from_control(self, ctrl):
		while ctrl is not None:
			if isinstance(ctrl, ConversationSessionPanel):
				return ctrl
			try:
				ctrl = ctrl.GetParent()
			except Exception:
				break
		return None

	@property
	def messagesTextCtrl(self):
		rs = getattr(self, "_result_snapshot_page", None)
		if rs is not None:
			return rs.messagesTextCtrl
		wp = getattr(self, "_worker_page", None)
		if wp is not None and getattr(wp, "worker", None):
			return wp.messagesTextCtrl
		try:
			f = wx.Window.FindFocus()
		except Exception:
			f = None
		p = self._page_from_control(f)
		if p is not None:
			return p.messagesTextCtrl
		return self.get_active_page().messagesTextCtrl

	@property
	def promptTextCtrl(self):
		return self._conversation_scope().promptTextCtrl

	@property
	def systemTextCtrl(self):
		return self._conversation_scope().systemTextCtrl

	@property
	def modelsListCtrl(self):
		return self._conversation_scope().modelsListCtrl

	@property
	def accountListCtrl(self):
		return self._conversation_scope().accountListCtrl

	def _attachment_scope(self):
		rs = getattr(self, "_result_snapshot_page", None)
		if rs is not None:
			return rs
		wp = getattr(self, "_worker_page", None)
		if wp is not None and getattr(wp, "worker", None):
			return wp
		return self.get_active_page()

	@property
	def filesLabel(self):
		return self._attachment_scope().filesLabel

	@property
	def filesListCtrl(self):
		return self._attachment_scope().filesListCtrl

	@property
	def audioLabel(self):
		return self._attachment_scope().audioLabel

	@property
	def audioListCtrl(self):
		return self._attachment_scope().audioListCtrl

	@property
	def firstBlock(self):
		return self._conversation_scope().firstBlock

	@firstBlock.setter
	def firstBlock(self, value):
		self._conversation_scope().firstBlock = value

	@property
	def lastBlock(self):
		return self._conversation_scope().lastBlock

	@lastBlock.setter
	def lastBlock(self, value):
		self._conversation_scope().lastBlock = value

	@property
	def filesList(self):
		return self._attachment_scope().filesList

	@filesList.setter
	def filesList(self, value):
		self._attachment_scope().filesList = value

	@property
	def audioPathList(self):
		return self._attachment_scope().audioPathList

	@audioPathList.setter
	def audioPathList(self, value):
		self._attachment_scope().audioPathList = value

	@property
	def previousPrompt(self):
		return self._conversation_scope().previousPrompt

	@previousPrompt.setter
	def previousPrompt(self, value):
		self._conversation_scope().previousPrompt = value

	@property
	def usageLedger(self):
		return self._conversation_scope().usageLedger

	@usageLedger.setter
	def usageLedger(self, value):
		self._conversation_scope().usageLedger = value

	@property
	def _conversationId(self):
		return self._conversation_scope()._conversationId

	@_conversationId.setter
	def _conversationId(self, value):
		self._conversation_scope()._conversationId = value

	@property
	def worker(self):
		wp = getattr(self, "_worker_page", None)
		if wp is not None:
			return wp.worker
		return None

	@worker.setter
	def worker(self, value):
		wp = getattr(self, "_worker_page", None)
		if wp is None:
			wp = self.get_active_page()
			self._worker_page = wp
		wp.worker = value

	@property
	def stopRequest(self):
		wp = getattr(self, "_worker_page", None)
		if wp is not None:
			return wp.stopRequest
		return None

	@stopRequest.setter
	def stopRequest(self, value):
		wp = getattr(self, "_worker_page", None)
		if wp is None:
			wp = self.get_active_page()
			self._worker_page = wp
		wp.stopRequest = value

	def _addConversationTab(self, title=None):
		idx = self.notebook.GetPageCount()
		# Translators: Default tab caption for a new empty conversation.
		tab_title = title if title is not None else _("Untitled conversation")
		prev_snapshot = ""
		if idx > 0:
			prev_sel = self.notebook.GetSelection()
			if prev_sel >= 0:
				prev_snapshot = self.notebook.GetPage(prev_sel).systemTextCtrl.GetValue()
		page = ConversationSessionPanel(self.notebook, self)
		self.notebook.AddPage(page, tab_title)
		self.addShortcutsForPage(page)
		self.notebook.SetSelection(idx)
		page.conversationSystemText = prev_snapshot
		page.systemTextCtrl.SetValue(prev_snapshot)
		self._syncWindowTitleFromActiveTab()
		return page

	def _notebook_page_index(self, page):
		for ti in range(self.notebook.GetPageCount()):
			if self.notebook.GetPage(ti) is page:
				return ti
		return -1

	def _onCloseConversationTab(self, evt=None):
		"""Ctrl+W: close the current tab; reset the last tab instead of closing the hub."""
		if not getattr(self, "notebook", None):
			return
		if self.notebook.GetPageCount() > 1:
			self._closeTabAt(self.notebook.GetSelection())
			return
		self._resetTabPage(self.get_active_page())
		wx.CallAfter(self.promptTextCtrl.SetFocus)

	def _clearMessagesSegmentsOnPage(self, page):
		page.messagesTextCtrl.Clear()
		if hasattr(page.messagesTextCtrl, "firstSegment"):
			page.messagesTextCtrl.firstSegment = None
			page.messagesTextCtrl.lastSegment = None
		page.messagesTextCtrl._aihub_saved_selection = None

	def _captureEphemeralToPage(self, page):
		if page is None or not hasattr(self, "ephemeralCheckBox"):
			return
		page.ephemeral = bool(self.ephemeralCheckBox.IsChecked())

	def _syncEphemeralChromeFromPage(self, page):
		if page is None or not hasattr(self, "ephemeralCheckBox"):
			return
		ephemeral = bool(getattr(page, "ephemeral", False))
		self.ephemeralCheckBox.SetValue(ephemeral)
		self._syncSaveControlsForEphemeral(ephemeral)

	def _is_active_tab_ephemeral(self):
		return bool(getattr(self.get_active_page(), "ephemeral", False))

	def _syncSaveControlsForEphemeral(self, ephemeral=None):
		if ephemeral is None:
			ephemeral = self._is_active_tab_ephemeral()
		if ephemeral:
			self.saveConversationBtn.Disable()
			self.saveConversationBtn.Show(False)
			self.renameConversationBtn.Disable()
		else:
			self.saveConversationBtn.Show(True)
			self.saveConversationBtn.Enable()
			self.renameConversationBtn.Enable()
		try:
			self.Layout()
		except Exception:
			pass

	def _onManualSaveRequested(self, evt=None):
		"""Ctrl+S / menu save — no-op while the active tab is ephemeral."""
		if self._is_active_tab_ephemeral():
			return
		self._saveConversation(evt)

	def _purgeSavedConversationForPage(self, page):
		"""Remove on-disk history for a tab entering ephemeral mode."""
		cid = getattr(page, "_conversationId", None)
		if not cid:
			return
		try:
			conversations.delete_conversation(cid)
		except Exception as err:
			log.error(f"purge ephemeral conversation {cid}: {err}", exc_info=True)
		try:
			conversations.prune_hub_session_references([cid])
		except Exception as err:
			log.error(f"prune hub session for ephemeral {cid}: {err}", exc_info=True)
		page._conversationId = None

	def _onEphemeralToggle(self, evt):
		if getattr(self, "_sync_suppress_tab_capture", False):
			evt.Skip()
			return
		page = self.get_active_page()
		ephemeral = bool(self.ephemeralCheckBox.IsChecked())
		if ephemeral == bool(getattr(page, "ephemeral", False)):
			evt.Skip()
			return
		page.ephemeral = ephemeral
		if ephemeral:
			self._purgeSavedConversationForPage(page)
			# Translators: Status after enabling ephemeral (no-save) mode on the current tab.
			self.message(_("Ephemeral mode: this conversation will not be saved."))
		else:
			# Translators: Status after disabling ephemeral mode; saving is available again.
			self.message(_("Saving is enabled for this conversation."))
		self._syncSaveControlsForEphemeral(ephemeral)
		evt.Skip()

	def _persistPageConversation(self, page):
		"""Save one tab to conversation storage for hub session close; returns id or None on failure."""
		if getattr(page, "ephemeral", False):
			return None
		if self.get_active_page() is page:
			self._captureEphemeralToPage(page)
			self._captureConversationChromeToPage(page)
		kw = self._storage_kwargs_for_page(page, for_autosave=False)
		if kw is None:
			return None
		try:
			new_id = self._saveConversationFromKw(kw)
			page._conversationId = new_id
			tab_name = kw.get("name")
			if not tab_name:
				entry = next((e for e in conversations.list_conversations() if e.get("id") == new_id), None)
				# Translators: Fallback saved-conversation title when no explicit name is available.
				tab_name = entry.get("name", _("Untitled conversation")) if entry else _("Untitled conversation")
			idx = self._notebook_page_index(page)
			if idx >= 0:
				self.notebook.SetPageText(idx, tab_name)
				if self.notebook.GetSelection() == idx:
					self._syncWindowTitleFromActiveTab()
			return new_id
		except Exception as err:
			log.error(f"persist tab conversation: {err}", exc_info=True)
			return None

	def _prepareLazySessionTab(self, page, cid, tab_title):
		"""Placeholder UI only; full load deferred until user switches to this tab."""
		page.session_lazy_load = True
		page.firstBlock = None
		page.lastBlock = None
		page.previousPrompt = None
		page.usageLedger = []
		page._regenerateBlock = None
		page._conversationId = cid
		page.conversationModelHint = ""
		page.conversationAccountKey = ""
		page.conversationSystemText = ""
		page.conversationUiState = {}
		page.ephemeral = False
		self._clearMessagesSegmentsOnPage(page)
		page.filesList = []
		page.audioPathList = []
		page.promptTextCtrl.Clear()
		idx = self._notebook_page_index(page)
		if idx >= 0:
			# Translators: Fallback tab label for a lazily restored conversation tab.
			self.notebook.SetPageText(idx, tab_title or _("Conversation"))

	def _hydrateLazySessionTabIfNeeded(self, page):
		if not getattr(page, "session_lazy_load", False):
			return
		cid = getattr(page, "_conversationId", None)
		if not cid:
			page.session_lazy_load = False
			return
		data = conversations.load_conversation(cid)
		if data:
			self._loadConversation(data)
			page.session_lazy_load = False
		else:
			self._resetTabPage(page)

	def _resetTabPage(self, page, *, select_tab=True, sync_attachment_widgets=True):
		"""Clear one tab to an empty state (no saved conversation id)."""
		page.session_lazy_load = False
		page.firstBlock = None
		page.lastBlock = None
		page.previousPrompt = None
		page.usageLedger = []
		page._regenerateBlock = None
		page._conversationId = None
		page.conversationModelHint = ""
		page.conversationAccountKey = ""
		page.conversationSystemText = ""
		page.conversationUiState = {}
		page.ephemeral = False
		self._clearMessagesSegmentsOnPage(page)
		page.filesList = []
		page.audioPathList = []
		page.promptTextCtrl.Clear()
		idx = self._notebook_page_index(page)
		if idx >= 0:
			# Translators: Default tab caption after clearing a conversation tab.
			self.notebook.SetPageText(idx, _("Untitled conversation"))
			if select_tab:
				self.notebook.SetSelection(idx)
		if sync_attachment_widgets:
			self.updateFilesList(focusPrompt=False)
			self.updateAudioList(focusPrompt=False)
		self._syncWindowTitleFromActiveTab()
		if self.get_active_page() is page:
			self._syncEphemeralChromeFromPage(page)

	def _saveHubSession(self):
		"""Persist every tab and write hub_session.json for restore on next open."""
		if not self.conf.get("autoSaveConversation", True):
			conversations.remove_hub_session_file()
			return
		active = self.get_active_page()
		self._captureEphemeralToPage(active)
		self._captureConversationChromeToPage(active)
		tab_entries = []
		for i in range(self.notebook.GetPageCount()):
			page = self.notebook.GetPage(i)
			cid = self._persistPageConversation(page)
			if cid:
				tab_entries.append({"id": cid})
		try:
			if tab_entries:
				conversations.write_hub_session_snapshot(tabs=tab_entries)
			else:
				conversations.write_hub_session_snapshot(tabs=[])
		except Exception as err:
			log.error(f"save hub session: {err}", exc_info=True)

	def _restoreHubSessionIfNeeded(self, *, append_fresh_tab=False):
		if not self.conf.get("autoSaveConversation", True):
			return
		if not os.path.isfile(conversations.HUB_SESSION_JSON):
			return
		try:
			with open(conversations.HUB_SESSION_JSON, "r", encoding="utf-8") as f:
				snap = json.load(f)
		except Exception as err:
			log.warning(f"restore hub session read: {err}")
			return
		tabs = snap.get("tabs") or []
		if not tabs:
			return
		while self.notebook.GetPageCount() < len(tabs):
			# Translators: Temporary tab caption while restoring a previous session.
			self._addConversationTab(_("Conversation"))
		# Which tab gets full load last (others lazy/reset). No longer persisted (v2).
		final_sel = 0
		# Translators: Fallback conversation name during hub-session restoration.
		name_by_id = {
			# Translators: Fallback tab title when restoring a hub session if the saved conversation id has no stored display name.
			e.get("id"): e.get("name") or _("Conversation")
			for e in conversations.list_conversations()
			if e.get("id")
		}

		def _entry_cid(entry):
			return (entry.get("id") if isinstance(entry, dict) else entry) or ""

		for i in range(len(tabs)):
			if i == final_sel:
				continue
			page = self.notebook.GetPage(i)
			cid = _entry_cid(tabs[i])
			if cid and conversations.conversation_file_exists(cid):
				# Translators: Fallback tab label when a restored conversation name is missing.
				self._prepareLazySessionTab(page, cid, name_by_id.get(cid, _("Conversation")))
			else:
				self._resetTabPage(page, select_tab=False, sync_attachment_widgets=False)

		self.notebook.SetSelection(final_sel)
		active_page = self.notebook.GetPage(final_sel)
		cid = _entry_cid(tabs[final_sel])
		if cid and conversations.conversation_file_exists(cid):
			data = conversations.load_conversation(cid)
			if data:
				self._loadConversation(data)
			else:
				self._resetTabPage(active_page)
		else:
			self._resetTabPage(active_page)
		if append_fresh_tab:
			self._addConversationTab()
		wx.CallAfter(self.promptTextCtrl.SetFocus)

	def _openConversationFromHistory(self, data):
		"""Open a saved conversation in a new tab (conversation list dialog)."""
		# Translators: Fallback name when opening an untitled saved conversation from history.
		name = data.get("name", _("Conversation")) if isinstance(data, dict) else _("Conversation")
		self._addConversationTab(name)
		self._loadConversation(data, focus_message_history=True)

	def _closeTabAt(self, idx):
		n = self.notebook.GetPageCount()
		if n <= 1:
			return False
		if idx < 0 or idx >= n:
			return False
		page = self.notebook.GetPage(idx)
		if getattr(page, "worker", None):
			sr = page.stopRequest
			if sr:
				sr.set()
			stop_worker_thread(page.worker)
			page.worker = None
		if getattr(self, "_worker_page", None) is page:
			self._worker_page = None
		new_sel = idx - 1 if idx > 0 else 0
		self.notebook.DeletePage(idx)
		n = self.notebook.GetPageCount()
		if n:
			self.notebook.SetSelection(min(new_sel, n - 1))
		self._cached_messages_hwnd = None
		try:
			self.Layout()
		except Exception:
			pass
		self.updateFilesList(False)
		self.updateAudioList(False)
		self._syncWindowTitleFromActiveTab()
		return True

	def _onAdvancedSamplingToggle(self, evt):
		self._update_advanced_controls_visibility()
		try:
			self.onModelChange(None)
		except Exception:
			pass
		try:
			self._captureConversationChromeToPage(self.get_active_page())
		except Exception:
			pass
		if evt:
			evt.Skip()

	def _update_advanced_controls_visibility(self):
		on = self._effective_advanced_mode()
		if hasattr(self, "advancedSamplingGroupBox"):
			self.advancedSamplingGroupBox.Show(on)
		if not on:
			for w in (
				self.temperatureLabel,
				self.temperatureSpinCtrl,
				self.topPLabel,
				self.topPSpinCtrl,
				self.advancedSeedLabel,
				self.advancedSeedSpinCtrl,
				self.advancedTopKLabel,
				self.advancedTopKSpinCtrl,
				self.advancedStopLabel,
				self.advancedStopTextCtrl,
				self.advancedFreqPenaltyLabel,
				self.advancedFreqPenaltySpinCtrl,
				self.advancedPresPenaltyLabel,
				self.advancedPresPenaltySpinCtrl,
			):
				w.Hide()
		parent = self.temperatureSpinCtrl.GetParent() if hasattr(self, "temperatureSpinCtrl") else None
		if parent:
			parent.Layout()
		self.Layout()

	def __init__(
		self,
		parent,
		client,
		conf,
		title=None,
		filesList=None,
		plugin=None,
		conversationData=None,
	):
		global addToSession
		if not client or not conf:
			raise ValueError("ConversationDialog requires client and conf")
		self._plugin = plugin
		self.client = client
		self._base_url = client.base_url
		self._api_key = client.api_key
		self._organization = client.organization
		self.conf = conf
		self.data = self.loadData()
		self._orig_data = self.data.copy() if isinstance(self.data, dict) else None
		self._showThinkingInHistory = bool(self.data.get("showThinkingInHistory", True))
		self._models = []
		self._worker_page = None
		self._pending_session_paths = list(filesList) if filesList else []
		self._fileToRemoveAfter = []
		self.lastFocusedItem = None
		self._lastSystem = None
		if self.conf["saveSystem"]:
			if "system" in self.data:
				self._lastSystem = self.data["system"]
			else:
				self._lastSystem = DEFAULT_SYSTEM_PROMPT
		else:
			self.data.pop("system", None)
		if conversationData:
			# Translators: Default name when opening a saved conversation without title.
			conv_name = conversationData.get("name", _("Untitled conversation"))
			# Translators: Main dialog title suffix.
			title = f"{conv_name} – {_('Conversation')}"
		else:
			# Translators: Title used when opening a fresh conversation window.
			title = _("New conversation") + " – " + _("Conversation")
		super().__init__(
			parent,
			title=title,
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
		)

		self.Bind(wx.EVT_CHILD_FOCUS, self.onSetFocus)
		content_panel = wx.Panel(self)
		mainSizer = wx.BoxSizer(wx.VERTICAL)

		# Translators: Section title for top action buttons in the conversation window.
		toolbar_box = wx.StaticBox(content_panel, label=_("Toolbar"))
		toolbar_sz = wx.StaticBoxSizer(toolbar_box, wx.HORIZONTAL)
		# Translators: Toolbar button labels in the conversation window.
		self.conversationListBtn = wx.Button(
			content_panel,
			# Translators: AI-Hub conversation window: title of a bordered settings group.
			label=_("Conversation &list...") + " (Ctrl+L)"
		)
		self.conversationListBtn.Bind(wx.EVT_BUTTON, self._onConversationList)
		toolbar_sz.Add(self.conversationListBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.saveConversationBtn = wx.Button(
			content_panel,
			# Translators: Toolbar button to save current conversation immediately.
			label=_("&Save conversation") + " (Ctrl+S)"
		)
		self.saveConversationBtn.Bind(wx.EVT_BUTTON, self._onManualSaveRequested)
		toolbar_sz.Add(self.saveConversationBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.ephemeralCheckBox = wx.CheckBox(
			content_panel,
			# Translators: Checkbox to keep the current tab out of history and disable auto-save.
			label=_("&Ephemeral conversation"),
		)
		self.ephemeralCheckBox.SetValue(False)
		self.ephemeralCheckBox.Bind(wx.EVT_CHECKBOX, self._onEphemeralToggle)
		toolbar_sz.Add(self.ephemeralCheckBox, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, UI_SECTION_SPACING_PX)
		self.newConversationBtn = wx.Button(
			content_panel,
			# Translators: Toolbar button to open a new conversation tab.
			label=_("&New conversation") + " (Ctrl+N)"
		)
		self.newConversationBtn.Bind(wx.EVT_BUTTON, self._newConversation)
		toolbar_sz.Add(self.newConversationBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.renameConversationBtn = wx.Button(
			content_panel,
			# Translators: Toolbar button to rename the current saved conversation.
			label=_("&Rename conversation") + " (F2)"
		)
		self.renameConversationBtn.Bind(wx.EVT_BUTTON, self._renameConversation)
		toolbar_sz.Add(self.renameConversationBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.toolbarCloseBtn = wx.Button(content_panel, id=wx.ID_CLOSE)
		self.toolbarCloseBtn.Bind(wx.EVT_BUTTON, self.onCancel)
		toolbar_sz.Add(self.toolbarCloseBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		mainSizer.Add(toolbar_sz, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)

		self.notebook = ConversationNotebook(content_panel)
		mainSizer.Add(self.notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGING, self._onNotebookPageChanging)
		self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._onNotebookPageChanged)
		self._addConversationTab()
		# When the dialog is opened with a pending attachment (e.g. NVDA+E /
		# NVDA+O screenshot, file-picker, drag-and-drop) we still want to
		# restore the previously saved tabs first, then drop the attachment in
		# a brand new active tab so the user does not lose their last session.
		# ``_restoreHubSessionIfNeeded`` already appends a fresh tab and selects
		# it, so afterwards the active page is the right destination.
		self._hub_session_restored = False
		if self._pending_session_paths and not conversationData:
			try:
				self._restoreHubSessionIfNeeded(append_fresh_tab=True)
				self._hub_session_restored = True
			except Exception:
				log.error("restore hub session before pending attachment", exc_info=True)
		if self._pending_session_paths:
			addToSession = self
			for path in self._pending_session_paths:
				self.addFileToList(path, removeAfter=True)
			self._pending_session_paths = []
		p0 = self.get_active_page()
		if p0.filesList:
			p0.promptTextCtrl.SetValue(
				self.getDefaultFilesDescriptionPrompt()
			)
		self.updateFilesList()
		self.updateAudioList()
		# Rename the freshly-created tab from "Untitled conversation" to the
		# attachment's display name (e.g. "Screenshot 12:34:56") so screenshots
		# and Navigator-Object captures are easy to identify in the notebook.
		self._retitleEmptyTabFromAttachments()

		if conf["saveSystem"]:
			self.get_active_page().systemTextCtrl.SetValue(self._lastSystem)
		else:
			self.get_active_page().systemTextCtrl.SetValue(DEFAULT_SYSTEM_PROMPT)

		# Translators: Section title for model generation options.
		gen_box = wx.StaticBox(content_panel, label=_("Generation"))
		gen_sz = wx.StaticBoxSizer(gen_box, wx.VERTICAL)
		modelOptionsSizer = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Checkbox labels for generation behavior toggles.
		self.reasoningModeCheckBox = wx.CheckBox(
			content_panel,
			# Translators: AI-Hub conversation window: title of a bordered settings group.
			label=_("&Reasoning mode")
		)
		self.reasoningModeCheckBox.SetValue(False)
		self.reasoningModeCheckBox.Bind(wx.EVT_CHECKBOX, self._onReasoningModeChange)
		modelOptionsSizer.Add(self.reasoningModeCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.adaptiveThinkingCheckBox = wx.CheckBox(
			content_panel,
			# Translators: Checkbox to enable adaptive reasoning behavior when supported.
			label=_("&Adaptive thinking")
		)
		self.adaptiveThinkingCheckBox.SetValue(conf.get("adaptiveThinking", True))
		self.adaptiveThinkingCheckBox.Bind(wx.EVT_CHECKBOX, self._onAdaptiveThinkingChange)
		modelOptionsSizer.Add(self.adaptiveThinkingCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.webSearchCheckBox = wx.CheckBox(
			content_panel,
			# Translators: Checkbox to allow built-in web search for supporting models.
			label=_("&Web search")
		)
		self.webSearchCheckBox.SetValue(False)
		self.webSearchCheckBox.Bind(wx.EVT_CHECKBOX, self._onConversationChromeEdited)
		modelOptionsSizer.Add(self.webSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.openRouterWebSearchCheckBox = wx.CheckBox(
			content_panel,
			# Translators: Checkbox for OpenRouter universal web search server tool (any tool-calling model).
			label=_("OpenRouter &web search"),
		)
		self.openRouterWebSearchCheckBox.SetValue(False)
		self.openRouterWebSearchCheckBox.Bind(wx.EVT_CHECKBOX, self._onConversationChromeEdited)
		modelOptionsSizer.Add(self.openRouterWebSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		gen_sz.Add(modelOptionsSizer, 0, wx.ALL, 0)

		self.reasoningEffortRow = wx.Panel(content_panel)
		reasoningEffortRowSz = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label for reasoning effort dropdown.
		self.reasoningEffortLabel = wx.StaticText(self.reasoningEffortRow, label=_("Reasoning &effort:"))
		self.reasoningEffortChoice = wx.Choice(self.reasoningEffortRow, choices=[])
		self.reasoningEffortChoice.Bind(wx.EVT_CHOICE, self._onReasoningEffortChange)
		reasoningEffortRowSz.Add(self.reasoningEffortLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		reasoningEffortRowSz.Add(self.reasoningEffortChoice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.reasoningEffortRow.SetSizer(reasoningEffortRowSz)
		gen_sz.Add(self.reasoningEffortRow, 0, wx.EXPAND, 0)

		self.maxTokensRow = wx.Panel(content_panel)
		maxTokensRowSz = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label for maximum output tokens numeric input.
		self.maxTokensLabel = wx.StaticText(self.maxTokensRow, label=_("Max to&kens:"))
		self.maxTokensSpinCtrl = wx.SpinCtrl(self.maxTokensRow, min=0)
		self.maxTokensSpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		maxTokensRowSz.Add(self.maxTokensLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		maxTokensRowSz.Add(self.maxTokensSpinCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.maxTokensRow.SetSizer(maxTokensRowSz)
		gen_sz.Add(self.maxTokensRow, 0, wx.EXPAND, 0)

		self.advancedSamplingCheckBox = wx.CheckBox(
			content_panel,
			# Translators: Checkbox that reveals/hides advanced sampling controls.
			label=_("Ad&vanced sampling (temperature, top-p, seed, stop, …)")
		)
		self.advancedSamplingCheckBox.SetValue(False)
		self.advancedSamplingCheckBox.Bind(wx.EVT_CHECKBOX, self._onAdvancedSamplingToggle)
		gen_sz.Add(self.advancedSamplingCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)

		# Translators: Group title for advanced sampling parameters.
		self.advancedSamplingGroupBox = wx.StaticBox(content_panel, label=_("Advanced sampling options"))
		advancedSamplingSz = wx.StaticBoxSizer(self.advancedSamplingGroupBox, wx.VERTICAL)

		self.temperatureSpinCtrl = wx.SpinCtrl(content_panel, min=0, max=200)
		self.temperatureSpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		# Translators: Label for temperature spin control.
		self.temperatureLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label before the temperature spin control (value is hundredths) in Advanced sampling on the conversation window.
			_("&Temperature:"),
			self.temperatureSpinCtrl,
		)

		self.topPSpinCtrl = wx.SpinCtrl(
			content_panel,
			min=TOP_P_MIN,
			max=TOP_P_MAX,
			initial=conf["topP"]
		)
		self.topPSpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		# Translators: Label for top-p (probability mass) spin control.
		self.topPLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label before the top-P (probability mass) spin control in Advanced sampling on the conversation window.
			_("Pro&bability Mass (top P):"),
			self.topPSpinCtrl,
		)

		self.advancedSeedSpinCtrl = wx.SpinCtrl(content_panel, min=-1, max=2147483647, initial=-1)
		self.advancedSeedSpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		# Translators: Label for optional deterministic seed spin control.
		self.advancedSeedLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label for optional deterministic seed spin control.
			_("See&d (−1 = omit):"),
			self.advancedSeedSpinCtrl,
		)

		self.advancedTopKSpinCtrl = wx.SpinCtrl(content_panel, min=0, max=1000000, initial=0)
		self.advancedTopKSpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		# Translators: Label for optional top-k spin control.
		self.advancedTopKLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label for optional top-k spin control.
			_("Top &K (0 = omit):"),
			self.advancedTopKSpinCtrl,
		)

		self.advancedStopTextCtrl = wx.TextCtrl(
			content_panel,
			size=(700, 72),
			style=wx.TE_MULTILINE,
		)
		self.advancedStopTextCtrl.Bind(wx.EVT_TEXT, self._onConversationChromeEdited)
		# Translators: Label for stop sequences multiline input.
		self.advancedStopLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label for stop sequences multiline input.
			_("St&op sequences (one per line):"),
			self.advancedStopTextCtrl,
		)

		self.advancedFreqPenaltySpinCtrl = wx.SpinCtrl(content_panel, min=-200, max=200, initial=0)
		self.advancedFreqPenaltySpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		# Translators: Label for frequency penalty spin control.
		self.advancedFreqPenaltyLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label for frequency penalty spin control.
			_("&Frequency penalty (−2..2, ×100):"),
			self.advancedFreqPenaltySpinCtrl,
		)

		self.advancedPresPenaltySpinCtrl = wx.SpinCtrl(content_panel, min=-200, max=200, initial=0)
		self.advancedPresPenaltySpinCtrl.Bind(wx.EVT_SPINCTRL, self._onConversationChromeEdited)
		# Translators: Label for presence penalty spin control.
		self.advancedPresPenaltyLabel = _labeled_control_row(
			content_panel,
			advancedSamplingSz,
			# Translators: Label for presence penalty spin control.
			_("&Presence penalty (−2..2, ×100):"),
			self.advancedPresPenaltySpinCtrl,
		)
		gen_sz.Add(advancedSamplingSz, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		mainSizer.Add(gen_sz, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self._update_advanced_controls_visibility()

		self._refreshAccountsList()
		self.onAccountChange(None)
		self.onModelChange(None)

		buttonsSizer = wx.BoxSizer(wx.HORIZONTAL)

		# Translators: Bottom-row toggles and tools button labels.
		self.streamModeCheckBox = wx.CheckBox(content_panel, label=_("&Stream mode"))
		self.streamModeCheckBox.SetValue(conf["stream"])
		self.streamModeCheckBox.Bind(wx.EVT_CHECKBOX, self._onConversationChromeEdited)
		buttonsSizer.Add(self.streamModeCheckBox, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, UI_SECTION_SPACING_PX)

		# Translators: Checkbox label to enable verbose debugging output.
		self.debugModeCheckBox = wx.CheckBox(content_panel, label=_("Debu&g mode"))
		self.debugModeCheckBox.SetValue(conf["debug"])
		self.debugModeCheckBox.Bind(wx.EVT_CHECKBOX, self._onConversationChromeEdited)
		buttonsSizer.Add(self.debugModeCheckBox, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, UI_SECTION_SPACING_PX)

		self.toolsBtn = wx.Button(
			content_panel,
			# Translators: Button opening provider-specific tools/actions menu.
			label=_("&Tools...")
		)
		self.toolsBtn.Bind(wx.EVT_BUTTON, self.onProviderTools)

		for btn in (
			self.toolsBtn,
		):
			buttonsSizer.Add(btn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		mainSizer.Add(buttonsSizer, 0, wx.ALL, UI_SECTION_SPACING_PX)

		submitCancelSizer = wx.BoxSizer(wx.HORIZONTAL)

		self.submitBtn = wx.Button(content_panel, id=wx.ID_OK)
		self.submitBtn.Bind(wx.EVT_BUTTON, self.onSubmit)
		self.submitBtn.SetDefault()
		submitCancelSizer.Add(self.submitBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)

		self.closeBtn = wx.Button(content_panel, id=wx.ID_CLOSE)
		self.closeBtn.Bind(wx.EVT_BUTTON, self.onCancel)
		submitCancelSizer.Add(self.closeBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)

		mainSizer.Add(submitCancelSizer, 0, wx.ALL | wx.ALIGN_CENTER, UI_SECTION_SPACING_PX)

		content_panel.SetSizer(mainSizer)
		rootSizer = wx.BoxSizer(wx.VERTICAL)
		rootSizer.Add(content_panel, 1, wx.EXPAND | wx.ALL, UI_DIALOG_BORDER_PX)
		self.SetSizerAndFit(rootSizer)
		rootSizer.SetSizeHints(self)
		if parent:
			self.CentreOnParent(wx.BOTH)
		else:
			self.Centre(wx.BOTH)
		self.SetEscapeId(wx.ID_CLOSE)

		self.promptTextCtrl.SetFocus()
		EVT_RESULT(self, self.OnResult)
		global activeChatDlg
		activeChatDlg = self

		self._audioPlayingPath = None  # Path of audio currently playing (for stop)
		self.timer = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self.onTimer, self.timer)
		self.timer.Start (100)
		self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
		self.Bind(wx.EVT_CLOSE, self.onCancel)
		if conversationData:
			self._loadConversation(conversationData, focus_message_history=True)
		elif not self._hub_session_restored:
			# After restoring saved tabs, always add a blank tab for a new conversation.
			self._restoreHubSessionIfNeeded(append_fresh_tab=True)
		self._syncWindowTitleFromActiveTab()
		wx.CallAfter(self._syncSharedChromeForActiveTab)

	def _onReasoningModeChange(self, evt):
		"""Update effort/adaptive visibility when reasoning checkbox toggles."""
		self.onModelChange(evt)

	def _onReasoningEffortChange(self, evt):
		"""Persist reasoning effort to config when user changes the dropdown."""
		opts = getattr(self, "_reasoningEffortOptions", ())
		idx = self.reasoningEffortChoice.GetSelection()
		if 0 <= idx < len(opts):
			self.conf["reasoningEffort"] = opts[idx][0]
		if not getattr(self, "_sync_suppress_tab_capture", False):
			try:
				self._captureConversationChromeToPage(self.get_active_page())
			except Exception:
				pass

	def _onAdaptiveThinkingChange(self, evt):
		"""Persist adaptive thinking preference when user changes the checkbox."""
		self.conf["adaptiveThinking"] = self.adaptiveThinkingCheckBox.IsChecked()
		if not getattr(self, "_sync_suppress_tab_capture", False):
			try:
				self._captureConversationChromeToPage(self.get_active_page())
			except Exception:
				pass

	def onResetSystemPrompt(self, event):
		self.systemTextCtrl.SetValue(DEFAULT_SYSTEM_PROMPT)

	def onDelete(self, event):
		self.systemTextCtrl.SetValue('')

	def addStandardMenuOptions(self, menu, include_paste=True):
		menu.Append(wx.ID_UNDO)
		menu.Append(wx.ID_REDO)
		menu.AppendSeparator()
		menu.Append(wx.ID_CUT)
		menu.Append(wx.ID_COPY)
		if include_paste:
			menu.Append(wx.ID_PASTE)
		menu.Append(wx.ID_DELETE)
		menu.AppendSeparator()
		menu.Append(wx.ID_SELECTALL)
		self.Bind(wx.EVT_MENU, self.onDelete, id=wx.ID_DELETE)

	def loadData(self):
		if not os.path.exists(DATA_JSON_FP):
			return {}
		try:
			with open(DATA_JSON_FP, 'r') as f :
				return json.loads(f.read())
		except Exception as err:
			log.error(f"loadData: {err}", exc_info=True)

	def saveData(self, force=False):
		if not force and self.data == self._orig_data:
			return
		tmp_path = DATA_JSON_FP + ".tmp"
		with open(tmp_path, "w", encoding="utf-8") as f:
			json.dump(self.data, f, indent=2, ensure_ascii=False)
		os.replace(tmp_path, DATA_JSON_FP)

	def _appendBlockToMessages(self, block):
		"""Render a completed HistoryBlock into the messages text control.
		Assign segment refs to block so j/k navigation and context menu work."""
		if block != self.firstBlock:
			block.previous.segmentBreakLine = TextSegment(self.messagesTextCtrl, "\n", block)
		# Translators: Prefix shown before user message content in history view.
		block.segmentPromptLabel = TextSegment(self.messagesTextCtrl, _("User:") + " ", block)
		prompt_text = block.prompt or ""
		if not prompt_text:
			tlist = getattr(block, "audioTranscriptList", None)
			if tlist and any(t for t in tlist):
				prompt_text = "\n".join(t for t in tlist if t).strip()
		block.segmentPrompt = TextSegment(self.messagesTextCtrl, (prompt_text or "") + "\n", block)
		# Translators: Prefix shown before assistant response content in history view.
		block.segmentResponseLabel = TextSegment(self.messagesTextCtrl, _("Assistant:") + " ", block)
		if self._showThinkingInHistory and (block.reasoningText or "").strip():
			block.segmentReasoningLabel = TextSegment(self.messagesTextCtrl, "", block)
			block.segmentReasoning = TextSegment(self.messagesTextCtrl, self._formatThinkingForHistory(block.reasoningText), block)
			block.segmentReasoningSuffix = None
			block.lastReasoningLen = len(block.reasoningText or "")
		block.segmentResponse = TextSegment(self.messagesTextCtrl, (block.responseText or "") + "\n", block)

	def _formatThinkingForHistory(self, reasoning_text):
		text = (reasoning_text or "").strip()
		if not text:
			return ""
		return f"{self._THINK_HISTORY_OPEN}{text}{self._THINK_HISTORY_CLOSE}\n"

	def _clearMessagesSegments(self):
		self.messagesTextCtrl.Clear()
		if hasattr(self.messagesTextCtrl, "firstSegment"):
			self.messagesTextCtrl.firstSegment = None
			self.messagesTextCtrl.lastSegment = None
		self.messagesTextCtrl._aihub_saved_selection = None

	def _getHistoryAnchor(self):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		if not segment:
			return None, "prompt"
		block = getattr(segment, "owner", None)
		if not block:
			return None, "prompt"
		if segment in (block.segmentPromptLabel, block.segmentPrompt):
			return block, "prompt"
		if segment in (block.segmentResponseLabel, block.segmentResponse):
			return block, "response"
		if segment in (block.segmentReasoningLabel, block.segmentReasoning):
			return block, "reasoning"
		return block, "response"

	def _restoreHistoryAnchor(self, block, part="response"):
		if not block:
			if self.firstBlock and self.firstBlock.segmentPrompt is not None:
				self.messagesTextCtrl.SetInsertionPoint(self.firstBlock.segmentPrompt.start)
			return
		target_segment = None
		if part == "reasoning":
			target_segment = block.segmentReasoning or block.segmentReasoningLabel
		if target_segment is None and part == "response":
			target_segment = block.segmentResponse or block.segmentResponseLabel
		if target_segment is None:
			target_segment = block.segmentPrompt or block.segmentPromptLabel
		if target_segment is None:
			target_segment = block.segmentResponse or block.segmentResponseLabel or block.segmentReasoning or block.segmentReasoningLabel
		if target_segment is not None:
			self.messagesTextCtrl.SetInsertionPoint(target_segment.start)

	def _rerenderMessages(self, anchor_block=None, anchor_part="response"):
		self._clearMessagesSegments()
		b = self.firstBlock
		while b:
			b.segmentBreakLine = None
			b.segmentPromptLabel = None
			b.segmentPrompt = None
			b.segmentResponseLabel = None
			b.segmentResponse = None
			b.segmentReasoningLabel = None
			b.segmentReasoning = None
			b.segmentReasoningSuffix = None
			self._appendBlockToMessages(b)
			b = b.next
		self._restoreHistoryAnchor(anchor_block, anchor_part)

	def onToggleThinkingInHistory(self, evt=None):
		anchor_block, anchor_part = self._getHistoryAnchor()
		self._showThinkingInHistory = not self._showThinkingInHistory
		self.data["showThinkingInHistory"] = self._showThinkingInHistory
		self._rerenderMessages(anchor_block, anchor_part)
		# Translators: Status message after toggling thinking text visibility in history.
		self.message(_("Thinking content shown in history.") if self._showThinkingInHistory else _("Thinking content hidden in history."))

	def _scheduleFocusMessageHistory(self):
		def _focus():
			try:
				self.messagesTextCtrl.SetFocus()
			except Exception:
				pass

		wx.CallAfter(_focus)

	def _loadConversation(self, data, *, focus_message_history=False):
		"""Load conversation data (blocks, system) from saved conversation."""
		blocks = data.get("blocks", [])
		if not isinstance(blocks, list):
			blocks = []
		# Translators: Fallback title for loaded conversations missing a name.
		conv_name = data.get("name", _("Untitled conversation"))
		idx_tab = self.notebook.GetSelection()
		if idx_tab >= 0:
			self.notebook.SetPageText(idx_tab, conv_name)
		active_pg = self.get_active_page()
		active_pg.conversationModelHint = (data.get("model") or "").strip()
		active_pg.conversationAccountKey = (data.get("accountKey") or "").strip()
		raw_ui = data.get("uiState")
		active_pg.conversationUiState = raw_ui if isinstance(raw_ui, dict) else {}
		active_pg.ephemeral = False
		active_pg.conversationSystemText = data.get("system", "") if isinstance(data.get("system"), str) else ""
		from .usage_ledger import deserialize_ledger, migrate_ledger_from_blocks

		raw_ledger = data.get("usageLedger")
		if isinstance(raw_ledger, list) and raw_ledger:
			active_pg.usageLedger = deserialize_ledger(raw_ledger)
		else:
			active_pg.usageLedger = migrate_ledger_from_blocks(blocks)
		self._clearMessagesSegments()
		self.firstBlock = None
		self.lastBlock = None
		system = data.get("system", "")
		if system and self.conf["saveSystem"]:
			self.systemTextCtrl.ChangeValue(system)
			self._lastSystem = system
		draft_prompt = data.get("draftPrompt", "")
		if isinstance(draft_prompt, str) and draft_prompt:
			self.promptTextCtrl.SetValue(draft_prompt)
			self.promptTextCtrl.SetInsertionPointEnd()
		draft_path_list = data.get("draftPathList", [])
		self.filesList = []
		if isinstance(draft_path_list, list):
			for item in draft_path_list:
				if isinstance(item, dict):
					path = item.get("path", "")
					name = item.get("name", "")
					if path:
						try:
							self.filesList.append(AttachmentFile(path, name=name or None))
						except Exception as err:
							log.warning(f"load draft image skipped {path}: {err}")
				elif isinstance(item, str) and item:
					try:
						self.filesList.append(AttachmentFile(item))
					except Exception as err:
						log.warning(f"load draft image skipped {item}: {err}")
		draft_audio_list = data.get("draftAudioPathList", [])
		self.audioPathList = []
		if isinstance(draft_audio_list, list):
			for item in draft_audio_list:
				if isinstance(item, str) and item:
					self.audioPathList.append(item)
		self.updateFilesList(focusPrompt=False)
		self.updateAudioList(focusPrompt=False)
		conv_id = data.get("id")
		if conv_id:
			self._conversationId = conv_id
		if not blocks:
			self._syncWindowTitleFromActiveTab()
			if focus_message_history:
				self._scheduleFocusMessageHistory()
			return
		prev = None
		for b in blocks:
			b.lastLen = len(b.responseText or "")
			b.lastReasoningLen = len(b.reasoningText or "")
			b.displayHeader = False
			if prev is not None:
				prev.next = b
				b.previous = prev
			else:
				self.firstBlock = b
			prev = b
		self.lastBlock = prev
		for b in blocks:
			self._appendBlockToMessages(b)
		if self.firstBlock and self.firstBlock.segmentPrompt is not None:
			self.messagesTextCtrl.SetInsertionPoint(self.firstBlock.segmentPrompt.start)
		self._syncWindowTitleFromActiveTab()
		if focus_message_history:
			self._scheduleFocusMessageHistory()
		if self.get_active_page() is active_pg:
			self._syncEphemeralChromeFromPage(active_pg)

	def _getBlocksForSave(self):
		"""Collect all blocks from firstBlock to lastBlock for saving."""
		return self._collect_blocks_from_page(self.get_active_page())

	def _autoSaveConversation(self, force=False):
		"""Save conversation state. Auto-save obeys setting unless forced (manual save)."""
		pg = self.get_active_page()
		if getattr(pg, "ephemeral", False):
			return False
		if not force and not self.conf.get("autoSaveConversation", True):
			return False
		self._captureConversationChromeToPage(pg)
		kw = self._storage_kwargs_for_page(pg, for_autosave=True, autosave_force=force)
		if kw is None:
			return False
		try:
			conv_id = self._saveConversationFromKw(kw)
			self._conversationId = conv_id
			new_title = kw.get("name")
			if new_title:
				idx = self.notebook.GetSelection()
				if idx >= 0:
					self.notebook.SetPageText(idx, new_title)
				self._syncWindowTitleFromActiveTab()
			return True
		except Exception as err:
			log.error(f"auto-save conversation: {err}", exc_info=True)
			return False

	def _saveConversation(self, evt=None):
		"""Save current conversation to storage (manual, from menu)."""
		if self._is_active_tab_ephemeral():
			return
		blocks = self._getBlocksForSave()
		draft_prompt = self.promptTextCtrl.GetValue().strip()
		if not blocks and not draft_prompt:
			# Translators: Message when manual save is requested for an empty conversation.
			ui.message(_("No messages or prompt to save."))
			return
		if self._autoSaveConversation(force=True):
			# Translators: Confirmation message after successful manual save.
			ui.message(_("Conversation saved."))
		else:
			gui.messageBox(
				# Translators: Error text when manual conversation save fails.
				_("Unable to save conversation. Check the NVDA log for details."),
				# Translators: Title for manual save error dialog.
				_("Save conversation"),
				wx.OK | wx.ICON_ERROR
			)

	def _renameConversation(self, evt=None):
		"""Rename current conversation."""
		if getattr(self.get_active_page(), "ephemeral", False):
			# Translators: Message when rename is blocked because ephemeral mode is on.
			ui.message(_("Ephemeral conversations cannot be renamed. Uncheck Ephemeral to save first."))
			return
		if not self._conversationId:
			# Translators: Message shown when trying to rename before first save.
			ui.message(_("Save the conversation first before renaming."))
			return
		entry = next((e for e in conversations.list_conversations() if e.get("id") == self._conversationId), None)
		# Translators: Default name suggested in rename dialog for untitled conversations.
		current_name = entry.get("name", _("Untitled conversation")) if entry else ""
		dlg = wx.TextEntryDialog(
			self,
			# Translators: Prompt text in rename conversation dialog.
			_("Enter new name for this conversation:"),
			# Translators: Title of rename conversation dialog.
			_("Rename conversation"),
			value=current_name
		)
		if dlg.ShowModal() != wx.ID_OK:
			dlg.Destroy()
			return
		new_name = dlg.GetValue().strip()
		dlg.Destroy()
		if not new_name:
			return
		if conversations.rename_conversation(self._conversationId, new_name):
			idx = self.notebook.GetSelection()
			if idx >= 0:
				self.notebook.SetPageText(idx, new_name)
			self._syncWindowTitleFromActiveTab()

	def _onConversationList(self, evt=None):
		"""Open the conversation history dialog."""
		from .conversations_manager_dialog import show_conversations_manager
		if self._plugin:
			wx.CallAfter(show_conversations_manager, self._plugin)

	def _newConversation(self, evt=None):
		"""Open a new conversation tab."""
		self._addConversationTab()
		wx.CallAfter(self.promptTextCtrl.SetFocus)
		# Translators: Spoken status message after creating a new conversation tab.
		ui.message(_("New conversation"))

	def onSubmit(self, evt):
		try:
			self._onSubmitImpl(evt)
		except Exception as err:
			log.error(f"onSubmit: {err}", exc_info=True)
			wp_err = getattr(self, "_worker_page", None)
			if wp_err:
				wp_err.worker = None
			self._worker_page = None
			self.enableControls()
			gui.messageBox(
				# Translators: Generic unexpected-error message in submit handler.
				_("An error occurred. More information is in the NVDA log."),
				# Translators: Title for generic add-on error dialog.
				_("OpenAI Error"),
				wx.OK | wx.ICON_ERROR
			)

	def _onSubmitImpl(self, evt):
		page = self.get_active_page()
		regenerate_block = getattr(page, "_regenerateBlock", None)
		if not regenerate_block:
			if not getattr(self, "_askPromptOverride", None) and not self.promptTextCtrl.GetValue().strip() and not self.filesList and not self.audioPathList:
				self.promptTextCtrl.SetFocus()
				return
		elif not self._block_has_submittable_content(regenerate_block):
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("This message has no prompt or attachments to regenerate."))
			page._regenerateBlock = None
			return
		if self.worker:
			return
		model = self._requireModel(modal=True)
		if not model:
			if regenerate_block:
				page._regenerateBlock = None
			return
		account = self._requireAccount(modal=True)
		if not account:
			if regenerate_block:
				page._regenerateBlock = None
			return
		if account["provider"] != model.provider:
			gui.messageBox(
				# Translators: Error when selected account provider differs from selected model provider.
				_(
					"The selected account provider ({accountProvider}) does not match the selected model provider ({modelProvider}). "
					"Please select a compatible account or model."
				).format(**{
					"accountProvider": account["provider"],
					"modelProvider": model.provider,
				}),
				# Translators: Title for account/provider mismatch dialog.
				_("Provider mismatch"),
				wx.OK | wx.ICON_ERROR
			)
			if regenerate_block:
				page._regenerateBlock = None
			return
		if not apikeymanager.get(model.provider).isReady(account_id=account["id"]):
			gui.messageBox(
				# Translators: Error explaining that the selected account is not configured/ready.
				_(
					"This model is only available with the {provider} provider and the selected account is not ready. "
					"Please verify your account API key in settings, or select another account/model."
				).format(**{
					"provider": model.provider,
				}),
				# Translators: Dynamic error dialog title when API key/account is missing for provider.
				_("No API key for {provider}").format(**{
					"provider": model.provider,
				}),
				wx.OK | wx.ICON_ERROR
			)
			if regenerate_block:
				page._regenerateBlock = None
			return

		submit_files = regenerate_block.filesList if regenerate_block else self.filesList
		submit_audio = regenerate_block.audioPathList if regenerate_block else self.audioPathList
		ok, validation_message = self.validateAttachmentsForProvider(provider=model.provider, filesList=submit_files or [])
		if not ok:
			gui.messageBox(
				validation_message,
				# Translators: Title for unsupported-attachments validation error.
				_("Unsupported attachments"),
				wx.OK | wx.ICON_ERROR
			)
			if regenerate_block:
				page._regenerateBlock = None
			return

		if not model.vision and submit_files:
			visionModels = [m.id for m in self._models if m.vision]
			gui.messageBox(
				# Translators: Error text when current model cannot process image attachments.
				_(
					"This model ({model}) does not support image description. "
					"Please select one of the following models: {models}."
				).format(**{
					"model": model.id,
					"models": ", ".join(visionModels),
				}),
				# Translators: Title for model capability validation errors.
				_("Invalid model"),
				wx.OK | wx.ICON_ERROR
			)
			if regenerate_block:
				page._regenerateBlock = None
			return
		if submit_audio and not getattr(model, "audioInput", False):
			audioModels = [m.id for m in self._models if getattr(m, "audioInput", False)]
			gui.messageBox(
				# Translators: Error text when current model cannot process audio attachments.
				_(
					"This model ({model}) does not support audio input. "
					"Please select one of the following models: {models}."
				).format(**{
					"model": model.id,
					# Translators: Fallback text when no audio-capable model is available.
					"models": ", ".join(audioModels) if audioModels else _("none available"),
				}),
				# Translators: Title of the error dialog when the user attaches audio but the chosen chat model does not accept audio input.
				_("Invalid model"),
				wx.OK | wx.ICON_ERROR
			)
			if regenerate_block:
				page._regenerateBlock = None
			return
		if (
			model.vision
			and not self.conf["images"]["resize"]
			and not self.conf["images"]["resizeInfoDisplayed"]
		):
			# Translators: Informational warning about automatic image resize behavior/cost impact.
			msg = _("Be aware that the add-on may auto-resize images before API submission to lower request sizes and costs. Adjust this feature in the AI-Hub settings if needed. This message won't show again.")
			gui.messageBox(
				msg,
				# Translators: Title for image-resize informational dialog.
				_("Image resizing"),
				wx.OK | wx.ICON_INFORMATION
			)
			self.conf["images"]["resizeInfoDisplayed"] = True
		if regenerate_block:
			self._truncateBlocksAfter(regenerate_block)
			self._resetBlockForRegenerate(regenerate_block)
			self._rerenderMessages(anchor_block=regenerate_block, anchor_part="prompt")
		system = self.systemTextCtrl.GetValue().strip()
		if self.conf["saveSystem"] and system != self._lastSystem:
			self.data["system"] = system
			self._lastSystem = system
		self.disableControls(keep_prompt_editable=self.conf.get("stream", False))
		api.processPendingEvents()
		page = self.get_active_page()
		page.conversationModelHint = model.id
		page.conversationAccountKey = account["key"]
		self._worker_page = page
		page.stopRequest = threading.Event()
		page.worker = CompletionThread(self)
		page.worker.start()

	def onCancel(self, evt):
		stop_progress_sound()  # Stop all sounds (progress, etc.) first
		global addToSession, activeChatDlg
		if addToSession and addToSession is self:
			addToSession = None
		if activeChatDlg is self:
			activeChatDlg = None
		plugin = getattr(self, "_plugin", None)
		if plugin:
			if getattr(plugin, "askRecordThread", None):
				stop_worker_thread(plugin.askRecordThread)
				plugin.askRecordThread = None
			if getattr(plugin, "_askAudioPlaying", False):
				from .ask_question import mci_stop_ask_audio
				mci_stop_ask_audio()
				plugin._askAudioPlaying = False
		for ti in range(getattr(self, "notebook", None) and self.notebook.GetPageCount() or 0):
			page = self.notebook.GetPage(ti)
			w = getattr(page, "worker", None)
			if not w:
				continue
			sr = page.stopRequest
			if sr:
				sr.set()
			stop_worker_thread(w)
			page.worker = None
		self._worker_page = None
		if plugin and hasattr(plugin, "_openMainDialogs"):
			try:
				if self in plugin._openMainDialogs:
					plugin._openMainDialogs.remove(self)
			except Exception:
				log.debug("Could not remove dialog from plugin open-dialog list", exc_info=True)
		if self.conf.get("autoSaveConversation", True):
			try:
				self._saveHubSession()
			except Exception as err:
				log.error(f"onCancel save hub session: {err}", exc_info=True)
		cleanup_temp_dir()
		for path in self._fileToRemoveAfter:
			if os.path.exists(path):
				try:
					os.remove(path)
				except Exception as err:
					log.error(f"onCancel delete file: {err}", exc_info=True)
					# Addon terminate sets _force_quiet_shutdown so NVDA shutdown is never blocked by a modal.
					if not getattr(self, "_force_quiet_shutdown", False):
						gui.messageBox(
							# Translators: Error shown when a temporary file could not be deleted.
							_("Unable to delete the file: %s\nPlease remove it manually.") % path,
							"AI-Hub",
							wx.OK | wx.ICON_ERROR
						)
		self.saveData()
		self.timer.Stop()
		self.Destroy()

	def OnResult(self, event):
		stop_progress_sound()
		is_success = (
			event.data is None
			or isinstance(event.data, (Choice, Transcription, WhisperTranscription, AudioInputResult))
		)
		if is_success and self.conf["chatFeedback"]["sndResponseReceived"]:
			winsound.PlaySound(SND_CHAT_RESPONSE_RECEIVED, winsound.SND_ASYNC)
		self.enableControls()
		self._result_snapshot_page = getattr(self, "_worker_page", None)
		try:
			if not event.data:
				self._autoSaveConversation()
				if getattr(self, "_askQuestionDeferred", False):
					self._askQuestionDeferred = False
					wx.CallAfter(self.onSubmit, None)
				return

			if isinstance(event.data, Choice):
				historyBlock = HistoryBlock()
				historyBlock.system = self.systemTextCtrl.GetValue().strip()
				historyBlock.prompt = self.promptTextCtrl.GetValue().strip()
				model = self.getCurrentModel()
				if model:
					historyBlock.model = model.id
					if self._effective_advanced_mode():
						historyBlock.temperature = self.temperatureSpinCtrl.GetValue() / 100
						historyBlock.topP = self.topPSpinCtrl.GetValue() / 100
						if hasattr(self, "advancedSeedSpinCtrl"):
							sv = self.advancedSeedSpinCtrl.GetValue()
							historyBlock.seed = int(sv) if sv >= 0 else None
						if hasattr(self, "advancedTopKSpinCtrl"):
							tk = self.advancedTopKSpinCtrl.GetValue()
							historyBlock.topK = int(tk) if tk > 0 else None
						if hasattr(self, "advancedStopTextCtrl"):
							historyBlock.stopText = self.advancedStopTextCtrl.GetValue()
						if hasattr(self, "advancedFreqPenaltySpinCtrl"):
							historyBlock.frequencyPenalty = self.advancedFreqPenaltySpinCtrl.GetValue() / 100.0
						if hasattr(self, "advancedPresPenaltySpinCtrl"):
							historyBlock.presencePenalty = self.advancedPresPenaltySpinCtrl.GetValue() / 100.0
					else:
						historyBlock.temperature = model.defaultTemperature
						historyBlock.topP = self.conf["topP"] / 100
				else:
					historyBlock.model = self._models[0].id if self._models else ""
					if self._effective_advanced_mode():
						historyBlock.temperature = self.temperatureSpinCtrl.GetValue() / 100
						historyBlock.topP = self.topPSpinCtrl.GetValue() / 100
						if hasattr(self, "advancedSeedSpinCtrl"):
							sv = self.advancedSeedSpinCtrl.GetValue()
							historyBlock.seed = int(sv) if sv >= 0 else None
						if hasattr(self, "advancedTopKSpinCtrl"):
							tk = self.advancedTopKSpinCtrl.GetValue()
							historyBlock.topK = int(tk) if tk > 0 else None
						if hasattr(self, "advancedStopTextCtrl"):
							historyBlock.stopText = self.advancedStopTextCtrl.GetValue()
						if hasattr(self, "advancedFreqPenaltySpinCtrl"):
							historyBlock.frequencyPenalty = self.advancedFreqPenaltySpinCtrl.GetValue() / 100.0
						if hasattr(self, "advancedPresPenaltySpinCtrl"):
							historyBlock.presencePenalty = self.advancedPresPenaltySpinCtrl.GetValue() / 100.0
					else:
						historyBlock.temperature = self.conf.get("temperature", 0.7)
						historyBlock.topP = self.conf["topP"] / 100
				historyBlock.maxTokens = self.maxTokensSpinCtrl.GetValue()
				historyBlock.responseText = event.data.message.content
				historyBlock.reasoningText = getattr(event.data.message, "reasoning", "") or ""
				historyBlock.responseTerminated = True
				if self.lastBlock is None:
					self.firstBlock = self.lastBlock = historyBlock
				else:
					self.lastBlock.next = historyBlock
					historyBlock.previous = self.lastBlock
					self.lastBlock = historyBlock
				self.previousPrompt = self.promptTextCtrl.GetValue()
				self.promptTextCtrl.Clear()
				self._autoSaveConversation()
				if getattr(self, "_askQuestionDeferred", False):
					self._askQuestionDeferred = False
					wx.CallAfter(self.onSubmit, None)
				return
			if isinstance(event.data, AudioInputResult):
				path = self.persistAudioPath(event.data.path)
				if path not in self.audioPathList:
					self.audioPathList.append(path)
				self.updateAudioList(focusPrompt=False)
				# Translators: Status message after attaching recorded/loaded audio input.
				self.message(_("Audio added for direct model input"))
				if getattr(self, "_askQuestionPending", False):
					self._askQuestionPending = False
					wx.CallAfter(self.onSubmit, None)
				return
			if isinstance(event.data, (Transcription, WhisperTranscription)):
				page = (
					getattr(self, "_dictation_page", None)
					or getattr(self, "_result_snapshot_page", None)
					or self.get_active_page()
				)
				text = event.data.text if event.data.text else ""
				self._insert_transcription_on_page(page, text)
				self.message(
					# Translators: Status message after inserting transcribed text into prompt.
					_("Insertion of: %s") % text,
					True
				)
				return

			# Translators: Generic fallback error text for API/result handling failures.
			errMsg = _("An error occurred. More information is in the NVDA log.")
			if isinstance(event.data, str):
				log.error(f"OpenAI add-on error: {event.data}", exc_info=True)
				errMsg = event.data if len(event.data) < 500 else event.data[:500] + "..."
			elif isinstance(event.data, (APIConnectionError, APIStatusError)):
				log.error(f"OpenAI add-on error: {event.data.message}", exc_info=True)
				errMsg = event.data.message
			else:
				log.error(f"OpenAI add-on error: {event.data}", exc_info=True)
				if hasattr(event.data, 'message'):
					errMsg = str(event.data.message)
				else:
					# Translators: Fallback error body when the background task failed with an exception object that has no message attribute (same wording as the generic API error).
					errMsg = _("An error occurred. More information is in the NVDA log.")
			url = re.search(r"https?://[^\s]+", errMsg)
			if url:
				# Translators: Prompt appended to error text when it contains a URL.
				errMsg += "\n\n" + _("Do you want to open the URL in your browser?")
			res = gui.messageBox(
				errMsg,
				# Translators: Title for runtime/API error dialog after submit.
				_("OpenAI Error"),
				wx.OK | wx.ICON_ERROR | wx.CENTRE if not url else wx.YES_NO | wx.ICON_ERROR | wx.CENTRE,
			)
			if url and res == wx.YES:
				os.startfile(url.group(0).rstrip("."))
			if "model's maximum context length is " in errMsg:
				self.modelsListCtrl.SetFocus()
			else:
				self.promptTextCtrl.SetFocus()
			if getattr(self, "_askQuestionDeferred", False):
				self._askQuestionDeferred = False
				wx.CallAfter(self.onSubmit, None)
				return

		finally:
			self._result_snapshot_page = None
			self._dictation_page = None
			wp_done = getattr(self, "_worker_page", None)
			if wp_done:
				wp_done.worker = None
				wp_done._regenerateBlock = None
			self._worker_page = None

	def onCharHook(self, evt):
		if self.conf["blockEscapeKey"] and evt.GetKeyCode() == wx.WXK_ESCAPE:
			# Translators: Hint spoken when Escape key is blocked by settings.
			self.message(_("Press Alt+F4 to close the dialog"))
		else:
			evt.Skip()

	def onTimer(self, event):
		if self.lastBlock is not None:
			block = self.lastBlock
			if block.displayHeader:
				if block != self.firstBlock:
					block.previous.segmentBreakLine = TextSegment(self.messagesTextCtrl, "\n", block)
				# Translators: Prefix shown before user message content in streaming history updates.
				block.segmentPromptLabel = TextSegment(self.messagesTextCtrl, _("User:") + ' ', block)
				prompt_text = block.prompt
				if not prompt_text:
					tlist = getattr(block, "audioTranscriptList", None)
					if tlist and any(t for t in tlist):
						prompt_text = "\n".join(t for t in tlist if t).strip()
				block.segmentPrompt = TextSegment(self.messagesTextCtrl, (prompt_text or "") + "\n", block)
				# Translators: Prefix shown before assistant response in streaming history updates.
				# Translators: Prefix shown before assistant response in streaming history updates.
				block.segmentResponseLabel = TextSegment(self.messagesTextCtrl, _("Assistant:") + ' ', block)
				block.displayHeader = False
			l = len(block.responseText)
			if block.lastLen == 0 and l > 0:
				block.responseText = block.responseText.lstrip()
				l = len(block.responseText)
			if l > block.lastLen:
				newText = block.responseText[block.lastLen:]
				first_assistant_content = block.lastLen == 0 and bool(newText.strip())
				block.lastLen = l
				if block.segmentResponse is None:
					block.segmentResponse = TextSegment(self.messagesTextCtrl, newText, block)
				else:
					block.segmentResponse.appendText(newText)
				if first_assistant_content:
					ip_after_label = None
					lbl = getattr(block, "segmentResponseLabel", None)
					if lbl is not None:
						try:
							ip_after_label = int(lbl.end)
						except Exception:
							ip_after_label = None
					if ip_after_label is None and block.segmentResponse is not None:
						try:
							ip_after_label = int(block.segmentResponse.start)
						except Exception:
							pass
					self._schedule_focus_message_history_on_assistant_response(ip_after_label)
			reasoning_len = len(block.reasoningText or "")
			last_reasoning_len = getattr(block, "lastReasoningLen", 0)
			if self._showThinkingInHistory and reasoning_len > last_reasoning_len:
				reasoning_delta = (block.reasoningText or "")[last_reasoning_len:]
				reasoning_suffix = getattr(block, "segmentReasoningSuffix", None)
				if block.segmentReasoning is not None and reasoning_suffix is not None:
					block.segmentReasoning.appendText(reasoning_delta)
				elif not (block.responseText or "").strip():
					block.segmentReasoningLabel = TextSegment(self.messagesTextCtrl, "", block)
					block.segmentReasoning = TextSegment(
						self.messagesTextCtrl, self._THINK_HISTORY_OPEN + (block.reasoningText or ""), block
					)
					block.segmentReasoningSuffix = TextSegment(self.messagesTextCtrl, self._THINK_HISTORY_CLOSE, block)
				else:
					anchor_block, anchor_part = self._getHistoryAnchor()
					if anchor_block is None:
						anchor_block = block
						anchor_part = "response"
					self._rerenderMessages(anchor_block=anchor_block, anchor_part=anchor_part)
				block.lastReasoningLen = reasoning_len
			if (
				self._showThinkingInHistory
				and block.responseTerminated
				and (block.reasoningText or "").strip()
				and block.segmentReasoning is None
			):
				anchor_block, anchor_part = self._getHistoryAnchor()
				if anchor_block is None:
					anchor_block, anchor_part = block, "response"
				self._rerenderMessages(anchor_block=anchor_block, anchor_part=anchor_part)

	def addEntry(self, accelEntries, modifiers, key, func):
		id_ = wx.Window.NewControlId()
		self.Bind(wx.EVT_MENU, func, id=id_)
		accelEntries.append ( (modifiers, key, id_))

	def addShortcutsForPage(self, page):
		page.messagesTextCtrl.Bind(wx.EVT_TEXT_COPY, self.onCopyMessage)
		page.messagesTextCtrl.Bind(wx.EVT_LEFT_UP, self._syncMessagesSelectionCache)
		page.messagesTextCtrl.Bind(wx.EVT_KEY_UP, self._syncMessagesSelectionCache)
		accelEntries = []
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("M"), self.onCurrentMessage)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, wx.WXK_UP, self.onPreviousMessage)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, wx.WXK_DOWN, self.onNextMessage)
		self.addEntry(accelEntries, wx.ACCEL_SHIFT, ord("B"), self.onMoveToStartOfThinking)
		self.addEntry(accelEntries, wx.ACCEL_SHIFT, ord("N"), self.onMoveToEndOfThinking)
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("B"), self.onMoveToBeginOfContent)
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("N"), self.onMoveToEndOfContent)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, ord("C"), lambda evt: self.onCopyMessage(evt, True))
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("D"), self.onDeleteBlock)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, ord("R"), self.onRegenerateBlock)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, ord("S"), self.onSaveHistory)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("P"), self.onAudioPlayPause)
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_RETURN, self.onMessageProperties)
		self.addEntry(accelEntries, wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_RETURN, self.onConversationProperties)
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, wx.WXK_SPACE, lambda evt: self.onWebviewMessage(evt, True))
		self.addEntry(accelEntries, wx.ACCEL_SHIFT, wx.WXK_SPACE, lambda evt: self.onWebviewMessage(evt, False))
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("R"), self.onToggleThinkingInHistory)
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_LEFT, self.onCopyResponseToSystem)
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_RIGHT, self.onCopyPromptToPrompt)
		accelTable = wx.AcceleratorTable(accelEntries)
		page.messagesTextCtrl.SetAcceleratorTable(accelTable)

		accelEntries = []
		self.addEntry(accelEntries, wx.ACCEL_CTRL, wx.WXK_UP, self.onPreviousPrompt)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("r"), self.onRecord)
		accelTable = wx.AcceleratorTable(accelEntries)
		page.promptTextCtrl.SetAcceleratorTable(accelTable)

		accelEntries = []
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, wx.WXK_F2, self._renameConversation)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("N"), self._newConversation)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("S"), self._onManualSaveRequested)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("L"), self._onConversationList)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("r"), self.onRecord)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("i"), self.onFileDescriptionFromFilePath)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("u"), self.onFileDescriptionFromURL)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("e"), self.onFileDescriptionFromScreenshot)
		self.addEntry(accelEntries, wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("T"), self.onProviderTools)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("W"), self._onCloseConversationTab)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.SetAcceleratorTable(accelTable)

	def getFilesContent(
		self,
		filesList: list = None,
		prompt: str = None
	) -> list:
		"""Build OpenAI-style content parts (image_url / input_file) for the given attachments."""
		conf = self.conf
		if not filesList:
			filesList = self.filesList
		parts = []
		if prompt:
			parts.append({
				"type": ContentType.TEXT,
				"text": prompt
			})
		for attachment in filesList:
			path = attachment.path
			if attachment.type == AttachmentFileTypes.IMAGE_URL:
				parts.append({"type": ContentType.IMAGE_URL, "image_url": {"url": path}})
			elif attachment.type == AttachmentFileTypes.DOCUMENT_URL:
				parts.append({"type": ContentType.INPUT_FILE, "file_url": path, "filename": attachment.name})
			elif attachment.type == AttachmentFileTypes.IMAGE_LOCAL:
				if conf["images"]["resize"]:
					ensure_temp_dir()
					fd, path_resized_image = tempfile.mkstemp(suffix=".jpg", dir=TEMP_DIR)
					os.close(fd)
					if resize_image(
						path,
						max_width=conf["images"]["maxWidth"],
						max_height=conf["images"]["maxHeight"],
						quality=conf["images"]["quality"],
						target=path_resized_image
					):
						path = path_resized_image
				base64_image = encode_image(path)
				mime_type, _ = mimetypes.guess_type(path)
				parts.append({
					"type": ContentType.IMAGE_URL,
					"image_url": {
						"url": f"data:{mime_type};base64,{base64_image}"
					}
				})
			elif attachment.type == AttachmentFileTypes.DOCUMENT_LOCAL:
				parts.append({
					"type": ContentType.INPUT_FILE,
					"file_path": path,
					"filename": attachment.name,
				})
			else:
				raise ValueError(f"Invalid attachment type for {path}")
		return parts

	def getAudioContent(self, audioPaths=None, prompt=None):
		"""Build input_audio content for audio-capable models."""
		audioPaths = audioPaths or self.audioPathList
		if not audioPaths:
			return []
		content = []
		if prompt:
			content.append({"type": ContentType.TEXT, "text": prompt})
		for path in audioPaths:
			path_str = path if isinstance(path, str) else getattr(path, "path", str(path))
			if not os.path.exists(path_str):
				continue
			ext = os.path.splitext(path_str)[1].lower()
			fmt = AUDIO_EXT_TO_FORMAT.get(ext, "wav")
			with open(path_str, "rb") as f:
				data_b64 = base64.b64encode(f.read()).decode("utf-8")
			content.append({
				"type": ContentType.INPUT_AUDIO,
				"input_audio": {"data": data_b64, "format": fmt}
			})
		return content

	def getMessages(
		self,
		messages: list,
		*,
		until_block=None,
	):
		"""Append chat history blocks to ``messages``.

		When ``until_block`` is set, include every prior turn (user + assistant) and
		only the user turn for ``until_block`` (no assistant reply), then stop.
		"""
		block = self.firstBlock
		while block:
			userContent = []
			if block.filesList or getattr(block, "audioPathList", None):
				if block.prompt:
					userContent.append({"type": ContentType.TEXT, "text": block.prompt})
				if block.filesList:
					userContent.extend(self.getFilesContent(block.filesList, prompt=None))
				if getattr(block, "audioPathList", None):
					tlist = getattr(block, "audioTranscriptList", None)
					if tlist is not None and len(tlist) == len(block.audioPathList) and any(t for t in tlist):
						for t in tlist:
							if t:
								userContent.append({"type": ContentType.TEXT, "text": t})
					else:
						userContent.extend(self.getAudioContent(block.audioPathList, prompt=None))
			elif block.prompt:
				userContent = block.prompt
			if userContent:
				messages.append({
					"role": Role.USER,
					"content": userContent
				})
			stop_after = until_block is not None and block is until_block
			if not stop_after and block.responseText:
				messages.append({
					"role": Role.ASSISTANT,
					"content": block.responseText
				})
			if stop_after:
				break
			block = block.next

	def onSetFocus(self, evt):
		global activeChatDlg
		activeChatDlg = self
		self.lastFocusedItem = evt.GetEventObject()
		evt.Skip()

	def _schedule_focus_message_history_on_assistant_response(self, insertion_point_after_label=None):
		"""If the setting is on, focus Messages and place the caret after the ``Assistant:`` label (first content token)."""
		try:
			if not (self.conf.get("chatFeedback") or {}).get("focusHistoryOnAssistantResponse", False):
				return
			wx.CallAfter(self._focus_message_history_control_impl, insertion_point_after_label)
		except Exception:
			pass

	def _focus_message_history_control_impl(self, insertion_point_after_label=None):
		"""``insertion_point_after_label`` is ``TextSegment.end`` for ``segmentResponseLabel`` (after translated prefix)."""
		try:
			if not self.IsShown():
				return
		except Exception:
			return
		if not self._foreground_window_is_this_dialog():
			return
		try:
			msgs = self.messagesTextCtrl
			msgs.SetFocus()
			if insertion_point_after_label is not None:
				ip = int(insertion_point_after_label)
				text_len = len(msgs.GetValue())
				if 0 <= ip <= text_len:
					msgs.SetInsertionPoint(ip)
		except Exception:
			pass

	def _foreground_window_is_this_dialog(self) -> bool:
		"""True when the foreground window's root top-level is this dialog (user did not switch away)."""
		dlg_hwnd = getattr(self, "_cached_dialog_hwnd", None)
		if not dlg_hwnd:
			try:
				dlg_hwnd = self.GetHandle()
			except Exception:
				return False
			if dlg_hwnd:
				self._cached_dialog_hwnd = dlg_hwnd
		if not dlg_hwnd:
			return False
		try:
			user32 = ctypes.windll.user32
			fg = user32.GetForegroundWindow()
			if not fg:
				return False
			return user32.GetAncestor(fg, _GA_ROOT) == dlg_hwnd
		except Exception:
			return False

	def canAutoReadStreamingResponse(self) -> bool:
		"""Return True when streamed response speech can be auto-read."""
		cf = self.conf.get("chatFeedback") or {}
		if not cf.get("speechResponseReceived", True):
			return False
		if not self._foreground_window_is_this_dialog():
			return False
		# Streaming speech is triggered from CompletionThread. wx.GetActiveWindow /
		# FindFocus must run on the GUI thread; off-thread they often return None and
		# blocked all streaming announcements before.
		try:
			off_gui_thread = threading.current_thread() is not threading.main_thread()
		except Exception:
			off_gui_thread = True
		if off_gui_thread:
			msgs_hwnd = getattr(self, "_cached_messages_hwnd", None)
			if not msgs_hwnd:
				try:
					msgs_hwnd = self.messagesTextCtrl.GetHandle()
				except Exception:
					msgs_hwnd = None
				if msgs_hwnd:
					self._cached_messages_hwnd = msgs_hwnd
			try:
				fo = api.getFocusObject()
				hw = getattr(fo, "windowHandle", None) if fo else None
				if hw and msgs_hwnd and hw == msgs_hwnd:
					return False
			except Exception:
				pass
			return True
		try:
			if not self.IsShown():
				return False
		except Exception:
			return False
		try:
			active = wx.GetActiveWindow()
		except Exception:
			active = None
		if active is not None:
			try:
				if wx.GetTopLevelParent(active) is not self:
					return False
			except Exception:
				if active is not self:
					return False
		try:
			focus = wx.Window.FindFocus()
		except Exception:
			focus = None
		if focus is self.messagesTextCtrl:
			return False
		if focus is not None:
			try:
				return wx.GetTopLevelParent(focus) is self
			except Exception:
				return False
		if (
			getattr(self, "worker", None)
			and self.conf.get("stream")
			and getattr(self, "lastFocusedItem", None) is self.promptTextCtrl
		):
			return True
		return False

	def message(
		self,
		msg: str,
		speechOnly: bool = False,
		onPromptFieldOnly: bool = False
	):
		if not msg:
			return
		if onPromptFieldOnly and self.lastFocusedItem is not self.promptTextCtrl:
			return
		if (
			not onPromptFieldOnly
			or (
				onPromptFieldOnly
				and self.conf["chatFeedback"]["speechResponseReceived"]
			)
		):
			queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, msg)
		if not speechOnly:
			queueHandler.queueFunction(queueHandler.eventQueue, braille.handler.message, msg)

	def _resolveDictationConfig(self):
		model = None
		account = None
		try:
			model = self.getCurrentModel()
		except Exception:
			model = None
		try:
			account = self.getCurrentAccount()
		except Exception:
			account = None
		use_direct = bool(getattr(model, "audioInput", False))
		transcription_provider = get_transcription_provider(self.conf["audio"])
		transcription_account_id = None
		transcription_model = None
		if not use_direct:
			if transcription_provider == TranscriptionProvider.OPENAI:
				transcription_account_id = self.conf["audio"].get("openaiTranscriptionAccountId", "").strip()
				if not transcription_account_id and account and account.get("provider") == Provider.OpenAI:
					transcription_account_id = account.get("id")
				model_id = getattr(model, "id", "")
				if (
					model
					and getattr(model, "provider", "") == Provider.OpenAI
					and isinstance(model_id, str)
					and (model_id == "whisper-1" or "transcribe" in model_id.lower())
				):
					transcription_model = model_id
			elif transcription_provider == TranscriptionProvider.MISTRAL:
				transcription_account_id = self.conf["audio"].get("mistralTranscriptionAccountId", "").strip()
				if not transcription_account_id and account and account.get("provider") == Provider.MistralAI:
					transcription_account_id = account.get("id")
				model_id = getattr(model, "id", "")
				if (
					model
					and getattr(model, "provider", "") == Provider.MistralAI
					and isinstance(model_id, str)
					and ("voxtral" in model_id.lower() or "transcribe" in model_id.lower())
				):
					transcription_model = model_id
		return {
			"use_direct": use_direct,
			"transcription_provider": transcription_provider,
			"transcription_account_id": transcription_account_id,
			"transcription_model": transcription_model,
		}

	def onRecord(self, evt):
		if isinstance(self.worker, RecordThread):
			self.onStopRecord(evt)
			return
		if self.worker:
			return
		cfg = self._resolveDictationConfig()
		self.disableControls()
		page = self.get_active_page()
		self._dictation_page = page
		self._worker_page = page
		page.worker = RecordThread(
			self.client,
			self,
			conf=self.conf["audio"],
			responseFormat="json",
			useDirectAudio=cfg["use_direct"],
			transcriptionProvider=cfg["transcription_provider"],
			transcriptionAccountId=cfg["transcription_account_id"],
			transcriptionModel=cfg["transcription_model"],
		)
		page.worker.start()

	def onProviderTools(self, evt):
		show_tools_menu(self, self.toolsBtn)

	def onStopRecord(self, evt):
		self.disableControls()
		wp = getattr(self, "_worker_page", None)
		if wp and isinstance(wp.worker, RecordThread):
			stop_worker_thread(wp.worker)
			wp.worker = None
		self.enableControls()

	def disableControls(self, keep_prompt_editable=False):
		self.submitBtn.Disable()
		self.closeBtn.Disable()
		self.toolbarCloseBtn.Disable()
		self.toolsBtn.Disable()
		self.reasoningModeCheckBox.Disable()
		self.reasoningEffortChoice.Disable()
		self.adaptiveThinkingCheckBox.Disable()
		self.webSearchCheckBox.Disable()
		try:
			self.openRouterWebSearchCheckBox.Disable()
		except Exception:
			pass
		self.maxTokensSpinCtrl.Disable()
		self.renameConversationBtn.Disable()
		self.newConversationBtn.Disable()
		self.conversationListBtn.Disable()
		self.saveConversationBtn.Disable()
		page = getattr(self, "_worker_page", None) or self.get_active_page()
		if not keep_prompt_editable:
			page.promptTextCtrl.SetEditable(False)
		page.systemTextCtrl.SetEditable(False)
		page.accountListCtrl.Disable()
		page.modelsListCtrl.Disable()
		page.filesListCtrl.Disable()
		page.audioListCtrl.Disable()
		try:
			self.advancedSamplingCheckBox.Disable()
		except Exception:
			pass
		try:
			self.streamModeCheckBox.Disable()
			self.debugModeCheckBox.Disable()
		except Exception:
			pass
		if self._effective_advanced_mode():
			self.temperatureSpinCtrl.Disable()
			self.topPSpinCtrl.Disable()
			for _w in (
				getattr(self, "advancedSeedSpinCtrl", None),
				getattr(self, "advancedTopKSpinCtrl", None),
				getattr(self, "advancedStopTextCtrl", None),
				getattr(self, "advancedFreqPenaltySpinCtrl", None),
				getattr(self, "advancedPresPenaltySpinCtrl", None),
			):
				if _w is not None:
					try:
						_w.Disable()
					except Exception:
						pass

	def enableControls(self):
		self.submitBtn.Enable()
		self.closeBtn.Enable()
		self.toolbarCloseBtn.Enable()
		self.toolsBtn.Enable()
		try:
			model = self.getCurrentModel()
			if model:
				if model.reasoning:
					if getattr(model, "reasoning_always_on", False):
						self.reasoningModeCheckBox.SetValue(True)
						self.reasoningModeCheckBox.Enable(False)
					else:
						self.reasoningModeCheckBox.Enable()
					reasoning_on = self.reasoningModeCheckBox.IsChecked()
					if model.reasoning_effort_options and reasoning_on:
						self.reasoningEffortChoice.Enable()
					if model.adaptive_choice_visible and reasoning_on:
						self.adaptiveThinkingCheckBox.Enable()
				self._updateWebSearchCheckbox(model)
				self._updateOpenRouterWebSearchCheckbox(model)
		except (IndexError, TypeError):
			pass
		self.maxTokensSpinCtrl.Enable()
		self.newConversationBtn.Enable()
		self.conversationListBtn.Enable()
		self._syncSaveControlsForEphemeral()
		if getattr(self, "notebook", None):
			for ti in range(self.notebook.GetPageCount()):
				p = self.notebook.GetPage(ti)
				p.systemTextCtrl.SetEditable(True)
				p.promptTextCtrl.SetEditable(True)
				p.accountListCtrl.Enable()
				p.modelsListCtrl.Enable()
				p.filesListCtrl.Enable()
				p.audioListCtrl.Enable()
		try:
			self.advancedSamplingCheckBox.Enable()
		except Exception:
			pass
		try:
			self.streamModeCheckBox.Enable()
			self.debugModeCheckBox.Enable()
		except Exception:
			pass
		if self._effective_advanced_mode():
			self.temperatureSpinCtrl.Enable()
			self.topPSpinCtrl.Enable()
			for _w in (
				getattr(self, "advancedSeedSpinCtrl", None),
				getattr(self, "advancedTopKSpinCtrl", None),
				getattr(self, "advancedStopTextCtrl", None),
				getattr(self, "advancedFreqPenaltySpinCtrl", None),
				getattr(self, "advancedPresPenaltySpinCtrl", None),
			):
				if _w is not None:
					try:
						_w.Enable()
					except Exception:
						pass
		try:
			self.Layout()
		except Exception:
			pass
		self.updateFilesList(False)
