"""Unit tests for Custom OpenAI-compatible endpoint transcription.

Verifies provider selection, custom endpoint resolution (base URL, API key,
model, account mapping) and the wiring of transcription requests to the
custom endpoint's /audio/transcriptions URL.
"""

import builtins
import enum
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if not hasattr(enum, "StrEnum"):
	class StrEnum(str, enum.Enum):
		def _generate_next_value_(name, start, count, last_values):
			return name.lower()

		def __str__(self):
			return str(self.value)

	enum.StrEnum = StrEnum

# Ensure built-in translation is available
builtins._ = lambda s: s

# Setup mocks for NVDA-specific dependencies before importing addon modules
mock_modules = [
	"addonHandler",
	"globalVars",
	"winsound",
	"config",
	"gui",
	"gui.guiHelper",
	"speech",
	"queueHandler",
	"controlTypes",
	"api",
	"core",
	"wx",
	"logHandler",
	"scriptHandler",
	"globalPluginHandler",
	"ui",
	"tones",
	"keyboardHandler",
	"braille",
	"PIL",
	"PIL.Image",
	"PIL.ImageGrab",
	"markdown_it",
]
for mod in mock_modules:
	if mod not in sys.modules:
		sys.modules[mod] = MagicMock()

class _GlobalPluginBase:
	pass

class _WxDialog:
	def __init__(self, *args, **kwargs):
		pass
	def Bind(self, *args, **kwargs):
		pass

sys.modules["wx"].Dialog = _WxDialog
sys.modules["wx"].Panel = type("Panel", (), {})
sys.modules["globalPluginHandler"].GlobalPlugin = _GlobalPluginBase
sys.modules["addonHandler"].initTranslation = MagicMock()
if not hasattr(sys.modules["addonHandler"], "Addon"):
	sys.modules["addonHandler"].Addon = MagicMock()

_mock_conf = MagicMock()
_mock_conf.spec = {}
_mock_conf.__getitem__.side_effect = lambda k: {"audio": {}, "images": {}, "chatFeedback": {}}.get(k, {})
sys.modules["config"].conf = _mock_conf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from addon.globalPlugins.AIHub.consts import (
	Provider,
	TranscriptionProvider,
	TRANSCRIPTION_PROVIDERS,
)
from addon.globalPlugins.AIHub import apikeymanager
from addon.globalPlugins.AIHub import transcription
from addon.globalPlugins.AIHub.apiclient import (
	OpenAIClient,
	configure_client_for_provider,
)
from addon.globalPlugins.AIHub.recordthread import RecordThread, transcribe_audio_file
from addon.globalPlugins.AIHub.tool_openai_transcription_dialog import OpenAITranscriptionToolDialog


class TestTranscriptionProviderListing(unittest.TestCase):
	"""Test transcription provider choices and resolution."""

	def test_choices_match_provider_ids(self):
		choice_ids = tuple(c[0] for c in transcription.get_transcription_provider_choices())
		self.assertEqual(choice_ids, TRANSCRIPTION_PROVIDERS)

	def test_get_transcription_provider_resolution(self):
		# Default fallback
		self.assertEqual(
			transcription.get_transcription_provider({}),
			TranscriptionProvider.OPENAI.value,
		)
		# Whisper.cpp backward compatibility flag
		self.assertEqual(
			transcription.get_transcription_provider({"whisper.cpp": {"enabled": True}}),
			TranscriptionProvider.WHISPER_CPP.value,
		)
		# Exact custom_openai
		self.assertEqual(
			transcription.get_transcription_provider({"transcriptionProvider": "custom_openai"}),
			TranscriptionProvider.CUSTOM_OPENAI.value,
		)
		# Casing / format variations
		self.assertEqual(
			transcription.get_transcription_provider({"transcriptionProvider": "CustomOpenAI"}),
			TranscriptionProvider.CUSTOM_OPENAI.value,
		)
		self.assertEqual(
			transcription.get_transcription_provider({"transcriptionProvider": "customopenai"}),
			TranscriptionProvider.CUSTOM_OPENAI.value,
		)
		self.assertEqual(
			transcription.get_transcription_provider({"transcriptionProvider": "CUSTOM-OPENAI"}),
			TranscriptionProvider.CUSTOM_OPENAI.value,
		)
		# Mistral variations
		self.assertEqual(
			transcription.get_transcription_provider({"transcriptionProvider": "MistralAI"}),
			TranscriptionProvider.MISTRAL.value,
		)


