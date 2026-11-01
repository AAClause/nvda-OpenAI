"""Custom OpenAI-compatible endpoint transcription.

Covers provider selection, custom endpoint resolution (base URL, API key,
model, account mapping) and the wiring of transcription requests to the
custom endpoint's /audio/transcriptions URL.
"""

from unittest.mock import patch

import pytest

from AIHub import transcription
from AIHub.apiclient import OpenAIClient, configure_client_for_provider
from AIHub.consts import Provider, TranscriptionProvider
from AIHub.recordthread import RecordThread, transcribe_audio_file

CUSTOM = TranscriptionProvider.CUSTOM_OPENAI.value


@pytest.fixture(autouse=True)
def keys(key_store):
	return key_store.get(Provider.CustomOpenAI)


@pytest.fixture
def wav(tmp_path):
	path = tmp_path / "sample.wav"
	path.write_bytes(b"RIFFdummywavecontentdata")
	return str(path)


def fake_response(text, requests=None):
	def open_bytes(opener, req, timeout=120):
		if requests is not None:
			requests.append(req)
		return ('{"text": "%s"}' % text).encode(), "application/json"
	return patch("AIHub.apiclient._client._open_bytes", side_effect=open_bytes)


@pytest.mark.parametrize("conf, expected", [
	({}, TranscriptionProvider.OPENAI.value),
	# Legacy whisper.cpp flag.
	({"whisper.cpp": {"enabled": True}}, TranscriptionProvider.WHISPER_CPP.value),
	({"transcriptionProvider": "custom_openai"}, CUSTOM),
	({"transcriptionProvider": "CustomOpenAI"}, CUSTOM),
	({"transcriptionProvider": "CUSTOM-OPENAI"}, CUSTOM),
	({"transcriptionProvider": "MistralAI"}, TranscriptionProvider.MISTRAL.value),
])
def test_get_transcription_provider(conf, expected):
	assert transcription.get_transcription_provider(conf) == expected


def test_resolve_custom_endpoint_active_account(keys):
	acc_id = keys.add_account(
		name="LocalAI Server", api_key="sk-local-test-key", base_url="http://192.168.1.100:8080/v1", set_active=True,
	)
	info = transcription.resolve_transcription_endpoint(
		{"audio": {"transcriptionProvider": CUSTOM, "whisperModel": "whisper-1"}}
	)
	assert info["provider"] == CUSTOM
	assert info["api_provider"] == Provider.CustomOpenAI
	assert info["account_id"] == acc_id
	assert info["base_url"] == "http://192.168.1.100:8080/v1"
	assert info["api_key"] == "sk-local-test-key"
	assert info["model"] == "whisper-1"


def test_resolve_custom_endpoint_mapped_account(keys):
	keys.add_account(name="Server 1", api_key="key-1", base_url="http://host1:8080/v1", set_active=True)
	acc2 = keys.add_account(name="OmniRoute", api_key="key-2", base_url="https://api.omniroute.ai/v1", set_active=False)
	info = transcription.resolve_transcription_endpoint({"audio": {
		"transcriptionProvider": CUSTOM,
		"customOpenaiTranscriptionAccountId": acc2,
		"customOpenaiTranscriptionModel": "custom-whisper-large",
	}})
	assert info["account_id"] == acc2
	assert info["base_url"] == "https://api.omniroute.ai/v1"
	assert info["api_key"] == "key-2"
	assert info["model"] == "custom-whisper-large"


def test_configure_client_for_custom_provider_normalizes_trailing_slash(keys):
	acc = keys.add_account(name="Custom", api_key="test-key", base_url="http://localhost:8000/v1/", set_active=True)
	client = configure_client_for_provider(OpenAIClient(), Provider.CustomOpenAI, account_id=acc)
	assert client.base_url == "http://localhost:8000/v1"
	assert client.api_key == "test-key"
	assert client.provider == Provider.CustomOpenAI


@pytest.mark.parametrize("api_key, authorization", [
	("sk-omni-test", "Bearer sk-omni-test"),
	# Local servers often need no key: no Authorization header at all.
	("", None),
])
def test_record_thread_transcribes_to_custom_url(keys, wav, api_key, authorization):
	keys.add_account(name="Server", api_key=api_key, base_url="https://proxy.example.com/v1", set_active=True)
	conf = {"transcriptionProvider": CUSTOM, "whisperModel": "whisper-1"}
	requests = []
	with fake_response("Transcribed", requests):
		result = RecordThread(OpenAIClient(), conf=conf, transcriptionProvider=CUSTOM)._transcribe_custom_openai(wav)
	assert requests[0].full_url == "https://proxy.example.com/v1/audio/transcriptions"
	assert requests[0].headers.get("Authorization") == authorization
	assert result.text == "Transcribed"


def test_transcribe_audio_file_dispatches_to_custom_openai(keys, wav):
	keys.add_account(name="LocalAI", api_key="key", base_url="http://localhost:8080/v1", set_active=True)
	with fake_response("File helper transcription"):
		text = transcribe_audio_file(wav, {"transcriptionProvider": CUSTOM, "whisperModel": "whisper-1"})
	assert text == "File helper transcription"


def test_record_thread_falls_back_to_active_account(keys):
	active_id = keys.add_account(name="Active", api_key="key", base_url="http://active:8000/v1", set_active=True)
	conf = {"transcriptionProvider": CUSTOM, "customOpenaiTranscriptionAccountId": "non_existent_id"}
	assert RecordThread(OpenAIClient(), conf=conf, transcriptionProvider=CUSTOM)._transcriptionAccountId == active_id
