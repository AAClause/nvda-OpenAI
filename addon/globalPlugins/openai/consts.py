import os
import globalVars
import addonHandler
from .model import Model

addonHandler.initTranslation()

ADDON_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(globalVars.appArgs.configPath, "openai")

DEFAULT_TOP_P = 100
DEFAULT_N = 1
TOP_P_MIN = 0
TOP_P_MAX = 100
N_MIN = 1
N_MAX = 10
TTS_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
TTS_DEFAULT_VOICE = "nova"
TTS_MODELS = ["tts-1", "tts-1-hd"]
TTS_DEFAULT_MODEL = "tts-1"
MODEL_VISION = "gpt-4-vision-preview"
MODELS = [
	Model("gpt-3.5-turbo-1106", _("Updated GPT 3.5 Turbo. The latest GPT-3.5 Turbo model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."), 16385, 4096),
	Model("gpt-3.5-turbo-0613", _("Same capabilities as the standard gpt-3.5-turbo model but with 4 times the context"), 16384, 4096),
	Model("gpt-4-0613", _("More capable than any GPT-3.5 model, able to do more complex tasks, and optimized for chat"), 8192),
	Model("gpt-4-1106-preview", _("The latest GPT-4 model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."), 128000, 4096),
	Model(MODEL_VISION, _("GPT-4 Turbo with vision. Ability to understand images, in addition to all other GPT-4 Turbo capabilities."), 128000, 4096),
	Model("gpt-4-32k-0613", _("Same capabilities as the standard gpt-4 mode but with 4x the context length."), 32768, 8192),
]
DEFAULT_MODEL = MODELS[0]
