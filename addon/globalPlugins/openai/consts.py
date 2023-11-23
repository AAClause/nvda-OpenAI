import os
import globalVars

ADDON_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(globalVars.appArgs.configPath, "openai")

DEFAULT_MODEL = "gpt-3.5-turbo"
DEFAULT_TEMPERATURE = 100
DEFAULT_TOP_P = 100
DEFAULT_MAX_TOKENS = 2048
DEFAULT_N = 1

TEMPERATURE_MIN = 0
TEMPERATURE_MAX = 200
TOP_P_MIN = 0
TOP_P_MAX = 100
MAX_TOKENS_MIN = 0
MAX_TOKENS_MAX = 8192
N_MIN = 1
N_MAX = 10
TTS_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
TTS_DEFAULT_VOICE = "nova"
TTS_MODELS = ["tts-1", "tts-1-hd"]
TTS_DEFAULT_MODEL = "tts-1"