class TestCustomEndpointResolution(unittest.TestCase):
	"""Test custom endpoint resolution for base URL, API key, account, and model."""

	def setUp(self):
		self.test_dir = tempfile.TemporaryDirectory()
		apikeymanager._managers.clear()
		apikeymanager._store_cache_by_dir.clear()
		apikeymanager._migrated_dirs.clear()
		apikeymanager._loaded_data_dir = None
		apikeymanager.load(self.test_dir.name)
		self.manager = apikeymanager.get(Provider.CustomOpenAI)

	def tearDown(self):
		self.test_dir.cleanup()

	def test_resolve_custom_endpoint_active_account(self):
		acc_id = self.manager.add_account(
			name="LocalAI Server",
			api_key="sk-local-test-key",
			base_url="http://192.168.1.100:8080/v1",
			set_active=True,
		)
		conf = {
			"audio": {
				"transcriptionProvider": "custom_openai",
				"whisperModel": "whisper-1",
			}
		}
		endpoint_info = transcription.resolve_transcription_endpoint(conf)
		self.assertEqual(endpoint_info["provider"], "custom_openai")
		self.assertEqual(endpoint_info["api_provider"], Provider.CustomOpenAI)
		self.assertEqual(endpoint_info["account_id"], acc_id)
		self.assertEqual(endpoint_info["base_url"], "http://192.168.1.100:8080/v1")
		self.assertEqual(endpoint_info["api_key"], "sk-local-test-key")
		self.assertEqual(endpoint_info["model"], "whisper-1")

	def test_resolve_custom_endpoint_mapped_account(self):
		acc1 = self.manager.add_account(
			name="LocalAI Server 1",
			api_key="key-1",
			base_url="http://host1:8080/v1",
			set_active=True,
		)
		acc2 = self.manager.add_account(
			name="OmniRoute Endpoint",
			api_key="key-2",
			base_url="https://api.omniroute.ai/v1",
			set_active=False,
		)
		conf = {
			"audio": {
				"transcriptionProvider": "custom_openai",
				"customOpenAITranscriptionAccountId": acc2,
				"customOpenAITranscriptionModel": "custom-whisper-large",
			}
		}
		endpoint_info = transcription.resolve_transcription_endpoint(conf)
		self.assertEqual(endpoint_info["account_id"], acc2)
		self.assertEqual(endpoint_info["base_url"], "https://api.omniroute.ai/v1")
		self.assertEqual(endpoint_info["api_key"], "key-2")
		self.assertEqual(endpoint_info["model"], "custom-whisper-large")

	def test_configure_client_for_custom_provider_normalizes_trailing_slash(self):
		acc = self.manager.add_account(
			name="Custom",
			api_key="test-key",
			base_url="http://localhost:8000/v1/",
			set_active=True,
		)
		client = OpenAIClient()
		configured = configure_client_for_provider(
			client,
			Provider.CustomOpenAI,
			account_id=acc,
		)
		self.assertEqual(configured.base_url, "http://localhost:8000/v1")
		self.assertEqual(configured.api_key, "test-key")
		self.assertEqual(configured.provider, Provider.CustomOpenAI)


