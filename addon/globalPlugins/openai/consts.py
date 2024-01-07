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
		"gpt-3.5-turbo-1106",
		# Translators: This is a model description
		_("Updated GPT 3.5 Turbo. The latest GPT-3.5 Turbo model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."),
		16385,
		4096
	),
	Model(
		"gpt-3.5-turbo-0613",
		# Translators: This is a model description
		_("Same capabilities as the standard gpt-3.5-turbo model but with 4 times the context"),
		16384,
		4096
	),
	Model(
		"gpt-4-0613",
		# Translators: This is a model description
		_("More capable than any GPT-3.5 model, able to do more complex tasks, and optimized for chat"),
		8192
	),
	Model(
		"gpt-4-1106-preview",
		# Translators: This is a model description
		_("The latest GPT-4 model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."),
		128000,
		4096,
		preview=True
	),
	Model(
		"gpt-4-vision-preview",
		# Translators: This is a model description
		_("GPT-4 Turbo with vision. Ability to understand images, in addition to all other GPT-4 Turbo capabilities."),
		128000,
		4096,
		vision=True,
		preview=True
	),
	Model(
		"gpt-4-32k-0613",
		# Translators: This is a model description
		_("Same capabilities as the standard gpt-4 mode but with 4x the context length."),
		32768,
		8192
	),
	Model(
		"mistral-tiny",
		# Translators: This is a model description
		_("Used for large batch processing tasks where cost is a significant factor but reasoning capabilities are not crucial. Uses the Mistral API."),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"mistral-small",
		# Translators: This is a model description
		_("Higher reasoning capabilities and more capabilities. Use the Mistral API."),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"mistral-medium",
		# Translators: This is a model description
		_("Internal prototype model. Uses the Mistral API."),
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"openrouter/auto",
		# Translators: This is a model description
		_("Depending on their size, subject, and complexity, your prompts will be sent to MythoMax 13B, MythoMax 13B 8k, or GPT-4 Turbo.")
	),
	Model(
		"alpindale/goliath-120b",
		# Translators: This is a model description
		_("A large LLM created by combining two fine-tuned Llama 70B models into one 120B model. Combines Xwin and Euryale."),
		6144
	),
	Model(
		"anthropic/claude-2",
		# Translators: This is a model description
		_("Claude 2.1 delivers advancements in key capabilities for enterprises—including an industry-leading 200K token context window, significant reductions in rates of model hallucination, system prompts and new beta feature: tool use."),
		200000
	),
	Model(
		"anthropic/claude-2.0",
		# Translators: This is a model description
		_("Superior performance on tasks that require complex reasoning. Supports up to 100k tokens in one pass, or hundreds of pages of text."),
		100000
	),
	Model(
		"anthropic/claude-instant-v1",
		# Translators: This is a model description
		_("For low-latency, high throughput text generation. Supports up to 100k tokens in one pass, or hundreds of pages of text."),
		100000
	),
	Model(
		"cognitivecomputations/dolphin-mixtral-8x7b",
		# Translators: This is a model description
		_("This is a 16k context fine-tune of Mixtral-8x7b. It excels in coding tasks due to extensive training with coding data and is known for its obedience, although it lacks DPO tuning. The model is uncensored and is stripped of alignment and bias. It requires an external alignment layer for ethical use."),
		32000
	),
	Model(
		"google/gemini-pro",
		# Translators: This is a model description
		_("Designed to handle natural language tasks, multiturn text and code chat, and code generation."),
		131040,
		32768,
		preview=True
	),
	Model(
		"google/gemini-pro-vision",
		# Translators: This is a model description
		_("Google's flagship multimodal model, supporting image and video in text or chat prompts for a text or code response."),
		65536,
		8192,
		vision=True,
		preview=True
	),
	Model(
		"google/palm-2-chat-bison",
		# Translators: This is a model description
		_("PaLM 2 is a language model by Google with improved multilingual, reasoning and coding capabilities."),
		36864,
		4096
	),
	Model(
		"google/palm-2-chat-bison-32k",
		# Translators: This is a model description
		_("PaLM 2 is a language model by Google with improved multilingual, reasoning and coding capabilities."),
		131072,
		32768
	),
	Model(
		"google/palm-2-codechat-bison",
		# Translators: This is a model description
		_("PaLM 2 fine-tuned for chatbot conversations that help with code-related questions."),
		28672,
		4096
	),
	Model(
		"google/palm-2-codechat-bison-32k",
		# Translators: This is a model description
		_("PaLM 2 fine-tuned for chatbot conversations that help with code-related questions."),
		131072,
		32768
	),
	Model(
		"gryphe/mythomax-l2-13b",
		# Translators: This is a model description
		_("One of the highest performing fine-tunes of Llama 2 13B, with rich descriptions and roleplay."),
		4096
	),
	Model(
		"gryphe/mythomist-7b",
		# Translators: This is a model description
		_("Merges a suite of models to reduce word anticipation, ministrations, and other undesirable words in ChatGPT roleplaying data. It combines Neural Chat 7B, Airoboros 7b, Toppy M 7B, Zepher 7b beta, Nous Capybara 34B, OpenHeremes 2.5, and many others."),
		32768,
		2048
	),
	Model(
		"haotian-liu/llava-13b",
		# Translators: This is a model description
		_("LLaVA is a large multimodal model that combines a vision encoder and Vicuna for general-purpose visual and language understanding, achieving impressive chat capabilities mimicking GPT-4 and setting a new state-of-the-art accuracy on Science QA"),
		2048,
		vision=True
	),
	Model(
		"huggingfaceh4/zephyr-7b-beta",
		# Translators: This is a model description
		_("A fine-tuned version of mistralai/Mistral-7B-v0.1 that was trained on a mix of publicly available, synthetic datasets using Direct Preference Optimization (DPO)."),
		4096
	),
	Model(
		"meta-llama/codellama-34b-instruct",
		# Translators: This is a model description
		_("Built upon Llama 2 and excels at filling in code, handling extensive input contexts, and folling programming instructions without prior training for various programming tasks."),
		8192
	),
	Model(
		"meta-llama/llama-2-13b-chat",
		# Translators: This is a model description
		_("A 13 billion parameter language model from Meta, fine tuned for chat completions"),
		4096
	),
	Model(
		"meta-llama/llama-2-70b-chat",
		# Translators: This is a model description
		_("A 70 billion parameter language model from Meta, fine tuned for chat completions."),
		4096
	),
	Model(
		"mistralai/mixtral-8x7b",
		# Translators: This is a model description
		_("A pretrained generative Sparse Mixture of Experts, by Mistral AI. Incorporates 8 experts (feed-forward networks) for a total of 47B parameters. Base model (not fine-tuned for instructions) - see Mixtral 8x7B Instruct, for an instruct-tuned model."),
		32768
	),
	Model(
		"mistralai/mistral-7b-instruct",
		_("A 7.3B parameter model that outperforms Llama 2 13B on all benchmarks, with optimizations for speed and context length."),
		8192
	),
	Model(
		"neversleep/noromaid-20b",
		# Translators: This is a model description
		_("A merge suitable for RP, ERP, and general knowledge."),
		8192
	),
	Model(
		"open-orca/mistral-7b-openorca",
		# Translators: This is a model description
		_("A fine-tune of Mistral using the OpenOrca dataset. First 7B model to beat all other models <30B."),
		8192
	),
	Model(
		"nousresearch/nous-capybara-7b",
		# Translators: This is a model description
		_("A collection of datasets and models made by fine-tuning on data created by Nous, mostly in-house. V1.9 uses unalignment techniques for more consistent and dynamic control. It also leverages a significantly better foundation model, Mistral 7B."),
		4096
	),
	Model(
		"nousresearch/nous-hermes-2-vision",
		# Translators: This is a model description
		_("Built on innovations from the popular OpenHermes-2.5 model, by Teknium. It adds vision support, and is trained on a custom dataset enriched with function calling."),
		4096,
		vision=True
	),
	Model(
		"nousresearch/nous-hermes-llama2-13b",
		# Translators: This is a model description
		_("A state-of-the-art language model fine-tuned on over 300k instructions by Nous Research, with Teknium and Emozilla leading the fine tuning process."),
		4096
	),
	Model(
		"nousresearch/nous-hermes-yi-34b",
		# Translators: This is a model description
		_("Was trained on 1,000,000 entries of primarily GPT-4 generated data, as well as other high quality data from open datasets across the AI landscape."),
		4096
	),
	Model(
		"openchat/openchat-7b",
		# Translators: This is a model description
		_("A library of open-source language models, fine-tuned with \"C-RLFT (Conditioned Reinforcement Learning Fine-Tuning)\" - a strategy inspired by offline reinforcement learning. It has been trained on mixed-quality data without preference labels."),
		8192
	),
	Model(
		"rwkv/rwkv-5-world-3b",
		# Translators: This is a model description
		_("RWKV is an RNN (recurrent neural network) with transformer-level performance. It aims to combine the best of RNNs and transformers - great performance, fast inference, low VRAM, fast training, \"infinite\" context length, and free sentence embedding. RWKV-5 is trained on 100+ world languages (70% English, 15% multilang, 15% code)."),
		10000
	)
]
DEFAULT_MODEL = MODELS[0]
DEFAULT_MODEL_VISION = "gpt-4-vision-preview"

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
