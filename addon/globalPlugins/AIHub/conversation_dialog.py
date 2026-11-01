import ctypes
import os
import re
import sys
import threading
import winsound
import gui
import wx

import addonHandler
import api
import braille
import queueHandler
import speech
from logHandler import log

from . import apikeymanager
from .conversation_tabs import ConversationTabsMixin
from .conversation_persistence import ConversationPersistenceMixin
from .conversation_messages import ConversationMessagesMixin
from .conversation_dictation import ConversationDictationMixin
from .audiohandlers import AudioHandlersMixin
from .chatcompletion import CompletionThread
from .historyhandlers import HistoryHandlersMixin
from .filehandlers import FileHandlersMixin
from .modelhandlers import ModelHandlersMixin
from .consts import (
	LIBS_BASE,
	cleanup_temp_dir,
	stop_progress_sound,
	TOP_P_MIN,
	TOP_P_MAX,
	DEFAULT_SYSTEM_PROMPT,
	SND_CHAT_RESPONSE_RECEIVED,
	UI_DIALOG_BORDER_PX,
	UI_SECTION_SPACING_PX,
)
from .history import HistoryBlock, TextSegment
from .detached_branch import assistant_label_for_block
from .recordthread import WhisperTranscription, AudioInputResult
from .thread_shutdown import stop_worker_thread
from .resultevent import EVT_RESULT_ID
from .toolsmenu import show_tools_menu
from .attachment_ui import AttachmentListUIMixin
from .conversation_chrome import GenerationChromePanel
from .conversation_session_panel import ConversationSessionPanel

sys.path.insert(0, LIBS_BASE)
from markdown_it import MarkdownIt
sys.path.remove(LIBS_BASE)

from .apiclient import APIConnectionError, APIStatusError, Choice, Transcription

