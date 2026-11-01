"""Dictation in the conversation window: transcription provider choice and recording."""

import addonHandler
from logHandler import log
from .consts import Provider, TranscriptionProvider
from .recordthread import RecordThread
from .thread_shutdown import stop_worker_thread
from .transcription import get_transcription_provider

addonHandler.initTranslation()


class ConversationDictationMixin:
	"""Mixin for ConversationDialog."""

	def _resolveDictationConfig(self):
		model = None
		account = None
		try:
			model = self.getCurrentModel()
		except Exception:
			log.debug("Ignored error", exc_info=True)
			model = None
		try:
			account = self.getCurrentAccount()
		except Exception:
			log.debug("Ignored error", exc_info=True)
			account = None
		use_direct = bool(getattr(model, "audioInput", False))
		transcription_provider = get_transcription_provider(self.conf["audio"])
		transcription_account_id = None
		transcription_model = None
		if not use_direct:
			if transcription_provider == TranscriptionProvider.OPENAI:
				transcription_account_id = self.conf["audio"].get("openaiTranscriptionAccountId", "").strip()
				if not transcription_account_id and account and account.get("provider") == Provider.OpenAI:
					transcription_account_id = account.get("id")
				model_id = getattr(model, "id", "")
				if (
					model
					and getattr(model, "provider", "") == Provider.OpenAI
					and isinstance(model_id, str)
					and (model_id == "whisper-1" or "transcribe" in model_id.lower())
				):
					transcription_model = model_id
			elif transcription_provider == TranscriptionProvider.MISTRAL:
				transcription_account_id = self.conf["audio"].get("mistralTranscriptionAccountId", "").strip()
				if not transcription_account_id and account and account.get("provider") == Provider.MistralAI:
					transcription_account_id = account.get("id")
				model_id = getattr(model, "id", "")
				if (
					model
					and getattr(model, "provider", "") == Provider.MistralAI
					and isinstance(model_id, str)
					and ("voxtral" in model_id.lower() or "transcribe" in model_id.lower())
				):
					transcription_model = model_id
			elif transcription_provider == TranscriptionProvider.CUSTOM_OPENAI:
				transcription_account_id = self.conf["audio"].get("customOpenaiTranscriptionAccountId", "").strip()
				if not transcription_account_id and account and account.get("provider") == Provider.CustomOpenAI:
					transcription_account_id = account.get("id")
				model_id = getattr(model, "id", "")
				if (
					model
					and getattr(model, "provider", "") == Provider.CustomOpenAI
					and isinstance(model_id, str)
					and (model_id == "whisper-1" or "transcribe" in model_id.lower() or "whisper" in model_id.lower())
				):
					transcription_model = model_id
		return {
			"use_direct": use_direct,
			"transcription_provider": transcription_provider,
			"transcription_account_id": transcription_account_id,
			"transcription_model": transcription_model,
		}

	def onRecord(self, evt):
		if isinstance(self.worker, RecordThread):
			self.onStopRecord(evt)
			return
		if self.worker:
			return
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
			useDirectAudio=cfg["use_direct"],
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
