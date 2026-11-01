"""Recording in the conversation window: dictation and voice messages.

Two explicit commands, so the result never depends on the selected model:

- Dictate (Ctrl+R) transcribes the recording into the prompt with the
  transcription service set in the settings, whatever the model.
- Voice message (Ctrl+Shift+A) attaches the recording itself to the next
  message, for models that accept audio input.

Either command stops a recording in progress.
"""

import addonHandler
from logHandler import log
from .consts import Provider, TranscriptionProvider
from .recordthread import RecordThread
from .thread_shutdown import stop_worker_thread
from .transcription import get_transcription_provider, transcription_ready

addonHandler.initTranslation()

# (transcription provider, account provider, account-id config key, model-id hints)
# The selected chat account and model are reused for transcription when they
# belong to the transcription provider and the model looks like a speech one.
_TRANSCRIPTION_ACCOUNTS = (
	(TranscriptionProvider.OPENAI, Provider.OpenAI, "openaiTranscriptionAccountId", ("whisper", "transcribe")),
	(TranscriptionProvider.MISTRAL, Provider.MistralAI, "mistralTranscriptionAccountId", ("voxtral", "transcribe")),
	(TranscriptionProvider.CUSTOM_OPENAI, Provider.CustomOpenAI, "customOpenaiTranscriptionAccountId", ("whisper", "transcribe")),
)


class ConversationDictationMixin:
	"""Mixin for ConversationDialog."""

	def _modelAcceptsAudio(self) -> bool:
		model = self.getCurrentModel()
		return bool(model and model.audioInput)

	def _resolveDictationConfig(self):
		"""Transcription service, account and model used to dictate."""
		model = None
		account = None
		try:
			model = self.getCurrentModel()
		except Exception:
			log.debug("Ignored error", exc_info=True)
		try:
			account = self.getCurrentAccount()
		except Exception:
			log.debug("Ignored error", exc_info=True)
		transcription_provider = get_transcription_provider(self.conf["audio"])
		transcription_account_id = None
		transcription_model = None
		for provider, account_provider, account_key, model_hints in _TRANSCRIPTION_ACCOUNTS:
			if transcription_provider != provider:
				continue
			transcription_account_id = self.conf["audio"].get(account_key, "").strip()
			if not transcription_account_id and account and account.get("provider") == account_provider:
				transcription_account_id = account.get("id")
			model_id = getattr(model, "id", "")
			if (
				model
				and model.provider == account_provider
				and isinstance(model_id, str)
				and any(hint in model_id.lower() for hint in model_hints)
			):
				transcription_model = model_id
		return {
			"transcription_provider": transcription_provider,
			"transcription_account_id": transcription_account_id,
			"transcription_model": transcription_model,
		}

	def _stopRecordingIfAny(self, evt) -> bool:
		if isinstance(self.worker, RecordThread):
			self.onStopRecord(evt)
			return True
		return False

	def onRecord(self, evt):
		"""Dictate into the prompt (Ctrl+R)."""
		if self._stopRecordingIfAny(evt) or self.worker:
			return
		if not transcription_ready(self.conf["audio"]):
			# Translators: Spoken when dictation (Ctrl+R) is requested but no transcription service has an account or URL.
			msg = _("No transcription service is configured. Choose one in the AI-Hub settings, Recording section.")
			if self._modelAcceptsAudio():
				# Translators: Added to the previous message when the selected model accepts audio input.
				msg += " " + _("You can also press Ctrl+Shift+A to send a voice message to this model.")
			self.message(msg)
			return
		# Translators: Spoken when dictation starts; the recording will be transcribed into the prompt.
		self.message(_("Dictation"))
		self._startRecording(direct=False)

	def onRecordVoiceMessage(self, evt):
		"""Attach a voice recording to the next message (Ctrl+Shift+A)."""
		if self._stopRecordingIfAny(evt) or self.worker:
			return
		if not self._modelAcceptsAudio():
			# Translators: Spoken when a voice message (Ctrl+Shift+A) is requested but the selected model does not accept audio.
			self.message(_("This model does not accept audio. Press Ctrl+R to dictate instead."))
			return
		# Translators: Spoken when recording a voice message starts; the audio will be attached to the next message.
		self.message(_("Voice message"))
		self._startRecording(direct=True)

	def _startRecording(self, direct: bool):
		cfg = self._resolveDictationConfig()
		self.disableControls()
		page = self.get_active_page()
		self._dictation_page = page
		self._worker_page = page
		page.worker = RecordThread(
			self.client,
			self,
			conf=self.conf["audio"],
			responseFormat="json",
			useDirectAudio=direct,
			transcriptionProvider=cfg["transcription_provider"],
			transcriptionAccountId=cfg["transcription_account_id"],
			transcriptionModel=cfg["transcription_model"],
		)
		page.worker.start()

	def onStopRecord(self, evt):
		self.disableControls()
		wp = getattr(self, "_worker_page", None)
		if wp and isinstance(wp.worker, RecordThread):
			stop_worker_thread(wp.worker)
			wp.worker = None
		self.enableControls()
