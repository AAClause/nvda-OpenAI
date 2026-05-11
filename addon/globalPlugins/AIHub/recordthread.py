import ctypes
from ctypes import wintypes
import json
import os
import re
import threading
from urllib.request import Request, urlopen
import uuid
import wave
import winsound
import wx
import gui

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
from .apiclient import (
	Transcription,
	transcribe_audio_mistral,
	configure_client_for_provider,
	truncate_error_for_user,
)
from .audioutils import trim_silence_wav, downsample_to_voice_wav
from .consts import (
	ADDON_DIR,
	DATA_DIR,
	Provider,
	TEMP_DIR,
	TranscriptionProvider,
	ensure_temp_dir,
	stop_progress_sound,
)
from .transcription import get_transcription_provider, get_transcription_text
from .resultevent import ResultEvent

WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
CALLBACK_EVENT = 0x50000
WHDR_DONE = 1
WAIT_OBJECT_0 = 0

addonHandler.initTranslation()


def transcribe_audio_file(path, conf, client=None):
	"""Transcribe an audio file and return the text. Returns None on invalid input. Raises on API/transcription errors."""
	if not path or not os.path.exists(path) or not path.lower().endswith((".wav", ".mp3", ".m4a", ".webm", ".mp4")):
		return None
	provider = get_transcription_provider(conf)
	if provider == TranscriptionProvider.WHISPER_CPP:
		rt = RecordThread(client or object(), conf=conf)
		result = rt._transcribe_whisper_cpp(path)
	elif provider == TranscriptionProvider.MISTRAL:
		rt = RecordThread(client or object(), conf=conf)
		result = rt._transcribe_mistral(path)
	else:
		if not client:
			return None
		client = configure_client_for_provider(client, Provider.OpenAI, clone=True)
		rt = RecordThread(client, conf=conf)
		result = rt._transcribe_openai(path)
	if result is None:
		return None
	return get_transcription_text(result)


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
			# Translators: Text in recording thread status and error messages.
			speech.speakMessage(_("Transcription copied to clipboard"))


class WhisperTranscription:

	def __init__(self, text):
		self.text = text


class AudioInputResult:
	"""Result when useDirectAudio: path to audio file for direct model input."""
	def __init__(self, path: str):
		self.path = path