class TestTranscriptionWiring(unittest.TestCase):
	"""Test wiring of transcription calls to /audio/transcriptions on custom endpoint."""

	def setUp(self):
		self.test_dir = tempfile.TemporaryDirectory()
		apikeymanager._managers.clear()
		apikeymanager._store_cache_by_dir.clear()
		apikeymanager._migrated_dirs.clear()
		apikeymanager._loaded_data_dir = None
		apikeymanager.load(self.test_dir.name)
		self.manager = apikeymanager.get(Provider.CustomOpenAI)

	def tearDown(self):
		self.test_dir.cleanup()

	def test_record_thread_transcribes_to_custom_url(self):
		self.manager.add_account(
			name="OmniRoute",
			api_key="sk-omni-test",
			base_url="https://proxy.example.com/v1",
			set_active=True,
		)
		conf = {
			"transcriptionProvider": "custom_openai",
			"whisperModel": "whisper-1",
		}

		captured_req = None

		def fake_open_bytes(opener, req, timeout=120):
			nonlocal captured_req
			captured_req = req
			return b'{"text": "Transcribed text successfully"}', "application/json"

		with patch("addon.globalPlugins.AIHub.apiclient._client._open_bytes", side_effect=fake_open_bytes):
			audio_path = os.path.join(self.test_dir.name, "sample.wav")
			with open(audio_path, "wb") as f:
				f.write(b"RIFFdummywavecontentdata")

			rt = RecordThread(
				OpenAIClient(),
				conf=conf,
				transcriptionProvider="custom_openai",
			)
			res = rt._transcribe_custom_openai(audio_path)

		self.assertIsNotNone(captured_req)
		self.assertEqual(captured_req.full_url, "https://proxy.example.com/v1/audio/transcriptions")
		self.assertEqual(captured_req.headers.get("Authorization"), "Bearer sk-omni-test")
		self.assertEqual(res.text, "Transcribed text successfully")

	def test_record_thread_custom_endpoint_without_api_key(self):
		self.manager.add_account(
			name="Local Server Without Key",
			api_key="",
			base_url="http://localhost:11434/v1",
			set_active=True,
		)
		conf = {
			"transcriptionProvider": "custom_openai",
			"whisperModel": "whisper-1",
		}

		captured_req = None

		def fake_open_bytes(opener, req, timeout=120):
			nonlocal captured_req
			captured_req = req
			return b'{"text": "Local transcription response"}', "application/json"

		with patch("addon.globalPlugins.AIHub.apiclient._client._open_bytes", side_effect=fake_open_bytes):
			audio_path = os.path.join(self.test_dir.name, "sample.wav")
			with open(audio_path, "wb") as f:
				f.write(b"RIFFdummywavecontentdata")

			rt = RecordThread(
				OpenAIClient(),
				conf=conf,
				transcriptionProvider="custom_openai",
			)
			res = rt._transcribe_custom_openai(audio_path)

		self.assertIsNotNone(captured_req)
		self.assertEqual(captured_req.full_url, "http://localhost:11434/v1/audio/transcriptions")
		self.assertNotIn("Authorization", captured_req.headers)
		self.assertEqual(res.text, "Local transcription response")

	def test_transcribe_audio_file_dispatches_to_custom_openai(self):
		self.manager.add_account(
			name="LocalAI",
			api_key="key",
			base_url="http://localhost:8080/v1",
			set_active=True,
		)
		conf = {
			"transcriptionProvider": "custom_openai",
			"whisperModel": "whisper-1",
		}

		def fake_open_bytes(opener, req, timeout=120):
			return b'{"text": "File helper transcription"}', "application/json"

		with patch("addon.globalPlugins.AIHub.apiclient._client._open_bytes", side_effect=fake_open_bytes):
			audio_path = os.path.join(self.test_dir.name, "test.wav")
			with open(audio_path, "wb") as f:
				f.write(b"RIFFdummydata")

			result_text = transcribe_audio_file(audio_path, conf)

		self.assertEqual(result_text, "File helper transcription")


class TestToolDialogAccounts(unittest.TestCase):
	"""Test account handling in OpenAITranscriptionToolDialog and CustomOpenAITranscriptionToolDialog."""

	def setUp(self):
		self.test_dir = tempfile.TemporaryDirectory()
		apikeymanager._managers.clear()
		apikeymanager._store_cache_by_dir.clear()
		apikeymanager._migrated_dirs.clear()
		apikeymanager._loaded_data_dir = None
		apikeymanager.load(self.test_dir.name)
		self.custom_mgr = apikeymanager.get(Provider.CustomOpenAI)
		self.openai_mgr = apikeymanager.get(Provider.OpenAI)

	def tearDown(self):
		self.test_dir.cleanup()

	def test_load_transcription_accounts_includes_custom_when_openai_empty(self):
		acc_id = self.custom_mgr.add_account(
			name="Local Whisper",
			api_key="sk-local",
			base_url="http://localhost:5000/v1",
			set_active=True,
		)
		# Create a dummy dialog instance without GUI initialization
		dlg = OpenAITranscriptionToolDialog.__new__(OpenAITranscriptionToolDialog)
		dlg.provider = Provider.OpenAI
		dlg.manager = self.openai_mgr
		accounts = dlg._load_transcription_accounts()

		self.assertEqual(len(accounts), 1)
		self.assertEqual(accounts[0]["id"], acc_id)
		self.assertEqual(accounts[0]["_provider"], Provider.CustomOpenAI)
		self.assertEqual(accounts[0]["base_url"], "http://localhost:5000/v1")

	def test_configure_client_with_custom_account(self):
		acc_id = self.custom_mgr.add_account(
			name="OmniRoute Endpoint",
			api_key="omni-secret-key",
			base_url="https://api.omniroute.ai/v1",
			set_active=True,
		)
		dlg = OpenAITranscriptionToolDialog.__new__(OpenAITranscriptionToolDialog)
		dlg.provider = Provider.OpenAI
		dlg.manager = self.openai_mgr
		dlg._accounts = dlg._load_transcription_accounts()
		dlg.client = None

		dlg.configure_client(acc_id)
		self.assertIsNotNone(dlg.client)
		self.assertEqual(dlg.client.base_url, "https://api.omniroute.ai/v1")
		self.assertEqual(dlg.client.api_key, "omni-secret-key")
		self.assertEqual(dlg.client.provider, Provider.CustomOpenAI)


