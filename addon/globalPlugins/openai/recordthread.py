import os
import sys
import threading
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


class RecordThread(threading.Thread):

	def __init__(self, client, notifyWindow=None, pathList=None, conf=None):
		super(RecordThread, self).__init__()
		self.client = client
		self.pathList = pathList
		self.conf = conf
		self._stopRecord = False
		self._notifyWindow = notifyWindow
		self._wantAbort = 0
		self._recording = False

	def run(self):
		if self.pathList:
			self.process_transcription(self.pathList)
			return
		if not self.conf:
			self.conf = {
				"channels": 1,
				"sampleRate": 16000,
				"dtype": "int16",
			}
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
			transcription = self.client.audio.transcriptions.create(
				model="whisper-1", 
				file=audio_file
			)
		except BaseException as err:
			if self._notifyWindow:
				wx.PostEvent(self._notifyWindow, ResultEvent(repr(err)))
			else:
				log.error(repr(err))
				ui.message(_("Error!"))
			return
		if self._notifyWindow:
			wx.PostEvent(self._notifyWindow, ResultEvent(transcription))
		else:
			winsound.PlaySound(None, winsound.SND_ASYNC)
			core.callLater(200, retrieveTranscription, transcription)

	def abort(self):
		self._stopRecord = 1
		self._wantAbort = 1
