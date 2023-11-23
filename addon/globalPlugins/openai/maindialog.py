import json
import os
import sys
import threading
import winsound
import gui
import wx

import addonHandler
import api
import config
import queueHandler
import speech
import tones
import ui
from logHandler import log
from .consts import ADDON_DIR, DATA_DIR
from .imagehelper import describeFromImageFileList
additionalLibsPath = os.path.join(ADDON_DIR, "lib")
sys.path.insert(0, additionalLibsPath)
import openai
import sounddevice as sd
import numpy as np
import wave

sys.path.remove(additionalLibsPath)

from .consts import (
	ADDON_DIR, DATA_DIR,
	TEMPERATURE_MIN, TEMPERATURE_MAX,
	TOP_P_MIN, TOP_P_MAX,
	MAX_TOKENS_MIN, MAX_TOKENS_MAX,
	N_MIN, N_MAX
)

addonHandler.initTranslation()

DEFAULT_PROMPT_IMAGE_DESCRIPTION = _("Describe the images in as much detail as possible.")
MODEL_VISION = "gpt-4-vision-preview"
TTS_FILE_NAME = os.path.join(DATA_DIR, "tts.wav")
EVT_RESULT_ID = wx.NewId()
DATA_JSON_FP = os.path.join(DATA_DIR, "data.json")

def EVT_RESULT(win, func):
	win.Connect(-1, -1, EVT_RESULT_ID, func)


class ResultEvent(wx.PyEvent):

	def __init__(self, data=None):
		wx.PyEvent.__init__(self)
		self.SetEventType(EVT_RESULT_ID)
		self.data = data


class CompletionThread(threading.Thread):

	def __init__(self, notifyWindow):
		threading.Thread.__init__(self)
		self._notifyWindow = notifyWindow
		self._wantAbort = 0

	def run(self):
		wnd = self._notifyWindow
		client = wnd.client
		conf = wnd.conf
		block = HistoryBlock()
		system = wnd.systemText.GetValue().strip()
		block.system = system
		prompt = wnd.promptText.GetValue().strip()
		block.userPrompt = prompt
		model = list(wnd.models.keys())[wnd.modelListBox.GetSelection()]
		block.model = model
		conf["model"] = model
		temperature = conf["temperature"] / 100
		topP = conf["topP"] / 100
		stream = conf["stream"]
		debug = conf["debug"]
		if conf["advancedMode"]:
			temperature = wnd.temperature.GetValue() / 100
			conf["temperature"] = wnd.temperature.GetValue()

			topP = wnd.topP.GetValue() / 100
			conf["topP"] = wnd.topP.GetValue()

			debug = wnd.debugModeCheckBox.IsChecked()
			conf["debug"] = debug

			stream = wnd.streamModeCheckBox.IsChecked()
			conf["stream"] = stream

		block.temperature = temperature
		block.topP = topP

		maxTokens = wnd.maxTokens.GetValue()
		conf["maxTokens"] = maxTokens
		n = 1 # wnd.n.GetValue()
		if not TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX:
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid temperature")))
			return
		if not TOP_P_MIN <= topP <= TOP_P_MAX:
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid top P")))
			return
		params = {
			"model": model,
			"messages": [
				{"role": "system", "content": system},
				{"role": "user", "content": prompt}
			],
			"temperature": temperature,
			"max_tokens": maxTokens,
			"top_p": topP,
			#"n": n,
			"stream": stream
		}
		try:
			response = client.chat.completions.create(**params)
		except BaseException as err:
			wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			return
		if wnd.lastBlock is None:
			wnd.firstBlock = wnd.lastBlock = block
		else:
			wnd.lastBlock.next = block
			block.previous = wnd.lastBlock
			wnd.lastBlock = block
		wnd.previousPrompt = wnd.promptText.GetValue()
		wnd.promptText.Clear()

		if stream:
			self._responseWithStream(response, block, debug)
		else:
			self._responseWithoutStream(response, block, debug)
		wx.PostEvent(self._notifyWindow, ResultEvent())
		wnd.message(_("Ready"))

	def abort(self):
		self._wantAbort = True

	def _responseWithStream(self, response, block, debug=False):
		wnd = self._notifyWindow
		text = ""
		for i, event in enumerate(response):
			if wnd.stopRequest.is_set():
				break
			delta = event.choices[0].delta
			finish = event.choices[0].finish_reason
			text = ""
			if delta.content:
				text = "%s" % delta.content

			block.responseText += text
		block.responseTerminated = True

	def _responseWithoutStream(self, response, block, debug=False):
		wnd = self._notifyWindow
		text = ""
		n = 1 # len(response.choices)
		if n > 1:
			text += f"# {n} completions"
		for i, choice in enumerate(response.choices):
			if self._wantAbort: break
			if debug:
				text = f"{json.dumps(response, indent=2, ensure_ascii=False)}"
				break
			if n > 1:
				text += f"\n\n## completion {i+1}\n\n"
			text += choice.message.content
		block.responseText += text
		block.responseTerminated = True


