"""Helpers for ask-question voice flow and audio playback."""

import base64
import ctypes
import os
import speech
import threading
import uuid

import config
import queueHandler
import ui
import wx
from logHandler import log

from . import apikeymanager
from .apiclient import configure_client_for_provider, truncate_error_for_user
from .consts import (
	AUDIO_EXT_TO_FORMAT,
	ContentType,
	Role,
	TEMP_DIR,
	TTS_DEFAULT_VOICE,
	ensure_temp_dir,
)
from .model import getModels

MCI_ALIAS_ASK = "ask_audio"


def find_provider_for_ask(model_id: str, requires_audio_model: bool):
	"""Find an eligible provider/model for ask-question flow."""
	for provider in apikeymanager.AVAILABLE_PROVIDERS:
		if not apikeymanager.get(provider).isReady():
			continue
		try:
			for model in getModels(provider):
				if model.id == model_id:
					if requires_audio_model and not getattr(model, "audioInput", False):
						continue
					if (not requires_audio_model) and getattr(model, "audioInput", False) and not getattr(model, "audioOutput", False):
						continue
					return (provider, model_id, model)
		except Exception:
			continue
	if requires_audio_model:
		for provider in apikeymanager.AVAILABLE_PROVIDERS:
			if not apikeymanager.get(provider).isReady():
				continue
			try:
				for model in getModels(provider):
					if getattr(model, "audioInput", False):
						return (provider, model.id, model)
			except Exception:
				continue
	for provider in apikeymanager.AVAILABLE_PROVIDERS:
		if not apikeymanager.get(provider).isReady():
			continue
		try:
			for model in getModels(provider):
				if getattr(model, "audioOutput", False):
					return (provider, model.id, model)
				if not getattr(model, "audioInput", False):
					return (provider, model.id, model)
		except Exception:
			continue
	return (None, None, None)


def mci_play_wav(path: str) -> bool:
	"""Play WAV via MCI (no size limit, can be stopped)."""
	try:
		winmm = ctypes.windll.winmm
		path_escaped = path.replace("\\", "\\\\")
		cmd_open = f'open "{path_escaped}" type waveaudio alias {MCI_ALIAS_ASK}'
		if winmm.mciSendStringW(cmd_open, None, 0, 0) != 0:
			return False
		cmd_play = f"play {MCI_ALIAS_ASK}"
		if winmm.mciSendStringW(cmd_play, None, 0, 0) != 0:
			winmm.mciSendStringW(f"close {MCI_ALIAS_ASK}", None, 0, 0)
			return False
		return True
	except Exception:
		return False


def mci_stop_ask_audio() -> bool:
	"""Stop and close ask-question audio."""
	try:
		winmm = ctypes.windll.winmm
		winmm.mciSendStringW(f"stop {MCI_ALIAS_ASK}", None, 0, 0)
		winmm.mciSendStringW(f"close {MCI_ALIAS_ASK}", None, 0, 0)
		return True
	except Exception:
		return False


class AskQuestionThread(threading.Thread):
	"""One-shot API call: send question (text/audio), then play or speak response."""

	def __init__(self, client, question=None, conf=None, audio_path=None, plugin=None):
		super().__init__(daemon=True)
		self._client = client
		self._question = (question or "").strip()
		self._conf = conf or config.conf.get("AIHub", {})
		self._audio_path = audio_path
		self._plugin = plugin

	def run(self):
		content = None
		requires_audio_model = bool(self._audio_path)
		if self._audio_path and os.path.exists(self._audio_path):
			ext = os.path.splitext(self._audio_path)[1].lower()
			audio_format = AUDIO_EXT_TO_FORMAT.get(ext, "wav")
			with open(self._audio_path, "rb") as audio_file:
				data_b64 = base64.b64encode(audio_file.read()).decode("utf-8")
			content = [{"type": ContentType.INPUT_AUDIO, "input_audio": {"data": data_b64, "format": audio_format}}]
		elif self._question:
			content = self._question
		if not content:
			return
		model_id = self._conf.get("model", "gpt-4o")
		provider, model_id, model_obj = find_provider_for_ask(model_id, requires_audio_model)
		if not provider:
			if requires_audio_model:
				# Translators: Brief NVDA message when Ask Question was used with a recording but no model supports direct audio input.
				msg = _("No audio-capable model found. Enable direct audio in settings.")
			else:
				# Translators: Brief NVDA message when Ask Question cannot run because no API account is configured.
				msg = _("No API key configured. Please add one in AI-Hub settings.")
			queueHandler.queueFunction(queueHandler.eventQueue, ui.message, msg)
			return
		client = configure_client_for_provider(self._client, provider, clone=True)
		params = {
			"model": model_id,
			"messages": [{"role": Role.USER, "content": content}],
			"stream": False,
		}
		if model_obj and getattr(model_obj, "audioOutput", False):
			voice = self._conf.get("TTSVoice") or TTS_DEFAULT_VOICE
			params["modalities"] = ["text", "audio"]
			params["audio"] = {"voice": voice, "format": "wav"}
		try:
			response = client.chat.completions.create(**params)
			text = ""
			audio_path = None
			if response and response.choices:
				message = response.choices[0].message
				text = (message.content or "").strip()
				audio = getattr(message, "audio", None)
				if isinstance(audio, dict) and audio.get("data"):
					try:
						ensure_temp_dir()
						data = base64.b64decode(audio["data"])
						audio_path = os.path.join(TEMP_DIR, f"ask_response_{uuid.uuid4().hex}.wav")
						with open(audio_path, "wb") as audio_file:
							audio_file.write(data)
					except Exception as error:
						log.error(f"Failed to save audio response: {error}", exc_info=True)
						audio_path = None
			if audio_path and os.path.exists(audio_path):
				plugin_ref = self._plugin

				def _play():
					try:
						if mci_play_wav(audio_path):
							if plugin_ref:
								plugin_ref._askAudioPlaying = True
							# Translators: Brief NVDA message when Ask Question starts playing the synthesized answer through NVDA’s audio path.
							queueHandler.queueFunction(queueHandler.eventQueue, ui.message, _("Playing audio response"))
						else:
							os.startfile(audio_path)
							# Translators: Brief NVDA message when Ask Question opens the answer WAV in the default application because in-process playback failed or was skipped.
							queueHandler.queueFunction(queueHandler.eventQueue, ui.message, _("Playing audio response"))
					except Exception as error:
						log.error(f"Failed to play audio: {error}", exc_info=True)
						if text:
							queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, text)

				wx.CallAfter(_play)
			elif text:
				queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, text)
		except Exception as error:
			log.error(f"Ask question error: {error}", exc_info=True)
			queueHandler.queueFunction(
				queueHandler.eventQueue,
				ui.message,
				truncate_error_for_user(error),
			)