class RecordThread(threading.Thread):

	def __init__(
		self,
		client,
		notifyWindow=None,
		audioFile=None,
		conf=None,
		responseFormat="json",
		useDirectAudio=False,
		onTranscription=None,
		onAudioPath=None,
		transcriptionProvider=None,
		transcriptionAccountId=None,
		transcriptionModel=None,
	):
		super(RecordThread, self).__init__(daemon=True)
		self.client = client
		# Optional pre-recorded audio file to transcribe instead of capturing live.
		# Accepts a path string or a single-element list (legacy callers).
		self.audioFile = audioFile
		self.conf = conf
		self.responseFormat = responseFormat
		self.useDirectAudio = useDirectAudio
		self._stopRecord = False
		self._notifyWindow = notifyWindow
		self._wantAbort = 0
		self._recording = False
		self._onTranscription = onTranscription
		self._onAudioPath = onAudioPath
		self._transcriptionProvider = transcriptionProvider or get_transcription_provider(conf or {})
		self._transcriptionAccountId = transcriptionAccountId
		self._transcriptionModel = transcriptionModel
		if self._transcriptionProvider == TranscriptionProvider.OPENAI:
			if not self._transcriptionAccountId:
				self._transcriptionAccountId = (conf or {}).get("openaiTranscriptionAccountId", "")
			if self._transcriptionAccountId and not apikeymanager.get(Provider.OpenAI).isReady(account_id=self._transcriptionAccountId):
				self._transcriptionAccountId = ""
			if not self._transcriptionAccountId:
				self._transcriptionAccountId = apikeymanager.get(Provider.OpenAI).get_active_account_id()
			if not self._transcriptionModel:
				self._transcriptionModel = (conf or {}).get("whisperModel", "whisper-1")
		elif self._transcriptionProvider == TranscriptionProvider.MISTRAL:
			if not self._transcriptionAccountId:
				self._transcriptionAccountId = (conf or {}).get("mistralTranscriptionAccountId", "")
			if self._transcriptionAccountId and not apikeymanager.get(Provider.MistralAI).isReady(account_id=self._transcriptionAccountId):
				self._transcriptionAccountId = ""
			if not self._transcriptionAccountId:
				self._transcriptionAccountId = apikeymanager.get(Provider.MistralAI).get_active_account_id()
			if not self._transcriptionModel:
				self._transcriptionModel = (conf or {}).get("voxtralModel", "voxtral-mini-latest")

	def run(self):
		if self.audioFile:
			path = self.audioFile[0] if isinstance(self.audioFile, (list, tuple)) else self.audioFile
			if (
				self.conf.get("trimSilence", True)
				and path
				and os.path.exists(path)
				and path.lower().endswith(".wav")
			):
				ensure_temp_dir()
				import tempfile
				fd, out_path = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
				os.close(fd)
				result = trim_silence_wav(
					path,
					output_path=out_path,
					min_silence_sec=float(self.conf.get("minSilenceSec", 2.0)),
				)
				if result and result != path:
					path = result
			if path.lower().endswith(".wav"):
				path = downsample_to_voice_wav(path) or path
			if self.useDirectAudio:
				self._post_audio_path(path)
			else:
				self.process_transcription(path)
			return
		if not self.conf:
			raise ValueError("No configuration provided.")
		self.audioData = bytearray()
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
		if self.conf.get("trimSilence", True):
			trim_silence_wav(
				filename,
				min_silence_sec=float(self.conf.get("minSilenceSec", 2.0)),
			)
		downsample_to_voice_wav(filename)
		if self.useDirectAudio:
			self._post_audio_path(filename)
			return
		if self._notifyWindow:
			# Translators: Text in recording thread status and error messages.
			self._notifyWindow.message(_("Transcribing..."))
		self.process_transcription(filename)

	def record_audio(self, sampleRate):
		"""Record from microphone using Windows WinMM API (no external deps)."""
		self._recording = True
		try:
			self._record_audio_winmm(sampleRate)
		except Exception as e:
			log.error(f"Recording failed: {e}", exc_info=True)
		self._recording = False

	def _record_audio_winmm(self, sampleRate):
		"""Record using winmm.dll - Windows only, standard library only."""
		channels = self.conf["channels"]
		bps = 16  # 16-bit PCM
		block_align = channels * 2
		bytes_per_sec = sampleRate * block_align

		class WAVEFORMATEX(ctypes.Structure):
			_fields_ = [
				("wFormatTag", wintypes.WORD),
				("nChannels", wintypes.WORD),
				("nSamplesPerSec", wintypes.DWORD),
				("nAvgBytesPerSec", wintypes.DWORD),
				("nBlockAlign", wintypes.WORD),
				("wBitsPerSample", wintypes.WORD),
				("cbSize", wintypes.WORD),
			]

		class WAVEHDR(ctypes.Structure):
			_fields_ = [
				("lpData", ctypes.c_void_p),
				("dwBufferLength", wintypes.DWORD),
				("dwBytesRecorded", wintypes.DWORD),
				("dwUser", ctypes.POINTER(ctypes.c_void_p)),
				("dwFlags", wintypes.DWORD),
				("dwLoops", wintypes.DWORD),
				("lpNext", ctypes.c_void_p),
				("reserved", ctypes.c_void_p),
			]

		wfx = WAVEFORMATEX()
		wfx.wFormatTag = WAVE_FORMAT_PCM
		wfx.nChannels = channels
		wfx.nSamplesPerSec = sampleRate
		wfx.nAvgBytesPerSec = bytes_per_sec
		wfx.nBlockAlign = block_align
		wfx.wBitsPerSample = bps
		wfx.cbSize = 0

		winmm = ctypes.windll.winmm
		hwi = wintypes.HANDLE()
		hEvent = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
		if not hEvent:
			raise RuntimeError("CreateEvent failed")

		try:
			# waveInOpen(phwi, uDeviceID, pwfx, dwCallback, dwCallbackInstance, fdwOpen)
			res = winmm.waveInOpen(
				ctypes.byref(hwi),
				WAVE_MAPPER,
				ctypes.byref(wfx),
				hEvent,
				0,
				CALLBACK_EVENT,
			)
			if res != 0:
				raise RuntimeError(f"waveInOpen failed: {res}")

			# Two buffers, ~62.5ms each at 16kHz mono
			buf_size = 2048
			buffers = []
			headers = []
			for _ in range(2):
				buf = (ctypes.c_char * buf_size)()
				hdr = WAVEHDR()
				hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
				hdr.dwBufferLength = buf_size
				hdr.dwFlags = 0
				headers.append((hdr, buf))

				res = winmm.waveInPrepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
				if res != 0:
					raise RuntimeError(f"waveInPrepareHeader failed: {res}")
				res = winmm.waveInAddBuffer(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
				if res != 0:
					winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
					raise RuntimeError(f"waveInAddBuffer failed: {res}")

			res = winmm.waveInStart(hwi)
			if res != 0:
				raise RuntimeError(f"waveInStart failed: {res}")

			while not self._stopRecord and self._recording and not self._wantAbort:
				wait = ctypes.windll.kernel32.WaitForSingleObject(hEvent, 100)
				if wait == WAIT_OBJECT_0:
					for hdr, buf in headers:
						if hdr.dwFlags & WHDR_DONE:
							if hdr.dwBytesRecorded > 0:
								self.audioData.extend(buf[: hdr.dwBytesRecorded])
							hdr.dwFlags = 0
							hdr.dwBytesRecorded = 0
							winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
							winmm.waveInPrepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
							winmm.waveInAddBuffer(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
		finally:
			winmm.waveInStop(hwi)
			winmm.waveInReset(hwi)
			for hdr, _ in headers:
				if hdr.dwFlags != 0:
					winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
			winmm.waveInClose(hwi)
			ctypes.windll.kernel32.CloseHandle(hEvent)

	def save_wav(self, filename, data, sampleRate):
		if self._wantAbort:
			return
		wavefile = wave.open(filename, "wb")
		wavefile.setnchannels(self.conf["channels"])
		wavefile.setsampwidth(2)
		wavefile.setframerate(sampleRate)
		wavefile.writeframes(data)
		wavefile.close()

	def stop(self):
		self._stopRecord = True
		self._recording = False

	def get_filename(self):
		ensure_temp_dir()
		return os.path.join(TEMP_DIR, f"tmp_{uuid.uuid4().hex}.wav")

	def _post_audio_path(self, path):
		if isinstance(path, str):
			path_str = path
		elif isinstance(path, (list, tuple)) and path:
			path_str = path[0] if isinstance(path[0], str) else str(path[0])
		else:
			path_str = None
		if not path_str or not os.path.exists(path_str):
			return
		stop_progress_sound()
		if self._notifyWindow:
			wx.PostEvent(self._notifyWindow, ResultEvent(AudioInputResult(path_str)))
		elif self._onAudioPath:
			core.callLater(200, self._onAudioPath, path_str)
		else:
			# Translators: AI-Hub — global dictation recording: brief status feedback (speech/braille), not a full dialog.
			ui.message(_("Audio file ready for direct input"))

	def _get_transcription_provider(self):
		return self._transcriptionProvider

	def _transcribe_whisper_cpp(self, filename):
		"""Transcribe via local whisper.cpp server."""
		host = self.conf["whisper.cpp"]["host"].strip()
		if not host:
			host = "http://127.0.0.1:8081"
		if not re.match(r"^https?://", host, re.I):
			host = "http://" + host
		url = host + "/inference"
		boundary = uuid.uuid4().hex
		with open(filename, "rb") as audio_file:
			file_data = audio_file.read()
		body = (
			f'--{boundary}\r\n'.encode()
			+ f'Content-Disposition: form-data; name="file"; filename="tmp.wav"\r\n'.encode()
			+ b'Content-Type: audio/wav\r\n\r\n'
			+ file_data
			+ f'\r\n--{boundary}\r\n'.encode()
			+ b'Content-Disposition: form-data; name="temperature"\r\n\r\n0\r\n'
			+ f'--{boundary}\r\n'.encode()
			+ b'Content-Disposition: form-data; name="response-format"\r\n\r\njson\r\n'
			+ f'--{boundary}--\r\n'.encode()
		)
		headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
		req = Request(url, body, headers)
		response = urlopen(req, timeout=3600)
		if response.getcode() != 200:
			raise RuntimeError(f"Error: {response.getcode()}")
		data = json.loads(response.read().decode("utf-8"))
		if "error" in data:
			raise RuntimeError(f"Error: {data['error']}")
		return WhisperTranscription(data.get("text", ""))

	def _transcribe_mistral(self, filename):
		"""Transcribe via Mistral Voxtral API."""
		manager = apikeymanager.get(Provider.MistralAI)
		api_key = manager.get_api_key(account_id=self._transcriptionAccountId)
		if not api_key or not api_key.strip():
			# Translators: Text in recording thread status and error messages.
			raise ValueError(_("No Mistral API key configured. Please add one in AI-Hub settings."))
		model = self._transcriptionModel or self.conf.get("voxtralModel", "voxtral-mini-latest")
		return transcribe_audio_mistral(api_key=api_key, file_path=filename, model=model)

	def _transcribe_openai(self, filename):
		"""Transcribe via OpenAI Whisper API."""
		if not self.client:
			# Translators: Text in recording thread status and error messages.
			raise ValueError(_("OpenAI client is not available for transcription."))
		client = configure_client_for_provider(
			self.client,
			Provider.OpenAI,
			account_id=self._transcriptionAccountId,
			clone=True,
		)
		model = self._transcriptionModel or self.conf.get("whisperModel", "whisper-1")
		with open(filename, "rb") as audio_file:
			return client.audio.transcriptions.create(
				model=model,
				file=audio_file,
				response_format=self.responseFormat,
			)

	def process_transcription(self, filename):
		if self._wantAbort:
			return
		provider = self._get_transcription_provider()
		try:
			transcription = None
			if provider == TranscriptionProvider.WHISPER_CPP:
				transcription = self._transcribe_whisper_cpp(filename)
			elif provider == TranscriptionProvider.MISTRAL:
				transcription = self._transcribe_mistral(filename)
			else:
				transcription = self._transcribe_openai(filename)
		except Exception as err:
			log.error(f"Transcription error: {err}", exc_info=True)
			stop_progress_sound()
			msg = truncate_error_for_user(err)
			if self._notifyWindow:
				wx.PostEvent(self._notifyWindow, ResultEvent(msg))
			else:
				def _show_error():
					gui.messageBox(
						msg,
						# Translators: Title of the error dialog when global dictation transcription fails; body text is a shortened technical error from the engine.
						_("Transcription Error"),
						wx.OK | wx.ICON_ERROR,
					)
				wx.CallAfter(_show_error)
			return
		if transcription is None:
			return
		if self._notifyWindow:
			if isinstance(transcription, str):
				transcription = WhisperTranscription(
					transcription
				)
			wx.PostEvent(self._notifyWindow, ResultEvent(transcription))
		elif self._onTranscription:
			stop_progress_sound()
			text = get_transcription_text(transcription)
			if text:
				core.callLater(200, self._onTranscription, text)
		else:
			stop_progress_sound()
			core.callLater(200, retrieveTranscription, transcription)

	def abort(self):
		self._stopRecord = 1
		self._wantAbort = 1
