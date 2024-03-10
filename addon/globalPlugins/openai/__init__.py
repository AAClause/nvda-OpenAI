import json
import os
import sys
import time
import addonHandler
import api
import config
import controlTypes
import globalPluginHandler
import gui
import wx
import ui
from logHandler import log
from scriptHandler import script, getLastScriptRepeatCount
from . import apikeymanager
from . import configspec
from . import updatecheck
from .consts import (
	ADDON_DIR, BASE_URLs, DATA_DIR,
	LIBS_DIR_PY,
	TTS_MODELS, TTS_VOICES
)
from .recordthread import RecordThread
sys.path.insert(0, LIBS_DIR_PY)
import mss
from openai import OpenAI
sys.path.remove(LIBS_DIR_PY)

addonHandler.initTranslation()

ROOT_ADDON_DIR = "\\".join(ADDON_DIR.split(os.sep)[:-2])
ADDON_INFO = addonHandler.Addon(
	ROOT_ADDON_DIR
).manifest

NO_AUTHENTICATION_KEY_PROVIDED_MSG = _("No API key provided for any provider, please provide at least one API key in the settings dialog")

conf = config.conf["OpenAI"]


class APIAccessDialog(wx.Dialog):

	def __init__(
		self,
		parent,
		title: str,
		APIKeyManager: apikeymanager.APIKeyManager,
	):
		super(APIAccessDialog, self).__init__(parent, title=title)
		self.APIKeyManager = APIKeyManager
		self.provider_name = APIKeyManager.provider
		self.InitUI()
		self.CenterOnParent()
		self.SetSize((500, 200))

	def InitUI(self):
		pnl = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)
		fgs = wx.FlexGridSizer(3, 2, 9, 25)  # 3 rows, 2 columns, vertical and horizontal gap

		lblAPIKey = wx.StaticText(pnl, label=f"{self.provider_name} API Key:")
		self.txtAPIKey = wx.TextCtrl(pnl)

		lblOrgName = wx.StaticText(pnl, label="Organization name:")
		self.txtOrgName = wx.TextCtrl(pnl)

		lblOrgKey = wx.StaticText(pnl, label="Organization key:")
		self.txtOrgKey = wx.TextCtrl(pnl)

		# Adding Rows to the FlexGridSizer
		fgs.AddMany(
			[
				lblAPIKey, (self.txtAPIKey, 1, wx.EXPAND),
				lblOrgName, (self.txtOrgName, 1, wx.EXPAND),
				lblOrgKey, (self.txtOrgKey, 1, wx.EXPAND),
			])

		# Configure an expanding column for text controls
		fgs.AddGrowableCol(1, 1)

		APIKey = self.APIKeyManager.get_api_key()
		if APIKey:
			self.txtAPIKey.SetValue(
				APIKey
			)
		orgKey = self.APIKeyManager.get_organization_key()
		orgName = self.APIKeyManager.get_organization_name()
		if orgKey and orgName:
			self.txtOrgName.SetValue(
				orgName
			)
			self.txtOrgKey.SetValue(
				orgKey
			)

		btnsizer = wx.StdDialogButtonSizer()
		btnOK = wx.Button(pnl, wx.ID_OK)
		btnOK.SetDefault()
		btnsizer.AddButton(btnOK)
		btnsizer.AddButton(wx.Button(pnl, wx.ID_CANCEL))
		btnsizer.Realize()

		# Layout sizers
		vbox.Add(fgs, proportion=1, flag=wx.ALL|wx.EXPAND, border=10)
		vbox.Add(btnsizer, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=10)
		pnl.SetSizer(vbox)


