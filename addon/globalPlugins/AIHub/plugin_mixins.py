"""Mixins used by GlobalPlugin to keep __init__.py focused.

NVDA @script decorators are applied only on GlobalPlugin (__init__.py), not here,
so each gesture is registered once.
"""

import ctypes
import os
import time

import addonHandler
import api
import config
import gui
import ui
import wx
from logHandler import log

from . import apikeymanager
from .consts import ADDON_DIR, CHAT_PROVIDERS, NO_AUTHENTICATION_KEY_PROVIDED_MSG, TEMP_DIR, ensure_temp_dir
from .thread_shutdown import stop_worker_thread

addonHandler.initTranslation()

ROOT_ADDON_DIR = "\\".join(ADDON_DIR.split(os.sep)[:-2])
ADDON_INFO = addonHandler.Addon(ROOT_ADDON_DIR).manifest
conf = config.conf["AIHub"]


class MenuMixin:
	def createMenu(self):
		from .toolsmenu import append_tools_submenu

		self.submenu = wx.Menu()
		tray_menu = gui.mainFrame.sysTrayIcon
		for title, help_text, handler in (
			# Translators: Text in AI-Hub menu items and script prompts.
			(_("Docu&mentation"), _("Open the documentation of this addon"), self.onDocumentation),
			# Translators: AI-Hub NVDA menu / global scripts: entry in a context menu or submenu.
			(_("API &accounts..."), _("Manage API keys and provider accounts"), self.onManageApiAccounts),
			# Translators: AI-Hub NVDA menu / global scripts: entry in a context menu or submenu.
			(_("&Conversation..."), _("Show or focus the AI-Hub conversation window"), self.onShowMainDialog),
			# Translators: AI-Hub NVDA menu / global scripts: entry in a context menu or submenu.
			(_("Conversation &history..."), _("Manage saved conversations"), self.onShowConversationsManager),
		):
			item = self.submenu.Append(wx.ID_ANY, title, help_text)
			tray_menu.Bind(wx.EVT_MENU, handler, item)
		append_tools_submenu(self.submenu, parent=None, plugin=self)

		self.submenu.AppendSeparator()

		# Translators: AI-Hub NVDA menu / global scripts: entry in a context menu or submenu.
		item = self.submenu.Append(wx.ID_ANY, _("Git&Hub repository"), _("Open the GitHub repository of this addon"))
		tray_menu.Bind(wx.EVT_MENU, self.onGitRepo, item)

		self.submenu.AppendSeparator()

		# Translators: AI-Hub NVDA menu / global scripts: entry in a context menu or submenu.
		item = self.submenu.Append(wx.ID_ANY, _("BasiliskLLM"), _("Open the BasiliskLLM website"))
		tray_menu.Bind(wx.EVT_MENU, self.onBasiliskLLM, item)

		self.submenu_item = tray_menu.menu.InsertMenu(
			2,
			wx.ID_ANY,
			# Translators: Text in AI-Hub menu items and script prompts.
			_("&AI Hub {addon_version}").format(addon_version=ADDON_INFO["version"]),
			self.submenu
		)

	def onGitRepo(self, evt):
		os.startfile("https://github.com/aaclause/nvda-OpenAI/")

	def onDocumentation(self, evt):
		import languageHandler
		languages = ["en"]
		language = languageHandler.getLanguage()
		if "_" in language:
			languages.insert(0, language.split("_")[0])
		languages.insert(0, language)
		for lang in languages:
			fp = os.path.join(ROOT_ADDON_DIR, "doc", lang, "readme.html")
			if os.path.exists(fp):
				os.startfile(fp)
				break

	def onBasiliskLLM(self, evt):
		os.startfile("https://github.com/SigmaNight/basiliskLLM/")

	def onShowConversationsManager(self, evt):
		from .conversations_manager_dialog import show_conversations_manager
		wx.CallAfter(show_conversations_manager, self)

	def onManageApiAccounts(self, evt):
		from .accounts_dialog import show_accounts_management
		show_accounts_management(gui.mainFrame)

	def script_showConversationsManager(self, gesture):
		self.onShowConversationsManager(None)

	def _getSelectedText(self) -> str:
		"""Return the text selected in the focused object (browse mode aware), or ""."""
		import textInfos
		obj = api.getFocusObject()
		tree_interceptor = getattr(obj, "treeInterceptor", None)
		if tree_interceptor is not None and not getattr(tree_interceptor, "passThrough", True):
			obj = tree_interceptor
		try:
			info = obj.makeTextInfo(textInfos.POSITION_SELECTION)
		except Exception:
			log.debug("Ignored error", exc_info=True)
			return ""
		if not info or info.isCollapsed:
			return ""
		return info.text or ""

	def script_pangramCheckSelection(self, gesture):
		from .toolsmenu import open_pangram_with_text
		text = self._getSelectedText()
		if not text.strip():
			try:
				text = api.getClipData() or ""
			except Exception:
				log.debug("Ignored error", exc_info=True)
				text = ""
		if not text.strip():
			# Translators: AI-Hub global script: feedback when there is neither selected text nor clipboard text to send to Pangram.
			ui.message(_("No selected text or clipboard text to check"))
			return
		wx.CallAfter(open_pangram_with_text, None, text, plugin=self)