addonHandler.initTranslation()


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
		log.debug("Ignored error", exc_info=True)
	sizer.Add(lbl, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
	sizer.Add(ctrl, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
	return lbl


class ConversationNotebook(wx.Notebook):
	"""Notebook hosting parallel conversation sessions."""


class ConversationDialog(
	ConversationTabsMixin,
	ConversationPersistenceMixin,
	ConversationMessagesMixin,
	ConversationDictationMixin,
	ModelHandlersMixin,
	AttachmentListUIMixin,
	FileHandlersMixin,
	AudioHandlersMixin,
	HistoryHandlersMixin,
	wx.Dialog,
):
	"""NVDA AI-Hub conversation window (parallel sessions in a notebook)."""
	_THINK_HISTORY_OPEN = "<think>\n"
	_THINK_HISTORY_CLOSE = "\n</think>\n"
	_THINKING_WRAP_STREAMING = "streaming"
	_THINKING_WRAP_COMPLETE = "complete"

	def _onNotebookPageChanging(self, evt):
		try:
			old = evt.GetOldSelection()
			if old >= 0 and getattr(self, "notebook", None):
				old_page = self.notebook.GetPage(old)
				self._captureEphemeralToPage(old_page)
				self._captureConversationChromeToPage(old_page)
		except Exception:
			log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)
		evt.Skip()

	def _onSystemTextEdited(self, evt):
		if getattr(self, "_sync_suppress_tab_capture", False):
			evt.Skip()
			return
		try:
			self.get_active_page().conversationSystemText = self.systemTextCtrl.GetValue()
		except Exception:
			log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)
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
		try:
			st["maxTokens"] = self.maxTokensSpinCtrl.GetValue()
		except Exception:
			log.debug("Ignored error", exc_info=True)
		if model:
			self._generation_chrome.capture(st, model)
			try:
				st["advancedSampling"] = self.advancedSamplingCheckBox.IsChecked()
			except Exception:
				log.debug("Ignored error", exc_info=True)
			if hasattr(self, "streamModeCheckBox"):
				st["stream"] = self.streamModeCheckBox.IsChecked()
			if hasattr(self, "debugModeCheckBox"):
				st["debug"] = self.debugModeCheckBox.IsChecked()
			if self._effective_advanced_mode():
				if model.allows_request_parameter("temperature") and hasattr(self, "temperatureSpinCtrl"):
					st["temperature"] = self.temperatureSpinCtrl.GetValue()
				if model.allows_request_parameter("top_p") and hasattr(self, "topPSpinCtrl"):
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
				log.debug("Ignored error", exc_info=True)
			self._update_advanced_controls_visibility()
		model = self.getCurrentModel()
		try:
			self.onModelChange(None)
		except Exception:
			log.debug("Ignored error", exc_info=True)
		if "reasoningMode" in st:
			self._apply_reasoning_mode_from_state(st)
		model = self.getCurrentModel()
		if model:
			self._generation_chrome.apply(st, model)
		if "maxTokens" in st:
			try:
				self.maxTokensSpinCtrl.SetValue(int(st["maxTokens"]))
			except Exception:
				log.debug("Ignored error", exc_info=True)
		if self._effective_advanced_mode() and model:
			if model.allows_request_parameter("temperature") and "temperature" in st and hasattr(self, "temperatureSpinCtrl"):
				try:
					self.temperatureSpinCtrl.SetValue(int(st["temperature"]))
				except Exception:
					log.debug("Ignored error", exc_info=True)
			if model.allows_request_parameter("top_p") and "topP" in st and hasattr(self, "topPSpinCtrl"):
				try:
					self.topPSpinCtrl.SetValue(int(st["topP"]))
				except Exception:
					log.debug("Ignored error", exc_info=True)
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
						log.debug("Ignored error", exc_info=True)
			if "advancedStopText" in st and hasattr(self, "advancedStopTextCtrl"):
				try:
					self.advancedStopTextCtrl.SetValue(str(st["advancedStopText"] or ""))
				except Exception:
					log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)

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
				log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)
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

	def _onAdvancedSamplingToggle(self, evt):
		self._update_advanced_controls_visibility()
		try:
			self.onModelChange(evt)
		except Exception:
			log.debug("Ignored error", exc_info=True)
		try:
			self._captureConversationChromeToPage(self.get_active_page())
		except Exception:
			log.debug("Ignored error", exc_info=True)
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
		self._orig_data = self.data.copy()
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

		self._generation_chrome = GenerationChromePanel(self, content_panel)
		gen_sz = self._generation_chrome.build()

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

	def _apply_reasoning_mode_from_state(self, st):
		"""Restore the unified reasoning combo (mode + effort) from saved tab state."""
		model = self.getCurrentModel()
		if not model:
			return
		mode_opts = getattr(self, "_reasoningModeOptions", ())
		if not mode_opts or not self.reasoningModeChoice.IsShown():
			return
		sel_mode = st.get("reasoningSelectionMode")
		if sel_mode in ("disabled", "enabled", "adaptive"):
			effort = st.get("reasoningEffort")
			if sel_mode == "disabled":
				idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "disabled"), None)
			elif sel_mode == "adaptive":
				idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "adaptive"), None)
			else:
				idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "enabled" and e == effort), None)
				if idx is None:
					idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "enabled"), None)
		else:
			enabled = bool(st.get("reasoningMode", True))
			adaptive = bool(st.get("adaptiveThinking", False)) and getattr(model, "adaptive_choice_visible", False)
			effort = st.get("reasoningEffort")
			if not enabled:
				idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "disabled"), None)
			elif adaptive:
				idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "adaptive"), None)
			else:
				idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "enabled" and e == effort), None)
				if idx is None:
					idx = next((i for i, (m, e, l) in enumerate(mode_opts) if m == "enabled"), None)
		if idx is None:
			return
		self.reasoningModeChoice.SetSelection(idx)
		self._apply_reasoning_selection(model)
		try:
			self.onModelChange(chrome_source=self.reasoningModeChoice)
		except Exception:
			log.debug("Ignored error", exc_info=True)

	def _apply_reasoning_selection(self, model):
		"""Persist config/per-model state from the current reasoning combo selection."""
		opt = self._selected_reasoning_option(model)
		if opt is None:
			return
		mode, effort, _label = opt
		if getattr(model, "adaptive_choice_visible", False):
			self.conf["adaptiveThinking"] = mode == "adaptive"
		if mode == "enabled" and effort is not None:
			self.conf["reasoningEffort"] = effort
		if not getattr(model, "reasoning_always_on", False):
			self._persist_reasoning_mode(model, mode != "disabled")

	def _onReasoningModeChange(self, evt):
		"""Apply the unified reasoning combo (disabled / effort level / adaptive)."""
		model = self.getCurrentModel()
		if model:
			self._apply_reasoning_selection(model)
		self.onModelChange(chrome_source=self.reasoningModeChoice)
		if not getattr(self, "_sync_suppress_tab_capture", False):
			try:
				self._captureConversationChromeToPage(self.get_active_page())
			except Exception:
				log.debug("Ignored error", exc_info=True)

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
		assistant_label = assistant_label_for_block(
			self._conversation_scope(),
			block,
			default_label=_("Assistant:") + " ",
		)
		block.segmentResponseLabel = TextSegment(self.messagesTextCtrl, assistant_label, block)
		if self._showThinkingInHistory and (block.reasoningText or "").strip():
			block.segmentReasoningLabel = TextSegment(self.messagesTextCtrl, "", block)
			block.segmentReasoning = TextSegment(self.messagesTextCtrl, self._formatThinkingForHistory(block.reasoningText), block)
			block.segmentReasoningSuffix = None
			block.thinkingWrapState = self._THINKING_WRAP_COMPLETE
			block.lastReasoningLen = len(block.reasoningText or "")
		block.segmentResponse = TextSegment(self.messagesTextCtrl, (block.responseText or "") + "\n", block)
		block.displayHeader = False

	def _formatThinkingForHistory(self, reasoning_text):
		from .propertiesutils import _normalize_reasoning_for_properties

		text = _normalize_reasoning_for_properties(reasoning_text)
		if not text:
			return ""
		return f"{self._THINK_HISTORY_OPEN}{text}{self._THINK_HISTORY_CLOSE}\n"

	def _closeThinkingHistoryTags(self, block):
		"""Append the closing thinking marker after streaming reasoning (open tag only)."""
		if not self._showThinkingInHistory:
			return
		if block.segmentReasoning is None:
			return
		if block.thinkingWrapState != self._THINKING_WRAP_STREAMING:
			return
		if block.segmentReasoningSuffix is not None:
			block.thinkingWrapState = self._THINKING_WRAP_COMPLETE
			return
		block.segmentReasoningSuffix = TextSegment(
			self.messagesTextCtrl, self._THINK_HISTORY_CLOSE, block
		)
		block.thinkingWrapState = self._THINKING_WRAP_COMPLETE

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
		if segment == getattr(block, "segmentReasoningSuffix", None):
			return block, "reasoning"
		return block, "response"

	def _restoreHistoryAnchor(self, block, part="response"):
		# During active streaming the user may be reading elsewhere; re-renders must not
		# repeatedly pull the caret back to the assistant reply.
		if block and block is self.lastBlock and not block.responseTerminated:
			return
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
			b.thinkingWrapState = ""
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
				log.debug("Ignored error", exc_info=True)

		wx.CallAfter(_focus)

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
			self._detachBlocksAfterForRegenerate(regenerate_block)
			self._resetBlockForRegenerate(regenerate_block)
			self._rerenderMessages(anchor_block=regenerate_block, anchor_part="prompt")
			self._announceDetachedBranchIfAny()
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
				if block.segmentPrompt is not None:
					block.displayHeader = False
				else:
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
					self._position_assistant_reply_start(block)
			l = len(block.responseText)
			if block.lastLen == 0 and l > 0:
				block.responseText = block.responseText.lstrip()
				l = len(block.responseText)
			if l > block.lastLen:
				newText = block.responseText[block.lastLen:]
				first_assistant_content = block.lastLen == 0 and bool(newText.strip())
				block.lastLen = l
				if first_assistant_content:
					# Close the thinking block BEFORE creating the answer segment so
					# the </think> marker is positioned between reasoning and answer.
					# TextSegment always appends at the end of the control, so segment
					# creation order is visual order.
					self._closeThinkingHistoryTags(block)
				if block.segmentResponse is None:
					block.segmentResponse = TextSegment(self.messagesTextCtrl, newText, block)
				else:
					block.segmentResponse.appendText(newText)
				if first_assistant_content:
					self._position_assistant_reply_start(block)
			reasoning_len = len(block.reasoningText or "")
			last_reasoning_len = getattr(block, "lastReasoningLen", 0)
			if self._showThinkingInHistory and reasoning_len > last_reasoning_len:
				reasoning_delta = (block.reasoningText or "")[last_reasoning_len:]
				if block.segmentReasoning is not None:
					block.segmentReasoning.appendText(reasoning_delta)
				elif not (block.responseText or "").strip():
					block.segmentReasoningLabel = TextSegment(self.messagesTextCtrl, "", block)
					block.segmentReasoning = TextSegment(
						self.messagesTextCtrl, self._THINK_HISTORY_OPEN + (block.reasoningText or ""), block
					)
					block.segmentReasoningSuffix = None
					block.thinkingWrapState = self._THINKING_WRAP_STREAMING
				else:
					anchor_block, anchor_part = self._getHistoryAnchor()
					if anchor_block is None:
						anchor_block = block
						anchor_part = "response"
					self._rerenderMessages(anchor_block=anchor_block, anchor_part=anchor_part)
				block.lastReasoningLen = reasoning_len
			if getattr(block, "_needsHistoryRerender", False):
				block._needsHistoryRerender = False
				anchor_block, anchor_part = self._getHistoryAnchor()
				if anchor_block is None:
					anchor_block, anchor_part = block, "response"
				self._rerenderMessages(anchor_block=anchor_block, anchor_part=anchor_part)
			elif block.responseTerminated and block.thinkingWrapState == self._THINKING_WRAP_STREAMING:
				self._closeThinkingHistoryTags(block)
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

	def onSetFocus(self, evt):
		global activeChatDlg
		activeChatDlg = self
		self.lastFocusedItem = evt.GetEventObject()
		evt.Skip()

	def _assistant_content_caret_ip(self, block):
		"""Live insertion point for the start of ``block``'s assistant content (after the label).

		Computed on demand because segment offsets shift as content streams in and can be
		rebuilt entirely by ``_rerenderMessages``; a value captured earlier may be stale.
		"""
		lbl = getattr(block, "segmentResponseLabel", None)
		if lbl is not None:
			try:
				return int(lbl.end)
			except Exception:
				log.debug("Ignored error", exc_info=True)
		resp = getattr(block, "segmentResponse", None)
		if resp is not None:
			try:
				return int(resp.start)
			except Exception:
				log.debug("Ignored error", exc_info=True)
		return None

	def _position_assistant_reply_start(self, block):
		"""Place the caret once at the start of ``block``'s assistant reply (stream or not).

		Runs synchronously when the "Assistant:" label exists, or on first content if the
		label path was skipped (e.g. regenerate). Optionally focuses the history field once
		when ``focusHistoryOnAssistantResponse`` is enabled. Never repeats for the same block.
		"""
		if block is None or getattr(block, "_assistantCaretPositioned", False):
			return
		try:
			if not self.IsShown():
				return
		except Exception:
			log.debug("Ignored error", exc_info=True)
			return
		ip = self._assistant_content_caret_ip(block)
		if ip is None:
			return
		try:
			msgs = self.messagesTextCtrl
			text_len = len(msgs.GetValue())
			if not (0 <= ip <= text_len):
				return
			msgs.SetInsertionPoint(ip)
			block._assistantCaretPositioned = True
			if (self.conf.get("chatFeedback") or {}).get("focusHistoryOnAssistantResponse", False):
				try:
					wx.CallAfter(self._focus_message_history_on_assistant_response, ip)
				except Exception:
					log.debug("Ignored error", exc_info=True)
		except Exception:
			log.debug("Ignored error", exc_info=True)

	def _focus_message_history_on_assistant_response(self, insertion_point):
		"""Focus Messages and place the caret at ``insertion_point`` (once per reply)."""
		try:
			if not self.IsShown():
				return
		except Exception:
			log.debug("Ignored error", exc_info=True)
			return
		if not self._foreground_window_is_this_dialog():
			return
		try:
			msgs = self.messagesTextCtrl
			msgs.SetFocus()
			ip = int(insertion_point)
			text_len = len(msgs.GetValue())
			if 0 <= ip <= text_len:
				msgs.SetInsertionPoint(ip)
		except Exception:
			log.debug("Ignored error", exc_info=True)

	def _foreground_window_is_this_dialog(self) -> bool:
		"""True when the foreground window's root top-level is this dialog (user did not switch away)."""
		dlg_hwnd = getattr(self, "_cached_dialog_hwnd", None)
		if not dlg_hwnd:
			try:
				dlg_hwnd = self.GetHandle()
			except Exception:
				log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)
			off_gui_thread = True
		if off_gui_thread:
			msgs_hwnd = getattr(self, "_cached_messages_hwnd", None)
			if not msgs_hwnd:
				try:
					msgs_hwnd = self.messagesTextCtrl.GetHandle()
				except Exception:
					log.debug("Ignored error", exc_info=True)
					msgs_hwnd = None
				if msgs_hwnd:
					self._cached_messages_hwnd = msgs_hwnd
			try:
				fo = api.getFocusObject()
				hw = getattr(fo, "windowHandle", None) if fo else None
				if hw and msgs_hwnd and hw == msgs_hwnd:
					return False
			except Exception:
				log.debug("Ignored error", exc_info=True)
			return True
		try:
			if not self.IsShown():
				return False
		except Exception:
			log.debug("Ignored error", exc_info=True)
			return False
		try:
			active = wx.GetActiveWindow()
		except Exception:
			log.debug("Ignored error", exc_info=True)
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
			log.debug("Ignored error", exc_info=True)
			focus = None
		if focus is self.messagesTextCtrl:
			return False
		if focus is not None:
			try:
				return wx.GetTopLevelParent(focus) is self
			except Exception:
				log.debug("Ignored error", exc_info=True)
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

	def onProviderTools(self, evt):
		show_tools_menu(self, self.toolsBtn)

	def disableControls(self, keep_prompt_editable=False):
		self.submitBtn.Disable()
		self.closeBtn.Disable()
		self.toolbarCloseBtn.Disable()
		self.toolsBtn.Disable()
		if hasattr(self, "_generation_chrome"):
			self._generation_chrome.disable_all()
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
			log.debug("Ignored error", exc_info=True)
		try:
			self.streamModeCheckBox.Disable()
			self.debugModeCheckBox.Disable()
		except Exception:
			log.debug("Ignored error", exc_info=True)
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
						log.debug("Ignored error", exc_info=True)

	def enableControls(self):
		self.submitBtn.Enable()
		self.closeBtn.Enable()
		self.toolbarCloseBtn.Enable()
		self.toolsBtn.Enable()
		try:
			model = self.getCurrentModel()
			if model:
				if model.reasoning:
					if len(getattr(self, "_reasoningModeOptions", ())) >= 2:
						self.reasoningModeChoice.Enable()
				spn = getattr(self, "reasoningBudgetSpinCtrl", None)
				if spn is not None and spn.IsShown():
					spn.Enable()
				if hasattr(self, "_generation_chrome"):
					self._generation_chrome.update_for_model(model)
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
			log.debug("Ignored error", exc_info=True)
		try:
			self.streamModeCheckBox.Enable()
			self.debugModeCheckBox.Enable()
		except Exception:
			log.debug("Ignored error", exc_info=True)
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
						log.debug("Ignored error", exc_info=True)
		try:
			self.Layout()
		except Exception:
			log.debug("Ignored error", exc_info=True)
		self.updateFilesList(False)
