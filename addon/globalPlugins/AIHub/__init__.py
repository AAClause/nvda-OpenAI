import os
import addonHandler
import config
import globalPluginHandler
import gui
from logHandler import log
from scriptHandler import script
from . import apikeymanager
from . import configspec
from .apiclient import OpenAIClient
from .consts import ADDON_DIR, BASE_URLs, DATA_DIR, Provider
from .plugin_mixins import AskRecordingMixin, DialogSessionMixin, MenuMixin
from .thread_shutdown import stop_worker_thread
from .settings_dialog import AIHubSettingsPanel


# Providers that may legitimately have an empty API key (Ollama uses none;
# CustomOpenAI may rely on a base URL with no auth header).
_OPTIONAL_API_KEY_PROVIDERS = (Provider.CustomOpenAI, Provider.Ollama)

addonHandler.initTranslation()
ROOT_ADDON_DIR = "\\".join(ADDON_DIR.split(os.sep)[:-2])
ADDON_INFO = addonHandler.Addon(ROOT_ADDON_DIR).manifest
conf = config.conf["AIHub"]


class GlobalPlugin(MenuMixin, DialogSessionMixin, AskRecordingMixin, globalPluginHandler.GlobalPlugin):
	scriptCategory = "AI-Hub"

	def __init__(self):
		super().__init__()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(AIHubSettingsPanel)
		self._openMainDialogs = []
		self.recordThread = None
		self.askRecordThread = None
		self._askAudioPlaying = False
		self.createMenu()
		apikeymanager.load(DATA_DIR)
		log.info("AI-Hub initialized. Version: %s. %d providers", ADDON_INFO["version"], len(apikeymanager._managers or []))

	def terminate(self):
		from .consts import cleanup_temp_dir
		from .ask_question import mci_stop_ask_audio

		dialogs = list(getattr(self, "_openMainDialogs", []) or [])
		for dlg in dialogs:
			try:
				if dlg is not None:
					dlg._force_quiet_shutdown = True
					dlg.onCancel(None)
			except Exception:
				log.warning("Failed to close AI-Hub conversation dialog during addon terminate", exc_info=True)
		self._openMainDialogs = []

		if self.recordThread:
			stop_worker_thread(self.recordThread)
			self.recordThread = None
		if self.askRecordThread:
			stop_worker_thread(self.askRecordThread)
			self.askRecordThread = None
		if self._askAudioPlaying:
			mci_stop_ask_audio()
			self._askAudioPlaying = False
		cleanup_temp_dir()
		if AIHubSettingsPanel in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(AIHubSettingsPanel)
		if getattr(self, "submenu_item", None):
			gui.mainFrame.sysTrayIcon.menu.DestroyItem(self.submenu_item)
		super().terminate()

	def getClient(self):
		if conf["renewClient"]:
			conf["renewClient"] = False
		for provider in apikeymanager.AVAILABLE_PROVIDERS:
			manager = apikeymanager.get(provider)
			if not manager.isReady():
				continue
			api_key = manager.get_api_key()
			base_url = manager.get_base_url() or BASE_URLs[manager.provider]
			if provider not in _OPTIONAL_API_KEY_PROVIDERS and (not api_key or not api_key.strip()):
				return None
			organization = manager.get_api_key(use_org=True)
			org_val = organization.split(":=", 1)[1] if organization and organization.count(":=") == 1 else None
			return OpenAIClient(api_key=api_key, base_url=base_url, organization=org_val)
		return None

	# Translators: Script description shown in NVDA input gestures for opening/focusing AI-Hub.
	@script(
		gesture="kb:nvda+g",
		# Translators: AI-Hub Input Gestures (assignable scripts): description of an assignable NVDA script.
		description=_("Show or focus the AI-Hub conversation window"),
	)
	def script_showMainDialog(self, gesture):
		super().script_showMainDialog(gesture)

	# Translators: Script description shown in NVDA input gestures for screenshot description.
	@script(
		gesture="kb:nvda+e",
		# Translators: AI-Hub Input Gestures (assignable scripts): description of an assignable NVDA script.
		description=_("Take a screenshot and describe it"),
	)
	def script_recognizeScreen(self, gesture):
		super().script_recognizeScreen(gesture)

	# Translators: Script description shown in NVDA input gestures for navigator object description.
	@script(
		gesture="kb:nvda+o",
		# Translators: AI-Hub Input Gestures (assignable scripts): description of an assignable NVDA script.
		description=_("Grab the current navigator object and describe it"),
	)
	def script_recognizeObject(self, gesture):
		super().script_recognizeObject(gesture)

	# Translators: Script description shown in NVDA input gestures for opening saved conversations manager.
	@script(
		# Translators: AI-Hub Input Gestures (assignable scripts): description of an assignable NVDA script.
		description=_("Manage saved conversations"),
	)
	def script_showConversationsManager(self, gesture):
		super().script_showConversationsManager(gesture)

	# Translators: Script description shown in NVDA input gestures for voice question workflow.
	@script(
		# Translators: AI-Hub Input Gestures (assignable scripts): description of an assignable NVDA script.
		description=_("Ask a question via voice: record, send to AI, and play the response"),
	)
	def script_askQuestion(self, gesture):
		super().script_askQuestion(gesture)

	# Translators: Script description shown in NVDA input gestures for global microphone toggle.
	@script(
		# Translators: AI-Hub Input Gestures (assignable scripts): description of an assignable NVDA script.
		description=_("Toggle the microphone recording and transcribe the audio from anywhere"),
	)
	def script_toggleRecording(self, gesture):
		super().script_toggleRecording(gesture)
