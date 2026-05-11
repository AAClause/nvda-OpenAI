"""Dedicated dialog for Mistral Speech to Text."""

import json
import os
import threading
import urllib.error
import urllib.request
import uuid
import winsound

import addonHandler
import wx
from logHandler import log

from .apiclient import APIConnectionError, APIStatusError, _resolve_error_message
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

def _split_csv(text: str) -> list[str]:
	if not isinstance(text, str):
		return []
	return [item.strip() for item in text.split(",") if item.strip()]


_AUDIO_MIME = {
	".wav": "audio/wav",
	".mp3": "audio/mpeg",
	".m4a": "audio/mp4",
	".webm": "audio/webm",
	".mp4": "audio/mp4",
	".flac": "audio/flac",
	".ogg": "audio/ogg",
	".mpga": "audio/mpeg",
	".mpeg": "audio/mpeg",
}


class MistralSpeechToTextToolDialog(ToolDialogBase):
	SUGGESTED_MODELS = (
		"voxtral-mini-latest",
		"voxtral-mini-2507",
	)

	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub Mistral Speech-to-Text tool dialog.
			title=_("Tool: Mistral Speech to Text"),
			provider=Provider.MistralAI,
			size=(860, 820),
			parentDialog=parentDialog,
			plugin=plugin,
		)
		self._worker = None
		self._textResultPath = ""
		self._rawResultPath = ""
		dialogSizer = wx.BoxSizer(wx.VERTICAL)
		self.formPanel = wx.Panel(self)
		main = wx.BoxSizer(wx.VERTICAL)

		self.accountChoice = add_labeled_factory(
			# Translators: Label before the Mistral account drop-down in the Mistral Speech-to-Text tool.
			self.formPanel, main, _("&Account:"), lambda: self.build_account_choice(self.formPanel)
		)
		self.inputAudioPathText = add_labeled_factory(
			# Translators: Label before the path field for the audio file to transcribe in the Mistral Speech-to-Text tool.
			self.formPanel, main, _("&Input audio file:"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.inputAudioPathText.Bind(wx.EVT_TEXT, lambda evt: (self._syncOpenButtons(), evt.Skip()))
		# Translators: Button that opens a file picker for the input recording in Mistral Speech-to-Text.
		self.browseInputBtn = wx.Button(self.formPanel, label=_("Browse input audio..."))
		self.browseInputBtn.Bind(wx.EVT_BUTTON, self.onBrowseInputAudio)
		main.Add(self.browseInputBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the selected input audio file in the default application.
		self.openInputBtn = wx.Button(self.formPanel, label=_("Open input audio"))
		self.openInputBtn.Bind(wx.EVT_BUTTON, self.onOpenInputAudio)
		main.Add(self.openInputBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the plain-text transcript file from the last Mistral Speech-to-Text run.
		self.openTextResultBtn = wx.Button(self.formPanel, label=_("Open transcription text result"))
		self.openTextResultBtn.Bind(wx.EVT_BUTTON, self.onOpenTextResult)
		main.Add(self.openTextResultBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the raw JSON API dump from the last Mistral Speech-to-Text run.
		self.openRawResultBtn = wx.Button(self.formPanel, label=_("Open raw transcription result"))
		self.openRawResultBtn.Bind(wx.EVT_BUTTON, self.onOpenRawResult)
		main.Add(self.openRawResultBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)

		self.modelChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the Mistral speech-to-text model combo box in this tool window.
			_("&Model:"),
			lambda: wx.ComboBox(
				self.formPanel,
				choices=list(self.SUGGESTED_MODELS),
				style=wx.CB_DROPDOWN,
				value=self.SUGGESTED_MODELS[0],
			),
		)
		self.languageText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional ISO-639 language code hint field for Mistral transcription.
			_("&Language (ISO code, optional):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		# Translators: Checkbox turning on speaker diarization in the Mistral Speech-to-Text request.
		self.diarizeCheck = wx.CheckBox(self.formPanel, label=_("Enable speaker diarization"))
		main.Add(self.diarizeCheck, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		self.contextBiasText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional comma-separated vocabulary hints sent as context bias to Mistral.
			_("Context bias terms (comma-separated, optional):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.timestampGranularitiesText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional segment/word timestamp granularity list for Mistral transcription.
			_("Timestamp granularities (segment,word; optional):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.temperatureText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional sampling temperature text field for Mistral transcription.
			_("&Temperature (optional):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that starts the Mistral Speech-to-Text HTTP request with the current form settings.
		self.runBtn = wx.Button(self.formPanel, label=_("Run transcription"))
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
			self.inputAudioPathText,
			self.browseInputBtn,
			self.openInputBtn,
			self.openTextResultBtn,
			self.openRawResultBtn,
			self.modelChoice,
			self.languageText,
			self.diarizeCheck,
			self.contextBiasText,
			self.timestampGranularitiesText,
			self.temperatureText,
			self.runBtn,
			self.closeBtn,
		):
			ctrl.Enable(not busy)

	def _syncOpenButtons(self):
		self.openInputBtn.Enable(bool(self.inputAudioPathText.GetValue().strip()))
		self.openTextResultBtn.Show(bool(self._textResultPath))
		self.openRawResultBtn.Show(bool(self._rawResultPath))
		self.formPanel.Layout()
		self.Layout()

	def onBrowseInputAudio(self, evt):
		dlg = wx.FileDialog(
			self,
			# Translators: Title of the file picker for the input recording in the Mistral speech-to-text tool.
			message=_("Select audio file"),
			defaultFile="",
			# Translators: File-type filter in the Mistral transcription tool’s input-audio picker.
			wildcard=_("Audio files (*.flac;*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.ogg;*.wav;*.webm)|*.flac;*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.ogg;*.wav;*.webm"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		if dlg.ShowModal() == wx.ID_OK:
			self.inputAudioPathText.SetValue(dlg.GetPath())
			self._syncOpenButtons()

	def _build_multipart_body(self, file_path, model, language, diarize, context_bias, timestamp_granularities, temperature):
		boundary = uuid.uuid4().hex
		with open(file_path, "rb") as f:
			file_data = f.read()
		filename = os.path.basename(file_path) or "audio.wav"
		ext = os.path.splitext(filename)[1].lower() or ".wav"
		mime = _AUDIO_MIME.get(ext, "audio/wav")
		parts = []

		def _add_field(name: str, value):
			if value is None:
				return
			if isinstance(value, (list, tuple)):
				for item in value:
					_add_field(name, item)
				return
			parts.append(
				(
					f'--{boundary}\r\n'
					f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
					f'{value}\r\n'
				).encode("utf-8")
			)

		_add_field("model", model)
		if language:
			_add_field("language", language)
		if diarize:
			_add_field("diarize", "true")
		if context_bias:
			_add_field("context_bias", context_bias)
		if timestamp_granularities:
			_add_field("timestamp_granularities", timestamp_granularities)
		if temperature is not None:
			_add_field("temperature", temperature)

		file_part = (
			f'--{boundary}\r\n'
			f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
			f"Content-Type: {mime}\r\n\r\n"
		).encode("utf-8") + file_data + b"\r\n"
		body = b"".join(parts) + file_part + (f"--{boundary}--\r\n").encode("utf-8")
		return boundary, body

	def _run_thread(self, account_id, file_path, model, language, diarize, context_bias, timestamp_granularities, temperature):
		err = None
		result = {}
		try:
			api_key = self.manager.get_api_key(account_id=account_id)
			if not api_key:
				# Translators: Error raised before the HTTP request when the chosen Mistral account has no API key.
				raise ValueError(_("No API key available for the selected Mistral account."))
			base_url = self.manager.get_base_url(account_id=account_id) or "https://api.mistral.ai/v1"
			url = base_url.rstrip("/") + "/audio/transcriptions"
			boundary, body = self._build_multipart_body(
				file_path=file_path,
				model=model,
				language=language,
				diarize=diarize,
				context_bias=context_bias,
				timestamp_granularities=timestamp_granularities,
				temperature=temperature,
			)
			headers = {
				"Authorization": f"Bearer {api_key.strip()}",
				"x-api-key": api_key.strip(),
				"Content-Type": f"multipart/form-data; boundary={boundary}",
			}
			req = urllib.request.Request(url, data=body, headers=headers, method="POST")
			with urllib.request.urlopen(req, timeout=300) as resp:
				if resp.status != 200:
					text = resp.read().decode("utf-8", errors="replace")
					raise APIStatusError(
						_resolve_error_message(text, resp.status),
						status_code=resp.status,
						response_body=text,
					)
				raw = resp.read().decode("utf-8", errors="replace")
			result = json.loads(raw) if raw else {}
		except urllib.error.HTTPError as e:
			text = e.read().decode("utf-8", errors="replace") if e.fp else ""
			err = APIStatusError(
				_resolve_error_message(text, e.code),
				status_code=e.code,
				response_body=text,
			)
		except (urllib.error.URLError, OSError, ConnectionError) as e:
			err = APIConnectionError(str(e))
		except Exception as e:
			err = e
		wx.CallAfter(
			self._onThreadDone,
			file_path,
			model,
			language,
			diarize,
			context_bias,
			timestamp_granularities,
			temperature,
			result,
			err,
		)

	def _onThreadDone(self, file_path, model, language, diarize, context_bias, timestamp_granularities, temperature, result, err):
		stop_progress_sound()
		if not self._isDialogAlive():
			self._worker = None
			return
		if self.conf["chatFeedback"]["sndResponseReceived"]:
			winsound.PlaySound(SND_CHAT_RESPONSE_RECEIVED, winsound.SND_ASYNC)
		if not self.end_long_task(focus_ctrl=self.openTextResultBtn):
			self._worker = None
			return
		self._worker = None
		if err is not None:
			if isinstance(err, (APIConnectionError, APIStatusError)):
				# Translators: Error body when Mistral Speech-to-Text fails with a network or HTTP error; placeholder is the provider message (title is «OpenAI»).
				wx.MessageBox(_("Mistral Speech to Text failed: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
			else:
				log.error(f"Mistral Speech to Text failed: {err}", exc_info=True)
				# Translators: Error body for an unexpected Mistral Speech-to-Text failure (title is «OpenAI»; details in the log).
				wx.MessageBox(_("Mistral Speech to Text failed. See NVDA log for details."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		text = ""
		if isinstance(result, dict):
			text = result.get("text", "") or ""
		text_path = build_media_path("documents", ".txt", prefix="mistral_stt")
		raw_path = build_media_path("documents", ".json", prefix="mistral_stt")
		try:
			with open(text_path, "w", encoding="utf-8") as f:
				f.write(text)
			with open(raw_path, "w", encoding="utf-8") as f:
				json.dump(result if isinstance(result, dict) else {}, f, ensure_ascii=False, indent=2)
		except Exception as write_err:
			# Translators: Error body when saving Mistral transcript or JSON dump fails; placeholder is the OS error (title is «OpenAI»).
			wx.MessageBox(_("Unable to save transcription outputs: %s") % write_err, "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._textResultPath = text_path
		self._rawResultPath = raw_path
		self._syncOpenButtons()
		self.open_local_path(text_path, err_title="OpenAI")
		if text:
			response_text = text[:4000]
		else:
			# Translators: Short completion line stored with the tool run when Mistral transcription returns no text body.
			response_text = _("Transcription completed.")
		self.save_tool_conversation(
			# Translators: Title stored on the synthetic «tool output» conversation tab after Mistral Speech-to-Text finishes successfully.
			title=_("Tool output: Mistral Speech to Text"),
			conversation_format=ConversationFormat.TOOL_MISTRAL_SPEECH_TO_TEXT,
			prompt=file_path,
			response_text=response_text,
			model=model,
			format_data={
				"input_audio_path": file_path,
				"model": model,
				"language": language,
				"diarize": diarize,
				"context_bias": self.contextBiasText.GetValue().strip(),
				"timestamp_granularities": self.timestampGranularitiesText.GetValue().strip(),
				"temperature": temperature if temperature is not None else "",
				"text_path": text_path,
				"raw_path": raw_path,
			},
		)

	def _applyConversationData(self, conversationData):
		if not isinstance(conversationData, dict):
			return
		fd = conversationData.get("formatData", {})
		if not isinstance(fd, dict):
			return
		self.inputAudioPathText.SetValue(fd.get("input_audio_path", ""))
		self.modelChoice.SetValue(fd.get("model", self.modelChoice.GetValue()))
		self.languageText.SetValue(fd.get("language", ""))
		self.diarizeCheck.SetValue(bool(fd.get("diarize", False)))
		self.contextBiasText.SetValue(fd.get("context_bias", ""))
		self.timestampGranularitiesText.SetValue(fd.get("timestamp_granularities", ""))
		temp = fd.get("temperature", "")
		if temp not in ("", None):
			self.temperatureText.SetValue(str(temp))
		text_path = fd.get("text_path", "")
		raw_path = fd.get("raw_path", "")
		if isinstance(text_path, str) and text_path:
			self._textResultPath = text_path
		if isinstance(raw_path, str) and raw_path:
			self._rawResultPath = raw_path

	def onOpenInputAudio(self, evt):
		self.open_local_path(self.inputAudioPathText.GetValue().strip(), err_title="OpenAI")

	def onOpenTextResult(self, evt):
		self.open_local_path(self._textResultPath, err_title="OpenAI")

	def onOpenRawResult(self, evt):
		self.open_local_path(self._rawResultPath, err_title="OpenAI")

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
		file_path = self.inputAudioPathText.GetValue().strip()
		if not file_path or not os.path.exists(file_path):
			# Translators: Error body when Run is pressed without a valid on-disk audio file in the Mistral Speech-to-Text tool (title is «OpenAI»).
			wx.MessageBox(_("Please select a valid audio file."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.inputAudioPathText.SetFocus()
			return
		model = self.modelChoice.GetValue().strip() or self.SUGGESTED_MODELS[0]
		language = self.languageText.GetValue().strip()
		diarize = self.diarizeCheck.IsChecked()
		context_bias = _split_csv(self.contextBiasText.GetValue().strip())
		timestamp_granularities = [v.lower() for v in _split_csv(self.timestampGranularitiesText.GetValue().strip())]
		invalid_tg = [v for v in timestamp_granularities if v not in ("segment", "word")]
		if invalid_tg:
			# Translators: Error body when the comma-separated timestamp granularity field contains tokens other than segment or word (title is «OpenAI»).
			wx.MessageBox(_("Timestamp granularities must only contain 'segment' and/or 'word'."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.timestampGranularitiesText.SetFocus()
			return
		if diarize:
			if not timestamp_granularities:
				timestamp_granularities = ["segment"]
				self.timestampGranularitiesText.SetValue("segment")
			elif timestamp_granularities != ["segment"]:
				wx.MessageBox(
					# Translators: Error body when diarization is on but the timestamp granularity list is not exactly the single value «segment» (title is «OpenAI»).
					_("When diarization is enabled, timestamp granularities must be exactly 'segment'."),
					"OpenAI",
					wx.OK | wx.ICON_ERROR,
				)
				self.timestampGranularitiesText.SetFocus()
				return
		temp_raw = self.temperatureText.GetValue().strip()
		temperature = None
		if temp_raw:
			temperature = safe_float(temp_raw, default=None)
			if temperature is None:
				# Translators: Error body when the optional temperature field is not a valid decimal number in Mistral Speech-to-Text (title is «OpenAI»).
				wx.MessageBox(_("Temperature must be a valid number."), "OpenAI", wx.OK | wx.ICON_ERROR)
				self.temperatureText.SetFocus()
				return
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		# Translators: Status line on the modal progress window while Mistral is transcribing the selected audio file.
		self.begin_long_task(_("Transcription in progress..."), self._setBusy)
		self._worker = threading.Thread(
			target=self._run_thread,
			args=(account_id, file_path, model, language, diarize, context_bias, timestamp_granularities, temperature),
			daemon=True,
		)
		self._worker.start()
