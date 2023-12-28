import json
import os
import sys
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
from .apikeymanager import APIKeyManager
from .consts import (
	ADDON_DIR, DATA_DIR,
	DEFAULT_MODEL, DEFAULT_TOP_P, DEFAULT_N,
	TOP_P_MIN, TOP_P_MAX,
	N_MIN, N_MAX,
	TTS_VOICES, TTS_MODELS, TTS_DEFAULT_VOICE, TTS_DEFAULT_MODEL
)
from .recordthread import RecordThread

additionalLibsPath = os.path.join(ADDON_DIR, "lib")
sys.path.insert(0, additionalLibsPath)
import mss
from openai import OpenAI
sys.path.remove(additionalLibsPath)

addonHandler.initTranslation()

ROOT_ADDON_DIR = "\\".join(ADDON_DIR.split(os.sep)[:-2])
ADDON_INFO = addonHandler.Addon(
	ROOT_ADDON_DIR
).manifest

confSpecs = {
	"use_org": "boolean(default=False)",
	"model": f"string(default={DEFAULT_MODEL.name})",
	"topP": f"integer(min={TOP_P_MIN}, max={TOP_P_MAX}, default={DEFAULT_TOP_P})",
	"n": f"integer(min={N_MIN}, max={N_MAX}, default={DEFAULT_N})",
	"stream": "boolean(default=True)",
	"TTSModel": f"option({', '.join(TTS_MODELS)}, default={TTS_DEFAULT_MODEL})",
	"TTSVoice": f"option({', '.join(TTS_VOICES)}, default={TTS_DEFAULT_VOICE})",
	"blockEscapeKey": "boolean(default=False)",
	"conversationMode": "boolean(default=True)",
	"saveSystem": "boolean(default=true)",
	"advancedMode": "boolean(default=False)",
	"images": {
		"maxHeight": "integer(min=0, default=720)",
		"maxWidth": "integer(min=0, default=0)",
		"quality": "integer(min=0, max=100, default=85)",
		"resize": "boolean(default=False)",
		"resizeInfoDisplayed": "boolean(default=False)",
		"useCustomPrompt": "boolean(default=False)",
		"customPromptText": 'string(default="")'
	},
	"audio": {
		"sampleRate": "integer(min=8000, max=48000, default=16000)",
		"channels": "integer(min=1, max=2, default=1)",
		"dtype": "string(default=int16)"
	},
	"renewClient": "boolean(default=False)",
	"debug": "boolean(default=False)"
}
config.conf.spec["OpenAI"] = confSpecs
conf = config.conf["OpenAI"]

NO_AUTHENTICATION_KEY_PROVIDED_MSG = _("No authentication key provided. Please set it in the Preferences dialog.")

api_key_manager = APIKeyManager(DATA_DIR)

