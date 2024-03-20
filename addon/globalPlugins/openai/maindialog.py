import datetime
import json
import os
import re
import speech
import sys
import threading
import time
import winsound
import gui
import wx
from enum import Enum

import addonHandler
import api
import braille
import config
import controlTypes
import queueHandler
import speech
import tones
import ui
from logHandler import log

from . import apikeymanager
from .consts import (
	ADDON_DIR, BASE_URLs, DATA_DIR,
	LIBS_DIR_PY,
	MODELS, DEFAULT_MODEL_VISION,
	TOP_P_MIN, TOP_P_MAX,
	N_MIN, N_MAX,
	DEFAULT_SYSTEM_PROMPT
)
from .imagehelper import (
	describeFromImageFileList,
	encode_image,
	get_image_dimensions,
	resize_image,
)
from .model import getOpenRouterModels
from .recordthread import RecordThread, WhisperTranscription
from .resultevent import ResultEvent, EVT_RESULT_ID

sys.path.insert(0, LIBS_DIR_PY)
import openai
import markdown2
sys.path.remove(LIBS_DIR_PY)

addonHandler.initTranslation()

TTS_FILE_NAME = os.path.join(DATA_DIR, "tts.wav")
DATA_JSON_FP = os.path.join(DATA_DIR, "data.json")
URL_PATTERN = re.compile(r"^(?:http)s?://(?:[A-Z0-9-]+\.)+[A-Z]{2,6}(?::\d+)?(?:/?|[/?]\S+)$", re.IGNORECASE)
SND_CHAT_RESPONSE_PENDING = os.path.join(
	ADDON_DIR, "sounds", "chatResponsePending.wav"
)
SND_CHAT_RESPONSE_RECEIVED = os.path.join(
	ADDON_DIR, "sounds", "chatResponseReceived.wav"
)
SND_CHAT_RESPONSE_SENT = os.path.join(
	ADDON_DIR, "sounds", "chatRequestSent.wav"
)
SND_PROGRESS = os.path.join(
	ADDON_DIR, "sounds", "progress.wav"
)
# Translators: This is a message emitted by the add-on when an operation is in progress.
PROCESSING_MSG = _("Please wait...")
RESP_AUDIO_FORMATS = ("json", "srt", "vtt")
RESP_AUDIO_FORMATS_LABELS = (
	_("Text"),
	_("SubRip (SRT)"),
	_("Web Video Text Tracks (VTT)")
)

addToSession = None

def EVT_RESULT(win, func):
	win.Connect(-1, -1, EVT_RESULT_ID, func)


def copyToClipAsHTML(html_content):
	html_data_object = wx.HTMLDataObject()
	html_data_object.SetHTML(html_content)
	if wx.TheClipboard.Open():
		wx.TheClipboard.Clear()
		wx.TheClipboard.SetData(html_data_object)
		wx.TheClipboard.Close()
	else:
		raise RuntimeError("Unable to open the clipboard")


def get_display_size(size):
	if size < 1024:
		return f"{size} B"
	if size < 1024 * 1024:
		return f"{size / 1024:.2f} KB"
	return f"{size / 1024 / 1024:.2f} MB"


class ImageFileTypes(Enum):

	UNKNOWN = 0
	IMAGE_LOCAL = 1
	IMAGE_URL = 2


class ImageFile:

	def __init__(
		self,
		path: str,
		name: str=None,
		description: str=None,
		size: int=-1,
		dimensions: tuple=None
	):
		if not isinstance(path, str):
			raise TypeError("path must be a string")
		self.path = path
		self.type = self._get_type()
		self.name = name or self._get_name()
		self.description = description
		if size and size > 0:
			self.size = get_display_size(size)
		else:
			self.size = self._get_size()
		self.dimensions = dimensions or self._get_dimensions()

	def _get_type(self):
		if os.path.exists(self.path):
			return ImageFileTypes.IMAGE_LOCAL
		if re.match(
			URL_PATTERN,
			self.path
		):
			return ImageFileTypes.IMAGE_URL
		return ImageFileTypes.UNKNOWN

	def _get_name(self):
		if self.type == ImageFileTypes.IMAGE_LOCAL:
			return os.path.basename(self.path)
		if self.type == ImageFileTypes.IMAGE_URL:
			return self.path.split("/")[-1]
		return "N/A"

	def _get_size(self):
		if self.type == ImageFileTypes.IMAGE_LOCAL:
			size = os.path.getsize(self.path)
			return get_display_size(size)
		return "N/A"

	def _get_dimensions(self):
		if self.type == ImageFileTypes.IMAGE_LOCAL:
			return get_image_dimensions(self.path)
		return None

	def __str__(self):
		return f"{self.name} ({self.path}, {self.size}, {self.dimensions}, {self.description})"


