"""Dedicated dialog for Mistral Voxtral TTS."""

import base64
import json
import threading
import urllib.request
import winsound

import addonHandler
import wx
from logHandler import log

from .apiclient import APIConnectionError, APIStatusError
from .conversations import ConversationFormat
from .consts import (
	Provider,
	SND_CHAT_RESPONSE_RECEIVED,
	SND_PROGRESS,
	stop_progress_sound,
	UI_DIALOG_BORDER_PX,
	UI_FORM_ROW_BORDER_PX,
	UI_SECTION_SPACING_PX,
)
from .mediastore import build_media_path, persist_local_file
from .providertools_helpers import add_labeled_factory
from .thread_shutdown import stop_worker_thread
from .tool_dialog_base import ToolDialogBase

addonHandler.initTranslation()

class VoxtralTTSToolDialog(ToolDialogBase):
	SUGGESTED_MODELS = (
		"voxtral-mini-tts-latest",
		"voxtral-mini-tts-2603",
	)
	SUGGESTED_VOICE_IDS = (
		"",
	)

	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub Mistral Voxtral text-to-speech tool dialog.
			title=_("Tool: Voxtral TTS"),
			provider=Provider.MistralAI,
			size=(760, 650),
			parentDialog=parentDialog,
			plugin=plugin,
		)
		self._worker = None
		self._voiceFetchWorker = None
		self._voiceSuggestions = list(self.SUGGESTED_VOICE_IDS)
		self._voiceLabelToId = {}
		self._generatedAudioPath = ""
		self._restoredRefAudioPath = ""
		dialogSizer = wx.BoxSizer(wx.VERTICAL)
		self.formPanel = wx.Panel(self)
		main = wx.BoxSizer(wx.VERTICAL)
		# Translators: Group box title around the Mistral account selector in the Voxtral TTS tool.
		accountBox = wx.StaticBoxSizer(wx.VERTICAL, self.formPanel, _("Account"))
		# Translators: Group box title around the main synthesis request fields (text, model, output format) in Voxtral TTS.
		requestBox = wx.StaticBoxSizer(wx.VERTICAL, self.formPanel, _("Synthesis"))
		# Translators: Group box title around voice ID, reference audio, and voice refresh in the Voxtral TTS tool.
		voiceBox = wx.StaticBoxSizer(wx.VERTICAL, self.formPanel, _("Voice"))
		# Translators: Group box title around opening the generated file and the main action buttons in Voxtral TTS.
		outputBox = wx.StaticBoxSizer(wx.VERTICAL, self.formPanel, _("Output"))

		self.accountChoice = add_labeled_factory(
			self.formPanel,
			accountBox,
			# Translators: Label before the Mistral account drop-down in the Voxtral TTS tool.
			_("&Account:"),
			lambda: self.build_account_choice(self.formPanel),
		)
		self.inputText = add_labeled_factory(
			self.formPanel,
			requestBox,
			# Translators: Label before the multiline text to speak in the Voxtral TTS tool.
			_("&Text input:"),
			lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 140)),
		)
		self.modelText = add_labeled_factory(
			self.formPanel,
			requestBox,
			# Translators: Label before the Voxtral TTS model combo box in this tool window.
			_("&Model:"),
			lambda: wx.ComboBox(
				self.formPanel,
				choices=list(self.SUGGESTED_MODELS),
				style=wx.CB_DROPDOWN,
				value=self.SUGGESTED_MODELS[0],
			),
		)
		self.voiceIdText = add_labeled_factory(
			self.formPanel,
			voiceBox,
			# Translators: Label before the optional saved voice id combo box used for Voxtral voice selection or cloning.
			_("Saved &voice ID (optional):"),
			lambda: wx.ComboBox(
				self.formPanel,
				choices=self._voiceSuggestions,
				style=wx.CB_DROPDOWN,
				value="",
			),
		)
		self.formatChoice = add_labeled_factory(
			self.formPanel,
			requestBox,
			# Translators: Label before the output audio format drop-down (wav, mp3, etc.) in the Voxtral TTS tool.
			_("Output &format:"),
			lambda: wx.Choice(self.formPanel, choices=["wav", "mp3", "flac", "opus", "pcm"]),
		)
		self.formatChoice.SetStringSelection("wav")
		self.refAudioText = add_labeled_factory(
			self.formPanel,
			voiceBox,
			# Translators: Label before the optional path field for a reference audio clip used in voice cloning.
			_("Reference &audio (optional):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.refAudioText.Bind(wx.EVT_TEXT, lambda evt: (self._syncOpenButtons(), evt.Skip()))
		# Translators: Button that opens a file picker for the optional Voxtral reference-audio clip.
		self.browseRefAudioBtn = wx.Button(self.formPanel, label=_("Browse reference audio..."))
		self.browseRefAudioBtn.Bind(wx.EVT_BUTTON, self.onBrowseRefAudio)
		voiceBox.Add(self.browseRefAudioBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the selected reference audio file from disk or URL handler.
		self.openRefAudioBtn = wx.Button(self.formPanel, label=_("Open reference audio"))
		self.openRefAudioBtn.Bind(wx.EVT_BUTTON, self.onOpenReferenceAudio)
		voiceBox.Add(self.openRefAudioBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		voiceHint = wx.StaticText(
			self.formPanel,
			# Translators: Static hint under the voice section explaining how saved voice IDs and reference clips interact.
			label=_("Use a saved voice ID or provide a reference audio clip for voice cloning."),
		)
		voiceBox.Add(voiceHint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that re-fetches the list of saved voice IDs from the Mistral API for the combo box.
		self.refreshVoicesBtn = wx.Button(self.formPanel, label=_("Refresh voices"))
		self.refreshVoicesBtn.Bind(wx.EVT_BUTTON, self.onRefreshVoices)
		voiceBox.Add(self.refreshVoicesBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the last synthesized audio file from the Voxtral TTS tool.
		self.openGeneratedAudioBtn = wx.Button(self.formPanel, label=_("Open generated audio"))
		self.openGeneratedAudioBtn.Bind(wx.EVT_BUTTON, self.onOpenGeneratedAudio)
		outputBox.Add(self.openGeneratedAudioBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that starts Voxtral TTS synthesis with the current form settings.
		self.runBtn = wx.Button(self.formPanel, label=_("Generate speech"))
		self.runBtn.Bind(wx.EVT_BUTTON, self.onRun)
		self.bind_ctrl_enter_submit(self.onRun)
		self.closeBtn = wx.Button(self.formPanel, id=wx.ID_CLOSE)
		self.closeBtn.Bind(wx.EVT_BUTTON, self.onClose)
		buttons.Add(self.runBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		buttons.Add(self.closeBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		outputBox.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, UI_SECTION_SPACING_PX)

		main.Add(accountBox, 0, wx.EXPAND | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		main.Add(requestBox, 0, wx.EXPAND | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		main.Add(voiceBox, 0, wx.EXPAND | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		main.Add(outputBox, 0, wx.EXPAND | wx.BOTTOM, 2)

		self.formPanel.SetSizer(main)
		dialogSizer.Add(self.formPanel, 1, wx.EXPAND | wx.ALL, UI_DIALOG_BORDER_PX)
		self.SetSizer(dialogSizer)
		if parent:
			self.CentreOnParent(wx.BOTH)
		else:
			self.Centre(wx.BOTH)
		self.accountChoice.Bind(wx.EVT_CHOICE, self.onAccountChange)
		self._refreshVoicesAsync()
		self._applyConversationData(conversationData)
		self._syncOpenButtons()

	def onBrowseRefAudio(self, evt):
		dlg = wx.FileDialog(
			self,
			# Translators: Title of the file picker for the optional Voxtral reference-voice clip.
			message=_("Select reference audio file"),
			defaultFile="",
			# Translators: File-type filter in the reference-audio picker for Voxtral TTS.
			wildcard=_("Audio files (*.wav;*.mp3;*.flac;*.opus)|*.wav;*.mp3;*.flac;*.opus"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		if dlg.ShowModal() == wx.ID_OK:
			self.refAudioText.SetValue(dlg.GetPath())
			self._syncOpenButtons()

	def _setBusy(self, busy: bool):
		for ctrl in (
			self.accountChoice,
			self.inputText,
			self.modelText,
			self.voiceIdText,
			self.formatChoice,
			self.refAudioText,
			self.browseRefAudioBtn,
			self.refreshVoicesBtn,
			self.openRefAudioBtn,
			self.openGeneratedAudioBtn,
			self.runBtn,
			self.closeBtn,
		):
			ctrl.Enable(not busy)

	def _run_thread(self, acc_id, text, model, voice_id, fmt, ref_b64, ref_audio_path):
		err = None
		out_path = None
		try:
			self.configure_client(acc_id)
			resp = self.client.audio.speech.create(
				model=model,
				voice=voice_id or "",
				voice_id=voice_id or None,
				input=text,
				response_format=fmt,
				ref_audio=ref_b64,
			)
			ext = ".wav" if fmt == "pcm" else f".{fmt}"
			out_path = build_media_path("audio", ext, prefix="voxtral_tts")
			resp.stream_to_file(out_path)
			out_path = persist_local_file(out_path, "audio", prefix="voxtral_tts", fallback_ext=ext)
		except Exception as e:
			err = e
		wx.CallAfter(self._onThreadDone, out_path, err, text, model, voice_id, fmt, ref_audio_path)

	def _onThreadDone(self, out_path, err, text, model, voice_id, fmt, ref_audio):
		stop_progress_sound()
		if not self._isDialogAlive():
			self._worker = None
			return
		if self.conf["chatFeedback"]["sndResponseReceived"]:
			winsound.PlaySound(SND_CHAT_RESPONSE_RECEIVED, winsound.SND_ASYNC)
		if not self.end_long_task(focus_ctrl=self.openGeneratedAudioBtn):
			self._worker = None
			return
		self._worker = None
		if err is not None:
			if isinstance(err, (APIConnectionError, APIStatusError)):
				# Translators: Error body when Voxtral TTS fails with a network or HTTP error; placeholder is the provider error text (title is «OpenAI»).
				wx.MessageBox(_("Voxtral TTS failed: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
			else:
				log.error(f"Voxtral TTS failed: {err}", exc_info=True)
				# Translators: Error body when Voxtral TTS fails with an unexpected exception (title is «OpenAI»; details in the log).
				wx.MessageBox(_("Voxtral TTS failed. See NVDA log for details."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._generatedAudioPath = out_path
		self._restoredRefAudioPath = ref_audio or ""
		self._syncOpenButtons()
		self.suggest_open_audio(out_path)
		self.save_tool_conversation(
			# Translators: Title stored on the synthetic «tool output» conversation tab after Voxtral TTS finishes successfully.
			title=_("Tool output: Voxtral TTS"),
			conversation_format=ConversationFormat.TOOL_MISTRAL_VOXTRAL_TTS,
			prompt=text,
			# Translators: Short assistant reply stored with the tool run when Voxtral TTS produced an audio file (shown in chat history).
			response_text=_("Audio generated with Voxtral TTS."),
			model=model,
			audio_paths=[out_path],
			format_data={
				"input_text": text,
				"model": model,
				"voice_id": voice_id,
				"response_format": fmt,
				"ref_audio_path": ref_audio,
				"audio_path": out_path,
			},
		)

	def _syncOpenButtons(self):
		self.openRefAudioBtn.Enable(bool(self.refAudioText.GetValue().strip() or self._restoredRefAudioPath))
		self.openGeneratedAudioBtn.Show(bool(self._generatedAudioPath))
		self.formPanel.Layout()
		self.Layout()

	def _applyConversationData(self, conversationData):
		if not isinstance(conversationData, dict):
			return
		fd = conversationData.get("formatData", {})
		if not isinstance(fd, dict):
			return
		self.inputText.SetValue(fd.get("input_text", ""))
		self.modelText.SetValue(fd.get("model", self.modelText.GetValue()))
		self.voiceIdText.SetValue(fd.get("voice_id", ""))
		fmt = fd.get("response_format", "")
		if isinstance(fmt, str) and fmt:
			idx = self.formatChoice.FindString(fmt)
			if idx != wx.NOT_FOUND:
				self.formatChoice.SetSelection(idx)
			else:
				self.formatChoice.SetStringSelection("wav")
		ref_path = fd.get("ref_audio_path", "")
		if isinstance(ref_path, str) and ref_path:
			self.refAudioText.SetValue(ref_path)
			self._restoredRefAudioPath = ref_path
		audio_path = fd.get("audio_path", "")
		if isinstance(audio_path, str) and audio_path:
			self._generatedAudioPath = audio_path

	def onOpenReferenceAudio(self, evt):
		path = self.refAudioText.GetValue().strip() or self._restoredRefAudioPath
		self.open_local_path(path, err_title="OpenAI")

	def onOpenGeneratedAudio(self, evt):
		self.open_local_path(self._generatedAudioPath, err_title="OpenAI")

	def onClose(self, evt):
		self._markClosing()
		stop_progress_sound()
		self.end_long_task()
		stop_worker_thread(self._worker)
		stop_worker_thread(self._voiceFetchWorker)
		self._worker = None
		self._voiceFetchWorker = None
		if isinstance(evt, wx.CloseEvent):
			evt.Skip()
			return
		self.Close()

	def _populate_voice_combo(self):
		current = self.voiceIdText.GetValue().strip()
		self.voiceIdText.Clear()
		for item in self._voiceSuggestions:
			self.voiceIdText.Append(item)
		if current:
			self.voiceIdText.SetValue(current)
		else:
			self.voiceIdText.SetValue("")

	def _fetch_voices_thread(self, api_key):
		error = None
		labels = []
		label_to_id = {}
		try:
			url = "https://api.mistral.ai/v1/audio/voices?limit=100"
			req = urllib.request.Request(
				url,
				headers={
					"Authorization": f"Bearer {api_key}",
					"x-api-key": api_key,
					"Content-Type": "application/json",
				},
				method="GET",
			)
			with urllib.request.urlopen(req, timeout=30) as resp:
				data = json.loads(resp.read().decode("utf-8", errors="replace"))
			items = []
			if isinstance(data, dict):
				raw = data.get("items")
				if isinstance(raw, list):
					items = raw
				raw = data.get("data")
				if isinstance(raw, list):
					items = raw
				elif isinstance(data.get("voices"), list):
					items = data.get("voices")
			elif isinstance(data, list):
				items = data
			for item in items:
				if not isinstance(item, dict):
					continue
				vid = item.get("id") or item.get("voice_id")
				name = item.get("name")
				if not isinstance(vid, str) or not vid.strip():
					continue
				vid = vid.strip()
				label = f"{name} ({vid})" if isinstance(name, str) and name.strip() else vid
				labels.append(label)
				label_to_id[label] = vid
		except Exception as e:
			error = e
		wx.CallAfter(self._on_voices_fetched, labels, label_to_id, error)

	def _on_voices_fetched(self, labels, label_to_id, error):
		if not self._isDialogAlive():
			self._voiceFetchWorker = None
			return
		self._voiceFetchWorker = None
		base = list(self.SUGGESTED_VOICE_IDS)
		if labels:
			base.extend([lab for lab in labels if lab not in base])
		self._voiceSuggestions = base
		self._voiceLabelToId = label_to_id or {}
		self._populate_voice_combo()
		if error:
			log.warning(f"Voxtral voices fetch failed: {error}")

	def _refreshVoicesAsync(self):
		if self._voiceFetchWorker and self._voiceFetchWorker.is_alive():
			return
		acc_id = self.get_selected_account_id(self.accountChoice)
		if not acc_id:
			self._voiceSuggestions = list(self.SUGGESTED_VOICE_IDS)
			self._voiceLabelToId = {}
			self._populate_voice_combo()
			return
		api_key = self.manager.get_api_key(account_id=acc_id)
		if not api_key:
			self._voiceSuggestions = list(self.SUGGESTED_VOICE_IDS)
			self._voiceLabelToId = {}
			self._populate_voice_combo()
			return
		self._voiceFetchWorker = threading.Thread(
			target=self._fetch_voices_thread,
			args=(api_key,),
			daemon=True,
		)
		self._voiceFetchWorker.start()

	def onRefreshVoices(self, evt):
		self._refreshVoicesAsync()

	def onAccountChange(self, evt):
		self._refreshVoicesAsync()
		evt.Skip()

	def onRun(self, evt):
		if self._worker and self._worker.is_alive():
			return
		acc_id = self.require_account(self.accountChoice)
		if not acc_id:
			return
		text = self.inputText.GetValue().strip()
		if not text:
			# Translators: Error body when Generate is pressed with an empty main text field in the Voxtral TTS tool (title is «OpenAI»).
			wx.MessageBox(_("Please enter text for speech synthesis."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.inputText.SetFocus()
			return
		model = self.modelText.GetValue().strip() or "voxtral-mini-tts-latest"
		voice_entry = self.voiceIdText.GetValue().strip()
		voice_id = self._voiceLabelToId.get(voice_entry, voice_entry)
		fmt = self.formatChoice.GetStringSelection() or "wav"
		ref_audio = self.refAudioText.GetValue().strip()
		ref_b64 = None
		if ref_audio:
			try:
				with open(ref_audio, "rb") as f:
					ref_b64 = base64.b64encode(f.read()).decode("ascii")
			except Exception as err:
				# Translators: Error body when the optional reference-audio file cannot be read before synthesis; placeholder is the OS error (title is «OpenAI»).
				wx.MessageBox(_("Could not read reference audio: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
				return
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		# Translators: Status line on the modal progress window while Voxtral TTS is synthesizing speech.
		self.begin_long_task(_("Speech generation in progress..."), self._setBusy)
		self._worker = threading.Thread(
			target=self._run_thread,
			args=(acc_id, text, model, voice_id, fmt, ref_b64, ref_audio),
			daemon=True,
		)
		self._worker.start()
