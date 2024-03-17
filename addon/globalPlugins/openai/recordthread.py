import json
import os
import re
import sys
import threading
import uuid
import wave
import winsound
import wx

from logHandler import log
import addonHandler
import api
import brailleInput
import controlTypes
import queueHandler
import speech
import tones
import core
import ui

from . import apikeymanager
from .consts import ADDON_DIR, DATA_DIR, LIBS_DIR_PY
from .resultevent import ResultEvent

sys.path.insert(0, LIBS_DIR_PY)
import numpy as np
import sounddevice as sd
sys.path.remove(LIBS_DIR_PY)

addonHandler.initTranslation()

def retrieveTranscription(transcription):
	if transcription and transcription.text:
		obj = api.getFocusObject()
		if (
			obj
			and (
				controlTypes.State.MULTILINE in obj.states
				or controlTypes.State.EDITABLE in obj.states
			) and controlTypes.State.FOCUSED in obj.states
		):
			brailleInput.handler.sendChars(transcription.text)
			queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, transcription.text)
		else:
			api.copyToClip(transcription.text)
			speech.speakMessage(_("Transcription copied to clipboard"))


class WhisperTranscription:

	def __init__(self, text):
		self.text = text


class RecordThread(threading.Thread):

	def __init__(
		self,
		client,
		notifyWindow=None,
		pathList=None,
		conf=None,
		responseFormat="json"
	):
		super(RecordThread, self).__init__()
		provider = "OpenAI"
		manager = apikeymanager.get(provider)
		client.base_url =  apikeymanager.BASE_URLs[provider]
		client.api_key = manager.get_api_key()
		client.organization = manager.get_organization_key()
		self.client = client
		self.pathList = pathList
		self.conf = conf
		self.responseFormat = responseFormat
		self._stopRecord = False
		self._notifyWindow = notifyWindow
		self._wantAbort = 0
		self._recording = False

	def run(self):
		if self.pathList:
			self.process_transcription(self.pathList)
			return
		if not self.conf:
			raise ValueError("No configuration provided.")
		self.audioData = np.array([], dtype=self.conf["dtype"])
		filename = self.get_filename()
		tones.beep(200, 100)
		self.record_audio(self.conf["sampleRate"])
		tones.beep(200, 200)
		winsound.PlaySound(f"{ADDON_DIR}/sounds/progress.wav", winsound.SND_ASYNC|winsound.SND_LOOP)

		if self._wantAbort:
			return
		self.save_wav(
			filename,
			self.audioData,
			self.conf["sampleRate"]
		)
		if self._notifyWindow:
			self._notifyWindow.message(_("Transcribing..."))
		self.process_transcription(filename)

	def record_audio(self, sampleRate):
		chunk_size = 1024
		self._recording = True
		with sd.InputStream(
			samplerate=sampleRate,
			channels=self.conf["channels"],
			dtype=self.conf["dtype"],
		) as stream:
			while not self._stopRecord and self._recording:
				frame, overflowed = stream.read(chunk_size)
				if overflowed:
					log.error("Audio buffer has overflowed.")
				self.audioData = np.append(self.audioData, frame)
				if self._wantAbort:
					break
		self._recording = False

	def save_wav(self, filename, data, sampleRate):
		if self._wantAbort:
			return
		wavefile = wave.open(filename, "wb")
		wavefile.setnchannels(self.conf["channels"])
		wavefile.setsampwidth(2) # 16 bits
		wavefile.setframerate(sampleRate)
		wavefile.writeframes(data.tobytes())
		wavefile.close()

	def stop(self):
		self._stopRecord = True
		self._recording = False

	def get_filename(self):
		return os.path.join(DATA_DIR, "tmp.wav")

	def process_transcription(self, filename):
		if self._wantAbort:
			return
		try:
			audio_file = open(filename, "rb")
			transcription = None
			if self.conf["whisper.cpp"]["enabled"]:
				from urllib.request import Request, urlopen
				host = self.conf["whisper.cpp"]["host"].strip()
				if not host:
					host = "http://127.0.0.1:8081"
				if not re.match(r"^https?://", host, re.I):
					host = "http://" + host
				url = host + "/inference"
				boundary = uuid.uuid4().hex
				content_type = 'multipart/form-data; boundary={}'.format(boundary)
				body = '--{}\r\n'.format(boundary).encode('utf-8')
				body += 'Content-Disposition: form-data; name="file"; filename="{}"\r\n'.format("tmp.wav").encode('utf-8')
				body += 'Content-Type: {}\r\n\r\n'.format("audio/wav").encode('utf-8')
				body += audio_file.read()
				body += '\r\n'.encode('utf-8')
				body += '--{}\r\n'.format(boundary).encode('utf-8')
				body += 'Content-Disposition: form-data; name="temperature"\r\n\r\n'.encode('utf-8')
				body += '{}\r\n'.format(0).encode('utf-8')
				body += '--{}\r\n'.format(boundary).encode('utf-8')
				body += 'Content-Disposition: form-data; name="response-format"\r\n\r\n'.encode('utf-8')
				body += '{}\r\n'.format("json").encode('utf-8')
				body += '--{}--\r\n'.format(boundary).encode('utf-8')

				headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary}

				req = Request(url, body, headers)
				response = urlopen(req, timeout=3600)
				if response.getcode() != 200:
					msg = "Error: {}".format(response.getcode())
					if self._notifyWindow:
						wx.PostEvent(self._notifyWindow, ResultEvent(msg))
					else:
						log.error(msg)
						ui.message(_("Error!"))
					return
				data = json.loads(response.read().decode('utf-8'))
				if "error" in data:
					msg = "Error: {}".format(data["error"])
					if self._notifyWindow:
						wx.PostEvent(self._notifyWindow, ResultEvent(msg))
					else:
						log.error(msg)
						ui.message(_("Error!"))
					return
				transcription = WhisperTranscription(
					data["text"]
				)
			else:
				transcription = self.client.audio.transcriptions.create(
					model="whisper-1",
					file=audio_file,
					response_format=self.responseFormat
				)
		except BaseException as err:
			if self._notifyWindow:
				wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			else:
				log.error(repr(err))
				ui.message(_("Error!"))
			return
		if self._notifyWindow:
			if isinstance(transcription, str):
				transcription = WhisperTranscription(
					transcription
				)
			wx.PostEvent(self._notifyWindow, ResultEvent(transcription))
		else:
			winsound.PlaySound(None, winsound.SND_ASYNC)
			core.callLater(200, retrieveTranscription, transcription)

	def abort(self):
		self._stopRecord = 1
		self._wantAbort = 1