class CompletionThread(threading.Thread):

	def __init__(self, notifyWindow):
		threading.Thread.__init__(self)
		self._notifyWindow = notifyWindow
		self._wantAbort = False
		self.lastTime = int(time.time())

	def run(self):
		wnd = self._notifyWindow
		client = wnd.client
		conf = wnd.conf
		data = wnd.data
		block = HistoryBlock()
		system = wnd.systemTextCtrl.GetValue().strip()
		block.system = system
		prompt = wnd.promptTextCtrl.GetValue().strip()
		block.prompt = prompt
		model = wnd.getCurrentModel()
		block.model = model.id
		conf["modelVision" if model.vision else "model"] = model.id
		stream = conf["stream"]
		debug = conf["debug"]
		maxTokens = wnd.maxTokensSpinCtrl.GetValue()
		block.maxTokens = maxTokens
		key_maxTokens = "maxTokens_%s" % model.id
		data[key_maxTokens] = maxTokens
		temperature = 1
		topP = 1
		if conf["advancedMode"]:
			temperature = wnd.temperatureSpinCtrl.GetValue() / 100
			key_temperature = "temperature_%s" % model.id
			data[key_temperature] = wnd.temperatureSpinCtrl.GetValue()

			topP = wnd.topPSpinCtrl.GetValue() / 100
			conf["topP"] = wnd.topPSpinCtrl.GetValue()

			debug = wnd.debugModeCheckBox.IsChecked()
			conf["debug"] = debug

			stream = wnd.streamModeCheckBox.IsChecked()
			conf["stream"] = stream

		block.temperature = temperature
		block.topP = topP
		conversationMode = conf["conversationMode"]
		conf["conversationMode"] = wnd.conversationCheckBox.IsChecked()
		block.pathList = wnd.pathList.copy()

		if not 0 <= temperature <= model.maxTemperature * 100:
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid temperature")))
			return
		if not TOP_P_MIN <= topP <= TOP_P_MAX:
			wx.PostEvent(self._notifyWindow, ResultEvent(_("Invalid top P")))
			return
		messages = self._getMessages(system, prompt)
		nbImages = 0
		for message in messages:
			if (
				message["role"] == "user"
				and not isinstance(message["content"], str)
			):
				for content in message["content"]:
					if content["type"] == "image_url":
						nbImages += 1
		msg = ""
		if nbImages == 1:
			# Translators: This is a message displayed when uploading one image to the API.
			msg = _("Uploading one image, please wait...")
		elif nbImages > 1:
			# Translators: This is a message displayed when uploading multiple images to the API.
			msg = _("Uploading %d images, please wait...") % nbImages
		else:
			msg = PROCESSING_MSG
		wnd.message(msg)
		if conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC|winsound.SND_LOOP)

		manager = apikeymanager.get(
			model.provider
		)
		client.base_url =  BASE_URLs[model.provider]
		client.api_key = manager.get_api_key()
		client.organization = manager.get_organization_key()
		params = {
			"model": model.id,
			"messages": messages,
			"temperature": temperature,
			"max_tokens": maxTokens,
			"top_p": topP,
			"stream": stream
		}

		if debug:
			log.info("Client base URL: %s" % client.base_url)
			if nbImages:
				log.info(f"{nbImages} images")
			log.info(f"{json.dumps(params, indent=2, ensure_ascii=False)}")

		try:
			response = client.chat.completions.create(**params)
			if conf["chatFeedback"]["sndResponseSent"]:
				winsound.PlaySound(SND_CHAT_RESPONSE_SENT, winsound.SND_ASYNC)
		except BaseException as err:
			wx.PostEvent(self._notifyWindow, ResultEvent(err))
			return
		if wnd.lastBlock is None:
			wnd.firstBlock = wnd.lastBlock = block
		else:
			wnd.lastBlock.next = block
			block.previous = wnd.lastBlock
			wnd.lastBlock = block
		wnd.previousPrompt = wnd.promptTextCtrl.GetValue()
		wnd.promptTextCtrl.Clear()

		if stream:
			self._responseWithStream(response, block, debug)
		else:
			self._responseWithoutStream(response, block, debug)
		wnd.pathList.clear()
		wx.PostEvent(self._notifyWindow, ResultEvent())

	def _getMessages(self, system=None, prompt=None):
		wnd = self._notifyWindow
		messages = []
		if system:
			messages.append({"role": "system", "content": system})
		wnd.getMessages(messages)
		if wnd.pathList:
			images = wnd.getImages(prompt=prompt)
			if images:
				messages.append({"role": "user", "content": images})
		elif prompt:
			messages.append({"role": "user", "content": prompt})
		return messages

	def abort(self):
		self._wantAbort = True

	def _responseWithStream(self, response, block, debug=False):
		wnd = self._notifyWindow
		text = ""
		speechBuffer = ""
		for i, event in enumerate(response):
			if time.time() - self.lastTime > 4:
				self.lastTime = int(time.time())
				if wnd.conf["chatFeedback"]["sndResponsePending"]:
					winsound.PlaySound(SND_CHAT_RESPONSE_PENDING, winsound.SND_ASYNC)
			if wnd.stopRequest.is_set():
				break
			delta = event.choices[0].delta
			finish = event.choices[0].finish_reason
			text = ""
			if delta and delta.content:
				text = delta.content
				speechBuffer += text
				if (
					speechBuffer.endswith('\n')
					or speechBuffer.endswith(". ")
					or speechBuffer.endswith("? ")
					or speechBuffer.endswith("! ")
					or speechBuffer.endswith(": ")
				):
					if speechBuffer.strip():
						wnd.message(speechBuffer, speechOnly=True, onPromptFieldOnly=True)
					speechBuffer = ""
			block.responseText += text
		if speechBuffer:
			wnd.message(speechBuffer, speechOnly=True, onPromptFieldOnly=True)
		block.responseTerminated = True

	def _responseWithoutStream(self, response, block, debug=False):
		wnd = self._notifyWindow
		text = ""
		if isinstance(
			response, (
				openai.types.chat.chat_completion.Choice,
				openai.types.chat.chat_completion.ChatCompletion
			)
		):
			for i, choice in enumerate(response.choices):
				if self._wantAbort:
					break
				text += choice.message.content
		else:
			responseType = type(response)
			raise TypeError(f"Invalid response type: {responseType}")
		block.responseText += text
		wnd.message(text, speechOnly=True, onPromptFieldOnly=True)
		block.responseTerminated = True


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
		provider = "OpenAI"
		manager = apikeymanager.get(provider)
		client.base_url =  BASE_URLs[provider]
		client.api_key = manager.get_api_key()
		client.organization = manager.get_organization_key()
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
			wx.PostEvent(self._notifyWindow, ResultEvent(err))
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
		global addToSession
		if not client or not conf:
			return
		self.client = client
		self._base_url = client.base_url
		self._api_key = client.api_key
		self._organization = client.organization
		self.conf = conf
		self.data = self.loadData()
		self._orig_data = self.data.copy() if isinstance(self.data, dict) else None
		self._historyPath = None
		self.blocks = []
		self._models = MODELS.copy()
		if apikeymanager.get("OpenRouter").isReady():
			self._models.extend(getOpenRouterModels())
		self.pathList = []
		self._fileToRemoveAfter = []
		self.lastFocusedItem = None
		self.historyObj = None
		self.foregroundObj = None
		if pathList:
			addToSession = self
			for path in pathList:
				self.addImageToList(
					path,
					removeAfter=True
				)
		self.previousPrompt = None
		self._lastSystem = None
		self._model_ids = [model.id for model in self._models]
		if self.conf["saveSystem"]:
			# If the user has chosen to save the system prompt, use the last system prompt used by the user as the default value, otherwise use the default system prompt.
			if "system" in self.data:
				self._lastSystem = self.data["system"]
			else:
				self._lastSystem = DEFAULT_SYSTEM_PROMPT
		else:
			# removes the system entry from data so that the last system prompt is not remembered when the user unchecks the save system prompt checkbox.
			self.data.pop("system", None)
		l = []
		for manager in apikeymanager._managers.values():
			if not manager.isReady():
				continue
			e = manager.provider
			organization = manager.get_api_key(use_org=True)
			if organization and organization != ":=":
				e += " (organization)"
			else:
				e += " (personal)"
			l.append(e)
		title = ", ".join(l)
		super().__init__(parent, title=title)

		self.Bind(wx.EVT_CHILD_FOCUS, self.onSetFocus)
		mainSizer = wx.BoxSizer(wx.VERTICAL)

		self.conversationCheckBox = wx.CheckBox(
			self,
			# Translators: This is the label for the conversation mode checkbox in the main dialog.
			label=_("&Conversation mode")
		)
		self.conversationCheckBox.SetValue(conf["conversationMode"])
		mainSizer.Add(self.conversationCheckBox, 0, wx.ALL, 5)

		systemLabel = wx.StaticText(
			self,
			# Translators: This is the label for the system prompt text control in the main dialog.
			label=_("S&ystem prompt:")
		)
		self.systemTextCtrl = wx.TextCtrl(
			self,
			size=(700, -1),
			style=wx.TE_MULTILINE,
		)
		mainSizer.Add(systemLabel, 0, wx.ALL, 5)
		mainSizer.Add(self.systemTextCtrl, 0, wx.ALL, 5)
		# Adds event handler to reset the system prompt to the default value when the user opens the context menu on the system prompt.
		self.systemTextCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onSystemContextMenu)
		# If the system prompt has been defined by the user, use it as the default value, otherwise use the default system prompt.
		if conf["saveSystem"]:
			self.systemTextCtrl.SetValue(self._lastSystem)
		else:
			self.systemTextCtrl.SetValue(DEFAULT_SYSTEM_PROMPT)

		messagesLabel = wx.StaticText(
			self,
			# Translators: This is the label for the messages text control in the main dialog.
			label=_("&Messages:")
		)
		self.messagesTextCtrl = wx.TextCtrl(
			self,
			# Translators: This is the label for the messages text control in the main dialog.
			style=wx.TE_MULTILINE | wx.TE_READONLY,
			size=(700, -1)
		)
		mainSizer.Add(messagesLabel, 0, wx.ALL, 5)
		mainSizer.Add(self.messagesTextCtrl, 0, wx.ALL, 5)
		self.messagesTextCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onHistoryContextMenu)

		promptLabel = wx.StaticText(
			self,
			# Translators: This is the label for the prompt text control in the main dialog.
			label=_("&Prompt:")
		)
		self.promptTextCtrl = wx.TextCtrl(
			self,
			size=(700, -1),
		style=wx.TE_MULTILINE
		)
		mainSizer.Add(promptLabel, 0, wx.ALL, 5)
		mainSizer.Add(self.promptTextCtrl, 0, wx.ALL, 5)
		self.promptTextCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onPromptContextMenu)

		self.imagesLabel = wx.StaticText(
			self,
			# Translators: This is the label for the images list control in the main dialog.
			label=_("Images:")
		)
		self.imagesListCtrl = wx.ListCtrl(
			self,
			style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
			size=(700, 200)
		)
		self.imagesListCtrl.InsertColumn(
			0,
			# Translators: This is the label for the name column in the image list control in the main dialog.
			_("name")
		)
		self.imagesListCtrl.InsertColumn(
			1,
			# Translators: This is the label for the path column in the image list control in the main dialog.
			_("path")
		)
		self.imagesListCtrl.InsertColumn(
			2,
			# Translators: This is the label for the size column in the image list control in the main dialog.
			_("size")
		)
		self.imagesListCtrl.InsertColumn(
			3,
			# Translators: This is the label for the dimensions column in the image list control in the main dialog.
			_("Dimensions")
		)
		self.imagesListCtrl.InsertColumn(
			4,
			# Translators: This is the label for the description column in the image list control in the main dialog.
			_("description")
		)
		self.imagesListCtrl.SetColumnWidth(0, 100)
		self.imagesListCtrl.SetColumnWidth(1, 200)
		self.imagesListCtrl.SetColumnWidth(2, 100)
		self.imagesListCtrl.SetColumnWidth(3, 100)
		self.imagesListCtrl.SetColumnWidth(4, 200)
		mainSizer.Add(self.imagesLabel, 0, wx.ALL, 5)
		mainSizer.Add(self.imagesListCtrl, 0, wx.ALL, 5)
		self.imagesListCtrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.onImageListContextMenu)
		self.imagesListCtrl.Bind(wx.EVT_KEY_DOWN, self.onImageListKeyDown)
		self.imagesListCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onImageListContextMenu)
		self.imagesListCtrl.Bind(wx.EVT_RIGHT_UP, self.onImageListContextMenu)

		if self.pathList:
			self.promptTextCtrl.SetValue(
				self.getDefaultImageDescriptionsPrompt()
			)
		self.updateImageList()

		modelsLabel = wx.StaticText(
			self,
			# Translators: This is the label for the model list box in the main dialog.
			label=_("M&odel:")
		)
		self.modelsListCtrl = wx.ListCtrl(
			self,
			style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
			size=(700, 200)
		)
		self.modelsListCtrl.InsertColumn(
			0,
			# Translators: This is the label for the model name column in the model list control in the main dialog.
			_("Name")
		)
		self.modelsListCtrl.InsertColumn(
			1,
			# Translators: This is the label for the model provider column in the model list control in the main dialog.
			_("Provider")
		)
		self.modelsListCtrl.InsertColumn(
			2,
			# Translators: This is the label for the model ID column in the model list control in the main dialog.
			_("ID")
		)
		self.modelsListCtrl.InsertColumn(
			3,
			# Translators: This is the label for the model context window column in the model list control in the main dialog.
			_("Context window")
		)
		self.modelsListCtrl.InsertColumn(
			4,
			# Translators: This is the label for the model max output token column in the model list control in the main dialog.
			_("Max output token")
		)
		self.modelsListCtrl.SetColumnWidth(0, 250)
		self.modelsListCtrl.SetColumnWidth(1, 125)
		self.modelsListCtrl.SetColumnWidth(2, 100)
		self.modelsListCtrl.SetColumnWidth(3, 100)
		self.modelsListCtrl.SetColumnWidth(4, 100)
		self.modelsListCtrl.Bind(wx.EVT_KEY_DOWN, self.onModelKeyDown)
		self.modelsListCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onModelContextMenu)
		self.modelsListCtrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.onModelContextMenu)
		self.modelsListCtrl.Bind(wx.EVT_RIGHT_UP, self.onModelContextMenu)

		for i, model in enumerate(self._models):
			self.modelsListCtrl.InsertItem(i, model.name)
			self.modelsListCtrl.SetItem(i, 1, model.provider)
			self.modelsListCtrl.SetItem(i, 2, model.id)
			self.modelsListCtrl.SetItem(i, 3, str(model.contextWindow))
			self.modelsListCtrl.SetItem(
				i,
				4,
				str(model.maxOutputToken) if model.maxOutputToken > 1 else ""
			)
		model_id = conf["modelVision" if self.pathList else "model"]
		model_index = self._getModelIndex(model_id)
		self.modelsListCtrl.SetItemState(
			model_index,
			wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
			wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED
		)
		self.modelsListCtrl.EnsureVisible(model_index)
		mainSizer.Add(modelsLabel, 0, wx.ALL, 5)
		mainSizer.Add(self.modelsListCtrl, 0, wx.ALL, 5)

		maxTokensLabel = wx.StaticText(
			self,
			# Translators: This is the label for the max tokens spin control in the main dialog.
			label=_("Max to&kens:")
		)
		self.maxTokensSpinCtrl = wx.SpinCtrl(
			self,
			min=0
		)
		mainSizer.Add(maxTokensLabel, 0, wx.ALL, 5)
		mainSizer.Add(self.maxTokensSpinCtrl, 0, wx.ALL, 5)

		if conf["advancedMode"]:
			temperatureLabel = wx.StaticText(
				self,
				# Translators: This is the label for the temperature spin control in the main dialog.
				label=_("&Temperature:")
			)
			self.temperatureSpinCtrl = wx.SpinCtrl(
				self,
				min=0,
				max=200
			)
			mainSizer.Add(temperatureLabel, 0, wx.ALL, 5)
			mainSizer.Add(self.temperatureSpinCtrl, 0, wx.ALL, 5)

			topPLabel = wx.StaticText(
				self,
				# Translators: This is the label for the top P spin control in the main dialog.
				label=_("Pro&bability Mass (top P):")
			)
			self.topPSpinCtrl = wx.SpinCtrl(
				self,
				min=TOP_P_MIN,
				max=TOP_P_MAX,
				initial=conf["topP"]
			)
			mainSizer.Add(topPLabel, 0, wx.ALL, 5)
			mainSizer.Add(self.topPSpinCtrl, 0, wx.ALL, 5)

			self.whisperResponseFormatLabel = wx.StaticText(
				self,
				label=_("&Whisper Response Format:")
			)
			self.whisperResponseFormatListBox = wx.Choice(
				self,
				choices=RESP_AUDIO_FORMATS_LABELS
			)
			self.whisperResponseFormatListBox.SetSelection(0)
			mainSizer.Add(self.whisperResponseFormatLabel, 0, wx.ALL, 5)
			mainSizer.Add(self.whisperResponseFormatListBox, 0, wx.ALL, 5)

			self.streamModeCheckBox = wx.CheckBox(
				self,
				label=_("&Stream mode")
			)
			self.streamModeCheckBox.SetValue(conf["stream"])
			mainSizer.Add(self.streamModeCheckBox, 0, wx.ALL, 5)

			self.debugModeCheckBox = wx.CheckBox(
				self,
				label=_("Debu&g mode")
			)
			self.debugModeCheckBox.SetValue(conf["debug"])
			mainSizer.Add(self.debugModeCheckBox, 0, wx.ALL, 5)

		self.onModelChange(None)

		buttonsSizer = wx.BoxSizer(wx.HORIZONTAL)

		self.recordBtn = wx.Button(
			self,
			# Translators: This is the label for the record button in the main dialog.
			label=_("Start &recording") + " (Ctrl+R)"
		)
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onRecord)
		self.recordBtn.SetToolTip(_("Record audio from microphone"))

		self.transcribeFromFileBtn = wx.Button(
			self,
			# Translators: This is the label for the transcribe from audio file button in the main dialog.
			label=_("Transcribe from &audio file") + " (Ctrl+Shift+R)"
		)
		self.transcribeFromFileBtn.Bind(wx.EVT_BUTTON, self.onRecordFromFilePath)
		self.transcribeFromFileBtn.SetToolTip(_("Transcribe audio from a file path"))

		self.imageDescriptionBtn = wx.Button(
			self,
			# Translators: This is the label for the image description button in the main dialog.
			label=_("&Image description")
		)
		self.imageDescriptionBtn.Bind(wx.EVT_BUTTON, self.onImageDescription)
		self.imageDescriptionBtn.SetToolTip(_("Describe an image from a file path or an URL"))

		self.TTSBtn = wx.Button(
			self,
			# Translators: This is the label for the text to speech button in the main dialog.
			label=_("&Vocalize the prompt") + " (Ctrl+T)"
		)
		self.TTSBtn.Bind(wx.EVT_BUTTON, self.onTextToSpeech)

		for btn in (
			self.recordBtn,
			self.transcribeFromFileBtn,
			self.imageDescriptionBtn,
			self.TTSBtn
		):
			buttonsSizer.Add(btn, 0, wx.ALL, 5)
		mainSizer.Add(buttonsSizer, 0, wx.ALL, 5)

		submitCancelSizer = wx.BoxSizer(wx.HORIZONTAL)

		self.submitBtn = wx.Button(
			self,
			id=wx.ID_OK,
			# Translators: This is the label for the submit button in the main dialog.
			label=_("Submit") + " (Ctrl+Enter)"
		)
		self.submitBtn.Bind(wx.EVT_BUTTON, self.onSubmit)
		self.submitBtn.SetDefault()
		submitCancelSizer.Add(self.submitBtn, 0, wx.ALL, 5)

		self.cancelBtn = wx.Button(
			self,
			id=wx.ID_CANCEL
		)
		self.cancelBtn.Bind(wx.EVT_BUTTON, self.onCancel)
		submitCancelSizer.Add(self.cancelBtn, 0, wx.ALL, 5)

		mainSizer.Add(submitCancelSizer, 0, wx.ALL | wx.ALIGN_CENTER, 5)

		self.SetSizerAndFit(mainSizer)
		mainSizer.SetSizeHints(self)
		self.CentreOnParent(wx.BOTH)

		self.addShortcuts()
		self.promptTextCtrl.SetFocus()
		EVT_RESULT(self, self.OnResult)
		self.worker = None
		self.firstBlock = None
		self.lastBlock = None
		self.timer = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self.onTimer, self.timer)
		self.timer.Start (100)
		self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
		self.Bind(wx.EVT_CLOSE, self.onCancel)


	def _getModelIndex(self, model_id):
		return list(self._model_ids).index(model_id) if model_id in self._model_ids else (
			list(self._model_ids).index(DEFAULT_MODEL_VISION) if self.pathList else 0
		)

	def addImageToList(
		self,
		path,
		removeAfter=False
	):
		if not path:
			return
		if isinstance(path, ImageFile):
			self.pathList.append(path)
		elif isinstance(path, str):
			self.pathList.append(
				ImageFile(
					path
				)
			)
		elif (
			isinstance(path, tuple)
			and len(path) == 2
		):
			location, name = path
			self.pathList.append(
				ImageFile(
					location,
					name=name
				)
			)
			if removeAfter:
				self._fileToRemoveAfter.append(location)
		else:
			raise ValueError(f"Invalid path: {path}")

	def getDefaultImageDescriptionsPrompt(self):
		if self.conf["images"]["useCustomPrompt"]:
			return self.conf["images"]["customPromptText"]
		return _("Describe the images in as much detail as possible.")

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
		return self._models[self.modelsListCtrl.GetFocusedItem()]

	def onResetSystemPrompt(self, event):
		self.systemTextCtrl.SetValue(DEFAULT_SYSTEM_PROMPT)
	def onDelete(self, event):
		self.systemTextCtrl.SetValue('')

	def addStandardMenuOptions(self, menu):
		menu.Append(wx.ID_UNDO)
		menu.Append(wx.ID_REDO)
		menu.AppendSeparator()
		menu.Append(wx.ID_CUT)
		menu.Append(wx.ID_COPY)
		menu.Append(wx.ID_PASTE)
		menu.Append(wx.ID_DELETE)
		menu.AppendSeparator()
		menu.Append(wx.ID_SELECTALL)
		self.Bind(wx.EVT_MENU, self.onDelete, id=wx.ID_DELETE)

	def onModelChange(self, evt):
		model = self.getCurrentModel()
		self.maxTokensSpinCtrl.SetRange(
			0,
			model.maxOutputToken if model.maxOutputToken > 1 else model.contextWindow
		)
		defaultMaxOutputToken = 512
		key_maxTokens = "maxTokens_%s" % model.id
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
		self.maxTokensSpinCtrl.SetValue(defaultMaxOutputToken)
		if self.conf["advancedMode"]:
			self.temperatureSpinCtrl.SetRange(
				0,
				int(model.maxTemperature * 100)
			)
			key_temperature = "temperature_%s" % model.id
			if key_temperature in self.data:
				self.temperatureSpinCtrl.SetValue(
					int(self.data[key_temperature])
				)
			else:
				self.temperatureSpinCtrl.SetValue(
					int(model.defaultTemperature * 100)
				)

	def showModelDetails(self, evt=None):
		model = self.getCurrentModel()
		details = (
			"<h1>%s (%s)</h1>"
			"<blockquote>%s</blockquote>"
		) % (
			model.name,
			model.id,
			model.description
		)
		if model.extraInfo:
			details += "<ul>"
			extraInfo = model.extraInfo
			if "pricing" in extraInfo:
				for k, v in extraInfo["pricing"].items():
					if re.match("^[0-9.]+$", v) and float(v) > 0:
						details += f"<li><b>{k}</b> cost: {v}/token.</li>"

			details += "</ul>"

		ui.browseableMessage(
			details,
			_("Model details"),
			True
		)

	def onModelKeyDown(self, evt):
		if evt.GetKeyCode() == wx.WXK_SPACE:
			self.showModelDetails()
		else:
			evt.Skip()
	def onSubmit(self, evt):
		if not self.promptTextCtrl.GetValue().strip() and not self.pathList:
			self.promptTextCtrl.SetFocus()
			return
		if self.worker:
			return
		model = self.getCurrentModel()
		if not model:
			gui.messageBox(
				_("Please select a model."),
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return
		if not apikeymanager.get(model.provider).isReady():
			gui.messageBox(
				_("This model is only available with the %s provider. Please provide an API key for this provider in the add-on settings. Otherwise, please select another model with a different provider.") % (
					model.provider
				),
				_("No API key for %s") % model.provider,
				wx.OK | wx.ICON_ERROR
			)
			return

		if (
			model.vision
			and not self.conversationCheckBox.IsChecked()
			and not self.pathList
		):
			gui.messageBox(
				_("Please use the Image Description button and select one or more images. Otherwise, please select another model."),
				_("No image provided"),
				wx.OK | wx.ICON_ERROR
			)
			return
		if not model.vision and self.pathList:
			visionModels = [model.id for model in self._models if model.vision]
			gui.messageBox(
				_("This model (%s) does not support image description. Please select one of the following models: %s.") % (
					model.id,
					", ".join(visionModels)
				),
				_("Invalid model"),
				wx.OK | wx.ICON_ERROR
			)
			return
		if (
			model.vision
			and not self.conf["images"]["resize"]
			and not self.conf["images"]["resizeInfoDisplayed"]
		):
			msg = _("Be aware that the add-on may auto-resize images before API submission to lower request sizes and costs. Adjust this feature in the Open AI settings if needed. This message won't show again.")
			gui.messageBox(
				msg,
				_("Image resizing"),
				wx.OK | wx.ICON_INFORMATION
			)
			self.conf["images"]["resizeInfoDisplayed"] = True
		system = self.systemTextCtrl.GetValue().strip()
		if self.conf["saveSystem"] and system != self._lastSystem:
			self.data["system"] = system
			self._lastSystem = system
		self.disableControls()
		api.processPendingEvents()
		self.foregroundObj = api.getForegroundObject()
		if not self.foregroundObj:
			log.error("Unable to retrieve the foreground object")
		try:
			obj = self.foregroundObj.children[4]
			if obj and obj.role == controlTypes.ROLE_EDITABLETEXT:
				self.historyObj = obj
			else:
				self.historyObj = None
				log.error("Unable to find the history object")
		except BaseException as err:
			log.error(err)
			self.historyObj = None
		self.stopRequest = threading.Event()
		self.worker = CompletionThread(self)
		self.worker.start()

	def onCancel(self, evt):
		global addToSession
		if addToSession and addToSession is self:
			addToSession = None
		# remove files marked for deletion
		for path in self._fileToRemoveAfter:
			if os.path.exists(path):
				try:
					os.remove(path)
				except BaseException as err:
					log.error(err)
					gui.messageBox(
						_("Unable to delete the file: %s\nPlease remove it manually.") % path,
						"Open AI",
						wx.OK | wx.ICON_ERROR
					)
		self.saveData()
		if self.worker:
			self.worker.abort()
			self.worker = None
		self.timer.Stop()
		self.Destroy()

	def OnResult(self, event):
		if self.conf["chatFeedback"]["sndResponseReceived"]:
			winsound.PlaySound(SND_CHAT_RESPONSE_RECEIVED, winsound.SND_ASYNC)
		else:
			winsound.PlaySound(None, winsound.SND_ASYNC)
		self.enableControls()
		self.worker = None

		if not event.data:
			return

		if isinstance(event.data, openai.types.chat.chat_completion.Choice):
			historyBlock = HistoryBlock()
			historyBlock.system = self.systemTextCtrl.GetValue().strip()
			historyBlock.prompt = self.promptTextCtrl.GetValue().strip()
			model = self.getCurrentModel()
			historyBlock.model = model.id
			if self.conf["advancedMode"]:
				historyBlock.temperature = self.temperatureSpinCtrl.GetValue() / 100
				historyBlock.topP = self.topPSpinCtrl.GetValue() / 100
			else:
				historyBlock.temperature = model.defaultTemperature
				historyBlock.topP = self.conf["topP"] / 100
			historyBlock.maxTokens = self.maxTokensSpinCtrl.GetValue()
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
			self.previousPrompt = self.promptTextCtrl.GetValue()
			self.promptTextCtrl.Clear()
			return
		if isinstance(event.data, (
			openai.types.audio.transcription.Transcription,
			WhisperTranscription
		)):
			self.promptTextCtrl.AppendText(
				event.data.text if event.data.text else ""
			)
			self.promptTextCtrl.SetFocus()
			self.promptTextCtrl.SetInsertionPointEnd()
			self.message(
				_("Insertion of: %s") % event.data.text,
				True
			)
			return

		if isinstance(event.data, openai._base_client.HttpxBinaryResponseContent):
			if os.path.exists(TTS_FILE_NAME):
				os.startfile(TTS_FILE_NAME)
			return
		errMsg = _("Unknown error")
		if isinstance(event.data, str):
			errMsg = event.data
		elif isinstance(
			event.data, (
				openai.APIConnectionError,
				openai.APIStatusError
			)
		):
			errMsg = event.data.message
		else:
			log.error(errMsg)
			log.error(type(event.data))
		# check if the error contains an URL, retrieve it to ask if the user wants to open it in the browser
		url = re.search("https?://[^\s]+", errMsg)
		if url:
			errMsg += "\n\n" + _("Do you want to open the URL in your browser?")
		res = gui.messageBox(
			errMsg,
			_("OpenAI Error"),
			wx.OK | wx.ICON_ERROR | wx.CENTRE if not url else wx.YES_NO | wx.ICON_ERROR | wx.CENTRE,
		)
		if url and res == wx.YES:
			os.startfile(url.group(0).rstrip("."))
		if "model's maximum context length is " in errMsg:
			self.modelsListCtrl.SetFocus()
		else:
			self.promptTextCtrl.SetFocus()
		raise Exception(errMsg)

	def onCharHook(self, evt):
		if self.conf["blockEscapeKey"] and evt.GetKeyCode() == wx.WXK_ESCAPE:
			self.message(_("Press Alt+F4 to close the dialog"))
		else:
			evt.Skip()

	def onTimer(self, event):
		if self.lastBlock is not None:
			block = self.lastBlock
			if block.displayHeader:
				if block != self.firstBlock:
					block.previous.segmentBreakLine = TextSegment(self.messagesTextCtrl, "\n", block)
				block.segmentPromptLabel = TextSegment(self.messagesTextCtrl, _("User:") + ' ', block)
				block.segmentPrompt = TextSegment(self.messagesTextCtrl, block.prompt + "\n", block)
				block.segmentResponseLabel = TextSegment(self.messagesTextCtrl, _("Assistant:") + ' ', block)
				block.displayHeader = False
			l = len(block.responseText)
			if block.lastLen == 0 and l > 0:

				self.messagesTextCtrl.SetInsertionPointEnd()
				if (
					self.historyObj
					and self.foregroundObj is api.getForegroundObject()
				):
					if braille.handler.buffer is braille.handler.messageBuffer:
						braille.handler._dismissMessage()
					self.focusHistoryBrl()
				else:
					log.error("Unable to focus the history object or the foreground object has changed")
				block.responseText = block.responseText.lstrip()
				l = len(block.responseText)
			if l > block.lastLen:
				newText = block.responseText[block.lastLen:]
				block.lastLen = l
				if block.segmentResponse is None:
					block.segmentResponse = TextSegment(self.messagesTextCtrl, newText, block)
				else:
					block.segmentResponse.appendText(newText)

	def addEntry(self, accelEntries, modifiers, key, func):
		id_ = wx.Window.NewControlId()
		self.Bind(wx.EVT_MENU, func, id=id_)
		accelEntries.append ( (modifiers, key, id_))

	def addShortcuts(self):
		self.messagesTextCtrl.Bind(wx.EVT_TEXT_COPY, self.onCopyMessage)
		accelEntries  = []
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("M"), self.onCurrentMessage)
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("J"), self.onPreviousMessage)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, wx.WXK_UP, self.onPreviousMessage)
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, ord("K"), self.onNextMessage)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, wx.WXK_DOWN, self.onNextMessage)
		self.addEntry(accelEntries, wx.ACCEL_CTRL + wx.ACCEL_SHIFT, ord("C"), lambda evt: self.onCopyMessage(evt, True))
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("D"), self.onDeleteBlock)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("S"), self.onSaveHistory)
		self.addEntry(accelEntries, wx.ACCEL_NORMAL, wx.WXK_SPACE, lambda evt: self.onWebviewMessage(evt, True))
		self.addEntry(accelEntries, wx.ACCEL_SHIFT, wx.WXK_SPACE, lambda evt: self.onWebviewMessage(evt, False))
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_LEFT, self.onCopyResponseToSystem)
		self.addEntry(accelEntries, wx.ACCEL_ALT, wx.WXK_RIGHT, self.onCopyPromptToPrompt)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.messagesTextCtrl.SetAcceleratorTable(accelTable)

		accelEntries  = []
		self.addEntry (accelEntries, wx.ACCEL_CTRL, wx.WXK_UP, self.onPreviousPrompt)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.promptTextCtrl.SetAcceleratorTable(accelTable)

		accelEntries  = []
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("r"), self.onRecord)
		self.addEntry(accelEntries, wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("r"), self.onRecordFromFilePath)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("i"), self.onImageDescriptionFromFilePath)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("u"), self.onImageDescriptionFromURL)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("e"), self.onImageDescriptionFromScreenshot)
		self.addEntry(accelEntries, wx.ACCEL_CTRL, ord("t"), self.onTextToSpeech)
		accelTable = wx.AcceleratorTable(accelEntries)
		self.SetAcceleratorTable(accelTable)

	def getImages(
		self,
		pathList: list = None,
		prompt: str = None
	) -> list:
		conf = self.conf
		if not pathList:
			pathList = self.pathList
		images = []
		if prompt:
			images.append({
				"type": "text",
				"text": prompt
			})
		for imageFile in pathList:
			path = imageFile.path
			log.debug(f"Processing {path}")
			if imageFile.type == ImageFileTypes.IMAGE_URL:
				images.append({"type": "image_url", "image_url": {"url": path}})
			elif imageFile.type == ImageFileTypes.IMAGE_LOCAL:
				if conf["images"]["resize"]:
					path_resized_image = os.path.join(DATA_DIR, "last_resized.jpg")
					resize_image(
						path,
						max_width=conf["images"]["maxWidth"],
						max_height=conf["images"]["maxHeight"],
						quality=conf["images"]["quality"],
						target=path_resized_image
					)
					path = path_resized_image
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
				raise ValueError(f"Invalid image type for {path}")
				break
		return images

	def getMessages(
		self,
		messages: list
	):
		if not self.conversationCheckBox.IsChecked():
			return
		block = self.firstBlock
		while block:
			userContent = []
			if block.pathList:
				userContent.extend(self.getImages(block.pathList, block.prompt))
			elif block.prompt:
				userContent = block.prompt
			if userContent:
				messages.append({
					"role": "user",
					"content": userContent
				})
			if block.responseText:
				messages.append({
					"role": "assistant",
					"content": block.responseText
				})
			block = block.next

	def onPreviousPrompt(self, event):
		value = self.previousPrompt
		if value:
			self.promptTextCtrl.SetValue(value)

	def onPreviousMessage(self, evt):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
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
		self.messagesTextCtrl.SetInsertionPoint(start)
		self.message(label + text)

	def onNextMessage(self, evt):
		segment = TextSegment.getCurrentSegment (self.messagesTextCtrl)
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
		self.messagesTextCtrl.SetInsertionPoint(start)
		self.message(label + text)

	def onCurrentMessage(self, evt):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		if segment is None:
			return
		block = segment.owner
		if segment == block.segmentPromptLabel or segment == block.segmentPrompt:
			text = block.segmentPrompt.getText ()
		elif segment == block.segmentResponseLabel or segment == block.segmentResponse:
			text = block.segmentResponse.getText ()
		self.message(text)


	def onEditBlock (self, evt):
		segment = TextSegment.getCurrentSegment (self.messagesTextCtrl)
		if segment is None:
			return
		block = segment.owner
		self.systemTextCtrl.SetValue(block.system)
		self.promptTextCtrl.SetValue (block.userPrompt)
		self.promptTextCtrl.SetFocus ()

	def onCopyResponseToSystem (self, evt):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		if segment is None:
			return
		block = segment.owner
		text = block.segmentResponse.getText ()
		self.systemTextCtrl.SetValue(text)
		self.message(_("Response copied to system: %s") % text)

	def onCopyPromptToPrompt(self, evt):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		if segment is None:
			return
		block = segment.owner
		self.promptTextCtrl.SetValue (block.segmentPrompt.getText ())
		self.promptTextCtrl.SetFocus ()
		self.message(_("Compied to prompt"))

	def onCopyMessage(self, evt, isHtml=False):
		text = self.messagesTextCtrl.GetStringSelection()
		msg = _("Copy")
		if not text:
			segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
			if segment is None:
				return
			block = segment.owner
			if segment == block.segmentPromptLabel or segment == block.segmentPrompt:
				text = block.segmentPrompt.getText ()
				msg = _("Copy prompt")
			elif segment == block.segmentResponseLabel or segment == block.segmentResponse:
				text = block.segmentResponse.getText()
				msg = _("Copy response")
		if isHtml:
			text = markdown2.markdown(
				text,
				extras=["fenced-code-blocks", "footnotes", "header-ids", "spoiler", "strike", "tables", "task_list", "underline", "wiki-tables"]
			)
			copyToClipAsHTML(text)
			msg += ' ' + _("as formatted HTML")
		else:
			api.copyToClip(text)
		self.message(msg)

	def onDeleteBlock(self, evt):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
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

	def onWebviewMessage(self, evt, isHtml=False):
		segment = TextSegment.getCurrentSegment (self.messagesTextCtrl)
		if segment is None:
			return
		block = segment.owner
		if segment == block.segmentPromptLabel or segment == block.segmentPrompt:
			text = block.segmentPrompt.getText ()
		elif segment == block.segmentResponseLabel or segment == block.segmentResponse:
			text = block.segmentResponse.getText ()
		ui.browseableMessage(
			markdown2.markdown(
				text,
				extras=["fenced-code-blocks", "footnotes", "header-ids", "spoiler", "strike", "tables", "task_list", "underline", "wiki-tables"]
			),
			title="OpenAI",
			isHtml=isHtml
		)

	def onSaveHistory(self, evt):
		"""
		Save the history to a file.
		"""
		path = None
		if self._historyPath and os.path.exists(self._historyPath):
			path = self._historyPath
		else:
			now = datetime.datetime.now()
			now_str = now.strftime("%Y-%m-%d_%H-%M-%S")
			defaultFile = "openai_history_%s.txt" % now_str
			dlg = wx.FileDialog(
				None,
				message=_("Save history"),
				defaultFile=defaultFile,
				wildcard=_("Text file") + " (*.txt)|*.txt",
				style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
			)
			if dlg.ShowModal() != wx.ID_OK:
				return
			path = dlg.GetPath()
		if not path:
			return
		self._historyPath = path
		with open(path, "w", encoding="utf-8") as f:
			f.write(self.messagesTextCtrl.GetValue())
		self.message(_("History saved"))

	def onSystemContextMenu(self, event):
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		resetItem = menu.Append(item_id, _("Reset to default"))
		self.Bind(wx.EVT_MENU, self.onResetSystemPrompt, id=item_id)
		menu.AppendSeparator()
		self.addStandardMenuOptions(menu)
		self.systemTextCtrl.PopupMenu(menu)
		menu.Destroy()

	def onHistoryContextMenu(self, evt):
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Show message in web view as formatted HTML") + " (Space)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onWebviewMessage(evt, True), id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Show message in web view as HTML source") + " (Shift+Space)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onWebviewMessage(evt, False), id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Copy message as plain text") + " (Ctrl+C)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onCopyMessage(evt, False), id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Copy message as formatted HTML") + " (Ctrl+Shift+C)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onCopyMessage(evt, True), id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Copy response to system") + " (Alt+Left)")
		self.Bind(wx.EVT_MENU, self.onCopyResponseToSystem, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Copy prompt to prompt") + " (Alt+Right)")
		self.Bind(wx.EVT_MENU, self.onCopyPromptToPrompt, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Delete block") + " (Ctrl+D)")
		self.Bind(wx.EVT_MENU, self.onDeleteBlock, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Save history as text file") + " (Ctrl+S)")
		self.Bind(wx.EVT_MENU, self.onSaveHistory, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Say message") + " (M)")
		self.Bind(wx.EVT_MENU, self.onCurrentMessage, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Move to previous message") + " (j)")
		self.Bind(wx.EVT_MENU, self.onPreviousMessage, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Move to next message") + " (k)")
		self.Bind(wx.EVT_MENU, self.onNextMessage, id=item_id)
		menu.AppendSeparator()
		self.addStandardMenuOptions(menu)
		self.messagesTextCtrl.PopupMenu(menu)
		menu.Destroy()

	def onPromptContextMenu(self, evt):
		menu = wx.Menu()
		if self.previousPrompt:
			item_id = wx.NewIdRef()
			menu.Append(item_id, _("Insert previous prompt") + " (Ctrl+Up)")
			self.Bind(wx.EVT_MENU, self.onPreviousPrompt, id=item_id)
			menu.AppendSeparator()
		self.addStandardMenuOptions(menu)
		self.promptTextCtrl.PopupMenu(menu)
		menu.Destroy()

	def onSetFocus(self, evt):
		self.lastFocusedItem = evt.GetEventObject()
		evt.Skip()

	def focusHistoryBrl(self, force=False):
		if (
			not force
			and not self.conf["chatFeedback"]["brailleAutoFocusHistory"]
		):
			return
		if (
			self.historyObj
			and self.foregroundObj is api.getForegroundObject()
		):
			if api.getNavigatorObject() is not self.historyObj:
				api.setNavigatorObject(self.historyObj)
			braille.handler.handleUpdate(self.historyObj)
			braille.handler.handleReviewMove(True)

	def message(
		self,
		msg: str,
		speechOnly: bool = False,
		onPromptFieldOnly: bool = False
	):
		if not msg:
			return
		if onPromptFieldOnly and self.lastFocusedItem is not self.promptTextCtrl:
			return
		if (
			not onPromptFieldOnly
			or (
				onPromptFieldOnly
				and self.conf["chatFeedback"]["speechResponseReceived"]
			)
		):
			queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, msg)
		if not speechOnly:
			queueHandler.queueFunction(queueHandler.eventQueue, braille.handler.message, msg)
		if onPromptFieldOnly:
			self.focusHistoryBrl()

	def onModelContextMenu(self, evt):
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Show model details") + " (Space)")
		self.Bind(wx.EVT_MENU, self.showModelDetails, id=item_id)
		menu.AppendSeparator()
		self.modelsListCtrl.PopupMenu(menu)
		menu.Destroy()

	def onImageDescription(self, evt):
		"""
		Display a menu to select the source of the image.
		"""
		menu = wx.Menu()

		item_id = wx.NewIdRef()
		menu.Append(item_id, _("From f&ile path...") + " (Ctrl+I)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromFilePath, id=item_id)

		item_id = wx.NewIdRef()
		menu.Append(item_id, _("From &URL...") + " (Ctrl+U)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromURL, id=item_id)

		item_id = wx.NewIdRef()
		menu.Append(item_id, _("From &screenshot") + " (Ctrl+E)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromScreenshot, id=item_id)

		self.PopupMenu(menu)
		menu.Destroy()

	def onImageListKeyDown(self, evt):
		key_code = evt.GetKeyCode()
		if key_code == wx.WXK_DELETE:
			self.onRemoveSelectedImages(evt)
		elif key_code == ord('A') and evt.ControlDown():
			self.onImageListSelectAll(evt)
		evt.Skip()

	def onImageListContextMenu(self, evt):
		"""
		Display a menu to manage the image list.
		"""
		menu = wx.Menu()
		if self.pathList:
			if self.imagesListCtrl.GetItemCount() > 0 and self.imagesListCtrl.GetSelectedItemCount() > 0:
				item_id = wx.NewIdRef()
				menu.Append(item_id, _("&Remove selected images") + " (Del)")
				self.Bind(wx.EVT_MENU, self.onRemoveSelectedImages, id=item_id)
			item_id = wx.NewIdRef()
			menu.Append(item_id, _("Remove &all images"))
			self.Bind(wx.EVT_MENU, self.onRemoveAllImages, id=item_id)
			menu.AppendSeparator()
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Add from f&ile path...") + " (Ctrl+I)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromFilePath, id=item_id)
		item_id = wx.NewIdRef()
		menu.Append(item_id, _("Add from &URL...") + " (Ctrl+U)")
		self.Bind(wx.EVT_MENU, self.onImageDescriptionFromURL, id=item_id)
		self.PopupMenu(menu)
		menu.Destroy()

	def onImageListSelectAll(self, evt):
		for i in range(self.imagesListCtrl.GetItemCount()):
			self.imagesListCtrl.Select(i)

	def onRemoveSelectedImages(self, evt):
		if not self.pathList:
			return
		focused_item = self.imagesListCtrl.GetFocusedItem()
		items_to_remove = []
		selectedItem = self.imagesListCtrl.GetFirstSelected()
		while selectedItem != wx.NOT_FOUND:
			items_to_remove.append(selectedItem)
			selectedItem = self.imagesListCtrl.GetNextSelected(selectedItem)

		if not items_to_remove:
			return
		self.pathList = [
			path for i, path in enumerate(self.pathList)
			if i not in items_to_remove
		]
		self.updateImageList()
		if focused_item == wx.NOT_FOUND:
			return
		if focused_item > self.imagesListCtrl.GetItemCount() - 1:
			focused_item -= 1
		self.imagesListCtrl.Focus(focused_item)
		self.imagesListCtrl.Select(focused_item)

	def onRemoveAllImages(self, evt):
		self.pathList.clear()
		self.updateImageList()

	def imageExists(self, path, pathList=None):
		if not pathList:
			pathList = self.pathList
		for imageFile in pathList:
			if imageFile.path.lower() == path.lower():
				return True
		block = self.firstBlock
		while block is not None:
			if block.pathList:
				for imageFile in block.pathList:
					if imageFile.path.lower() == path.lower():
						return True
			block = block.next
		return False

	def updateImageList(self, focusPrompt=True):
		self.imagesListCtrl.DeleteAllItems()
		if not self.pathList:
			self.imagesLabel.Hide()
			self.imagesListCtrl.Hide()
			self.Layout()
			if focusPrompt:
				self.promptTextCtrl.SetFocus()
			return
		for path in self.pathList:
			self.imagesListCtrl.Append([
				path.name,
				path.path,
				path.size,
				f"{path.dimensions[0]}x{path.dimensions[1]}" if isinstance(path.dimensions, tuple) else "N/A",
				path.description or "N/A"
			])
		self.imagesListCtrl.SetItemState(
			0,
			wx.LIST_STATE_FOCUSED,
			wx.LIST_STATE_FOCUSED
		)
		self.imagesLabel.Show()
		self.imagesListCtrl.Show()
		self.Layout()

	def ensureModelVisionSelected(self):
		if not self.getCurrentModel().vision:
			model_index = self._getModelIndex(self.conf["modelVision"])
			self.modelsListCtrl.SetItemState(
				model_index,
				wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
				wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED
			)
			self.modelsListCtrl.EnsureVisible(model_index)

	def focusLastImage(self):
		index = self.imagesListCtrl.GetItemCount() - 1
		self.imagesListCtrl.SetItemState(
			index,
			wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
			wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED
		)
		self.imagesListCtrl.EnsureVisible(index)

	def onImageDescriptionFromFilePath(self, evt):
		"""
		Open a file dialog to select one or more images.
		"""
		if not self.pathList:
			self.pathList = []
		dlg = wx.FileDialog(
			None,
			# Translators: This is a message displayed in a dialog to select one or more images.
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
		for path in paths:
			if not self.imageExists(path):
				self.pathList.append(
					ImageFile(path)
				)
			else:
				gui.messageBox(
					# Translators: This message is displayed when the image has already been added.
					_("The following image has already been added and will be ignored:\n%s") % path,
					"OpenAI",
					wx.OK | wx.ICON_ERROR
				)
		self.ensureModelVisionSelected()
		if not self.promptTextCtrl.GetValue().strip():
			self.promptTextCtrl.SetValue(
				self.getDefaultImageDescriptionsPrompt()
			)
		self.updateImageList()
		self.focusLastImage()

	def onImageDescriptionFromURL(self, evt):
		"""
		Open a dialog to enter an image URL.
		"""
		dlg = wx.TextEntryDialog(
			None,
			# Translators: This is a message displayed in a dialog to enter an image URL.
			message=_("Enter image URL"),
			caption="OpenAI",
			style=wx.OK | wx.CANCEL
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		url = dlg.GetValue().strip()
		if not url:
			return
		url_pattern = re.compile(
			URL_PATTERN
		)
		if re.match(url_pattern, url) is None:
			gui.messageBox(
				_("Invalid URL, bad format."),
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return
		try:
			import urllib.request
			r = urllib.request.urlopen(url)
		except urllib.error.HTTPError as err:
			gui.messageBox(
				# Translators: This message is displayed when the image URL returns an HTTP error.
				_("HTTP error %s.") % err,
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return
		if not r.headers.get_content_type().startswith("image/"):
			gui.messageBox(
				# Translators: This message is displayed when the image URL does not point to an image.
				_("The URL does not point to an image."),
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return
		if not self.pathList:
			self.pathList = []
		description = []
		content_type = r.headers.get_content_type()
		if content_type:
			description.append(content_type)
		size = r.headers.get("Content-Length")
		if size and size.isdigit():
			size = int(size)
		if description:
			description = ", ".join(description)
		try:
			dimensions = get_image_dimensions(r)
		except BaseException as err:
			log.error(err)
			dimensions = None
			gui.messageBox(
				# Translators: This message is displayed when the add-on fails to get the image dimensions.
				_("Failed to get image dimensions. %s") % err,
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return
		self.pathList.append(
			ImageFile(
				url,
				description=description,
				size=size or -1,
				dimensions=dimensions
			)
		)
		self.ensureModelVisionSelected()
		if not self.promptTextCtrl.GetValue().strip():
			self.promptTextCtrl.SetValue(
				self.getDefaultImageDescriptionsPrompt()
			)
		self.updateImageList()
		self.focusLastImage()

	def onImageDescriptionFromScreenshot(self, evt):
		"""Define this session as a image receiving session."""
		global addToSession
		if addToSession and addToSession is self:
			addToSession = None
			self.message(
				# Translators: This message is displayed when a chat session stops receiving images.
				_("Screenshot reception disabled")
			)
			return
		addToSession = self
		self.message(
			# Translators: This message is displayed when a chat session starts to receive images.
			_("Screenshot reception enabled")
		)

	def getWhisperResponseFormat(self):
		choiceIndex = 0
		if self.conf["advancedMode"]:
			choiceIndex = self.whisperResponseFormatListBox.GetSelection()
		if choiceIndex == wx.NOT_FOUND:
			choiceIndex = 0
		return RESP_AUDIO_FORMATS[choiceIndex]

	def onRecord(self, evt):
		if self.worker:
			self.onStopRecord(evt)
			return
		self.disableControls()
		self.recordBtn.SetLabel(_("Stop &recording") + " (Ctrl+R)")
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onStopRecord)
		self.recordBtn.Enable()
		self.worker = RecordThread(
			self.client,
			self,
			conf=self.conf["audio"],
			responseFormat=self.getWhisperResponseFormat()
		)
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
		fileName = dlg.GetPath()
		self.message(PROCESSING_MSG)
		self.disableControls()
		self.worker = RecordThread(
			self.client,
			self,
			fileName,
			conf=self.conf["audio"],
			responseFormat=self.getWhisperResponseFormat()
		)
		self.worker.start()

	def onTextToSpeech(self, evt):
		if not self.promptTextCtrl.GetValue().strip():
			gui.messageBox(
				_("Please enter some text in the prompt field first."),
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			self.promptTextCtrl.SetFocus()
			return
		self.promptTextCtrl.SetFocus()
		self.disableControls()
		self.worker = TextToSpeechThread(self, self.promptTextCtrl.GetValue())
		self.worker.start()

	def onStopRecord(self, evt):
		self.disableControls()
		if self.worker:
			self.worker.stop()
			self.worker = None
		self.recordBtn.SetLabel(
			_("Start &recording") + " (Ctrl+R)"
		)
		self.recordBtn.Bind(wx.EVT_BUTTON, self.onRecord)
		self.enableControls()

	def disableControls(self):
		self.submitBtn.Disable()
		self.cancelBtn.Disable()
		self.recordBtn.Disable()
		self.transcribeFromFileBtn.Disable()
		self.imageDescriptionBtn.Disable()
		self.TTSBtn.Disable()
		self.modelsListCtrl.Disable()
		self.maxTokensSpinCtrl.Disable()
		self.conversationCheckBox.Disable()
		self.promptTextCtrl.SetEditable(False)
		self.systemTextCtrl.SetEditable(False)
		self.imagesListCtrl.Disable()
		if self.conf["advancedMode"]:
			self.temperatureSpinCtrl.Disable()
			self.topPSpinCtrl.Disable()
			self.whisperResponseFormatListBox.Disable()
			self.streamModeCheckBox.Disable()
			self.debugModeCheckBox.Disable()

	def enableControls(self):
		self.submitBtn.Enable()
		self.cancelBtn.Enable()
		self.recordBtn.Enable()
		self.transcribeFromFileBtn.Enable()
		self.imageDescriptionBtn.Enable()
		self.TTSBtn.Enable()
		self.modelsListCtrl.Enable()
		self.maxTokensSpinCtrl.Enable()
		self.conversationCheckBox.Enable()
		self.promptTextCtrl.SetEditable(True)
		self.systemTextCtrl.SetEditable(True)
		self.imagesListCtrl.Enable()
		if self.conf["advancedMode"]:
			self.temperatureSpinCtrl.Enable()
			self.topPSpinCtrl.Enable()
			self.whisperResponseFormatListBox.Enable()
			self.streamModeCheckBox.Enable()
			self.debugModeCheckBox.Enable()
		self.updateImageList(False)
