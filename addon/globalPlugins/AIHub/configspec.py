import config
from .consts import (
	DEFAULT_MODEL,
	DEFAULT_TOP_P,
	TOP_P_MIN,
	TOP_P_MAX,
	TTS_MODELS,
	TTS_DEFAULT_MODEL,
	TTS_VOICES,
	TTS_DEFAULT_VOICE,
	WHISPER_MODELS,
	VOXTRAL_MODELS,
	TRANSCRIPTION_PROVIDERS,
	DEFAULT_TRANSCRIPTION_PROVIDER,
	REASONING_EFFORT_OPTIONS,
	DEFAULT_REASONING_EFFORT,
)

confSpecs = {
	"model": f"string(default={DEFAULT_MODEL})",
	"modelVision": f"string(default={DEFAULT_MODEL})",
	"topP": f"integer(min={TOP_P_MIN}, max={TOP_P_MAX}, default={DEFAULT_TOP_P})",
	"stream": "boolean(default=True)",
	"reasoningEffort": f"option({', '.join(REASONING_EFFORT_OPTIONS)}, default={DEFAULT_REASONING_EFFORT})",
	"adaptiveThinking": "boolean(default=True)",
	"TTSModel": f"option({', '.join(TTS_MODELS)}, default={TTS_DEFAULT_MODEL})",
	"TTSVoice": f"option({', '.join(TTS_VOICES)}, default={TTS_DEFAULT_VOICE})",
	"blockEscapeKey": "boolean(default=False)",
	"saveSystem": "boolean(default=true)",
	"autoSaveConversation": "boolean(default=True)",
	"images": {
		"maxHeight": "integer(min=0, default=720)",
		"maxWidth": "integer(min=0, default=0)",
		"quality": "integer(min=0, max=100, default=85)",
		"resize": "boolean(default=False)",
		"resizeInfoDisplayed": "boolean(default=False)",
		"useCustomPrompt": "boolean(default=False)",
		"customPromptText": 'string(default="")'
	},
	"audio": {
		"transcriptionProvider": f"option({', '.join(TRANSCRIPTION_PROVIDERS)}, default='{DEFAULT_TRANSCRIPTION_PROVIDER}')",
		"whisperModel": f"string(default={WHISPER_MODELS[0]})",
		"voxtralModel": f"string(default={VOXTRAL_MODELS[0]})",
		"openaiTranscriptionAccountId": 'string(default="")',
		"mistralTranscriptionAccountId": 'string(default="")',
		"whisper.cpp": {
			"enabled": "boolean(default=False)",
			"host": "string(default='http://127.0.0.1:8081')"
		},
		"sampleRate": "integer(min=8000, max=48000, default=16000)",
		"channels": "integer(min=1, max=2, default=1)",
		"dtype": "string(default=int16)",
		"trimSilence": "boolean(default=True)",
		"minSilenceSec": "integer(min=1, max=10, default=2)"
	},
	"chatFeedback": {
		"sndResponsePending": "boolean(default=True)",
		"sndResponseReceived": "boolean(default=True)",
		"sndResponseSent": "boolean(default=True)",
		"sndTaskInProgress": "boolean(default=True)",
		"focusHistoryOnAssistantResponse": "boolean(default=False)",
		"speechResponseReceived": "boolean(default=True)",
	},
	"renewClient": "boolean(default=False)",
	"debug": "boolean(default=False)",
}


def _copy_missing(dst, src):
	try:
		items = list(src.items())
	except Exception:
		return
	for key, value in items:
		if key not in dst:
			dst[key] = value
			continue
		try:
			dst_child = dst.get(key)
		except Exception:
			dst_child = None
		if hasattr(dst_child, "items") and hasattr(value, "items"):
			_copy_missing(dst_child, value)


def _migrate_config_section_if_needed():
	legacy = config.conf.get("OpenAI")
	new = config.conf.get("AIHub")
	if new is None:
		config.conf["AIHub"] = {}
		new = config.conf["AIHub"]
	if hasattr(legacy, "items"):
		_copy_missing(new, legacy)


config.conf.spec["AIHub"] = confSpecs
config.conf.spec["OpenAI"] = confSpecs
_migrate_config_section_if_needed()