class SettingsDlg(gui.settingsDialogs.SettingsPanel):

	title = "Open AI"

	def makeSettings(self, settingsSizer):

		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		updateGroupLabel = _("Update")
		updateSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=updateGroupLabel)
		updateBox = updateSizer.GetStaticBox()
		updateGroup = gui.guiHelper.BoxSizerHelper(self, sizer=updateSizer)

		self.updateCheck = updateGroup.addItem(
			wx.CheckBox(
				updateBox,
				label=_("Check for &updates on startup and periodically")
			)
		)
		self.updateCheck.SetValue(conf["update"]["check"])

		self.updateChannel = updateGroup.addLabeledControl(
			_("&Channel:"),
			wx.Choice,
			choices=["stable", "dev"]
		)
		self.updateChannel.SetSelection(
			1 if conf["update"]["channel"] == "dev" else 0
		)

		sHelper.addItem(updateSizer)

		APIAccessGroupLabel = _("API Access Keys")
		APIAccessSizer = wx.StaticBoxSizer(wx.HORIZONTAL, self, label=APIAccessGroupLabel)
		APIAccessBox = APIAccessSizer.GetStaticBox()
		APIAccessGroup = gui.guiHelper.BoxSizerHelper(self, sizer=APIAccessSizer)

		for provider in apikeymanager.AVAILABLE_PROVIDERS:
			item = APIAccessGroup.addItem(
				wx.Button(
					APIAccessBox,
					label=_("%s API &keys...") % provider,
					id=wx.ID_ANY,
					name=provider
				)
			)
			item.Bind(
				wx.EVT_BUTTON,
				self.onAPIKeys
			)

		sHelper.addItem(APIAccessSizer)

		mainDialogGroupLabel = _("Main dialog")
		mainDialogSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=mainDialogGroupLabel)
		mainDialogBox = mainDialogSizer.GetStaticBox()
		mainDialogGroup = gui.guiHelper.BoxSizerHelper(self, sizer=mainDialogSizer)

		label = _("Block the closing using the &escape key")
		self.blockEscape = wx.CheckBox(
			self,
			label=label,
		)
		self.blockEscape.SetValue(conf["blockEscapeKey"])
		mainDialogGroup.addItem(self.blockEscape)

		label = _("Remember the content of the S&ystem field between sessions")
		self.saveSystem = wx.CheckBox(
			self,
			label=label,
		)
		self.saveSystem.SetValue(conf["saveSystem"])
		mainDialogGroup.addItem(self.saveSystem)

		label = _("Enable &advanced settings (including temperature and probability mass)")
		self.advancedMode = wx.CheckBox(
			self,
			label=label,
		)
		self.advancedMode.SetValue(conf["advancedMode"])
		mainDialogGroup.addItem(self.advancedMode)

		TTSGroupLabel = _("Text To Speech")
		TTSSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=TTSGroupLabel)
		TTSBox = TTSSizer.GetStaticBox()
		TTSGroup = gui.guiHelper.BoxSizerHelper(self, sizer=TTSSizer)

		self.voiceList = TTSGroup.addLabeledControl(
			_("&Voice:"),
			wx.Choice,
			choices=TTS_VOICES,
		)
		itemToSelect = 0
		if conf["TTSVoice"] in TTS_VOICES:
			itemToSelect = TTS_VOICES.index(conf["TTSVoice"])
		self.voiceList.SetSelection(itemToSelect)

		self.modelList = TTSGroup.addLabeledControl(
			_("&Model:"),
			wx.Choice,
			choices=TTS_MODELS,
		)
		itemToSelect = 0
		if conf["TTSModel"] in TTS_MODELS:
			itemToSelect = TTS_MODELS.index(conf["TTSModel"])
		self.modelList.SetSelection(itemToSelect)

		sHelper.addItem(TTSSizer)

		imageGroupLabel = _("Image description")
		imageSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=imageGroupLabel)
		imageBox = imageSizer.GetStaticBox()
		imageGroup = gui.guiHelper.BoxSizerHelper(self, sizer=imageSizer)

		label = _("&Resize images before sending them to the API")
		self.resize = imageGroup.addItem(
			wx.CheckBox(
				imageBox,
				label=label,
			)
		)
		self.resize.SetValue(conf["images"]["resize"])
		self.resize.Bind(
			wx.EVT_CHECKBOX,
			self.onResize
		)

		label = _("Maximum &width (0 to resize proportionally to the height):")
		self.maxWidth = imageGroup.addLabeledControl(
			label,
			wx.SpinCtrl,
			min=0,
			max=2000
		)
		self.maxWidth.SetValue(conf["images"]["maxWidth"])

		label = _("Maximum &height (0 to resize proportionally to the width):")
		self.maxHeight = imageGroup.addLabeledControl(
			label,
			wx.SpinCtrl,
			min=0,
			max=2000
		)
		self.maxHeight.SetValue(conf["images"]["maxHeight"])

		label = _("&Quality for JPEG images (0 [worst] to 95 [best], values above 95 should be avoided):")
		self.quality = imageGroup.addLabeledControl(
			label,
			wx.SpinCtrl,
			min=1,
			max=100
		)
		self.quality.SetValue(conf["images"]["quality"])

		self.useCustomPrompt = imageGroup.addItem(
			wx.CheckBox(
				imageBox,
				label=_("Customize default text &prompt")
			)
		)
		self.useCustomPrompt.Bind(wx.EVT_CHECKBOX, self.onDefaultPrompt)
		self.useCustomPrompt.SetValue(conf["images"]["useCustomPrompt"])
		self.customPromptText = imageGroup.addLabeledControl(
			_("Default &text prompt:"),
			wxCtrlClass=wx.TextCtrl,
			style=wx.TE_MULTILINE
		)
		self.customPromptText.SetMinSize((250, -1))
		self.customPromptText.Enable(False)
		if conf["images"]["useCustomPrompt"]:
			self.useCustomPrompt.SetValue(True)
			self.customPromptText.SetValue(conf["images"]["customPromptText"])
			self.customPromptText.Enable()

		sHelper.addItem(imageSizer)

		chatFeedback = _("Chat feedback")
		chatFeedbackSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=chatFeedback)
		chatFeedbackBox = chatFeedbackSizer.GetStaticBox()
		chatFeedbackGroup = gui.guiHelper.BoxSizerHelper(self, sizer=chatFeedbackSizer)

		self.chatFeedback = {
			"sndTaskInProgress": chatFeedbackGroup.addItem(
				wx.CheckBox(
					chatFeedbackBox,
					# Translators: This is a setting to play a sound when a task is in progress.
					label=_("Play sound when a task is in progress")
				)
			),
			"sndResponseSent": chatFeedbackGroup.addItem(
				wx.CheckBox(
					chatFeedbackBox,
					# Translators: This is a setting to play a sound when a response is sent.
					label=_("Play sound when a response is sent")
				)
			),
			"sndResponsePending": chatFeedbackGroup.addItem(
				wx.CheckBox(
					chatFeedbackBox,
					# Translators: This is a setting to play a sound when a response is pending.
					label=_("Play sound when a response is pending")
				)
			),
			"sndResponseReceived": chatFeedbackGroup.addItem(
				wx.CheckBox(
					chatFeedbackBox,
					# Translators: This is a setting to play a sound when a response is received.
					label=_("Play sound when a response is received")
				)
			),
			"brailleAutoFocusHistory": chatFeedbackGroup.addItem(
				wx.CheckBox(
					chatFeedbackBox,
					# Translators: This is a setting to attach braille to the history if the focus is in the prompt field.
					label=_("Attach braille to the history if the focus is in the prompt field")
				)
			),
			"speechResponseReceived": chatFeedbackGroup.addItem(
				wx.CheckBox(
					chatFeedbackBox,
					label=_("Speak response when the focus is in the prompt field")
				)
			)
		}
		for key, item in self.chatFeedback.items():
			item.SetValue(conf["chatFeedback"][key])

		sHelper.addItem(chatFeedbackSizer)

		# Translators: This is the name of a group of settings
		whisperGroupLabel = _("Recording")
		whisperSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=whisperGroupLabel)
		whisperBox = whisperSizer.GetStaticBox()
		whisperGroup = gui.guiHelper.BoxSizerHelper(self, sizer=whisperSizer)

		# Translators: This is the name of a setting in the Recording group
		label = _("Use &whisper.cpp for transcription")
		self.whisperCheckbox = whisperGroup.addItem(
			wx.CheckBox(
				whisperBox,
				label=label,
			)
		)
		self.whisperCheckbox.SetValue(
			conf["audio"]["whisper.cpp"]["enabled"]
		)
		self.whisperCheckbox.Bind(
			wx.EVT_CHECKBOX,
			self.onWhisperCheckbox
		)

		# Translators: This is the name of a setting in the Recording group
		label = _("&Host:")
		self.whisperHost = whisperGroup.addLabeledControl(
			label,
			wx.TextCtrl,
			value=conf["audio"]["whisper.cpp"]["host"]
		)

		sHelper.addItem(whisperSizer)

		sHelper.addItem(mainDialogSizer)

		self.onResize(None)
		self.onWhisperCheckbox(None)

	def onAPIKeys(self, evt):
		provider_name = evt.GetEventObject().GetName()
		manager = apikeymanager.get(provider_name)
		dlg = APIAccessDialog(
			self,
			"%s API Access Keys" % provider_name,
			manager
		)
		if dlg.ShowModal() == wx.ID_OK:
			manager.save_api_key(
				dlg.txtAPIKey.GetValue().strip()
			)
			manager.save_api_key(
				dlg.txtOrgKey.GetValue().strip(),
				org=True,
				org_name=dlg.txtOrgName.GetValue()
			)

	def onResize(self, evt):
		self.maxWidth.Enable(self.resize.GetValue())
		self.maxHeight.Enable(self.resize.GetValue())
		self.quality.Enable(self.resize.GetValue())

	def onWhisperCheckbox(self, evt):
		self.whisperHost.Enable(
			self.whisperCheckbox.GetValue()
		)

	def onDefaultPrompt(self, evt):
		if self.useCustomPrompt.GetValue():
			self.customPromptText.Enable()
			self.customPromptText.SetValue(conf["images"]["customPromptText"])
		else:
			self.customPromptText.Enable(False)

	def onSave(self):
		conf["update"]["check"] = self.updateCheck.GetValue()
		conf["update"]["channel"] = self.updateChannel.GetString(self.updateChannel.GetSelection())
		conf["blockEscapeKey"] = self.blockEscape.GetValue()
		conf["renewClient"] = True
		conf["saveSystem"] = self.saveSystem.GetValue()
		conf["advancedMode"] = self.advancedMode.GetValue()

		conf["TTSVoice"] = self.voiceList.GetString(self.voiceList.GetSelection())
		conf["TTSModel"] = self.modelList.GetString(self.modelList.GetSelection())

		conf["images"]["resize"] = self.resize.GetValue()
		conf["images"]["maxWidth"] = self.maxWidth.GetValue()
		conf["images"]["maxHeight"] = self.maxHeight.GetValue()
		conf["images"]["quality"] = self.quality.GetValue()
		if self.useCustomPrompt.GetValue():
			conf["images"]["useCustomPrompt"] = True
			conf["images"]["customPromptText"] = self.customPromptText.GetValue()
		else:
			conf["images"]["useCustomPrompt"] = False
		conf["audio"]["whisper.cpp"]["enabled"] = self.whisperCheckbox.GetValue()
		conf["audio"]["whisper.cpp"]["host"] = self.whisperHost.GetValue()


		for key, item in self.chatFeedback.items():
			conf["chatFeedback"][key] = item.GetValue()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

	scriptCategory = "Open AI"

	def __init__(self):
		super().__init__()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SettingsDlg)
		self.client = None
		self.recordThread = None
		self.createMenu()
		apikeymanager.load(DATA_DIR)
		log.info(
			"Open AI initialized. Version: %s. %d providers" % (
				ADDON_INFO["version"],
				len(apikeymanager._managers or [])
			)
		)

	def createMenu(self):
		self.submenu = wx.Menu()
		item = self.submenu.Append(
			wx.ID_ANY,
			_("Docu&mentation"),
			_("Open the documentation of this addon")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onDocumentation, item)
		item = self.submenu.Append(
			wx.ID_ANY,
			_("Main d&ialog..."),
			_("Show the Open AI dialog")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onShowMainDialog, item)

		self.submenu.AppendSeparator()

		item = self.submenu.Append(
			wx.ID_ANY,
			_("API &keys"),
			_("Manage the API keys")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onAPIKeys, item)
		item = self.submenu.Append(
			wx.ID_ANY,
			_("API &usage"),
			_("Open the API usage webpage")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onAPIUsage, item)
		item = self.submenu.Append(
			wx.ID_ANY,
			_("Git&Hub repository"),
			_("Open the GitHub repository of this addon")
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.onGitRepo, item)

		self.submenu.AppendSeparator()

		item = self.submenu.Append(
			wx.ID_ANY,
			_("Check for &updates..."),
			_("Check for updates")
		)
		gui.mainFrame.sysTrayIcon.Bind(
			wx.EVT_MENU,
			self.onCheckForUpdates,
			item
		)

		addon_name = ADDON_INFO["name"]
		addon_version = ADDON_INFO["version"]
		self.submenu_item = gui.mainFrame.sysTrayIcon.menu.InsertMenu(
			2,
			wx.ID_ANY,
			_("Open A&I {addon_version}".format(
				addon_version=addon_version)
			),
			self.submenu
		)

	def onAPIKeys(self, evt):
		url = "https://platform.openai.com/api-keys"
		os.startfile(url)

	def onAPIUsage(self, evt):
		url = "https://platform.openai.com/usage"
		os.startfile(url)

	def onGitRepo(self, evt):
		url = "https://github.com/aaclause/nvda-OpenAI/"
		os.startfile(url)

	def onDocumentation(self, evt):
		import languageHandler
		languages = ["en"]
		language = languageHandler.getLanguage()
		if '_' in language:
			languages.insert(0, language.split('_')[0])
		languages.insert(0, language)
		for lang in languages:
			fp = os.path.join(ROOT_ADDON_DIR, "doc", lang, "readme.html")
			if os.path.exists(fp):
				os.startfile(fp)
				break

	def onCheckForUpdates(self, evt):
		updatecheck.check_update(
			auto=False
		)
		updatecheck.update_last_check()

	def terminate(self):
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(SettingsDlg)
		gui.mainFrame.sysTrayIcon.menu.DestroyItem(self.submenu_item)
		super().terminate()

	def getClient(self):
		if conf["renewClient"]:
			self.client = None
			conf["renewClient"] = False
		if self.client:
			return self.client

		# initialize the client with the first available provider, will be adjusted on the fly if needed
		for provider in apikeymanager.AVAILABLE_PROVIDERS:
			manager = apikeymanager.get(provider)
			if not manager.isReady():
				continue
			api_key = manager.get_api_key()
			if not api_key or not api_key.strip():
				return None
			self.client = OpenAI(
				api_key=api_key
			)
			organization = manager.get_api_key(use_org=True)
			if organization and organization.count(":=") == 1:
				self.client.organization = organization.split(":=")[1]
			self.client.base_url = BASE_URLs[manager.provider]
			return self.client
		return None

	def checkScreenCurtain(self):
		from visionEnhancementProviders.screenCurtain import ScreenCurtainProvider
		import vision
		screenCurtainId = ScreenCurtainProvider.getSettings().getId()
		screenCurtainProviderInfo = vision.handler.getProviderInfo(screenCurtainId)
		isScreenCurtainRunning = bool(vision.handler.getProviderInstance(screenCurtainProviderInfo))
		if isScreenCurtainRunning:
			ui.message(_("Please disable the screen curtain before taking a screenshot"))
		return isScreenCurtainRunning

	def onShowMainDialog(self, evt):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		from . import maindialog
		gui.mainFrame.popupSettingsDialog (
			maindialog.OpenAIDlg,
			client=self.getClient(),
			conf=conf
		)

	@script(
		gesture="kb:nvda+g",
		description=_("Show Open AI dialog")
	)
	def script_showMainDialog(self, gesture):
		self.onShowMainDialog(None)

	def startChatSession(self, pathList):
		from . import maindialog
		if (
			maindialog.addToSession
			and isinstance(maindialog.addToSession, maindialog.OpenAIDlg)
		):
			instance = maindialog.addToSession
			if not instance.pathList:
				instance.pathList = []
			instance.addImageToList(
				pathList,
				True
			)
			instance.updateImageList()
			instance.SetFocus()
			instance.Raise()
			api.processPendingEvents()
			ui.message(
				_("Image added to an existing session")
			)
			return
		gui.mainFrame.popupSettingsDialog(
			maindialog.OpenAIDlg,
			client=self.getClient(),
			conf=conf,
			pathList=[pathList]
		)

	@script(
		gesture="kb:nvda+e",
		# Translators: This is the description of a command to take a screenshot and describe it.
		description=_("Take a screenshot and describe it")
	)
	def script_recognizeScreen(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self.checkScreenCurtain():
			return
		with mss.mss() as sct:
			now = time.strftime("%Y-%m-%d_-_%H:%M:%S")
			tmpPath = os.path.join(
				DATA_DIR,
				f"screen_{now}.png".replace(":", "")
			)
			if os.path.exists(tmpPath):
				return
			sct.shot(output=tmpPath)
			name = _("Screenshot %s") % (
				now.split("_-_")[-1]
			)
			self.startChatSession((tmpPath, name))

	@script(
		gesture="kb:nvda+o",
		# Translators: This is the description of a command to grab the current navigator object and describe it.
		description=_("Grab the current navigator object and describe it")
	)
	def script_recognizeObject(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self.checkScreenCurtain():
			return
		with mss.mss() as sct:
			now = time.strftime("%Y-%m-%d_-_%H:%M:%S")
			tmpPath = os.path.join(
				DATA_DIR,
				f"object_{now}.png".replace(":", "")
			)
			if os.path.exists(tmpPath):
				return
			nav = api.getNavigatorObject()
			name = nav.name
			nav.scrollIntoView()
			location = nav.location
			monitor = {"top": location.top, "left": location.left, "width": location.width, "height": location.height}
			sct_img = sct.grab(monitor)
			mss.tools.to_png(sct_img.rgb, sct_img.size, output=tmpPath)
		# Translators: This is the name of the screenshot to be described.
		default_name = _("Navigator Object %s") % (
			now.split("_-_")[-1]
		)
		name = nav.name
		if (
			not name
			or not name.strip()
			or '\n' in name
			or len(name) > 80
		):
			name = default_name
		else:
			name = "%s (%s)" % (name.strip(), default_name)
		self.startChatSession((tmpPath, name))

	@script(
		description=_("Toggle the microphone recording and transcribe the audio from anywhere")
	)
	def script_toggleRecording(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self.recordThread:
			self.recordThread.stop()
			self.recordThread = None
		else:
			self.recordThread = RecordThread(
				self.getClient(),
				conf=conf["audio"]
			)
			self.recordThread.start()
