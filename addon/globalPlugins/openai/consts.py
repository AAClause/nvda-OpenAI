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
		"gpt-4.1",
		# Translators: This is a model description
		_("Flagship GPT model for complex tasks"),
		1047576,
		32768,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-4.1-mini",
		# Translators: This is a model description
		_("Balanced for intelligence, speed, and cost"),
		1047576,
		32768,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-4.1-nano",
		# Translators: This is a model description
		_("Fastest, most cost-effective GPT-4.1 model"),
		1047576,
		32768,
		vision=True
	),
	Model(
		"OpenAI",
		"o4-mini",
		# Translators: This is a model description
		_("Faster, more affordable reasoning model"),
		200000,
		100000,
		vision=True,
		reasoning=True
	),
	Model(
		"OpenAI",
		"o3",
		# Translators: This is a model description
		_("Our most powerful reasoning model"),
		200000,
		100000,
		vision=True,
		reasoning=True
	),
	Model(
		"OpenAI",
		"gpt-4o",
		# Translators: This is a model description
		_("Points to one of the most recent iterations of gpt-4o-mini model"),
		128000,
		16384,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-4o-search-preview",
		# Translators: This is a model description
		_("GPT model for web search in Chat Completions"),
		128000,
		16384,
		vision=True,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4o-mini-search-preview",
		# Translators: This is a model description
		_("Fast, affordable small model for web search"),
		128000,
		16384,
		vision=True,
		preview=True
	),
	Model(
		"OpenAI",
		"chatgpt-4o-latest",
		# Translators: This is a model description
		_("Dynamic model continuously updated to the current version of GPT-4o in ChatGPT"),
		128000,
		16384,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-4o-mini",
		# Translators: This is a model description
		_("Points to one of the most recent iterations of gpt-4o-mini model"),
		128000,
		16384,
		vision=True
	),
	Model(
		"OpenAI",
		"o3-mini",
		# Translators: This is a model description
		_("Our most recent small reasoning model, providing high intelligence at the same cost and latency targets of o1-mini. o3-mini also supports key developer features, like Structured Outputs, function calling, Batch API, and more. Like other models in the o-series, it is designed to excel at science, math, and coding tasks."),
		200000,
		100000,
		vision=True,
		reasoning=True
	),
	Model(
		"OpenAI",
		"o1",
		# Translators: This is a model description
		_("Points to the most recent snapshot of the o1 model"),
		200000,
		100000,
		vision=True,
		reasoning=True
	),
	Model(
		"OpenAI",
		"o1-mini",
		# Translators: This is a model description
		_("Points to the most recent o1-mini snapshot"),
		128000,
		65536,
		vision=True,
		reasoning=True
	),
	Model(
		"OpenAI",
		"gpt-4-turbo",
		# Translators: This is a model description
		_("The latest GPT-4 Turbo model with vision capabilities"),
		128000,
		4096,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-3.5-turbo",
		# Translators: This is a model description
		_("Points to one of the most recent iterations of gpt-3.5 model"),
		16385,
		4096
	),
	Model(
		"OpenAI",
		"gpt-4o-2024-08-06",
		# Translators: This is a model description
		_("Latest snapshot that supports Structured Outputs"),
		128000,
		16384,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-4o-2024-05-13",
		# Translators: This is a model description
		_("Our high-intelligence flagship model for complex, multi-step tasks"),
		128000,
		4096,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-4o-mini-2024-07-18",
		# Translators: This is a model description
		_("Our affordable and intelligent small model for fast, lightweight tasks. GPT-4o mini is cheaper and more capable than GPT-3.5 Turbo"),
		128000,
		16384,
		vision=True
	),
	Model(
		"OpenAI",
		"gpt-3.5-turbo-0125",
		# Translators: This is a model description
		_("The latest GPT-3.5 Turbo model with higher accuracy at responding in requested formats and a fix for a bug which caused a text encoding issue for non-English language function calls"),
		16385,
		4096
	),
	Model(
		"OpenAI",
		"gpt-4-turbo-preview",
		# Translators: This is a model description
		_("Points to one of the most recent iterations of gpt-4 model"),
		128000,
		4096,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4-0125-preview",
		# Translators: This is a model description
		_("The latest GPT-4 model intended to reduce cases of 'laziness' where the model doesn't complete a task"),
		128000,
		4096,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4-1106-preview",
		# Translators: This is a model description
		_("GPT-4 Turbo model featuring improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more"),
		128000,
		4096,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4-vision-preview",
		# Translators: This is a model description
		_("GPT-4 Turbo with vision. Ability to understand images, in addition to all other GPT-4 Turbo capabilities"),
		128000,
		4096,
		vision=True,
		preview=True
	),
	Model(
		"OpenAI",
		"gpt-4-0613",
		# Translators: This is a model description
		_("More capable than any GPT-3.5 model, able to do more complex tasks, and optimized for chat"),
		8192,
		8192
	),
	Model(
		"OpenAI",
		"gpt-4-32k-0613",
		# Translators: This is a model description
		_("Same capabilities as the standard gpt-4 mode but with 4x the context length"),
		32768,
		8192
	),
	Model(
		"MistralAI",
		"open-mistral-7b",
		# Translators: This is a model description
		_("aka %s") % "mistral-tiny-2312",
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"MistralAI",
		"open-mixtral-8x7b",
		# Translators: This is a model description
		_("aka %s") % "mistral-small-2312",
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"MistralAI",
		"mistral-small-latest",
		# Translators: This is a model description
		_("Simple tasks (Classification, Customer Support, or Text Generation)"),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"MistralAI",
		"mistral-medium-latest",
		# Translators: This is a model description
		_("Intermediate tasks that require moderate reasoning (Data extraction, Summarizing a Document, Writing emails, Writing a Job Description, or Writing Product Descriptions)"),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"MistralAI",
		"mistral-large-latest",
		# Translators: This is a model description
		_("Complex tasks that require large reasoning capabilities or are highly specialized (Synthetic Text Generation, Code Generation, RAG, or Agents)"),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	)
]
DEFAULT_MODEL = MODELS[0]  # gpt-4.1
DEFAULT_MODEL_VISION = "gpt-4o"
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
	"this system prompt to describe images. Don't add any additional details unless the user specifically ask you."
)
LIBS_DIR = os.path.join(DATA_DIR, "libs")
LIBS_DIR_PY = os.path.join(
	LIBS_DIR,
	"lib_py%s.%s" % (
		sys.version_info.major,
		sys.version_info.minor
	)
)
