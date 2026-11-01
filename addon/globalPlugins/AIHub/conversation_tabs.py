"""Conversation tabs: opening, closing, ephemeral tabs, lazy restore and the hub session."""

import json
import os
import wx
import addonHandler
from logHandler import log
from . import conversations
from .thread_shutdown import stop_worker_thread
from .conversation_session_panel import ConversationSessionPanel

addonHandler.initTranslation()


class ConversationTabsMixin:
	"""Mixin for ConversationDialog."""

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
			log.debug("Ignored error", exc_info=True)

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
		page.detachedBranch = None
		page._regenerateBlock = None
		page._conversationId = cid
		page._promptCacheKey = None
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
		page.detachedBranch = None
		page._regenerateBlock = None
		page._conversationId = None
		page._promptCacheKey = None
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

	def _restoreHubSessionIfNeeded(self):
		"""Reopen the tabs of the last session, then add and show a fresh tab.

		Restored tabs are only loaded when the user switches to them: rendering
		a long history up front made the window slow to appear for a tab nobody
		looks at yet.
		"""
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
			page = self.notebook.GetPage(i)
			cid = _entry_cid(tabs[i])
			if cid and conversations.conversation_file_exists(cid):
				# Translators: Fallback tab label when a restored conversation name is missing.
				self._prepareLazySessionTab(page, cid, name_by_id.get(cid, _("Conversation")))
			else:
				self._resetTabPage(page, select_tab=False, sync_attachment_widgets=False)

		# The fresh tab starts from the first tab's system prompt. ChangeSelection
		# sends no page-change event, which would load that tab.
		self.notebook.ChangeSelection(0)
		self._inheritSystemPromptFromConversation(self.notebook.GetPage(0), _entry_cid(tabs[0]))
		self._addConversationTab()
		wx.CallAfter(self.promptTextCtrl.SetFocus)

	def _inheritSystemPromptFromConversation(self, page, cid):
		"""Show a saved conversation's system prompt on its lazy tab, as a full load would."""
		data = conversations.load_conversation(cid) if cid and conversations.conversation_file_exists(cid) else None
		system = data.get("system", "") if isinstance(data, dict) else ""
		if not isinstance(system, str):
			system = ""
		page.conversationSystemText = system
		if system and self.conf["saveSystem"]:
			page.systemTextCtrl.ChangeValue(system)
			self._lastSystem = system

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
			log.debug("Ignored error", exc_info=True)
		self.updateFilesList(False)
		self.updateAudioList(False)
		self._syncWindowTitleFromActiveTab()
		return True