class DialogSessionMixin:
	def _showNoAccountConfiguredDialog(self):
		wx.MessageBox(
			# Translators: Error dialog body when opening AI-Hub without any configured API account (menu path included for the user).
			_("No account is configured yet. Use API accounts from the AI Hub menu or NVDA Preferences → AI-Hub."),
			"OpenAI",
			wx.OK | wx.ICON_ERROR,
		)

	def _refocusHubWindow(self, dlg):
		"""Bring the AI-Hub window to the foreground (used after NVDA+G and history open)."""
		try:
			dlg.Raise()
			dlg.Show(True)
			dlg.SetFocus()
			api.processPendingEvents()
			hwnd = dlg.GetHandle()
			if hwnd:
				ctypes.windll.user32.SetForegroundWindow(int(hwnd))
		except Exception:
			log.debug("Refocus AI-Hub window failed", exc_info=True)

	def _openMainDialog(self, filesList=None, conversationData=None, forceNew=False, openConversationInNewTab=False):
		"""Create and show a non-modal conversation window."""
		from . import conversation_dialog
		client = self.getClient()
		if not client:
			self._showNoAccountConfiguredDialog()
			return

		self._openMainDialogs = [d for d in getattr(self, "_openMainDialogs", []) if d and d.IsShown()]
		if forceNew and self._openMainDialogs:
			dlg = self._openMainDialogs[-1]
			if hasattr(dlg, "_addConversationTab"):
				dlg._addConversationTab()
				wx.CallAfter(dlg.promptTextCtrl.SetFocus)
			self._refocusHubWindow(dlg)
			return
		if not forceNew and self._openMainDialogs:
			dlg = self._openMainDialogs[-1]
			if conversationData and hasattr(dlg, "_loadConversation"):
				if openConversationInNewTab and hasattr(dlg, "_openConversationFromHistory"):
					dlg._openConversationFromHistory(conversationData)
				else:
					dlg._loadConversation(conversationData, focus_message_history=True)
			self._refocusHubWindow(dlg)
			return

		started = time.perf_counter()
		dlg = conversation_dialog.ConversationDialog(
			gui.mainFrame,
			client=client,
			conf=conf,
			filesList=filesList,
			plugin=self,
			conversationData=conversationData
		)
		self._openMainDialogs.append(dlg)

		def _on_close(evt, dialog=dlg):
			try:
				if dialog in self._openMainDialogs:
					self._openMainDialogs.remove(dialog)
			except Exception:
				log.debug("Ignored error", exc_info=True)
			evt.Skip()

		dlg.Bind(wx.EVT_CLOSE, _on_close)
		dlg.Show()
		log.debug("AI-Hub conversation window shown in %d ms", (time.perf_counter() - started) * 1000)
		if len(self._openMainDialogs) > 1:
			last = self._openMainDialogs[-2]
			try:
				x, y = last.GetPosition()
				offset = 26
				dlg.SetPosition((x + offset, y + offset))
			except Exception:
				log.debug("Failed to offset stacked dialog position", exc_info=True)
		self._refocusHubWindow(dlg)

	def onShowMainDialog(self, evt=None, forceNew=False):
		# After the tray menu has closed; _openMainDialog checks for an account itself.
		wx.CallAfter(self._openMainDialog, None, None, forceNew)

	def script_showMainDialog(self, gesture):
		# Scripts already run on NVDA's main (wx) thread.
		self.onShowMainDialog(None, False)

	def startChatSession(self, attachment):
		"""Add an attachment to an open session, or open a new dialog with it."""
		instance = self._findOpenConversationDialog()
		if instance:
			# Bring the dialog forward FIRST so the active session panel is
			# laid out against a visible window. Adding/refreshing the file
			# list against a hidden window means the panel sizer never picks
			# up the Show() change and the new attachment stays invisible.
			self._refocusHubWindow(instance)
			page = instance.get_active_page()
			if page.filesList is None:
				page.filesList = []
			instance.addFileToList(attachment, True)
			instance.updateFilesList()
			# If we just dropped the attachment into a still-empty tab, retitle
			# it to the attachment's display name (Screenshot/Navigator Object).
			try:
				instance._retitleEmptyTabFromAttachments()
			except Exception:
				log.debug("retitle tab from attachment failed", exc_info=True)
			api.processPendingEvents()
			# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
			ui.message(_("Image added to an existing session"))
			return
		wx.CallAfter(self._openMainDialog, [attachment], None, False)

	def _findOpenConversationDialog(self):
		"""Return a still-open ConversationDialog if any, preferring the active one."""
		from . import conversation_dialog
		Cls = conversation_dialog.ConversationDialog
		for cand in (
			conversation_dialog.addToSession,
			conversation_dialog.activeChatDlg,
		):
			if isinstance(cand, Cls):
				try:
					if cand.IsShown():
						return cand
				except Exception:
					log.debug("Ignored error", exc_info=True)
					continue
		# Fallback: the global trackers can be cleared (Esc/onCancel) before the
		# window is destroyed, so also scan the open-dialogs registry.
		for dlg in reversed(getattr(self, "_openMainDialogs", []) or []):
			if isinstance(dlg, Cls):
				try:
					if dlg.IsShown():
						return dlg
				except Exception:
					log.debug("Ignored error", exc_info=True)
					continue
		return None

	def script_recognizeScreen(self, gesture):
		from .imagehelper import save_screenshot

		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		now = time.strftime("%Y-%m-%d_-_%H:%M:%S")
		ensure_temp_dir()
		tmpPath = os.path.join(TEMP_DIR, f"screen_{now}.png".replace(":", ""))
		if os.path.exists(tmpPath):
			return
		if not save_screenshot(tmpPath):
			# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
			return ui.message(_("Failed to capture screenshot"))
		# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
		name = _("Screenshot %s") % (now.split("_-_")[-1])
		self.startChatSession((tmpPath, name))

	def script_recognizeObject(self, gesture):
		from .imagehelper import save_screenshot

		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		now = time.strftime("%Y-%m-%d_-_%H:%M:%S")
		ensure_temp_dir()
		tmpPath = os.path.join(TEMP_DIR, f"object_{now}.png".replace(":", ""))
		if os.path.exists(tmpPath):
			return
		nav = api.getNavigatorObject()
		nav.scrollIntoView()
		location = nav.location
		bbox = (location.left, location.top, location.left + location.width, location.top + location.height)
		if not save_screenshot(tmpPath, bbox=bbox):
			# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
			return ui.message(_("Failed to capture object region"))
		# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
		default_name = _("Navigator Object %s") % (now.split("_-_")[-1])
		name = nav.name
		if (not name or not name.strip() or "\n" in name or len(name) > 80):
			name = default_name
		else:
			name = "%s (%s)" % (name.strip(), default_name)
		self.startChatSession((tmpPath, name))


