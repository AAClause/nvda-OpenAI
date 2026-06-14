"""Dedicated dialog for OpenAI TTS."""

import threading
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
from .mediastore import build_media_path
from .providertools_helpers import add_labeled_factory, safe_float
from .thread_shutdown import stop_worker_thread
from .tool_dialog_base import ToolDialogBase

addonHandler.initTranslation()

class OpenAITTSToolDialog(ToolDialogBase):
	SUGGESTED_MODELS = (
		"gpt-4o-mini-tts",
		"tts-1",
		"tts-1-hd",
	)
	SUGGESTED_VOICES = (
		"alloy",
		"ash",
		"ballad",
		"coral",
		"echo",
		"fable",
		"onyx",
		"nova",
		"sage",
		"shimmer",
		"verse",
		"marin",
		"cedar",
	)

	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub OpenAI text-to-speech tool dialog.
			title=_("Tool: OpenAI TTS"),
			provider=Provider.OpenAI,
			size=(780, 760),
			parentDialog=parentDialog,
			plugin=plugin,
		)
		self._worker = None
		self._generatedAudioPath = ""
		dialogSizer = wx.BoxSizer(wx.VERTICAL)
		self.formPanel = wx.Panel(self)
		main = wx.BoxSizer(wx.VERTICAL)

		self.accountChoice = add_labeled_factory(
			# Translators: Label before the OpenAI account drop-down in the OpenAI TTS tool.
			self.formPanel, main, _("&Account:"), lambda: self.build_account_choice(self.formPanel)
		)
		self.inputText = add_labeled_factory(
			# Translators: Label before the multiline text box for the words to synthesize in the OpenAI TTS tool.
			self.formPanel, main, _("&Input text:"), lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 130))
		)
		# Translators: Button that opens the last generated speech file in the default application.
		self.openGeneratedAudioBtn = wx.Button(self.formPanel, label=_("Open generated audio"))
		self.openGeneratedAudioBtn.Bind(wx.EVT_BUTTON, self.onOpenGeneratedAudio)
		main.Add(self.openGeneratedAudioBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		self.modelChoice = add_labeled_factory(
			# Translators: Label before the TTS model combo box (e.g. gpt-4o-mini-tts) in the OpenAI TTS tool.
			self.formPanel, main, _("&Model:"), lambda: wx.ComboBox(self.formPanel, choices=list(self.SUGGESTED_MODELS), style=wx.CB_DROPDOWN, value=self.SUGGESTED_MODELS[0])
		)
		self.voiceChoice = add_labeled_factory(
			# Translators: Label before the synthetic voice combo box (alloy, verse, etc.) in the OpenAI TTS tool.
			self.formPanel, main, _("&Voice:"), lambda: wx.ComboBox(self.formPanel, choices=list(self.SUGGESTED_VOICES), style=wx.CB_DROPDOWN, value=self.SUGGESTED_VOICES[0])
		)
		self.instructionsText = add_labeled_factory(
			# Translators: Label before the optional style or delivery instructions multiline field for OpenAI TTS.
			self.formPanel, main, _("&Instructions (optional):"), lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 90))
		)
		self.responseFormatChoice = add_labeled_factory(
			# Translators: Label before the audio container format drop-down (mp3, wav, etc.) in the OpenAI TTS tool.
			self.formPanel, main, _("Response &format:"), lambda: wx.Choice(self.formPanel, choices=["mp3", "opus", "aac", "flac", "wav", "pcm"])
		)
		self.responseFormatChoice.SetStringSelection("mp3")
		self.speedText = add_labeled_factory(
			# Translators: Label before the speech speed numeric field (allowed range 0.25–4.0) in the OpenAI TTS tool.
			self.formPanel, main, _("&Speed (0.25-4.0):"), lambda: wx.TextCtrl(self.formPanel, value="1.0")
		)
		self.streamFormatChoice = add_labeled_factory(
			# Translators: Label before the optional streaming mode drop-down (empty, audio, or sse) for OpenAI TTS advanced use.
			self.formPanel, main, _("Stream format (optional):"), lambda: wx.Choice(self.formPanel, choices=["", "audio", "sse"])
		)
		self.streamFormatChoice.SetSelection(0)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that starts OpenAI TTS synthesis with the current form settings.
		self.runBtn = wx.Button(self.formPanel, label=_("Generate speech"))
		self.runBtn.Bind(wx.EVT_BUTTON, self.onRun)
		self.bind_ctrl_enter_submit(self.onRun)
		self.closeBtn = wx.Button(self.formPanel, id=wx.ID_CLOSE)
		self.closeBtn.Bind(wx.EVT_BUTTON, self.onClose)
		buttons.Add(self.runBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		buttons.Add(self.closeBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		main.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, UI_SECTION_SPACING_PX)

		self.formPanel.SetSizer(main)
		dialogSizer.Add(self.formPanel, 1, wx.EXPAND | wx.ALL, UI_DIALOG_BORDER_PX)
		self.SetSizer(dialogSizer)
		if parent:
			self.CentreOnParent(wx.BOTH)
		else:
			self.Centre(wx.BOTH)
		self._applyConversationData(conversationData)
		self._syncOpenButtons()

	def _setBusy(self, busy: bool):
		for ctrl in (
			self.accountChoice,
			self.inputText,
			self.modelChoice,
			self.voiceChoice,
			self.instructionsText,
			self.responseFormatChoice,
			self.speedText,
			self.streamFormatChoice,
			self.openGeneratedAudioBtn,
			self.runBtn,
			self.closeBtn,
		):
			ctrl.Enable(not busy)

	def _syncOpenButtons(self):
		self.openGeneratedAudioBtn.Show(bool(self._generatedAudioPath))
		self.formPanel.Layout()
		self.Layout()

	def _run_thread(self, account_id, payload):
		err = None
		out_path = ""
		try:
			self.configure_client(account_id)
			resp = self.client.audio.speech.create(**payload)
			out_ext = ".wav" if payload.get("response_format") == "pcm" else f".{payload.get('response_format', 'mp3')}"
			out_path = build_media_path("audio", out_ext, prefix="openai_tts")
			resp.stream_to_file(out_path)
		except Exception as e:
			err = e
		wx.CallAfter(self._onThreadDone, out_path, err, payload)

	def _onThreadDone(self, out_path, err, payload):
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
				# Translators: Error body when OpenAI TTS fails with a network or HTTP error; placeholder is the provider error text (title is «OpenAI»).
				wx.MessageBox(_("OpenAI TTS failed: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
			else:
				log.error(f"OpenAI TTS failed: {err}", exc_info=True)
				# Translators: Error body when OpenAI TTS fails with an unexpected exception (title is «OpenAI»; details only in the log).
				wx.MessageBox(_("OpenAI TTS failed. See NVDA log for details."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._generatedAudioPath = out_path
		self._syncOpenButtons()
		self.suggest_open_audio(out_path)
		self.save_tool_conversation(
			# Translators: Title stored on the synthetic «tool output» conversation tab after OpenAI TTS finishes successfully.
			title=_("Tool output: OpenAI TTS"),
			conversation_format=ConversationFormat.TOOL_OPENAI_TTS,
			prompt=payload.get("input", ""),
			# Translators: Short assistant reply text stored with the tool run when OpenAI TTS produced an audio file (shown in chat history).
			response_text=_("Audio generated with OpenAI TTS."),
			model=payload.get("model", ""),
			audio_paths=[out_path],
			format_data={
				"input_text": payload.get("input", ""),
				"model": payload.get("model", ""),
				"voice": payload.get("voice", ""),
				"instructions": payload.get("instructions", ""),
				"response_format": payload.get("response_format", ""),
				"speed": payload.get("speed", ""),
				"stream_format": payload.get("stream_format", ""),
				"audio_path": out_path,
			},
		)

	def _applyConversationData(self, conversationData):
		if not isinstance(conversationData, dict):
			return
		fd = conversationData.get("formatData", {})
		if not isinstance(fd, dict):
			return
		self.inputText.SetValue(fd.get("input_text", ""))
		self.modelChoice.SetValue(fd.get("model", self.modelChoice.GetValue()))
		self.voiceChoice.SetValue(fd.get("voice", self.voiceChoice.GetValue()))
		self.instructionsText.SetValue(fd.get("instructions", ""))
		resp_fmt = fd.get("response_format", "")
		if isinstance(resp_fmt, str) and resp_fmt:
			idx = self.responseFormatChoice.FindString(resp_fmt)
			if idx != wx.NOT_FOUND:
				self.responseFormatChoice.SetSelection(idx)
		speed = fd.get("speed", "")
		if speed not in ("", None):
			self.speedText.SetValue(str(speed))
		stream_fmt = fd.get("stream_format", "")
		if isinstance(stream_fmt, str):
			idx = self.streamFormatChoice.FindString(stream_fmt)
			if idx != wx.NOT_FOUND:
				self.streamFormatChoice.SetSelection(idx)
		audio_path = fd.get("audio_path", "")
		if isinstance(audio_path, str) and audio_path:
			self._generatedAudioPath = audio_path

	def onOpenGeneratedAudio(self, evt):
		self.open_local_path(self._generatedAudioPath, err_title="OpenAI")

	def onClose(self, evt):
		self._markClosing()
		stop_progress_sound()
		self.end_long_task()
		stop_worker_thread(self._worker)
		self._worker = None
		if isinstance(evt, wx.CloseEvent):
			evt.Skip()
			return
		self.Close()

	def onRun(self, evt):
		if self._worker and self._worker.is_alive():
			return
		account_id = self.require_account(self.accountChoice)
		if not account_id:
			return
		input_text = self.inputText.GetValue().strip()
		if not input_text:
			# Translators: Error body when Generate is pressed with an empty text box in the OpenAI TTS tool (title is «OpenAI»).
			wx.MessageBox(_("Please enter text to synthesize."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.inputText.SetFocus()
			return
		speed = safe_float(self.speedText.GetValue(), default=1.0)
		if speed is None or speed < 0.25 or speed > 4.0:
			# Translators: Error body when the speech speed value in OpenAI TTS is outside the allowed numeric range (title is «OpenAI»).
			wx.MessageBox(_("Speed must be between 0.25 and 4.0."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.speedText.SetFocus()
			return
		payload = {
			"model": self.modelChoice.GetValue().strip() or "gpt-4o-mini-tts",
			"voice": self.voiceChoice.GetValue().strip() or "alloy",
			"input": input_text,
			"response_format": self.responseFormatChoice.GetStringSelection() or "mp3",
			"speed": speed,
		}
		instructions = self.instructionsText.GetValue().strip()
		if instructions:
			payload["instructions"] = instructions
		stream_format = self.streamFormatChoice.GetStringSelection().strip()
		if stream_format:
			payload["stream_format"] = stream_format
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		# Translators: Status line on the modal progress window while OpenAI TTS is synthesizing speech.
		self.begin_long_task(_("Speech generation in progress..."), self._setBusy)
		self._worker = threading.Thread(
			target=self._run_thread,
			args=(account_id, payload),
			daemon=True,
		)
		self._worker.start()