class TestRecordThreadEdgeCases(unittest.TestCase):
	"""Test RecordThread error handling and fallback behaviors."""

	def setUp(self):
		self.test_dir = tempfile.TemporaryDirectory()
		apikeymanager._managers.clear()
		apikeymanager._store_cache_by_dir.clear()
		apikeymanager._migrated_dirs.clear()
		apikeymanager._loaded_data_dir = None
		apikeymanager.load(self.test_dir.name)
		self.manager = apikeymanager.get(Provider.CustomOpenAI)

	def tearDown(self):
		self.test_dir.cleanup()

	def test_record_thread_fallback_to_active_account(self):
		active_id = self.manager.add_account(
			name="Active Account",
			api_key="key",
			base_url="http://active:8000/v1",
			set_active=True,
		)
		conf = {
			"transcriptionProvider": "custom_openai",
			"customOpenAITranscriptionAccountId": "non_existent_id",
		}
		rt = RecordThread(
			OpenAIClient(),
			conf=conf,
			transcriptionProvider="custom_openai",
		)
		self.assertEqual(rt._transcriptionAccountId, active_id)

	def test_record_thread_raises_when_no_base_url_configured(self):
		# No account configured in manager
		conf = {
			"transcriptionProvider": "custom_openai",
		}
		audio_path = os.path.join(self.test_dir.name, "sample.wav")
		with open(audio_path, "wb") as f:
			f.write(b"RIFFdata")

		rt = RecordThread(
			OpenAIClient(),
			conf=conf,
			transcriptionProvider="custom_openai",
		)
		with self.assertRaises(ValueError) as ctx:
			rt._transcribe_custom_openai(audio_path)
		self.assertIn("No Custom OpenAI endpoint URL configured", str(ctx.exception))

	def test_transcribe_audio_file_unsupported_extension_returns_none(self):
		txt_path = os.path.join(self.test_dir.name, "sample.txt")
		with open(txt_path, "w") as f:
			f.write("not an audio file")
		result = transcribe_audio_file(txt_path, {"transcriptionProvider": "custom_openai"})
		self.assertIsNone(result)


class TestConversationDialogResolution(unittest.TestCase):
	"""Test conversation dialog dictation config resolution for Custom OpenAI."""

	def test_resolve_dictation_config_custom_openai(self):
		from addon.globalPlugins.AIHub.conversation_dialog import ConversationDialog
		dlg = ConversationDialog.__new__(ConversationDialog)
		dlg.conf = {
			"audio": {
				"transcriptionProvider": "custom_openai",
				"customOpenAITranscriptionAccountId": "acc-123",
				"whisperModel": "whisper-1",
			}
		}
		model = MagicMock()
		model.audioInput = False
		model.provider = Provider.CustomOpenAI
		model.id = "custom-whisper"
		dlg.getCurrentModel = lambda: model
		dlg.getCurrentAccount = lambda: {"id": "acc-current", "provider": Provider.CustomOpenAI}

		cfg = dlg._resolveDictationConfig()
		self.assertFalse(cfg["use_direct"])
		self.assertEqual(cfg["transcription_provider"], "custom_openai")
		self.assertEqual(cfg["transcription_account_id"], "acc-123")
		self.assertEqual(cfg["transcription_model"], "custom-whisper")


if __name__ == "__main__":
	unittest.main()

