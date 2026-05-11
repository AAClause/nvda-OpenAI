"""Dedicated dialog for Google Lyria 3 Pro."""

import base64
import json
import threading
import urllib.parse
import urllib.request
import winsound

import addonHandler
import wx
from logHandler import log

from .conversations import ConversationFormat
from .consts import (
	Provider,
	Role,
	SND_CHAT_RESPONSE_RECEIVED,
	SND_PROGRESS,
	stop_progress_sound,
	UI_DIALOG_BORDER_PX,
	UI_FORM_ROW_BORDER_PX,
	UI_SECTION_SPACING_PX,
)
from .mediastore import build_media_path
from .providertools_helpers import add_labeled_factory, extract_audio_b64, safe_float, safe_int
from .thread_shutdown import stop_worker_thread
from .tool_dialog_base import ToolDialogBase

addonHandler.initTranslation()

class Lyria3ProToolDialog(ToolDialogBase):
	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub Google Lyria 3 Pro music-generation tool dialog.
			title=_("Tool: Lyria 3 Pro"),
			provider=Provider.Google,
			size=(760, 720),
			parentDialog=parentDialog,
			plugin=plugin,
		)
		self._worker = None
		self._generatedAudioPath = ""
		dialogSizer = wx.BoxSizer(wx.VERTICAL)
		self.formPanel = wx.Panel(self)
		main = wx.BoxSizer(wx.VERTICAL)

		self.accountChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the Google account drop-down in the Lyria 3 Pro tool.
			_("&Account:"),
			lambda: self.build_account_choice(self.formPanel),
		)
		self.promptText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the main multiline music prompt describing what Lyria should generate.
			_("&Prompt:"),
			lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 130)),
		)
		# Translators: Button that opens the last generated music audio file from Lyria 3 Pro.
		self.openGeneratedAudioBtn = wx.Button(self.formPanel, label=_("Open generated audio"))
		self.openGeneratedAudioBtn.Bind(wx.EVT_BUTTON, self.onOpenGeneratedAudio)
		main.Add(self.openGeneratedAudioBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		self.negativePromptText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional multiline negative prompt listing what Lyria should avoid.
			_("Negative prompt (&optional):"),
			lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 80)),
		)
		self.modelText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the Lyria model id text field (default lyria-3-pro-preview).
			_("&Model:"),
			lambda: wx.TextCtrl(self.formPanel, value="lyria-3-pro-preview"),
		)
		self.durationText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the requested clip duration in seconds for Lyria generation.
			_("&Duration seconds:"),
			lambda: wx.TextCtrl(self.formPanel, value="180"),
		)
		self.seedText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional random seed field for reproducible Lyria generations.
			_("See&d:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.temperatureText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional sampling temperature field for Lyria music generation.
			_("&Temperature:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.topPText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional nucleus sampling top-p parameter for Lyria.
			_("Top &P:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.topKText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional top-k truncation parameter for Lyria.
			_("Top &K:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.cfgScaleText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional classifier-free guidance scale field for Lyria.
			_("&CFG scale:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.sampleRateText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the output audio sample rate in hertz (default 48000) for Lyria.
			_("Sam&ple rate:"),
			lambda: wx.TextCtrl(self.formPanel, value="48000"),
		)
		self.formatChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the container format drop-down (wav, mp3, flac) for exported Lyria audio.
			_("Output f&ormat:"),
			lambda: wx.Choice(self.formPanel, choices=["wav", "mp3", "flac"]),
		)
		self.formatChoice.SetStringSelection("wav")

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that starts Lyria 3 Pro music generation with the current form settings.
		self.runBtn = wx.Button(self.formPanel, label=_("Generate music"))
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
			self.promptText,
			self.negativePromptText,
			self.modelText,
			self.durationText,
			self.seedText,
			self.temperatureText,
			self.topPText,
			self.topKText,
			self.cfgScaleText,
			self.sampleRateText,
			self.formatChoice,
			self.openGeneratedAudioBtn,
			self.runBtn,
			self.closeBtn,
		):
			ctrl.Enable(not busy)

	def _try_lyria_generate_via_openai_compat(self, account_id, model, prompt, negative_prompt, options):
		self.configure_client(account_id)
		user_prompt = prompt
		if negative_prompt:
			user_prompt += "\n\nNegative prompt: " + negative_prompt
		resp = self.client.chat.completions.create(
			model=model,
			messages=[{"role": Role.USER, "content": user_prompt}],
			stream=False,
			modalities=["audio"],
			audio={"format": "wav"},
		)
		if not resp or not getattr(resp, "choices", None):
			return None
		msg = getattr(resp.choices[0], "message", None)
		audio = getattr(msg, "audio", None) if msg else None
		if isinstance(audio, dict):
			return {"audio": audio}
		return None

	def _try_lyria_generate_via_gemini_api(self, api_key, model, prompt, negative_prompt, options):
		segments = [prompt.strip()]
		dur = options.get("duration_seconds")
		if isinstance(dur, int) and dur > 0:
			segments.append(f"Target duration: {dur} seconds.")
		if negative_prompt:
			segments.append(f"Avoid: {negative_prompt.strip()}")
		full_prompt = "\n\n".join(s for s in segments if s)
		body = {
			"contents": [
				{
					"role": Role.USER,
					"parts": [{"text": full_prompt}],
				}
			],
			"generationConfig": {
				"responseModalities": ["AUDIO", "TEXT"],
			},
		}
		for src, dst in (("temperature", "temperature"), ("top_p", "topP"), ("top_k", "topK"), ("seed", "seed")):
			val = options.get(src)
			if val is not None:
				body["generationConfig"][dst] = val
		encoded_model = urllib.parse.quote(model, safe="")
		url = f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}:generateContent?key={urllib.parse.quote(api_key)}"
		req = urllib.request.Request(
			url,
			data=json.dumps(body).encode("utf-8"),
			headers={"Content-Type": "application/json"},
			method="POST",
		)
		with urllib.request.urlopen(req, timeout=300) as resp:
			return json.loads(resp.read().decode("utf-8", errors="replace"))

	def _get_audio_ext(self, result):
		if isinstance(result, dict):
			candidates = result.get("candidates")
			if isinstance(candidates, list):
				for cand in candidates:
					if not isinstance(cand, dict):
						continue
					content = cand.get("content")
					if not isinstance(content, dict):
						continue
					parts = content.get("parts")
					if not isinstance(parts, list):
						continue
					for part in parts:
						if not isinstance(part, dict):
							continue
						inline = part.get("inlineData")
						if isinstance(inline, dict):
							mime = inline.get("mimeType")
							if isinstance(mime, str):
								mime = mime.lower().strip()
								if "mpeg" in mime or "mp3" in mime:
									return "mp3"
								if "flac" in mime:
									return "flac"
								if "wav" in mime:
									return "wav"
			audio = result.get("audio")
			if isinstance(audio, dict):
				fmt = audio.get("format")
				if isinstance(fmt, str) and fmt:
					fmt = fmt.lower().strip()
					if fmt in ("wav", "mp3", "flac"):
						return fmt
				mime = audio.get("mimeType") or audio.get("mime_type")
				if isinstance(mime, str):
					mime = mime.lower().strip()
					if "mpeg" in mime or "mp3" in mime:
						return "mp3"
					if "flac" in mime:
						return "flac"
		return "wav"

	def _run_generation_thread(self, account_id, api_key, prompt, negative, model, options):
		err = None
		result = None
		try:
			try:
				result = self._try_lyria_generate_via_gemini_api(api_key, model, prompt, negative, options)
			except Exception:
				result = self._try_lyria_generate_via_openai_compat(account_id, model, prompt, negative, options)
		except Exception as e:
			err = e
		wx.CallAfter(self._onGenerationDone, result, err, options.get("response_format", "wav"))

	def _onGenerationDone(self, result, err, requested_format):
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
			log.error(f"Lyria generation failed: {err}", exc_info=True)
			# Translators: Error body when Lyria 3 Pro music generation raises an exception; placeholder is the error text (title is «OpenAI»).
			wx.MessageBox(_("Lyria 3 Pro generation failed: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		b64 = extract_audio_b64(result)
		if not b64:
			# Translators: Error body when the Lyria API JSON contained no usable base64 audio block after a nominally successful response (title is «OpenAI»).
			wx.MessageBox(_("Lyria request completed but no audio payload was returned by the provider."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		try:
			audio_bytes = base64.b64decode(b64)
		except Exception as decode_err:
			# Translators: Error body when base64-decoding the Lyria audio chunk fails; placeholder is the decode error (title is «OpenAI»).
			wx.MessageBox(_("Invalid audio payload: %s") % decode_err, "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		actual_ext = self._get_audio_ext(result)
		out_path = build_media_path("audio", f".{actual_ext}", prefix="lyria")
		try:
			with open(out_path, "wb") as f:
				f.write(audio_bytes)
		except Exception as write_err:
			# Translators: Error body when writing the decoded Lyria WAV/MP3 to the temp folder fails; placeholder is the OS error (title is «OpenAI»).
			wx.MessageBox(_("Unable to save generated audio: %s") % write_err, "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._generatedAudioPath = out_path
		self._syncOpenButtons()
		self.suggest_open_audio(out_path)
		prompt_text = self.promptText.GetValue().strip()
		negative_text = self.negativePromptText.GetValue().strip()
		model_text = self.modelText.GetValue().strip() or "lyria-3-pro-preview"
		self.save_tool_conversation(
			# Translators: Title stored on the synthetic «tool output» conversation tab after Lyria 3 Pro finishes successfully.
			title=_("Tool output: Lyria 3 Pro"),
			conversation_format=ConversationFormat.TOOL_GOOGLE_LYRIA_PRO,
			prompt=prompt_text,
			# Translators: Short assistant reply stored with the tool run when Lyria 3 Pro produced an audio file (shown in chat history).
			response_text=_("Audio generated with Lyria 3 Pro."),
			model=model_text,
			audio_paths=[out_path],
			format_data={
				"prompt": prompt_text,
				"negative_prompt": negative_text,
				"model": model_text,
				"requested_format": requested_format,
				"actual_format": actual_ext,
				"audio_path": out_path,
				"options": {
					"duration_seconds": safe_int(self.durationText.GetValue(), default=None),
					"seed": safe_int(self.seedText.GetValue(), default=None),
					"temperature": safe_float(self.temperatureText.GetValue(), default=None),
					"top_p": safe_float(self.topPText.GetValue(), default=None),
					"top_k": safe_int(self.topKText.GetValue(), default=None),
					"cfg_scale": safe_float(self.cfgScaleText.GetValue(), default=None),
					"sample_rate_hz": safe_int(self.sampleRateText.GetValue(), default=48000),
				},
			},
		)

	def _syncOpenButtons(self):
		self.openGeneratedAudioBtn.Show(bool(self._generatedAudioPath))
		self.formPanel.Layout()
		self.Layout()

	def _applyConversationData(self, conversationData):
		if not isinstance(conversationData, dict):
			return
		fd = conversationData.get("formatData", {})
		if not isinstance(fd, dict):
			return
		self.promptText.SetValue(fd.get("prompt", ""))
		self.negativePromptText.SetValue(fd.get("negative_prompt", ""))
		self.modelText.SetValue(fd.get("model", self.modelText.GetValue()))
		req_fmt = fd.get("requested_format", "")
		if isinstance(req_fmt, str) and req_fmt:
			idx = self.formatChoice.FindString(req_fmt)
			if idx != wx.NOT_FOUND:
				self.formatChoice.SetSelection(idx)
		options = fd.get("options", {})
		if isinstance(options, dict):
			for key, ctrl in (
				("duration_seconds", self.durationText),
				("seed", self.seedText),
				("temperature", self.temperatureText),
				("top_p", self.topPText),
				("top_k", self.topKText),
				("cfg_scale", self.cfgScaleText),
				("sample_rate_hz", self.sampleRateText),
			):
				val = options.get(key)
				if val is not None:
					ctrl.SetValue(str(val))
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
		acc_id = self.require_account(self.accountChoice)
		if not acc_id:
			return
		prompt = self.promptText.GetValue().strip()
		if not prompt:
			# Translators: Error body when Generate is pressed with an empty main prompt in the Lyria 3 Pro tool (title is «OpenAI»).
			wx.MessageBox(_("Please enter a prompt for Lyria 3 Pro."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.promptText.SetFocus()
			return
		negative = self.negativePromptText.GetValue().strip()
		model = self.modelText.GetValue().strip() or "lyria-3-pro-preview"
		fmt = self.formatChoice.GetStringSelection() or "wav"
		options = {
			"duration_seconds": safe_int(self.durationText.GetValue(), default=None),
			"seed": safe_int(self.seedText.GetValue(), default=None),
			"temperature": safe_float(self.temperatureText.GetValue(), default=None),
			"top_p": safe_float(self.topPText.GetValue(), default=None),
			"top_k": safe_int(self.topKText.GetValue(), default=None),
			"cfg_scale": safe_float(self.cfgScaleText.GetValue(), default=None),
			"sample_rate_hz": safe_int(self.sampleRateText.GetValue(), default=48000),
			"response_format": fmt,
		}
		options = {k: v for k, v in options.items() if v is not None}
		api_key = self.manager.get_api_key(account_id=acc_id)
		if not api_key:
			# Translators: Error body when Lyria cannot run because the chosen Google API account has no key stored (title is «OpenAI»).
			wx.MessageBox(_("No API key available for the selected Google account."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		# Translators: Status line on the modal progress window while Lyria 3 Pro is generating music.
		self.begin_long_task(_("Music generation in progress..."), self._setBusy)
		self._worker = threading.Thread(
			target=self._run_generation_thread,
			args=(acc_id, api_key, prompt, negative, model, options),
			daemon=True,
		)
		self._worker.start()
