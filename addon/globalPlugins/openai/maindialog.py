import json
import os
import re
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
from .consts import (
	ADDON_DIR, DATA_DIR,
	MODELS, MODEL_VISION,
	TOP_P_MIN, TOP_P_MAX,
	N_MIN, N_MAX,
	DEFAULT_SYSTEM_PROMPT
)
from .imagehelper import resize_image, describeFromImageFileList, encode_image
from .recordthread import RecordThread
from .resultevent import ResultEvent, EVT_RESULT_ID

additionalLibsPath = os.path.join(ADDON_DIR, "lib")
sys.path.insert(0, additionalLibsPath)
import openai
sys.path.remove(additionalLibsPath)

addonHandler.initTranslation()

TTS_FILE_NAME = os.path.join(DATA_DIR, "tts.wav")
DATA_JSON_FP = os.path.join(DATA_DIR, "data.json")

def EVT_RESULT(win, func):
	win.Connect(-1, -1, EVT_RESULT_ID, func)


class CompletionThread(threading.Thread):

	def __init__(self, notifyWindow):
		threading.Thread.__init__(self)
		self._notifyWindow = notifyWindow
		self._wantAbort = 0

	def run(self):
		wnd = self._notifyWindow
		client = wnd.client
		conf = wnd.conf
		data = wnd.data
		block = HistoryBlock()
		system = wnd.systemText.GetValue().strip()
		block.system = system
		prompt = wnd.promptText.GetValue().strip()
		block.prompt = prompt
		model = wnd.getCurrentModel()
		block.model = model.name
		conf["model"] = model.name
		stream = conf["stream"]
		debug = conf["debug"]
		maxTokens = wnd.maxTokens.GetValue()
		block.maxTokens = maxTokens
		key_maxTokens = "maxTokens_%s" % model.name
		data[key_maxTokens] = maxTokens
		temperature = 1
		topP = 1
		if conf["advancedMode"]:
			temperature = wnd.temperature.GetValue() / 100
			key_temperature = "temperature_%s" % model.name
			data[key_temperature] = wnd.temperature.GetValue()

			topP = wnd.topP.GetValue() / 100
			conf["topP"] = wnd.topP.GetValue()

			debug = wnd.debugModeCheckBox.IsChecked()
			conf["debug"] = debug

			stream = wnd.streamModeCheckBox.IsChecked()
			conf["stream"] = stream

		block.temperature = temperature
		block.topP = topP
		conversationMode = conf["conversationMode"]
		conf["conversationMode"] = wnd.conversationCheckBox.IsChecked()
		block.pathList = wnd.pathList

		if not 0 <= temperature <= model.maxTemperature * 100:
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid temperature")))
			return
		if not TOP_P_MIN <= topP <= TOP_P_MAX:
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid top P")))
			return
		messages = []
		if system:
			messages.append({"role": "system", "content": system})
		wnd.getMessages(messages)
		if prompt:
			messages.append({"role": "user", "content": prompt})
		params = {
			"model": model.name,
			"messages": messages,
			"temperature": temperature,
			"max_tokens": maxTokens,
			"top_p": topP,
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

	def run(self):
		wnd = self._notifyWindow
		client = wnd.client
		prompt = wnd.promptText.GetValue().strip()
		messages = []
		wnd.getMessages(messages)
		if wnd.pathList:
			content = [
				{"type": "text", "text": prompt}
			]
			content.extend(wnd.getImages())
			messages.append({
				"role": "user",
				"content": content
			})
		else:
			messages.append({"role": "user", "content": prompt})
		nbImages = 0
		for message in messages:
			if message["role"] == "user":
				for content in message["content"]:
					if not isinstance(content, dict):
						continue
					if content["type"] == "image_url":
						nbImages += 1
		if nbImages == 1:
			wnd.message(_("One image to analyze..."))
		elif nbImages > 1:
			wnd.message(_("%d images to analyze...") % nbImages)
		max_tokens = wnd.maxTokens.GetValue()
		try:
			description = describeFromImageFileList(
				client,
				messages=messages,
				max_tokens=max_tokens
			)
		except BaseException as err:
			wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			return
		wx.PostEvent(self._notifyWindow, ResultEvent(description))

	def abort(self):
		pass


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
	prompt = ""
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
	pathList = None


class OpenAIDlg(wx.Dialog):

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
		if conf["images"]["useCustomPrompt"]:
			DEFAULT_PROMPT_IMAGE_DESCRIPTION = conf["images"]["customPromptText"]
		else:
			DEFAULT_PROMPT_IMAGE_DESCRIPTION = _("Describe the images in as much detail as possible.")
		self.client = client
		self.conf = conf
		self.data = self.loadData()
		self._orig_data = self.data.copy() if isinstance(self.data, dict) else None
		self.blocks = []
		self.pathList = pathList
		self.previousPrompt = None
		self._lastSystem = None
		self._model_names = [model.name for model in MODELS]
		if self.conf["saveSystem"]:
			# If the user has chosen to save the system prompt, use the last system prompt used by the user as the default value, otherwise use the default system prompt.
			if "system" in self.data:
				self._lastSystem = self.data["system"]
			else:
				self._lastSystem = DEFAULT_SYSTEM_PROMPT
		else:
			# removes the system entry from data so that the last system prompt is not remembered when the user unchecks the save system prompt checkbox.
			self.data.pop("system", None)
		if not title:
			title = "Open AI - %s" % (
				_("organization") if conf["use_org"] else _("personal")
			)
		super().__init__(parent, title=title)

		self.conversationCheckBox = wx.CheckBox(
			parent=self,
			label=_("Conversati&on mode")
		)
		self.conversationCheckBox.SetValue(conf["conversationMode"])
		systemLabel = wx.StaticText(
			parent=self,
			label=_("S&ystem:")
		)
		self.systemText = wx.TextCtrl(
			parent=self,
			size=(550, -1),
			style=wx.TE_MULTILINE,
		)
		# Adds event handler to reset the system prompt to the default value when the user opens the context menu on the system prompt.
		self.systemText.Bind(wx.EVT_CONTEXT_MENU, self.onMenuSystem)
		# If the system prompt has been defined by the user, use it as the default value, otherwise use the default system prompt.
		if conf["saveSystem"]:
			self.systemText.SetValue(self._lastSystem)
		else:
			self.systemText.SetValue(DEFAULT_SYSTEM_PROMPT)

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
		models = [str(model) for model in MODELS]
		self.modelListBox = wx.ListBox(
			parent=self,
			choices=models,
			style=wx.LB_SINGLE
		)
		model = MODEL_VISION if self.pathList else conf["model"]
		idx = list(self._model_names).index(model) if model in self._model_names else 0
		self.modelListBox.SetSelection(idx)
		self.modelListBox.Bind(wx.EVT_LISTBOX, self.onModelChange)

		maxTokensLabel = wx.StaticText(
			parent=self,
			label=_("Maximum to&kens for the completion:")
		)
		self.maxTokens = wx.SpinCtrl(
			parent=self,
			min=0
		)

		if conf["advancedMode"]:
			temperatureLabel = wx.StaticText(
				parent=self,
				label=_("&Temperature:")
			)
			self.temperature = wx.SpinCtrl(
				parent=self
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

		self.onModelChange(None)
		sizer1 = wx.BoxSizer(wx.VERTICAL)
		sizer1.Add(self.conversationCheckBox, 0, wx.ALL, 5)
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
			label=_("Start &recording") + " (ctrl+r)"
		)
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onRecord)
		self.recordBtn.SetToolTip(_("Record audio from microphone"))

		self.transcribeFromFileBtn = wx.Button(
			parent=self,
			label=_("Transcribe from &audio file") + " (ctrl+shift+r)"
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
			label=_("&Vocalize the prompt") + " (ctrl+t)"
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

	def loadData(self):
		if not os.path.exists(DATA_JSON_FP):
			return {}
		try:
			with open(DATA_JSON_FP, 'r') as f :
				return json.loads(f.read())
		except BaseException as err:
			log.error(err)

	def saveData(self, force=False):
		if not force and self.data == self._orig_data:
			return
		with open(DATA_JSON_FP, "w") as f:
			f.write(json.dumps(self.data))

	def getCurrentModel(self):
		return MODELS[self.modelListBox.GetSelection()]

	def resetSystemPrompt(self, event):
		self.systemText.SetValue(DEFAULT_SYSTEM_PROMPT)
	def onMenuSystem(self, event):
		menu = wx.Menu()
		resetItem = menu.Append(wx.NewId(), _("Reset to default"))
		menu.AppendSeparator()
		menu.Append(wx.ID_UNDO)
		menu.Append(wx.ID_CUT)
		menu.Append(wx.ID_COPY)
		menu.Append(wx.ID_PASTE)
		menu.Append(wx.ID_SELECTALL)
		menu.Append(wx.ID_DELETE)

		self.Bind(wx.EVT_MENU, self.resetSystemPrompt, resetItem)
		self.systemText.PopupMenu(menu)
		menu.Destroy()
	def onModelChange(self, evt):
		model = self.getCurrentModel()
		self.maxTokens.SetRange(
			0,
			model.maxOutputToken if model.maxOutputToken > 1 else model.contextWindow
		)
		defaultMaxOutputToken = 512
		key_maxTokens = "maxTokens_%s" % model.name
		if (
			key_maxTokens in self.data
			and isinstance(self.data[key_maxTokens], int)
			and self.data[key_maxTokens] > 0
		):
			defaultMaxOutputToken = self.data[key_maxTokens]
		else:
			defaultMaxOutputToken = model.maxOutputToken // 2
			if defaultMaxOutputToken < 1:
				defaultMaxOutputToken  = model.contextWindow // 2
		if defaultMaxOutputToken < 1:
			defaultMaxOutputToken = 1024
		self.maxTokens.SetValue(defaultMaxOutputToken)
		if self.conf["advancedMode"]:
			self.temperature.SetRange(0, model.maxTemperature * 100)
			key_temperature = "temperature_%s" % model.name
			if key_temperature in self.data:
				self.temperature.SetValue(self.data[key_temperature])
			else:
				self.temperature.SetValue(model.defaultTemperature * 100) 

	def onOk(self, evt):
		if not self.promptText.GetValue().strip() and not self.pathList:
			self.promptText.SetFocus()
			return
		if self.worker:
			return
		model = self.getCurrentModel()
		if not model:
			gui.messageBox(
				_("Please select a model."),
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		if (
			model.name == MODEL_VISION
			and not self.conversationCheckBox.IsChecked()
			and not self.pathList
		):
			gui.messageBox(
				_("No image provided. Please use the Image Description button and select one or more images. Otherwise, please select another model."),
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		if model.name != MODEL_VISION and self.pathList:
			gui.messageBox(
				_("This model does not support image description. Please select the %s model.") % MODEL_VISION,
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		if (
			model.name == MODEL_VISION
			and not self.conf["images"]["resize"]
			and not self.conf["images"]["resizeInfoDisplayed"]
		):
			msg = _("Be aware that the add-on may auto-resize images before API submission to lower request sizes and costs. Adjust this feature in the Open AI settings if needed. This message won't show again.")
			gui.messageBox(
				msg,
				_("Open AI"),
				wx.OK|wx.ICON_INFORMATION
			)
			self.conf["images"]["resizeInfoDisplayed"] = True
		system = self.systemText.GetValue().strip()
		if self.conf["saveSystem"] and system != self._lastSystem:
			self.data["system"] = system
			self._lastSystem = system
		self.message(_("Processing, please wait..."))
		winsound.PlaySound(f"{ADDON_DIR}/sounds/progress.wav", winsound.SND_ASYNC|winsound.SND_LOOP)
		self.disableButtons()
		self.historyText.SetFocus()
		self.stopRequest = threading.Event()
		if self.pathList:
			self.modelListBox.SetSelection(
				self._model_names.index(MODEL_VISION)
			)
			self.worker = ImageDescriptionThread(self)
		else:
			self.worker = CompletionThread(self)
		self.worker.start()

	def onCancel(self, evt):
		self.saveData()
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
			historyBlock.prompt = self.promptText.GetValue().strip()
			"""
			# TODO: Create a special history block for attached images and additional information
			if self.pathList:
				historyBlock.prompt += "\n\n" + _("Attached images:")
				for path in self.pathList:
					historyBlock.prompt += f"\n- \"{path}\""
			"""
			model = self.getCurrentModel()
			historyBlock.model = model.name
			if self.conf["advancedMode"]:
				historyBlock.temperature = self.temperature.GetValue() / 100
				historyBlock.topP = self.topP.GetValue() / 100
			else:
				historyBlock.temperature = model.defaultTemperature
				historyBlock.topP = self.conf["topP"] / 100
			historyBlock.maxTokens = self.maxTokens.GetValue()
			historyBlock.n = 1 # self.n.GetValue()
			historyBlock.response = event.data
			historyBlock.responseText = event.data.message.content
			historyBlock.responseTerminated = True
			historyBlock.pathList = self.pathList
			self.pathList = None
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
				block.segmentPrompt = TextSegment(self.historyText, block.prompt + "\n", block)
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
		self.addEntry(accelEntries, wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("r"), self.onRecordFromFilePath)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("i"), self.onImageDescriptionFromFilePath)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("u"), self.onImageDescriptionFromURL)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("t"), self.onTextToSpeech)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.SetAcceleratorTable(accelTable)

	def getImages(
		self,
		pathList: list = None
	) -> list:
		conf = self.conf
		if not pathList:
			pathList = self.pathList
		images = []
		for path in pathList:
			url_re = re.compile(r"^https?://")
			if url_re.match(path):
				images.append({"type": "image_url", "image_url": {"url": path}})
			elif os.path.isfile(path):
				if conf["images"]["resize"]:
					path_ = os.path.join(DATA_DIR, "last_resized.jpg")
					resize_image(
						path,
						max_width=conf["images"]["maxWidth"],
						max_height=conf["images"]["maxHeight"],
						quality=conf["images"]["quality"],
						target=path_
					)
					path = path_
				base64_image = encode_image(path)
				format = path.split(".")[-1]
				mime_type = f"image/{format}"
				images.append({
					"type": "image_url",
					"image_url": {
						"url": f"data:{mime_type};base64,{base64_image}"
					}
				})
			else:
				raise ValueError(f"Invalid path: {path}")
				break
		return images

	def getMessages(
		self,
		messages: list
	) -> list:
		model = self.getCurrentModel()
		if not self.conversationCheckBox.IsChecked():
			return messages
		block = self.firstBlock
		while block is not None:
			if block.prompt:
				if block.pathList:
					content = [
						{"type": "text", "text": block.prompt}
					]
					content.extend(self.getImages(block.pathList))
					messages.append({
						"role": "user",
						"content": content
					})
				else:
					messages.append({"role": "user", "content": block.prompt})
			if block.responseText:
				messages.append({"role": "system", "content": block.responseText})
			block = block.next

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
			block.segmentBreakLine.delete()
		block.segmentPromptLabel.delete()
		block.segmentPrompt.delete()
		block.segmentResponseLabel.delete()
		block.segmentResponse.delete()

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
		"""
		Display a menu to select the source of the image.
		"""
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("From f&ile path...") + " (Ctrl+i)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromFilePath, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("From &URL...") + " (Ctrl+u)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromURL, id=item_id)
		self.PopupMenu(menu)
		menu.Destroy()

	def onImageDescriptionFromFilePath(self, evt):
		"""
		Open a file dialog to select one or more images.
		"""
		if not self.pathList:
			self.pathList = []
		dlg = wx.FileDialog(
			None,
			message=_("Select image files"),
			defaultFile="",
			wildcard=_("Image files") + " (*.png;*.jpeg;*.jpg;*.gif)|*.png;*.jpeg;*.jpg;*.gif",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		paths = dlg.GetPaths()
		if not paths:
			return
		self.pathList.extend(paths)
		model_name = self.getCurrentModel().name
		if model_name != MODEL_VISION:
			self.modelListBox.SetSelection(
				self._model_names.index(MODEL_VISION)
			)
		if not self.promptText.GetValue().strip():
			self.promptText.SetValue(DEFAULT_PROMPT_IMAGE_DESCRIPTION)
		self.promptText.SetFocus()

	def onImageDescriptionFromURL(self, evt):
		"""
		Open a dialog to enter an image URL.
		"""
		dlg = wx.TextEntryDialog(
			None,
			message=_("Enter image URL"),
			caption=_("Open AI"),
			style=wx.OK|wx.CANCEL
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		url = dlg.GetValue().strip()
		if not url:
			return
		url_pattern = re.compile(
			r"^(?:http)s?://(?:[A-Z0-9-]+\.)+[A-Z]{2,6}(?::\d+)?(?:/?|[/?]\S+)$",
			re.IGNORECASE
		)
		if re.match(url_pattern, url) is None:
			gui.messageBox(
				_("Invalid URL, bad format."),
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		try:
			import urllib.request
			r = urllib.request.urlopen(url)
		except urllib.error.HTTPError as err:
			gui.messageBox(
				_("Invalid URL, HTTP error: %s.") % err,
				_("Open AI"),
				wx.OK|wx.ICON_ERROR
			)
			return
		if not self.pathList:
			self.pathList = []
		self.pathList.append(url)
		self.modelListBox.SetSelection(
			self._model_names.index(MODEL_VISION)
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
		self.worker = RecordThread(self.client, self)
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
		self.worker = RecordThread(self.client, self, filename)
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
		self.recordBtn.SetLabel(
			_("Start &recording") + " (Ctrl+R)"
		)
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