class SettingsDlg(gui.settingsDialogs.SettingsPanel):

	title = "Open AI"

	def makeSettings(self, settingsSizer):
		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		APIKey = api_key_manager.get_api_key()
		if not APIKey: APIKey = ''
		APIKeyOrg = api_key_manager.get_api_key(use_org=True)
		org_name = ""
		org_key = ""
		if APIKeyOrg and ":=" in APIKeyOrg :
			org_name, org_key = APIKeyOrg.split(":=")
		self.APIKey = sHelper.addLabeledControl(
			_("API Key:"),
			wx.TextCtrl,
			value=APIKey
		)

		orgGroupLabel = _("Organization")
		orgSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=orgGroupLabel)
		orgGroupBox = orgSizer.GetStaticBox()
		orgGroup = gui.guiHelper.BoxSizerHelper(self, sizer=orgSizer)

		self.use_org = orgGroup.addItem(
			wx.CheckBox(
				orgGroupBox,
				label=_("Use or&ganization"))
		)
		self.use_org.SetValue(
			conf["use_org"]
		)
		self.use_org.Bind(
			wx.EVT_CHECKBOX,
			self.onUseOrg
		)

		self.org_name = orgGroup.addLabeledControl(
			_("Organization &name:"),
			wx.TextCtrl,
			value=org_name
		)

		self.org_key = orgGroup.addLabeledControl(
			_("&Organization key:"),
			wx.TextCtrl,
			value=org_key
		)

		sHelper.addItem(orgSizer)

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

		sHelper.addItem(mainDialogSizer)

		self.onUseOrg(None)
		self.onResize(None)

	def onUseOrg(self, evt):
		self.org_name.Enable(self.use_org.GetValue())
		self.org_key.Enable(self.use_org.GetValue())

	def onResize(self, evt):
		self.maxWidth.Enable(self.resize.GetValue())
		self.maxHeight.Enable(self.resize.GetValue())
		self.quality.Enable(self.resize.GetValue())
	def onDefaultPrompt(self, evt):
		if self.useCustomPrompt.GetValue():
			self.customPromptText.Enable()
			self.customPromptText.SetValue(conf["images"]["customPromptText"])
		else:
			self.customPromptText.Enable(False)

	def onSave(self):
		api_key = self.APIKey.GetValue().strip()
		api_key_manager.save_api_key(api_key)
		api_key_org = self.org_key.GetValue().strip()
		conf["use_org"] = self.use_org.GetValue()
		org_name = self.org_name.GetValue().strip()
		if conf["use_org"]:
			if not api_key_org:
				self.org_key.SetFocus()
				return
			if not org_name:
				self.org_name.SetFocus()
				return
		api_key_manager.save_api_key(
			api_key_org,
			org=True,
			org_name=org_name
		)
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

class GlobalPlugin(globalPluginHandler.GlobalPlugin):

	scriptCategory = "Open AI"

	def __init__(self):
		super().__init__()
		APIKey = api_key_manager.get_api_key()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SettingsDlg)
		self.client = None
		self.recordtThread = None
		self.createMenu()

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
		api_key = api_key_manager.get_api_key()
		organization = api_key_manager.get_api_key(use_org=True)
		if not api_key or not api_key.strip():
			return None
		if conf["use_org"]:
			if not organization or not organization.strip():
				return None
			self.client = OpenAI(
				organization=organization.split(":=")[1],
				api_key=api_key
			)
		else:
			self.client = OpenAI(api_key=api_key)
		return self.client

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

	@script(
		gesture="kb:nvda+e",
		description=_("Take a screenshot and describe it")
	)
	def script_recognizeScreen(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self.checkScreenCurtain():
			return
		with mss.mss() as sct:
			tmpPath = os.path.join(DATA_DIR, "screen.png")
			sct.shot(output=tmpPath)
			from . import maindialog
			gui.mainFrame.popupSettingsDialog(
				maindialog.OpenAIDlg,
				client=self.getClient(),
				conf=conf,
				pathList=[tmpPath]
			)

	@script(
		gesture="kb:nvda+o",
		description=_("Grab the current navigator object and describe it")
	)
	def script_recognizeObject(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self.checkScreenCurtain():
			return
		with mss.mss() as sct:
			tmpPath = os.path.join(DATA_DIR, "object.png")
			nav = api.getNavigatorObject()
			nav.scrollIntoView()
			if (
				nav.role == controlTypes.ROLE_LINK
				and nav.value
				and nav.value.startswith("http")
			):
				tmpPath = [nav.value]
			else:
				location = nav.location
				monitor = {"top": location.top, "left": location.left, "width": location.width, "height": location.height}
				sct_img = sct.grab(monitor)
				mss.tools.to_png(sct_img.rgb, sct_img.size, output=tmpPath)
			from . import maindialog
			gui.mainFrame.popupSettingsDialog(
				maindialog.OpenAIDlg,
				client=self.getClient(),
				conf=conf,
				pathList=[tmpPath]
			)

	@script(
		description=_("Toggle the microphone recording and transcribe the audio from anywhere")
	)
	def script_toggleRecording(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		if self.recordtThread:
			self.recordtThread.stop()
			self.recordtThread = None
		else:
			self.recordtThread = RecordThread(
				self.getClient(),
				conf=conf["audio"]
			)
			self.recordtThread.start()