class AskRecordingMixin:
	def _onAskQuestionTranscription(self, question):
		from .ask_question import AskQuestionThread

		if not question or not question.strip():
			return
		question = question.strip()
		from . import conversation_dialog
		dlg = conversation_dialog.activeChatDlg
		if dlg:
			dlg._askPromptOverride = question
			if dlg.worker:
				dlg._askQuestionDeferred = True
			else:
				wx.CallAfter(dlg.onSubmit, None)
			return
		client = self.getClient()
		if not client:
			ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
			return
		AskQuestionThread(client, question=question, conf=conf, plugin=self).start()

	def _onAskQuestionAudio(self, path):
		from .ask_question import AskQuestionThread

		client = self.getClient()
		if not client:
			ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
			return
		AskQuestionThread(client, conf=conf, audio_path=path, plugin=self).start()

	def _useDirectAudioForAsk(self, model=None):
		return bool(model and getattr(model, "audioInput", False))

	def script_askQuestion(self, gesture):
		from .ask_question import mci_stop_ask_audio
		from .model import getModels
		from .recordthread import RecordThread
		from . import conversation_dialog
		from .transcription import get_transcription_provider
		from .consts import Provider, TranscriptionProvider
		from .apiclient import OpenAIClient, configure_client_for_provider

		client = self.getClient()
		if not client:
			provider = get_transcription_provider(conf.get("audio", {}))
			if provider == TranscriptionProvider.WHISPER_CPP.value:
				client = object()
			elif provider == TranscriptionProvider.CUSTOM_OPENAI.value and apikeymanager.get(Provider.CustomOpenAI).isReady():
				client = configure_client_for_provider(OpenAIClient(), Provider.CustomOpenAI)
			elif provider == TranscriptionProvider.MISTRAL.value and apikeymanager.get(Provider.MistralAI).isReady():
				client = object()
			else:
				return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self._askAudioPlaying:
			mci_stop_ask_audio()
			self._askAudioPlaying = False
			# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
			ui.message(_("Audio stopped"))
			return
		if self.askRecordThread:
			stop_worker_thread(self.askRecordThread)
			self.askRecordThread = None
			return

		dlg = conversation_dialog.activeChatDlg
		if dlg:
			model = dlg.getCurrentModel()
			if model and self._useDirectAudioForAsk(model):
				dlg._askQuestionPending = True
				# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
				ui.message(_("Recording question (direct audio)"))
				self.askRecordThread = RecordThread(
					self.getClient(),
					notifyWindow=dlg,
					conf=conf["audio"],
					useDirectAudio=True,
				)
			else:
				# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
				ui.message(_("Recording question"))
				self.askRecordThread = RecordThread(
					self.getClient(),
					conf=conf["audio"],
					onTranscription=self._onAskQuestionTranscription,
				)
			self.askRecordThread.start()
			return

		for provider in CHAT_PROVIDERS:
			if not apikeymanager.get(provider).isReady():
				continue
			try:
				for model in getModels(provider):
					if getattr(model, "audioInput", False):
						# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
						ui.message(_("Recording question (direct audio)"))
						self.askRecordThread = RecordThread(
							self.getClient(),
							conf=conf["audio"],
							useDirectAudio=True,
							onAudioPath=self._onAskQuestionAudio,
						)
						self.askRecordThread.start()
						return
			except Exception:
				log.debug("Ignored error", exc_info=True)

		# Translators: AI-Hub NVDA menu / global scripts: brief status feedback (speech/braille), not a full dialog.
		ui.message(_("Recording question"))
		self.askRecordThread = RecordThread(
			self.getClient(),
			conf=conf["audio"],
			onTranscription=self._onAskQuestionTranscription,
		)
		self.askRecordThread.start()

	def script_toggleRecording(self, gesture):
		from . import conversation_dialog
		from .recordthread import RecordThread
		from .transcription import get_transcription_provider
		from .consts import Provider, TranscriptionProvider
		from .apiclient import OpenAIClient, configure_client_for_provider

		client = self.getClient()
		if not client:
			provider = get_transcription_provider(conf.get("audio", {}))
			if provider == TranscriptionProvider.WHISPER_CPP.value:
				client = object()
			elif provider == TranscriptionProvider.CUSTOM_OPENAI.value and apikeymanager.get(Provider.CustomOpenAI).isReady():
				client = configure_client_for_provider(OpenAIClient(), Provider.CustomOpenAI)
			elif provider == TranscriptionProvider.MISTRAL.value and apikeymanager.get(Provider.MistralAI).isReady():
				client = object()
			else:
				return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		dlg = conversation_dialog.activeChatDlg
		if dlg and dlg.IsShown() and hasattr(dlg, "_prompt_ctrl_has_focus") and dlg._prompt_ctrl_has_focus():
			dlg.onRecord(None)
			return
		if self.recordThread:
			stop_worker_thread(self.recordThread)
			self.recordThread = None
			return
		self.recordThread = RecordThread(client, conf=conf["audio"])
		self.recordThread.start()
