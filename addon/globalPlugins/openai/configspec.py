import config
from .consts import (
	DEFAULT_MODEL,
	DEFAULT_TOP_P,
	DEFAULT_N,
	TOP_P_MIN,
	TOP_P_MAX,
	N_MIN,
	N_MAX,
	TTS_MODELS,
	TTS_DEFAULT_MODEL,
	TTS_VOICES,
	TTS_DEFAULT_VOICE
)

confSpecs = {
	"update": {
		"check": "boolean(default=True)",
		"channel": "string(default='stable')"
	},
	"model": f"string(default={DEFAULT_MODEL.name})",
	"topP": f"integer(min={TOP_P_MIN}, max={TOP_P_MAX}, default={DEFAULT_TOP_P})",
	"n": f"integer(min={N_MIN}, max={N_MAX}, default={DEFAULT_N})",
	"stream": "boolean(default=True)",
	"TTSModel": f"option({', '.join(TTS_MODELS)}, default={TTS_DEFAULT_MODEL})",
	"TTSVoice": f"option({', '.join(TTS_VOICES)}, default={TTS_DEFAULT_VOICE})",
	"blockEscapeKey": "boolean(default=False)",
	"conversationMode": "boolean(default=True)",
	"saveSystem": "boolean(default=true)",
	"advancedMode": "boolean(default=False)",
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
		"whisper.cpp": {
			"enabled": "boolean(default=False)",
			"host": "string(default='http://127.0.0.1:8081')"
		},
		"sampleRate": "integer(min=8000, max=48000, default=16000)",
		"channels": "integer(min=1, max=2, default=1)",
		"dtype": "string(default=int16)"
	},
	"renewClient": "boolean(default=False)",
	"debug": "boolean(default=False)"
}
config.conf.spec["OpenAI"] = confSpecs
