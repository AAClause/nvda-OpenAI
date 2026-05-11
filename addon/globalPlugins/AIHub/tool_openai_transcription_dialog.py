"""Dedicated dialog for OpenAI transcription."""

import json
import os
import threading
import winsound

import addonHandler
import wx
from logHandler import log

from .apiclient import APIConnectionError, APIStatusError, Transcription
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


class OpenAITranscriptionToolDialog(ToolDialogBase):
	SUGGESTED_MODELS = (
		"gpt-4o-transcribe",
		"gpt-4o-mini-transcribe",
		"whisper-1",
		"gpt-4o-transcribe-diarize",
	)

	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub OpenAI Transcription / Translation tool dialog.
			title=_("Tool: OpenAI Transcription"),
			provider=Provider.OpenAI,
			size=(860, 880),
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
			# Translators: Label before the OpenAI account drop-down in the OpenAI Transcription tool.
			self.formPanel, main, _("&Account:"), lambda: self.build_account_choice(self.formPanel)
		)

		def _build_task_choice():
			return wx.Choice(
				self.formPanel,
				choices=[
					# Translators: OpenAI Transcription tool — Task drop-down entry: transcribe audio in the original language.
					_("Transcription"),
					# Translators: OpenAI Transcription tool — Task drop-down entry: translate speech into English text.
					_("Translation to English"),
				],
			)

		self.taskChoice = add_labeled_factory(
			# Translators: Label before the Task drop-down selecting transcription versus translation to English.
			self.formPanel, main, _("Task:"), _build_task_choice,
		)
		self.taskChoice.SetSelection(0)
		self.taskChoice.Bind(wx.EVT_CHOICE, self.onTaskChange)
		self.inputAudioPathText = add_labeled_factory(
			# Translators: Label before the path field for the audio file to transcribe or translate in this tool.
			self.formPanel, main, _("&Input audio file:"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.inputAudioPathText.Bind(wx.EVT_TEXT, lambda evt: (self._syncOpenButtons(), evt.Skip()))

		# Translators: Button that opens a file picker for the input audio file in the OpenAI Transcription tool.
		self.browseInputBtn = wx.Button(self.formPanel, label=_("Browse input audio..."))
		self.browseInputBtn.Bind(wx.EVT_BUTTON, self.onBrowseInputAudio)
		main.Add(self.browseInputBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the selected input audio file in the default application.
		self.openInputBtn = wx.Button(self.formPanel, label=_("Open input audio"))
		self.openInputBtn.Bind(wx.EVT_BUTTON, self.onOpenInputAudio)
		main.Add(self.openInputBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the plain-text transcript file from the last OpenAI audio run.
		self.openTextResultBtn = wx.Button(self.formPanel, label=_("Open transcription text result"))
		self.openTextResultBtn.Bind(wx.EVT_BUTTON, self.onOpenTextResult)
		main.Add(self.openTextResultBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the raw API response file (JSON or text) from the last OpenAI audio run.
		self.openRawResultBtn = wx.Button(self.formPanel, label=_("Open raw transcription result"))
		self.openRawResultBtn.Bind(wx.EVT_BUTTON, self.onOpenRawResult)
		main.Add(self.openRawResultBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		self.modelChoice = add_labeled_factory(
			# Translators: Label before the Whisper / GPT-transcribe model combo box in the OpenAI Transcription tool.
			self.formPanel, main, _("&Model:"), lambda: wx.ComboBox(self.formPanel, choices=list(self.SUGGESTED_MODELS), style=wx.CB_DROPDOWN, value=self.SUGGESTED_MODELS[0])
		)
		self.languageText = add_labeled_factory(
			# Translators: Label before the optional ISO-639-1 language hint used for transcription (not for translation task).
			self.formPanel, main, _("&Language (ISO-639-1, optional):"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.promptText = add_labeled_factory(
			# Translators: Label before the optional vocabulary or style hint multiline field passed to the transcription API.
			self.formPanel, main, _("&Prompt (optional):"), lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 90))
		)
		self.responseFormatChoice = add_labeled_factory(
			# Translators: Label before the API response format drop-down (json, srt, diarized_json, etc.) in this tool.
			self.formPanel, main, _("Response &format:"), lambda: wx.Choice(self.formPanel, choices=["json", "text", "srt", "vtt", "verbose_json", "diarized_json"])
		)
		self.responseFormatChoice.SetStringSelection("json")
		self.temperatureText = add_labeled_factory(
			# Translators: Label before the optional sampling temperature text field for the OpenAI transcription API.
			self.formPanel, main, _("&Temperature (optional):"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.timestampGranularitiesText = add_labeled_factory(
			# Translators: Label before the optional comma-separated timestamp granularity tokens for verbose transcripts.
			self.formPanel, main, _("Timestamp granularities (comma-separated, optional):"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.includeText = add_labeled_factory(
			# Translators: Label before the optional comma-separated list of extra response segments to request from the API.
			self.formPanel, main, _("Include fields (comma-separated, optional):"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.chunkingStrategyText = add_labeled_factory(
			# Translators: Label before the optional multiline JSON for the audio chunking strategy sent to the API.
			self.formPanel, main, _("Chunking strategy JSON (optional):"), lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 70))
		)
		self.knownSpeakerNamesText = add_labeled_factory(
			# Translators: Label before the optional comma-separated known speaker names for diarized transcription.
			self.formPanel, main, _("Known speaker names (comma-separated, optional):"), lambda: wx.TextCtrl(self.formPanel, value="")
		)
		self.knownSpeakerReferencesText = add_labeled_factory(
			# Translators: Label before the optional comma-separated speaker reference ids for diarized transcription.
			self.formPanel, main, _("Known speaker references (comma-separated, optional):"), lambda: wx.TextCtrl(self.formPanel, value="")
		)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that starts the OpenAI audio transcription or translation request with the current form settings.
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
		self._refreshTaskDependentControls()
		self._syncOpenButtons()

	def _getTaskValue(self) -> str:
		return "translation" if self.taskChoice.GetSelection() == 1 else "transcription"

	def _setBusy(self, busy: bool):
		for ctrl in (
			self.accountChoice,
			self.taskChoice,
			self.inputAudioPathText,
			self.modelChoice,
			self.languageText,
			self.promptText,
			self.responseFormatChoice,
			self.temperatureText,
			self.timestampGranularitiesText,
			self.includeText,
			self.chunkingStrategyText,
			self.knownSpeakerNamesText,
			self.knownSpeakerReferencesText,
			self.browseInputBtn,
			self.openInputBtn,
			self.openTextResultBtn,
			self.openRawResultBtn,
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
			# Translators: Title of the file picker for the input recording in the OpenAI transcription/translation tool.
			message=_("Select audio file"),
			defaultFile="",
			# Translators: File-type filter in the OpenAI transcription tool’s input-audio picker.
			wildcard=_("Audio files (*.flac;*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.ogg;*.wav;*.webm)|*.flac;*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.ogg;*.wav;*.webm"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		if dlg.ShowModal() == wx.ID_OK:
			self.inputAudioPathText.SetValue(dlg.GetPath())
			self._syncOpenButtons()

	def _build_request_kwargs(self):
		kwargs = {}
		task = self._getTaskValue()
		lang = self.languageText.GetValue().strip()
		if lang and task == "transcription":
			kwargs["language"] = lang
		prompt = self.promptText.GetValue().strip()
		if prompt:
			kwargs["prompt"] = prompt
		temp_raw = self.temperatureText.GetValue().strip()
		if temp_raw:
			temp = safe_float(temp_raw, default=None)
			if temp is None:
				# Translators: Validation error raised (and shown) when the optional temperature field is not numeric in the OpenAI transcription tool.
				raise ValueError(_("Temperature must be a valid number."))
			kwargs["temperature"] = temp
		if task == "translation":
			return kwargs
		tg = _split_csv(self.timestampGranularitiesText.GetValue().strip())
		if tg:
			kwargs["timestamp_granularities[]"] = tg
		include = _split_csv(self.includeText.GetValue().strip())
		if include:
			kwargs["include[]"] = include
		chunking = self.chunkingStrategyText.GetValue().strip()
		if chunking:
			try:
				kwargs["chunking_strategy"] = json.loads(chunking)
			except Exception:
				# Translators: Validation error when the optional chunking-strategy field is not valid JSON in the OpenAI transcription tool.
				raise ValueError(_("Chunking strategy must be valid JSON."))
		known_names = _split_csv(self.knownSpeakerNamesText.GetValue().strip())
		if known_names:
			kwargs["known_speaker_names[]"] = known_names
		known_refs = _split_csv(self.knownSpeakerReferencesText.GetValue().strip())
		if known_refs:
			kwargs["known_speaker_references[]"] = known_refs
		return kwargs

	def _run_thread(self, account_id, task, file_path, model, response_format, kwargs):
		err = None
		result = None
		try:
			self.configure_client(account_id)
			with open(file_path, "rb") as f:
				if task == "translation":
					result = self.client.audio.translations.create(
						model=model,
						file=f,
						response_format=response_format,
						**kwargs,
					)
				else:
					result = self.client.audio.transcriptions.create(
						model=model,
						file=f,
						response_format=response_format,
						**kwargs,
					)
		except Exception as e:
			err = e
		wx.CallAfter(self._onThreadDone, task, file_path, model, response_format, kwargs, result, err)

	def _onThreadDone(self, task, file_path, model, response_format, kwargs, result, err):
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
				if task == "translation":
					# Translators: Lowercase noun inserted into the OpenAI audio error sentence when the tool ran in translation mode.
					action = _("translation")
				else:
					# Translators: Lowercase noun inserted into the OpenAI audio error sentence when the tool ran in transcription mode.
					action = _("transcription")
				wx.MessageBox(
					# Translators: Error dialog when OpenAI translation/transcription fails with a connection or HTTP status error.
					_("OpenAI {action} failed: {error}").format(**{
						"action": action,
						"error": err,
					}),
					"OpenAI",
					wx.OK | wx.ICON_ERROR,
				)
			else:
				log.error(f"OpenAI audio text task failed: {err}", exc_info=True)
				# Translators: Error body for an unexpected failure during OpenAI transcription or translation (title is «OpenAI»; details in the log).
				wx.MessageBox(_("OpenAI audio task failed. See NVDA log for details."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		if not isinstance(result, Transcription):
			# Translators: Error body when the API response could not be interpreted as a transcription/translation result object (title is «OpenAI»).
			wx.MessageBox(_("Invalid transcription result."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		text = result.text or ""
		prefix = "openai_translation" if task == "translation" else "openai_transcription"
		text_path = build_media_path("documents", ".txt", prefix=prefix)
		raw_ext = ".json" if response_format in ("json", "verbose_json", "diarized_json") else ".txt"
		raw_path = build_media_path("documents", raw_ext, prefix=prefix)
		try:
			with open(text_path, "w", encoding="utf-8") as f:
				f.write(text)
			with open(raw_path, "w", encoding="utf-8") as f:
				if isinstance(result.payload, dict):
					json.dump(result.payload, f, ensure_ascii=False, indent=2)
				else:
					f.write(str(result.payload if result.payload is not None else text))
		except Exception as write_err:
			# Translators: Error body when writing the transcript or raw API dump to disk fails; placeholder is the OS error (title is «OpenAI»).
			wx.MessageBox(_("Unable to save transcription outputs: %s") % write_err, "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._textResultPath = text_path
		self._rawResultPath = raw_path
		self._syncOpenButtons()
		self.open_local_path(text_path, err_title="OpenAI")
		if text:
			response_text = text[:4000]
		else:
			if task == "translation":
				# Translators: Short completion line stored with the tool run when OpenAI translation returns no text body.
				response_text = _("Translation completed.")
			else:
				# Translators: Short completion line stored with the tool run when OpenAI transcription returns no text body.
				response_text = _("Transcription completed.")
		if task == "translation":
			# Translators: Title stored on the synthetic «tool output» tab after OpenAI speech-to-English translation finishes.
			_tool_title = _("Tool output: OpenAI translation")
		else:
			# Translators: Title stored on the synthetic «tool output» tab after OpenAI speech-to-text transcription finishes.
			_tool_title = _("Tool output: OpenAI transcription")
		self.save_tool_conversation(
			title=_tool_title,
			conversation_format=ConversationFormat.TOOL_OPENAI_TRANSCRIPTION,
			prompt=file_path,
			response_text=response_text,
			model=model,
			format_data={
				"task": task,
				"input_audio_path": file_path,
				"model": model,
				"language": kwargs.get("language", ""),
				"prompt": kwargs.get("prompt", ""),
				"response_format": response_format,
				"temperature": kwargs.get("temperature", ""),
				"timestamp_granularities": self.timestampGranularitiesText.GetValue().strip(),
				"include": self.includeText.GetValue().strip(),
				"chunking_strategy": self.chunkingStrategyText.GetValue().strip(),
				"known_speaker_names": self.knownSpeakerNamesText.GetValue().strip(),
				"known_speaker_references": self.knownSpeakerReferencesText.GetValue().strip(),
				"text_path": text_path,
				"raw_path": raw_path,
			},
		)

	def _refreshTaskDependentControls(self):
		is_translation = self._getTaskValue() == "translation"
		self.languageText.Enable(not is_translation)
		self.timestampGranularitiesText.Enable(not is_translation)
		self.includeText.Enable(not is_translation)
		self.chunkingStrategyText.Enable(not is_translation)
		self.knownSpeakerNamesText.Enable(not is_translation)
		self.knownSpeakerReferencesText.Enable(not is_translation)
		choices = ["json", "text", "srt", "vtt"] if is_translation else ["json", "text", "srt", "vtt", "verbose_json", "diarized_json"]
		prev = self.responseFormatChoice.GetStringSelection()
		self.responseFormatChoice.SetItems(choices)
		if prev in choices:
			self.responseFormatChoice.SetStringSelection(prev)
		else:
			self.responseFormatChoice.SetStringSelection("json")

	def onTaskChange(self, evt):
		self._refreshTaskDependentControls()

	def _applyConversationData(self, conversationData):
		if not isinstance(conversationData, dict):
			return
		fd = conversationData.get("formatData", {})
		if not isinstance(fd, dict):
			return
		task = fd.get("task", "transcription")
		self.taskChoice.SetSelection(1 if task == "translation" else 0)
		self.inputAudioPathText.SetValue(fd.get("input_audio_path", ""))
		self.modelChoice.SetValue(fd.get("model", self.modelChoice.GetValue()))
		self.languageText.SetValue(fd.get("language", ""))
		self.promptText.SetValue(fd.get("prompt", ""))
		fmt = fd.get("response_format", "")
		if isinstance(fmt, str) and fmt:
			idx = self.responseFormatChoice.FindString(fmt)
			if idx != wx.NOT_FOUND:
				self.responseFormatChoice.SetSelection(idx)
		temp = fd.get("temperature", "")
		if temp not in ("", None):
			self.temperatureText.SetValue(str(temp))
		self.timestampGranularitiesText.SetValue(fd.get("timestamp_granularities", ""))
		self.includeText.SetValue(fd.get("include", ""))
		self.chunkingStrategyText.SetValue(fd.get("chunking_strategy", ""))
		self.knownSpeakerNamesText.SetValue(fd.get("known_speaker_names", ""))
		self.knownSpeakerReferencesText.SetValue(fd.get("known_speaker_references", ""))
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
			# Translators: Error body when Run is pressed without a real on-disk audio path in the OpenAI transcription tool (title is «OpenAI»).
			wx.MessageBox(_("Please select a valid audio file."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.inputAudioPathText.SetFocus()
			return
		model = self.modelChoice.GetValue().strip() or self.SUGGESTED_MODELS[0]
		response_format = self.responseFormatChoice.GetStringSelection() or "json"
		task = self._getTaskValue()
		try:
			kwargs = self._build_request_kwargs()
		except ValueError as err:
			wx.MessageBox(str(err), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		# Translators: Status line on the modal progress window while OpenAI is transcribing or translating the selected audio file.
		self.begin_long_task(_("Transcription in progress..."), self._setBusy)
		self._worker = threading.Thread(
			target=self._run_thread,
			args=(account_id, task, file_path, model, response_format, kwargs),
			daemon=True,
		)
		self._worker.start()