class ImageDescriptionThread(threading.Thread):

	def __init__(self, notifyWindow):
		threading.Thread.__init__(self)
		self._notifyWindow = notifyWindow
		self._pathList = notifyWindow.pathList

	def run(self):
		wnd = self._notifyWindow
		prompt = wnd.promptText.GetValue()
		max_tokens = wnd.maxTokens.GetValue()
		client = wnd.client
		try:
			description = describeFromImageFileList(client, self._pathList, prompt=prompt, max_tokens=max_tokens)
		except BaseException as err:
			wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			return
		wx.PostEvent(self._notifyWindow, ResultEvent(description))

	def abort(self):
		pass


class RecordThread(threading.Thread):

	def __init__(self, notifyWindow, pathList):
		super(RecordThread, self).__init__()
		self._notifyWindow = notifyWindow
		self.pathList = pathList
		self.stop_record = False
		self._wantAbort = 0
		self._recording = False
		self.audio_data = np.array([], dtype='int16')

	def run(self):
		if self.pathList:
			self.process_transcription(self.pathList)
			return
		framerate = 44100
		filename = self.get_filename()
		tones.beep(200, 100)
		self.record_audio(framerate)
		tones.beep(200, 200)
		winsound.PlaySound(f"{ADDON_DIR}/sounds/progress.wav", winsound.SND_ASYNC|winsound.SND_LOOP)

		if self._wantAbort:
			return
		self.save_wav(
			filename,
			self.audio_data,
			framerate
		)
		self._notifyWindow.message(_("Transcribing..."))
		self.process_transcription(filename)

	def record_audio(self, framerate):
		chunk_size = 1024  # Vous pouvez ajuster la taille du bloc selon vos besoins
		channels = 2
		dtype = 'int16'
		self._recording = True

		with sd.InputStream(samplerate=framerate, channels=channels, dtype=dtype) as stream:
			while not self.stop_record and self._recording:
				frame, overflowed = stream.read(chunk_size)
				if overflowed:
					print("Warning: audio buffer has overflowed.")
				self.audio_data = np.append(self.audio_data, frame)
				if self._wantAbort:
					break

		self._recording = False

	def save_wav(self, filename, data, framerate):
		if self._wantAbort:
			return
		wavefile = wave.open(filename, "wb")
		wavefile.setnchannels(2)
		wavefile.setsampwidth(2)
		wavefile.setframerate(framerate)
		wavefile.writeframes(data.tobytes())
		wavefile.close()

	def stop(self):
		self.stop_record = True
		self._recording = False

	def get_filename(self):
		return os.path.join(DATA_DIR, "tmp.wav")

	def process_transcription(self, filename):
		if self._wantAbort:
			return
		wnd = self._notifyWindow
		prompt = wnd.promptText.GetValue()
		client = wnd.client
		try:
			audio_file = open(filename, "rb")
			transcription = client.audio.transcriptions.create(
				model="whisper-1", 
				file=audio_file
			)
		except BaseException as err:
			wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			return
		wx.PostEvent(self._notifyWindow, ResultEvent(transcription))

	def abort(self):
		self.stop_record = 1
		self._wantAbort = 1


