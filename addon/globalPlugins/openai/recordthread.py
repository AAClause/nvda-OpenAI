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

from .consts import ADDON_DIR, DATA_DIR
from .resultevent import ResultEvent

additionalLibsPath = os.path.join(ADDON_DIR, "lib")
sys.path.insert(0, additionalLibsPath)
import numpy as np
import sounddevice as sd
sys.path.remove(additionalLibsPath)

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

	def __init__(self, client, notifyWindow=None, pathList=None):
		super(RecordThread, self).__init__()
		self._notifyWindow = notifyWindow
		self.client = client
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
		if self._notifyWindow:
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
		self.stop_record = 1
		self._wantAbort = 1

