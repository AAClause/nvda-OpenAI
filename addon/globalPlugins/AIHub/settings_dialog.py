"""Addon NVDA preferences panel for AI-Hub."""

import addonHandler
import config
import gui
import wx

from . import apikeymanager
from .consts import (
	Provider,
	TranscriptionProvider,
	TRANSCRIPTION_PROVIDERS,
	TTS_MODELS,
	TTS_VOICES,
)

addonHandler.initTranslation()

conf = config.conf["AIHub"]


class AIHubSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = "AI-Hub"

	def makeSettings(self, settingsSizer):
		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		accountsSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("API accounts"))
		accountsBox = accountsSizer.GetStaticBox()
		accountsGroup = gui.guiHelper.BoxSizerHelper(self, sizer=accountsSizer)
		# Translators: NVDA Preferences — AI-Hub category: button in the API accounts group that opens account management.
		self.manageAccountsBtn = wx.Button(accountsBox, label=_("Manage API &accounts..."))
		self.manageAccountsBtn.Bind(wx.EVT_BUTTON, self.onManageAccounts)
		accountsGroup.addItem(self.manageAccountsBtn)
		accountsHint = wx.StaticText(
			accountsBox,
			# Translators: NVDA Preferences — AI-Hub category: helper text under a settings group or next to a field.
			label=_("Opens a separate dialog to add, edit, or remove provider accounts."),
		)
		accountsGroup.addItem(accountsHint)
		sHelper.addItem(accountsSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		conversationSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Conversation"))
		conversationBox = conversationSizer.GetStaticBox()
		conversationGroup = gui.guiHelper.BoxSizerHelper(self, sizer=conversationSizer)

		# Translators: NVDA Preferences — AI-Hub category: Conversation section — blocks closing the hub with Escape.
		self.blockEscape = wx.CheckBox(conversationBox, label=_("Block the closing using the &escape key"))
		self.blockEscape.SetValue(conf["blockEscapeKey"])
		conversationGroup.addItem(self.blockEscape)

		# Translators: NVDA Preferences — AI-Hub category: Conversation section — persist system prompt between sessions.
		self.saveSystem = wx.CheckBox(conversationBox, label=_("Remember the content of the S&ystem field between sessions"))
		self.saveSystem.SetValue(conf["saveSystem"])
		conversationGroup.addItem(self.saveSystem)

		# Translators: NVDA Preferences — AI-Hub category: Conversation section — auto-save after each assistant reply.
		self.autoSaveConversation = wx.CheckBox(conversationBox, label=_("&Auto-save conversations after each response"))
		self.autoSaveConversation.SetValue(conf.get("autoSaveConversation", True))
		conversationGroup.addItem(self.autoSaveConversation)

		sHelper.addItem(conversationSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		ttsSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Text To Speech"))
		ttsGroup = gui.guiHelper.BoxSizerHelper(self, sizer=ttsSizer)
		# Translators: NVDA Preferences — AI-Hub category: Text To Speech section — TTS voice choice label.
		self.voiceList = ttsGroup.addLabeledControl(_("&Voice:"), wx.Choice, choices=TTS_VOICES)
		voiceIndex = TTS_VOICES.index(conf["TTSVoice"]) if conf["TTSVoice"] in TTS_VOICES else 0
		self.voiceList.SetSelection(voiceIndex)
		# Translators: NVDA Preferences — AI-Hub category: Text To Speech section — TTS model choice label.
		self.modelList = ttsGroup.addLabeledControl(_("&Model:"), wx.Choice, choices=TTS_MODELS)
		modelIndex = TTS_MODELS.index(conf["TTSModel"]) if conf["TTSModel"] in TTS_MODELS else 0
		self.modelList.SetSelection(modelIndex)
		sHelper.addItem(ttsSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		imageSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Image description"))
		imageBox = imageSizer.GetStaticBox()
		imageGroup = gui.guiHelper.BoxSizerHelper(self, sizer=imageSizer)
		self.resize = imageGroup.addItem(
			# Translators: NVDA Preferences — AI-Hub category: Image description section — resize images before API upload.
			wx.CheckBox(imageBox, label=_("&Resize images before sending them to the API"))
		)
		self.resize.SetValue(conf["images"]["resize"])
		self.resize.Bind(wx.EVT_CHECKBOX, self.onResize)
		self.maxWidth = imageGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Image description section — max width spin box label.
			_("Maximum &width (0 to resize proportionally to the height):"),
			wx.SpinCtrl,
			min=0,
			max=2000
		)
		self.maxWidth.SetValue(conf["images"]["maxWidth"])
		self.maxHeight = imageGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Image description section — max height spin box label.
			_("Maximum &height (0 to resize proportionally to the width):"),
			wx.SpinCtrl,
			min=0,
			max=2000
		)
		self.maxHeight.SetValue(conf["images"]["maxHeight"])
		self.quality = imageGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Image description section — JPEG quality spin box label.
			_("&Quality for JPEG images (0 [worst] to 95 [best], values above 95 should be avoided):"),
			wx.SpinCtrl,
			min=1,
			max=100
		)
		self.quality.SetValue(conf["images"]["quality"])
		self.useCustomPrompt = imageGroup.addItem(
			# Translators: NVDA Preferences — AI-Hub category: Image description section — allow editing the default describe-image prompt.
			wx.CheckBox(imageBox, label=_("Customize default text &prompt"))
		)
		self.useCustomPrompt.Bind(wx.EVT_CHECKBOX, self.onDefaultPrompt)
		self.useCustomPrompt.SetValue(conf["images"]["useCustomPrompt"])
		self.customPromptText = imageGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Image description section — custom default prompt for describe-image.
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

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		chatFeedbackSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Chat feedback"))
		chatFeedbackBox = chatFeedbackSizer.GetStaticBox()
		chatFeedbackGroup = gui.guiHelper.BoxSizerHelper(self, sizer=chatFeedbackSizer)
		self.chatFeedback = {
			"sndTaskInProgress": chatFeedbackGroup.addItem(
				# Translators: NVDA Preferences — AI-Hub category: Chat feedback section — sound while a request runs.
				wx.CheckBox(chatFeedbackBox, label=_("Play sound when a task is in progress"))
			),
			"sndResponseSent": chatFeedbackGroup.addItem(
				# Translators: NVDA Preferences — AI-Hub category: Chat feedback section — sound when your prompt is sent.
				wx.CheckBox(chatFeedbackBox, label=_("Play sound when a response is sent"))
			),
			"sndResponsePending": chatFeedbackGroup.addItem(
				# Translators: NVDA Preferences — AI-Hub category: Chat feedback section — sound while waiting for the model.
				wx.CheckBox(chatFeedbackBox, label=_("Play sound when a response is pending"))
			),
			"sndResponseReceived": chatFeedbackGroup.addItem(
				# Translators: NVDA Preferences — AI-Hub category: Chat feedback section — sound when the reply arrives.
				wx.CheckBox(chatFeedbackBox, label=_("Play sound when a response is received"))
			),
			"focusHistoryOnAssistantResponse": chatFeedbackGroup.addItem(
				# Translators: NVDA Preferences — AI-Hub category: Chat feedback section — focus message history and put the caret right after the «Assistant:» prefix when the first reply token streams in.
				wx.CheckBox(chatFeedbackBox, label=_("Move focus to message history when the first assistant reply token arrives"))
			),
			"speechResponseReceived": chatFeedbackGroup.addItem(
				# Translators: NVDA Preferences — AI-Hub category: Chat feedback section — speak incoming replies while focus stays in the prompt.
				wx.CheckBox(chatFeedbackBox, label=_("Speak response when the focus is in the prompt field"))
			),
		}
		for key, item in self.chatFeedback.items():
			item.SetValue(conf["chatFeedback"][key])
		sHelper.addItem(chatFeedbackSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		recordingSizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Recording"))
		recordingGroup = gui.guiHelper.BoxSizerHelper(self, sizer=recordingSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		providerSizer = wx.StaticBoxSizer(wx.VERTICAL, recordingSizer.GetStaticBox(), label=_("Provider"))
		providerGroup = gui.guiHelper.BoxSizerHelper(self, sizer=providerSizer)
		transcriptionChoices = [
			# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — local whisper.cpp option.
			_("whisper.cpp (local)"),
			# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — OpenAI Whisper option.
			_("OpenAI Whisper"),
			# Translators: NVDA Preferences — AI-Hub category: Recording — Transcription provider list — Mistral Voxtral option.
			_("Mistral Voxtral"),
		]
		self.transcriptionProviderChoice = providerGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Recording — Provider — which engine handles dictation transcription.
			_("Transcription &provider:"),
			wx.Choice,
			choices=transcriptionChoices,
		)
		provider = conf["audio"].get("transcriptionProvider", TranscriptionProvider.OPENAI.value)
		if conf["audio"]["whisper.cpp"]["enabled"]:
			provider = TranscriptionProvider.WHISPER_CPP.value
		providerIndex = list(TRANSCRIPTION_PROVIDERS).index(provider) if provider in TRANSCRIPTION_PROVIDERS else 1
		self.transcriptionProviderChoice.SetSelection(providerIndex)
		self.transcriptionProviderChoice.Bind(wx.EVT_CHOICE, self.onTranscriptionProviderChange)
		self.whisperHost = providerGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Recording — whisper.cpp HTTP host field label.
			_("&Host (whisper.cpp):"),
			wx.TextCtrl,
			value=conf["audio"]["whisper.cpp"]["host"]
		)
		recordingGroup.addItem(providerSizer)

		accountsMapSizer = wx.StaticBoxSizer(
			wx.VERTICAL,
			recordingSizer.GetStaticBox(),
			# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
			label=_("Provider account mapping")
		)
		accountsMapGroup = gui.guiHelper.BoxSizerHelper(self, sizer=accountsMapSizer)
		accountsMapHint = wx.StaticText(
			accountsMapSizer.GetStaticBox(),
			# Translators: NVDA Preferences — AI-Hub category: helper text under a settings group or next to a field.
			label=_("Use these only when dictation must always use a specific account.")
		)
		accountsMapGroup.addItem(accountsMapHint)
		self.openaiTranscriptionAccountChoice = accountsMapGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Recording — Provider account mapping — OpenAI account drop-down label.
			_("OpenAI transcription account:"),
			wx.Choice,
			choices=[],
		)
		self.mistralTranscriptionAccountChoice = accountsMapGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Recording — Provider account mapping — Mistral account drop-down label.
			_("Mistral transcription account:"),
			wx.Choice,
			choices=[],
		)
		self._refreshTranscriptionAccountChoices()
		recordingGroup.addItem(accountsMapSizer)

		# Translators: NVDA Preferences — AI-Hub category: title of a bordered settings group.
		cleanupSizer = wx.StaticBoxSizer(wx.VERTICAL, recordingSizer.GetStaticBox(), label=_("Audio preprocessing"))
		cleanupGroup = gui.guiHelper.BoxSizerHelper(self, sizer=cleanupSizer)
		self.trimSilenceCheckbox = cleanupGroup.addItem(
			wx.CheckBox(
				cleanupSizer.GetStaticBox(),
				# Translators: NVDA Preferences — AI-Hub category: Recording — Audio preprocessing — trim silence before transcription.
				label=_("&Trim silence (remove leading/trailing and silence > 2s)")
			)
		)
		self.trimSilenceCheckbox.SetValue(conf["audio"].get("trimSilence", True))
		self.trimSilenceCheckbox.Bind(wx.EVT_CHECKBOX, self.onTrimSilenceChange)
		self.minSilenceSec = cleanupGroup.addLabeledControl(
			# Translators: NVDA Preferences — AI-Hub category: Recording — Audio preprocessing — minimum silence length spin box.
			_("Minimum silence &duration to remove (seconds):"),
			wx.SpinCtrl,
			min=1,
			max=10
		)
		self.minSilenceSec.SetValue(int(conf["audio"].get("minSilenceSec", 2.0)))
		self.minSilenceSec.Enable(conf["audio"].get("trimSilence", True))
		recordingGroup.addItem(cleanupSizer)

		sHelper.addItem(recordingSizer)

		self.onResize(None)
		self.onTranscriptionProviderChange(None)

	def _buildTranscriptionAccountChoices(self, provider_name: str, configured_id: str):
		manager = apikeymanager.get(provider_name)
		active_id = manager.get_active_account_id()
		accounts = manager.list_accounts(include_env=True)
		# Translators: NVDA Preferences — AI-Hub category: Recording — first item in OpenAI/Mistral transcription account drop-downs.
		labels = [_("Use provider active account (default)")]
		ids = [""]
		selected_idx = 0
		for acc in accounts:
			acc_id = acc.get("id", "")
			if not acc_id:
				continue
			# Translators: NVDA Preferences — AI-Hub category: Recording — fallback account display name when the account has no custom name.
			name = acc.get("name") or _("Account")
			label = name
			if acc_id == active_id:
				# Translators: NVDA Preferences — AI-Hub category: Recording — suffix marking the provider’s active account in account lists.
				label = f"{label} ({_('default')})"
			labels.append(label)
			ids.append(acc_id)
			if configured_id and acc_id == configured_id:
				selected_idx = len(ids) - 1
		return labels, ids, selected_idx

	def _refreshTranscriptionAccountChoices(self):
		if not hasattr(self, "openaiTranscriptionAccountChoice") or not hasattr(self, "mistralTranscriptionAccountChoice"):
			return
		audio_conf = conf.get("audio", {})
		openai_id = audio_conf.get("openaiTranscriptionAccountId", "")
		mistral_id = audio_conf.get("mistralTranscriptionAccountId", "")
		labels, ids, selected_idx = self._buildTranscriptionAccountChoices(Provider.OpenAI, openai_id)
		self._openaiTranscriptionAccountIds = ids
		self.openaiTranscriptionAccountChoice.SetItems(labels)
		self.openaiTranscriptionAccountChoice.SetSelection(selected_idx)
		labels, ids, selected_idx = self._buildTranscriptionAccountChoices(Provider.MistralAI, mistral_id)
		self._mistralTranscriptionAccountIds = ids
		self.mistralTranscriptionAccountChoice.SetItems(labels)
		self.mistralTranscriptionAccountChoice.SetSelection(selected_idx)

	def onManageAccounts(self, evt):
		from .accounts_dialog import show_accounts_management
		show_accounts_management(self)
		self._refreshTranscriptionAccountChoices()

	def onResize(self, evt):
		self.maxWidth.Enable(self.resize.GetValue())
		self.maxHeight.Enable(self.resize.GetValue())
		self.quality.Enable(self.resize.GetValue())

	def onTranscriptionProviderChange(self, evt):
		idx = self.transcriptionProviderChoice.GetSelection()
		is_whisper_cpp = idx == 0
		is_openai = idx == 1
		is_mistral = idx == 2
		self.whisperHost.Enable(is_whisper_cpp)
		self.openaiTranscriptionAccountChoice.Enable(is_openai)
		self.mistralTranscriptionAccountChoice.Enable(is_mistral)

	def onTrimSilenceChange(self, evt):
		self.minSilenceSec.Enable(self.trimSilenceCheckbox.GetValue())

	def onWhisperCheckbox(self, evt):
		self.onTranscriptionProviderChange(None)

	def onDefaultPrompt(self, evt):
		if self.useCustomPrompt.GetValue():
			self.customPromptText.Enable()
			self.customPromptText.SetValue(conf["images"]["customPromptText"])
		else:
			self.customPromptText.Enable(False)

	def onSave(self):
		conf["blockEscapeKey"] = self.blockEscape.GetValue()
		conf["renewClient"] = True
		conf["saveSystem"] = self.saveSystem.GetValue()
		conf["autoSaveConversation"] = self.autoSaveConversation.GetValue()
		conf["TTSVoice"] = self.voiceList.GetString(self.voiceList.GetSelection())
		conf["TTSModel"] = self.modelList.GetString(self.modelList.GetSelection())
		conf["images"]["resize"] = self.resize.GetValue()
		conf["images"]["maxWidth"] = int(self.maxWidth.GetValue())
		conf["images"]["maxHeight"] = int(self.maxHeight.GetValue())
		conf["images"]["quality"] = int(self.quality.GetValue())
		if self.useCustomPrompt.GetValue():
			conf["images"]["useCustomPrompt"] = True
			conf["images"]["customPromptText"] = self.customPromptText.GetValue()
		else:
			conf["images"]["useCustomPrompt"] = False
		providerIndex = self.transcriptionProviderChoice.GetSelection()
		provider = TRANSCRIPTION_PROVIDERS[providerIndex]
		conf["audio"]["transcriptionProvider"] = provider
		conf["audio"]["whisper.cpp"]["enabled"] = provider == TranscriptionProvider.WHISPER_CPP
		conf["audio"]["whisper.cpp"]["host"] = self.whisperHost.GetValue()
		openai_idx = self.openaiTranscriptionAccountChoice.GetSelection()
		mistral_idx = self.mistralTranscriptionAccountChoice.GetSelection()
		conf["audio"]["openaiTranscriptionAccountId"] = (
			self._openaiTranscriptionAccountIds[openai_idx]
			if 0 <= openai_idx < len(getattr(self, "_openaiTranscriptionAccountIds", []))
			else ""
		)
		conf["audio"]["mistralTranscriptionAccountId"] = (
			self._mistralTranscriptionAccountIds[mistral_idx]
			if 0 <= mistral_idx < len(getattr(self, "_mistralTranscriptionAccountIds", []))
			else ""
		)
		conf["audio"]["trimSilence"] = self.trimSilenceCheckbox.GetValue()
		conf["audio"]["minSilenceSec"] = int(self.minSilenceSec.GetValue())
		for key, item in self.chatFeedback.items():
			conf["chatFeedback"][key] = item.GetValue()