class TextToSpeechThread(threading.Thread):

	def __init__(self, notifyWindow, text):
		threading.Thread.__init__(self)
		self._notifyWindow = notifyWindow
		self._text = text
		self._voice = notifyWindow.conf["TTSVoice"]
		self._model = notifyWindow.conf["TTSModel"]
		self._wantAbort = 0

	def run(self):
		wnd = self._notifyWindow
		client = wnd.client
		try:
			if os.path.exists(TTS_FILE_NAME):
				os.remove(TTS_FILE_NAME)
			response = client.audio.speech.create(
				model=self._model,
				voice=self._voice,
				input=self._text,
				response_format="mp3"
			)
			response.stream_to_file(
				TTS_FILE_NAME,
			)
		except BaseException as err:
			wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			return
		wx.PostEvent(self._notifyWindow, ResultEvent(response))

	def abort(self):
		self._wantAbort = True
		self._recording = False


class TextSegment:

	previous= None
	next= None
	originalText = ""
	start = 0
	end = 0
	owner = None

	def __init__(self, control, text, owner):
		self.control = control
		self.originalText = text
		self.owner = owner

		# Management of segment chain in Control
		if not hasattr (control, "lastSegment") or control.lastSegment is None:
			# The linked list is not yet initialized
			control.firstSegment = self
			control.lastSegment = self
		else:
			# Add the segment to the end of the list
			control.lastSegment.next = self
			self.previous = control.lastSegment
			control.lastSegment  = self

		# Save current position
		p = control.GetInsertionPoint()

		# Add the text at the end of the control
		control.SetInsertionPointEnd()
		self.start = control.GetInsertionPoint()
		control.AppendText(text)
		self.end = control.GetInsertionPoint()

		# Restore the previously saved position
		control.SetInsertionPoint(p)

	def appendText (self, text):
		# Save current position
		p = self.control.GetInsertionPoint()

		# Add the text at the end of the segment
		self.control.AppendText(text)
		self.end = self.control.GetInsertionPoint()

		# Restore the previously saved position
		self.control.SetInsertionPoint(p)

	def getText (self):
		return self.control.GetRange (self.start, self.end)

	@staticmethod
	def getCurrentSegment(control):
		# Get the current position
		p = control.GetInsertionPoint()

		if not hasattr (control, "firstSegment"):
			return None
		segment = control.firstSegment

		# Iterate through the segments
		while segment != None:
			if segment.start <= p and segment.end > p:
				# The current segment is found
				return segment
			segment = segment.next

		# No segment found, probably the insertion point is at the end of the text.
		return control.lastSegment

	def delete(self):
		# Remove the text from the control
		self.control.Remove (self.start, self.end)

		# Remove the segment from the linked list
		if self.previous != None:
			self.previous.next = self.next
		else:
			# This is the first segment
			self.control.firstSegment = self.next

		if self.next != None:
			self.next.previous = self.previous
		else:
			# This is the last segment
			self.control.lastSegment = self.previous

		# Update the start and end positions of all following segments
		segment = self.next
		while segment != None:
			segment.start -= (self.end - self.start)
			segment.end -= (self.end - self.start)
			segment = segment.next


class HistoryBlock():
	previous = None
	next = None
	contextPromp = ""
	userPrompt = ""
	response = {}
	responseText = ""
	segmentBreakLine = None
	segmentPromptLabel = None
	segmentPrompt = None
	segmentResponseLabel = None
	segmentResponse = None
	lastLen = 0
	model = ""
	temperature = 0
	topP = 0
	displayHeader = True
	focused = False
	responseTerminated = False


