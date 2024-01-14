import os
import sys
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

# References:
# - https://platform.openai.com/docs/models/
# - https://docs.mistral.ai/platform/endpoints/
# - https://openrouter.ai/api/v1/models
MODELS = [
	Model(
		"OpenAI",
		"gpt-3.5-turbo-1106",
		# Translators: This is a model description
		_("Updated GPT 3.5 Turbo. The latest GPT-3.5 Turbo model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."),
		16385,
		4096        
	),
	Model(
		"OpenAI",
		"gpt-3.5-turbo-0613",
		# Translators: This is a model description
		_("Same capabilities as the standard gpt-3.5-turbo model but with 4 times the context"),
		16384,
		4096
	),
	Model(
		"OpenAI",
		"gpt-4-0613",
		# Translators: This is a model description
		_("More capable than any GPT-3.5 model, able to do more complex tasks, and optimized for chat"),
		8192
	),
	Model(
		"OpenAI",
		"gpt-4-1106-preview",
		# Translators: This is a model description
		_("The latest GPT-4 model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."),
		128000,
		4096,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4-vision-preview",
		# Translators: This is a model description
		_("GPT-4 Turbo with vision. Ability to understand images, in addition to all other GPT-4 Turbo capabilities."),
		128000,
		4096,
		vision=True,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4-32k-0613",
		# Translators: This is a model description
		_("Same capabilities as the standard gpt-4 mode but with 4x the context length."),
		32768,
		8192
	),
	Model(
		"MistralAI",
		"mistral-tiny",
		# Translators: This is a model description
		_("Used for large batch processing tasks where cost is a significant factor but reasoning capabilities are not crucial. Uses the Mistral API."),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"MistralAI",
		"mistral-small",
		# Translators: This is a model description
		_("Higher reasoning capabilities and more capabilities. Use the Mistral API."),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"MistralAI",
		"mistral-medium",
		# Translators: This is a model description
		_("Internal prototype model. Uses the Mistral API."),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	)
]
DEFAULT_MODEL = MODELS[0]
DEFAULT_MODEL_VISION = "gpt-4-vision-preview"
BASE_URLs = {
	"MistralAI": "https://api.mistral.ai/v1",
	"OpenAI": "https://api.openai.com/v1",
	"OpenRouter": "https://openrouter.ai/api/v1"
}
DEFAULT_SYSTEM_PROMPT = _(
	"You are an accessibility assistant integrated in the NVDA screen reader that "
	"helps blind screen reader users access visual information that may not be accessible "
	"using the screen reader alone, and answer questions related to the use of Windows and "
	"other applications with NVDA. When answering questions, always make very clear to the "
	"user when something is a fact that comes from your training data versus an educated guess, "
	"and always consider that the user is primarily accessing content using the keyboard and "
	"a screen reader. When describing images, keep in mind that you are describing content to "
	"a blind screen reader user and they need assistance with accessing visual information in "
	"an image that they cannot see. Please describe any relevant details such as names, participant "
	"lists, or other information that would be visible to sighted users in the context of a call "
	"or application interface. When the user shares an image, it may be the screenshot of an entire "
	"window, a partial window or an individual control in an application user interface. Generate "
	"a detailed but succinct visual description. If the image is a control, tell the user the type "
	"of control and its current state if applicable, the visible label if present, and how the control "
	"looks like. If it is a window or a partial window, include the window title if present, and "
	"describe the rest of the screen, listing all sections starting from the top, and explaining the "
	"content of each section separately. For each control, inform the user about its name, value "
	"and current state when applicable, as well as which control has keyboard focus. Ensure to include "
	"all visible instructions and error messages. When telling the user about visible text, do not add "
	"additional explanations of the text unless the meaning of the visible text alone is not sufficient "
	"to understand the context. Do not make comments about the aesthetics, cleanliness or overall "
	"organization of the interface. If the image does not correspond to a computer screen, just generate "
	"a detailed visual description. If the user sends an image alone without additional instructions in text, "
	"describe the image exactly as prescribed in this system prompt. Adhere strictly to the instructions in "
	"this system prompt to describe images. Don’t add any additional details unless the user specifically ask you."
)
LIBS_DIR = os.path.join(DATA_DIR, "libs")
LIBS_DIR_PY = os.path.join(
	LIBS_DIR,
	"lib_py%s.%s" % (
		sys.version_info.major,
		sys.version_info.minor
	)
)
