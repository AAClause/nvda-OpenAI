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
from .imagehelper import describeFromImageFileList
additionalLibsPath = os.path.join(ADDON_DIR, "lib")
sys.path.insert(0, additionalLibsPath)
import mss
from openai import OpenAI
sys.path.remove(additionalLibsPath)

addonHandler.initTranslation()

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
	"saveSystem": "boolean(default=False)",
	"advancedMode": "boolean(default=False)",
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

		mainDialogGroup = _("Main dialog")
		mainDialogSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=mainDialogGroup)
		mainDialogBox = mainDialogSizer.GetStaticBox()
		mainDialog = gui.guiHelper.BoxSizerHelper(self, sizer=mainDialogSizer)

		if not conf["use_org"]:
			self.org_name.Disable()
			self.org_key.Disable()

		label = _("Block the closing using the &escape key")
		self.blockEscape = wx.CheckBox(
			self,
			label=label,
		)
		self.blockEscape.SetValue(conf["blockEscapeKey"])
		mainDialog.addItem(self.blockEscape)

		label = _("Remember the content of the S&ystem field between sessions")
		self.saveSystem = wx.CheckBox(
			self,
			label=label,
		)
		self.saveSystem.SetValue(conf["saveSystem"])
		mainDialog.addItem(self.saveSystem)

		label = _("Enable &advanced settings (including temperature and probability mass)")
		self.advancedMode = wx.CheckBox(
			self,
			label=label,
		)
		self.advancedMode.SetValue(conf["advancedMode"])
		mainDialog.addItem(self.advancedMode)

		sHelper.addItem(mainDialogSizer)

		TTSGroup = _("Text To Speech")
		TTSSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=TTSGroup)
		TTSBox = TTSSizer.GetStaticBox()
		TTS = gui.guiHelper.BoxSizerHelper(self, sizer=TTSSizer)

		self.voiceList = TTS.addLabeledControl(
			_("&Voice:"),
			wx.Choice,
			choices=TTS_VOICES,
		)
		itemToSelect = 0
		if conf["TTSVoice"] in TTS_VOICES:
			itemToSelect = TTS_VOICES.index(conf["TTSVoice"])
		self.voiceList.SetSelection(itemToSelect)

		self.modelList = TTS.addLabeledControl(
			_("&Model:"),
			wx.Choice,
			choices=TTS_MODELS,
		)
		itemToSelect = 0
		if conf["TTSModel"] in TTS_MODELS:
			itemToSelect = TTS_MODELS.index(conf["TTSModel"])
		self.modelList.SetSelection(itemToSelect)

		sHelper.addItem(TTSSizer)

	def onUseOrg(self, evt):
		self.org_name.Enable(self.use_org.GetValue())
		self.org_key.Enable(self.use_org.GetValue())

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


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

	scriptCategory = "Open AI"

	def __init__(self):
		super().__init__()
		APIKey = api_key_manager.get_api_key()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SettingsDlg)
		self.client = None

	def terminate(self):
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(SettingsDlg)
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

	@script(
		gesture="kb:nvda+g",
		description=_("Show Open AI dialog")
	)
	def script_showMainDialog(self, gesture):
		if not self.getClient():
			return ui.message(NO_AUTHENTICATION_KEY_PROVIDED_MSG)
		from . import maindialog
		gui.mainFrame.popupSettingsDialog (
			maindialog.OpenAIDlg,
			client=self.getClient(),
			conf=conf
		)

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

	def checkScreenCurtain(self):
		from visionEnhancementProviders.screenCurtain import ScreenCurtainProvider
		import vision
		screenCurtainId = ScreenCurtainProvider.getSettings().getId()
		screenCurtainProviderInfo = vision.handler.getProviderInfo(screenCurtainId)
		isScreenCurtainRunning = bool(vision.handler.getProviderInstance(screenCurtainProviderInfo))
		if isScreenCurtainRunning:
			ui.message(_("Please disable the screen curtain before taking a screenshot"))
		return isScreenCurtainRunning