class OpenAIDlg(wx.Dialog):

	models = {
		"gpt-3.5-turbo": _("4096 tokens max. Most capable GPT-3.5 model and optimized for chat at 1/10th the cost of text-davinci-003"),
		"gpt-3.5-turbo-16k": _("16384 tokens max. Same capabilities as the standard gpt-3.5-turbo model but with 4 times the context"),
		"gpt-4": _("8192 tokens max. More capable than any GPT-3.5 model, able to do more complex tasks, and optimized for chat"),
		MODEL_VISION: _("GPT-4 Turbo with vision"),
		"gpt-4-1106-preview": _("GPT-4 Turbo, 128K context, maximum of 4096 output tokens."),
		"gpt-4-32k": _("32768 tokens max. Same capabilities as the standard gpt-4 mode but with 4x the context length.")
	}

	def __init__(
		self,
		parent,
		client,
		conf,
		title=None,
		pathList=None
	):
		if not client or not conf:
			return
		self.client = client
		self.conf = conf
		self.blocks = []
		self.pathList = pathList
		self.previousPrompt = None
		self._lastSystem = None
		if self.conf["saveSystem"]:
			self._lastSystem = self.getLastSystem()
		if not title:
			title = "Open AI - %s" % (
				_("organization") if conf["use_org"] else _("personal")
			)
		super().__init__(parent, title=title)

		systemLabel = wx.StaticText(
			parent=self,
			label=_("S&ystem:")
		)
		self.systemText = wx.TextCtrl(
			parent=self,
			size=(550, -1),
			style=wx.TE_MULTILINE|wx.TE_READONLY,
		)
		if conf["saveSystem"] and self._lastSystem:
			self.systemText.SetValue(self._lastSystem)

		historyLabel = wx.StaticText(
			parent=self,
			label=_("&History:")
		)
		self.historyText = wx.TextCtrl(
			parent=self,
			style=wx.TE_MULTILINE|wx.TE_READONLY,
			size=(550, -1)
		)

		promptLabel = wx.StaticText(
			parent=self,
			label=_("&Prompt:")
		)
		self.promptText = wx.TextCtrl(
			parent=self,
			size=(550, -1),
			style=wx.TE_MULTILINE,
		)
		if self.pathList:
			self.promptText.SetValue(DEFAULT_PROMPT_IMAGE_DESCRIPTION)

		modelsLabel = wx.StaticText(
			parent=self,
			label=_("&Model:")
		)
		models = [f"{name} ({desc})" for name, desc in self.models.items()]
		self.modelListBox = wx.ListBox(
			parent=self,
			choices=models,
			style=wx.LB_SINGLE
		)
		model = MODEL_VISION if self.pathList else conf["model"]
		idx = list(self.models.keys()).index(model) if model in self.models else 0
		self.modelListBox.SetSelection(idx)

		maxTokensLabel = wx.StaticText(
			parent=self,
			label=_("Maximum to&kens for the completion:")
		)
		self.maxTokens = wx.SpinCtrl(
			parent=self,
			min=MAX_TOKENS_MIN,
			max=MAX_TOKENS_MAX,
			initial=conf["maxTokens"]
		)

		if conf["advancedMode"]:
			temperatureLabel = wx.StaticText(
				parent=self,
				label=_("&Temperature:")
			)
			self.temperature = wx.SpinCtrl(
				parent=self,
				min=TEMPERATURE_MIN,
				max=TEMPERATURE_MAX,
				initial=conf["temperature"]
			)

			topPLabel = wx.StaticText(
				parent=self,
				label=_("Probability &mass (Top P):")
			)
			self.topP = wx.SpinCtrl(
				parent=self,
				min=TOP_P_MIN,
				max=TOP_P_MAX,
				initial=conf["topP"]
			)

			self.streamModeCheckBox = wx.CheckBox(
				parent=self,
				label=_("Stream mode")
			)
			self.streamModeCheckBox.SetValue(conf["stream"])

			self.debugModeCheckBox = wx.CheckBox(
				parent=self,
				label=_("&Debug mode")
			)
			self.debugModeCheckBox.SetValue(conf["debug"])

		sizer1 = wx.BoxSizer(wx.VERTICAL)
		sizer1.Add(systemLabel, 0, wx.ALL, 5)
		sizer1.Add(self.systemText, 0, wx.ALL, 5)
		sizer1.Add(historyLabel, 0, wx.ALL, 5)
		sizer1.Add(self.historyText, 0, wx.ALL, 5)
		sizer1.Add(promptLabel, 0, wx.ALL, 5)
		sizer1.Add(self.promptText, 0, wx.ALL, 5)
		sizer1.Add(modelsLabel, 0, wx.ALL, 5)
		sizer1.Add(self.modelListBox, 0, wx.ALL, 5)
		sizer1.Add(maxTokensLabel, 0, wx.ALL, 5)
		sizer1.Add(self.maxTokens, 0, wx.ALL, 5)
		if conf["advancedMode"]:
			sizer1.Add(temperatureLabel, 0, wx.ALL, 5)
			sizer1.Add(self.temperature, 0, wx.ALL, 5)
			sizer1.Add(topPLabel, 0, wx.ALL, 5)
			sizer1.Add(self.topP, 0, wx.ALL, 5)
			sizer1.Add(self.streamModeCheckBox, 0, wx.ALL, 5)
			sizer1.Add(self.debugModeCheckBox, 0, wx.ALL, 5)

		self.recordBtn = wx.Button(
			parent=self,
			label=_("Start &recording")
		)
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onRecord)
		self.recordBtn.SetToolTip(_("Record audio from microphone"))

		self.transcribeFromFileBtn = wx.Button(
			parent=self,
			label=_("Transcribe from &audio file")
		)
		self.transcribeFromFileBtn.Bind(wx.EVT_BUTTON, self.onRecordFromFilePath)
		self.transcribeFromFileBtn.SetToolTip(_("Transcribe audio from a file path"))

		self.imageDescriptionBtn = wx.Button(
			parent=self,
			label=_("&Image description")
		)
		self.imageDescriptionBtn.Bind(wx.EVT_BUTTON, self.onImageDescription)
		self.imageDescriptionBtn.SetToolTip(_("Describe an image from a file path or an URL"))

		self.TTSBtn = wx.Button(
			parent=self,
			label=_("&Vocalize the prompt")
		)
		self.TTSBtn.Bind(wx.EVT_BUTTON, self.onTextToSpeech)

		sizer2 = wx.BoxSizer(wx.HORIZONTAL)
		sizer2.Add(self.recordBtn, 0, wx.ALL, 5)
		sizer2.Add(self.imageDescriptionBtn, 0, wx.ALL, 5)
		sizer2.Add(self.transcribeFromFileBtn, 0, wx.ALL, 5)
		sizer2.Add(self.TTSBtn, 0, wx.ALL, 5)

		self.okBtn = wx.Button(
			parent=self,
			id=wx.ID_OK
		)
		self.okBtn.Bind(wx.EVT_BUTTON, self.onOk)
		self.okBtn.SetDefault()

		self.cancelBtn = wx.Button(
			parent=self,
			id=wx.ID_CANCEL
		)
		self.cancelBtn.Bind(wx.EVT_BUTTON, self.onCancel)

		sizer3 = wx.BoxSizer(wx.HORIZONTAL)
		sizer3.Add(self.okBtn, 0, wx.ALL, 5)
		sizer3.Add(self.cancelBtn, 0, wx.ALL, 5)

		sizer4 = wx.BoxSizer(wx.VERTICAL)
		sizer4.Add(sizer1, 0, wx.ALL, 5)
		sizer4.Add(sizer2, 0, wx.ALL, 5)
		sizer4.Add(sizer3, 0, wx.ALL, 5)

		self.SetSizer(sizer4)
		self.SetAutoLayout(True)
		sizer4.Fit(self)
		self.Layout()
		self.Center()
		self.SetSize((600, 600))
		self.SetMinSize((600, 600))

		self.addShortcuts()
		self.promptText.SetFocus()
		EVT_RESULT(self, self.OnResult)
		self.worker = None
		self.firstBlock = None
		self.lastBlock = None
		self.timer = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self.onTimer, self.timer)
		self.timer.Start (100)
		self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
		self.Bind(wx.EVT_CLOSE, self.onCancel)

	def getLastSystem(self):
		if not os.path.exists(DATA_JSON_FP):
			return
		f = open(DATA_JSON_FP, "r")
		try:
			data = json.loads(f.read())
		except BaseException as err:
			log.error(f"Error while reading data.json: {err}")
			f.close()
			return
		f.close()
		return data.get("system")

	def onOk(self, evt):
		if not self.promptText.GetValue().strip():
			self.promptText.SetFocus()
			return
		if self.worker:
			return
		model = list(self.models.keys())[self.modelListBox.GetSelection()]
		if model == MODEL_VISION and not self.pathList:
			gui.messageBox(
				_("No image provided. Please use the Image Description button and select one or more images. Otherwise, please select another model."),
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		if model != MODEL_VISION and self.pathList:
			gui.messageBox(
				_("This model does not support image description. Please select the %s model.") % MODEL_VISION,
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		system = self.systemText.GetValue().strip()
		if self.conf["saveSystem"] and system != self._lastSystem and system:
			f = open(os.path.join(DATA_DIR, "data.json"), "w")
			data = {"system": system}
			f.write(json.dumps(data, indent=2, ensure_ascii=False))
			f.close()
			self._lastSystem = system
		self.message(_("Processing, please wait..."))
		winsound.PlaySound(f"{ADDON_DIR}/sounds/progress.wav", winsound.SND_ASYNC|winsound.SND_LOOP)
		self.disableButtons()
		self.historyText.SetFocus()
		self.stopRequest = threading.Event()
		if self.pathList:
			self.modelListBox.SetSelection(
				list(self.models.keys()).index(MODEL_VISION)
			)
			self.worker = ImageDescriptionThread(self)
		else:
			self.worker = CompletionThread(self)
		self.worker.start()

	def onCancel(self, evt):
		if self.worker:
			self.worker.abort()
			self.worker = None
			winsound.PlaySound(None, winsound.SND_ASYNC)
		self.timer.Stop()
		self.Destroy()

	def OnResult(self, event):
		self.enableButtons()
		self.worker = None
		winsound.PlaySound(None, winsound.SND_ASYNC)
		if not event.data:
			return
		if isinstance(event.data, openai.types.chat.chat_completion.Choice):
			historyBlock = HistoryBlock()
			historyBlock.system = self.systemText.GetValue().strip()
			historyBlock.userPrompt = self.promptText.GetValue().strip()
			if self.pathList:
				for path in self.pathList:
					historyBlock.userPrompt += f"\n  + <image: \"{path}\">"
			self.pathList = None
			historyBlock.model = list(self.models.keys())[self.modelListBox.GetSelection()]
			if self.conf["advancedMode"]:
				historyBlock.temperature = self.temperature.GetValue() / 100
				historyBlock.topP = self.topP.GetValue() / 100
			else:
				historyBlock.temperature = self.conf["temperature"] / 100
				historyBlock.topP = self.conf["topP"] / 100
			historyBlock.maxTokens = self.maxTokens.GetValue()
			historyBlock.n = 1 # self.n.GetValue()
			historyBlock.response = event.data
			historyBlock.responseText = event.data.message.content
			historyBlock.responseTerminated = True
			if self.lastBlock is None:
				self.firstBlock = self.lastBlock = historyBlock
			else:
				self.lastBlock.next = historyBlock
				historyBlock.previous = self.lastBlock
				self.lastBlock = historyBlock
			self.previousPrompt = self.promptText.GetValue()
			self.promptText.Clear()
			self.promptText.SetFocus()
			return
		if isinstance(event.data, openai.types.audio.transcription.Transcription):
			self.promptText.AppendText(event.data.text)
			self.promptText.SetFocus()
			self.promptText.SetInsertionPointEnd()
			self.message(
				_("Insertion of: %s") % event.data.text,
				True
			)
			return
		if isinstance(event.data, openai._base_client.HttpxBinaryResponseContent):
			if os.path.exists(TTS_FILE_NAME):
				os.startfile(TTS_FILE_NAME)
			return
		errMsg = repr(event.data)
		gui.messageBox(
			errMsg,
			_("Open AI error"),
			wx.OK|wx.ICON_ERROR
		)
		if "model's maximum context length is " in errMsg:
			self.modelListBox.SetFocus()
		else:
			self.promptText.SetFocus()

	def onCharHook(self, evt):
		if self.conf["blockEscapeKey"] and evt.GetKeyCode() == wx.WXK_ESCAPE:
			self.message(_("Press alt+f4 to close the dialog"))
		else:
			evt.Skip()

	def onTimer(self, event):
		if self.lastBlock is not None:
			block = self.lastBlock
			if block.displayHeader:
				if block != self.firstBlock:
					block.previous.segmentBreakLine = TextSegment(self.historyText, "\n", block)
				block.segmentPromptLabel = TextSegment(self.historyText, _("User:") + ' ', block)
				block.segmentPrompt = TextSegment(self.historyText, block.userPrompt + "\n", block)
				block.segmentResponseLabel = TextSegment(self.historyText, _("Assistant:") + ' ', block)
				block.displayHeader = False
			l = len(block.responseText)
			if block.lastLen == 0 and l > 0:
				self.historyText.SetInsertionPointEnd()
				block.responseText = block.responseText.lstrip()
				l = len(block.responseText)
			if l > block.lastLen:
				newText = block.responseText[block.lastLen:]
				block.lastLen = l
				if block.segmentResponse is None:
					block.segmentResponse = TextSegment(self.historyText, newText, block)
				else:
					block.segmentResponse.appendText (newText)
			if not block.focused and (block.responseTerminated or "\n" in block.responseText or len (block.responseText) > 180):
				self.historyText.SetFocus ()
				block.focused = True

	def addEntry(self, accelEntries, modifiers, key, func):
		id_ = wx.Window.NewControlId()
		self.Bind(wx.EVT_MENU, func, id=id_)
		accelEntries.append ( (modifiers, key, id_))

	def addShortcuts(self):
		self.historyText.Bind(wx.EVT_TEXT_COPY, self.onCopySegment)

		accelEntries  = []
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, wx.WXK_DOWN, self.onNextSegment)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, wx.WXK_UP, self.onPreviousSegment)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("E"), self.onEditBlock)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("D"), self.onDeleteBlock)
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_LEFT, self.onCopyResponseToContext)
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_RIGHT, self.onCopyPromptToPrompt)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.historyText.SetAcceleratorTable(accelTable)

		accelEntries  = []
		self.addEntry (accelEntries, wx.ACCEL_CTRL, wx.WXK_UP, self.onPreviousPrompt)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.promptText.SetAcceleratorTable(accelTable)

		accelEntries  = []
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("r"), self.onRecord)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("f"), self.onRecordFromFilePath)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("i"), self.onImageDescription)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("t"), self.onTextToSpeech)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.SetAcceleratorTable(accelTable)

	def onPreviousPrompt(self, event):
		value = self.previousPrompt
		if value:
			self.promptText.SetValue(value)

	def onPreviousSegment(self, evt):
		segment = TextSegment.getCurrentSegment(self.historyText)
		if segment is None:
			return
		block = segment.owner
		if segment == block.segmentPromptLabel or segment == block.segmentPrompt:
			prev = block.previous
			if prev is None:
				self.message(_("Begin"))
				return
			start = prev.segmentResponse.start
			text = prev.segmentResponse.getText ()
			label = prev.segmentResponseLabel.getText ()
		elif segment == block.segmentResponseLabel or segment == block.segmentResponse or segment == block.segmentBreakLine:
			start = block.segmentPrompt.start
			text = block.segmentPrompt.getText ()
			label = block.segmentPromptLabel.getText ()
		self.historyText.SetInsertionPoint(start)
		self.message(label + text)

	def onNextSegment(self, evt):
		segment = TextSegment.getCurrentSegment (self.historyText)
		if segment is None:
			return
		block = segment.owner
		if segment == block.segmentResponseLabel or segment == block.segmentResponse or segment == block.segmentBreakLine:
			next = block.next
			if next is None:
				self.message(_("End"))
				return
			start = next.segmentPrompt.start
			text = next.segmentPrompt.getText ()
			label = next.segmentPromptLabel.getText ()
		elif segment == block.segmentPromptLabel or segment == block.segmentPrompt:
			start = block.segmentResponse.start
			text = block.segmentResponse.getText ()
			label = block.segmentResponseLabel.getText ()
		self.historyText.SetInsertionPoint(start)
		self.message(label + text)

	def onEditBlock (self, evt):
		segment = TextSegment.getCurrentSegment (self.historyText)
		if segment is None:
			return
		block = segment.owner
		self.systemText.SetValue(block.system)
		self.promptText.SetValue (block.userPrompt)
		self.promptText.SetFocus ()

	def onCopyResponseToContext (self, evt):
		segment = TextSegment.getCurrentSegment(self.historyText)
		if segment is None:
			return
		block = segment.owner
		text = block.segmentResponse.getText ()
		self.systemText.SetValue(text)
		self.message(_("Response copied to system: %s") % text)

	def onCopyPromptToPrompt(self, evt):
		segment = TextSegment.getCurrentSegment(self.historyText)
		if segment is None:
			return
		block = segment.owner
		self.promptText.SetValue (block.segmentPrompt.getText ())
		self.promptText.SetFocus ()
		self.message(_("Compied to prompt"))

	def onCopySegment (self, evt):
		text = self.historyText.GetStringSelection()
		msg = _("Copy")
		if not text:
			segment = TextSegment.getCurrentSegment (self.historyText)
			if segment is None:
				return
			block = segment.owner
			if segment == block.segmentPromptLabel or segment == block.segmentPrompt:
				text = block.segmentPrompt.getText ()
				msg = _("Copy prompt")
			elif segment == block.segmentResponseLabel or segment == block.segmentResponse:
				text = block.segmentResponse.getText()
				msg = _("Copy response")
		api.copyToClip(text)
		self.message(msg)

	def onDeleteBlock(self, evt):
		segment = TextSegment.getCurrentSegment (self.historyText)
		if segment is None:
			return
		block = segment.owner

		if block.segmentBreakLine  is not None:
			block.segmentBreakLine.delete ()
		block.segmentPromptLabel.delete ()
		block.segmentPrompt.delete()
		block.segmentResponseLabel.delete ()
		block.segmentResponse.delete ()

		if block.previous is not None:
			block.previous.next = block.next
		else:
			self.firstBlock = block.next
		if block.next is not None:
			block.next.previous = block.previous
		else:
			self.lastBlock = block.previous
		self.message(_("Block deleted"))

	def message(self, msg, onlySpeech=False):
		func = ui.message if not onlySpeech else speech.speakMessage
		queueHandler.queueFunction(queueHandler.eventQueue, func, msg)

	def onImageDescription(self, evt):
		if not self.pathList:
			dlg = wx.FileDialog(
				None,
				message=_("Select image files"),
				defaultFile="",
				wildcard=_("Image files") + " (*.png;*.jpeg;*.jpg;*.gif)|*.png;*.jpeg;*.jpg;*.gif",
				style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE
			)
			if dlg.ShowModal() != wx.ID_OK:
				return
			self.pathList = dlg.GetPaths()
			if not self.pathList:
				return
		self.modelListBox.SetSelection(
			list(self.models.keys()).index(MODEL_VISION)
		)
		if not self.promptText.GetValue().strip():
			self.promptText.SetValue(DEFAULT_PROMPT_IMAGE_DESCRIPTION)
		self.promptText.SetFocus()

	def onRecord(self, evt):
		if self.worker:
			self.onStopRecord(evt)
			return
		self.disableButtons()
		self.recordBtn.SetLabel(_("Stop &recording") + " (Ctrl+R)")
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onStopRecord)
		self.recordBtn.Enable()
		self.worker = RecordThread(self, None)
		self.worker.start()

	def onRecordFromFilePath(self, evt):
		dlg = wx.FileDialog(
			None,
			message=_("Select audio file"),
			defaultFile="",
			wildcard=_("Audio files (*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.wav;*.webm)|*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.wav;*.webm"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		filename = dlg.GetPath()
		self.message(_("Processing, please wait..."))
		winsound.PlaySound(f"{ADDON_DIR}/sounds/progress.wav", winsound.SND_ASYNC|winsound.SND_LOOP)
		self.disableButtons()
		self.historyText.SetFocus()
		self.worker = RecordThread(self, filename)
		self.worker.start()

	def onTextToSpeech(self, evt):
		if not self.promptText.GetValue().strip():
			gui.messageBox(
				_("Please enter some text in the prompt field first."),
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			self.promptText.SetFocus()
			return
		self.message(_("Processing, please wait..."))
		winsound.PlaySound(f"{ADDON_DIR}/sounds/progress.wav", winsound.SND_ASYNC|winsound.SND_LOOP)
		self.disableButtons()
		self.promptText.SetFocus()
		self.worker = TextToSpeechThread(self, self.promptText.GetValue())
		self.worker.start()

	def onStopRecord(self, evt):
		if self.worker:
			self.worker.stop()
			self.worker = None
			winsound.PlaySound(None, winsound.SND_ASYNC)
		self.recordBtn.SetLabel(_("Start &recording"))
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onRecord)
		self.enableButtons()

	def disableButtons(self):
		self.okBtn.Disable()
		self.cancelBtn.Disable()
		self.recordBtn.Disable()
		self.transcribeFromFileBtn.Disable()
		self.imageDescriptionBtn.Disable()
		self.TTSBtn.Disable()

	def enableButtons(self):
		self.okBtn.Enable()
		self.cancelBtn.Enable()
		self.recordBtn.Enable()
		self.transcribeFromFileBtn.Enable()
		self.imageDescriptionBtn.Enable()
		self.TTSBtn.Enable()
