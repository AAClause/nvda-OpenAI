import os
import shutil
import struct
import sys
import winsound
from enum import StrEnum, auto
import globalVars
import addonHandler

addonHandler.initTranslation()

ADDON_DIR = os.path.dirname(__file__)
OLD_DATA_DIR = os.path.join(globalVars.appArgs.configPath, "openai")
DATA_DIR = os.path.join(globalVars.appArgs.configPath, "aihub")
TEMP_DIR = os.path.join(DATA_DIR, "tmp")
SND_CHAT_RESPONSE_PENDING = os.path.join(ADDON_DIR, "sounds", "chatResponsePending.wav")
SND_CHAT_RESPONSE_RECEIVED = os.path.join(ADDON_DIR, "sounds", "chatResponseReceived.wav")
SND_CHAT_RESPONSE_SENT = os.path.join(ADDON_DIR, "sounds", "chatRequestSent.wav")
SND_PROGRESS = os.path.join(ADDON_DIR, "sounds", "progress.wav")

# Dialog/panel spacing (px) — keep conversation window and tool dialogs visually aligned.
UI_DIALOG_BORDER_PX = 8
UI_SECTION_SPACING_PX = 5
UI_FORM_ROW_BORDER_PX = 6


def ensure_dir_exists(directory: str):
	if not os.path.exists(directory):
		os.mkdir(directory)


def _migrate_data_dir_if_needed():
	"""Migrate legacy data folder from 'openai' to 'aihub'."""
	if os.path.abspath(OLD_DATA_DIR) == os.path.abspath(DATA_DIR):
		return
	if not os.path.isdir(OLD_DATA_DIR):
		return
	if not os.path.isdir(DATA_DIR):
		try:
			os.replace(OLD_DATA_DIR, DATA_DIR)
			return
		except OSError:
			pass
	os.makedirs(DATA_DIR, exist_ok=True)
	for name in os.listdir(OLD_DATA_DIR):
		src = os.path.join(OLD_DATA_DIR, name)
		dst = os.path.join(DATA_DIR, name)
		if os.path.isdir(src):
			if not os.path.exists(dst):
				try:
					shutil.copytree(src, dst)
				except OSError:
					continue
		else:
			if not os.path.exists(dst):
				try:
					shutil.copy2(src, dst)
				except OSError:
					continue


_migrate_data_dir_if_needed()


def ensure_temp_dir():
	ensure_dir_exists(TEMP_DIR)


def stop_progress_sound():
	winsound.PlaySound(None, winsound.SND_ASYNC)


def cleanup_temp_dir():
	"""Remove all temporary files in TEMP_DIR. Called on dialog close and addon terminate."""
	if not os.path.isdir(TEMP_DIR):
		return
	for name in os.listdir(TEMP_DIR):
		path = os.path.join(TEMP_DIR, name)
		try:
			if os.path.isfile(path):
				os.remove(path)
		except OSError as err:
			import logHandler
			logHandler.log.error(f"cleanup_temp_dir: {err}", exc_info=True)

DEFAULT_TOP_P = 100
TOP_P_MIN = 0
TOP_P_MAX = 100
TTS_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
TTS_DEFAULT_VOICE = "nova"
TTS_MODELS = ["tts-1", "tts-1-hd"]
TTS_DEFAULT_MODEL = "tts-1"

DEFAULT_MODEL = "gpt-5"

WHISPER_MODELS = ["whisper-1"]
VOXTRAL_MODELS = ["voxtral-mini-latest"]


class Provider(StrEnum):
	"""Canonical chat-completion provider identifiers.

	Member names are used verbatim as the enum value (via the override of
	``_generate_next_value_`` below) so they double as the on-disk identifier
	stored in ``accounts.json``, the user config, conversation files, and the
	various provider-keyed dicts (``BASE_URLs``, ``PROVIDER_URL``, ...).

	Because ``StrEnum`` is a ``str`` subclass, comparisons like
	``model.provider == Provider.OpenAI`` work even when the left-hand side is
	a plain string read from JSON. New code SHOULD prefer enum members over
	string literals so a typo becomes an ``AttributeError`` at import time.
	"""

	@staticmethod
	def _generate_next_value_(name, start, count, last_values):
		# Default StrEnum auto() lowercases the name; we want to keep the
		# original casing (``OpenAI``, ``MistralAI``, ``xAI`` ...) which is the
		# identifier persisted on disk and used as dict keys throughout the
		# addon. Member names are intentionally not UPPER_CASE for the same
		# reason.
		return name

	OpenAI = auto()
	MistralAI = auto()
	DeepSeek = auto()
	CustomOpenAI = auto()
	Ollama = auto()
	OpenRouter = auto()
	Anthropic = auto()
	xAI = auto()
	Google = auto()


AVAILABLE_PROVIDERS = tuple(p.value for p in Provider)


class TranscriptionProvider(StrEnum):
	WHISPER_CPP = auto()
	OPENAI = auto()
	MISTRAL = auto()


TRANSCRIPTION_PROVIDERS = tuple(p.value for p in TranscriptionProvider)
DEFAULT_TRANSCRIPTION_PROVIDER = TranscriptionProvider.OPENAI.value

# Reasoning effort levels (unified; each provider may support a subset)

class ReasoningEffort(StrEnum):
	"""Standard reasoning_effort string values; each provider may support a subset.

	Anthropic also accepts ``xhigh`` and ``max`` levels but those are not part
	of this enum because the addon UI only persists the four canonical values
	(see ``model.Model.reasoning_effort_options``).
	"""

	MINIMAL = auto()
	LOW = auto()
	MEDIUM = auto()
	HIGH = auto()


REASONING_EFFORT_OPTIONS = tuple(e.value for e in ReasoningEffort)
DEFAULT_REASONING_EFFORT = ReasoningEffort.MEDIUM.value


class Role(StrEnum):
	"""Standard chat message roles shared by every provider's chat API."""

	SYSTEM = auto()
	USER = auto()
	ASSISTANT = auto()
	TOOL = auto()
	DEVELOPER = auto()


class ContentType(StrEnum):
	"""Standard ``content`` part types used in OpenAI-compatible messages."""

	TEXT = auto()
	IMAGE_URL = auto()
	INPUT_AUDIO = auto()
	INPUT_FILE = auto()


AUDIO_EXT_TO_FORMAT = {".wav": "wav", ".mp3": "mp3", ".m4a": "m4a", ".webm": "webm", ".mp4": "mp4"}

MIN_SAMPLES_FOR_TRIM = 0.1

BASE_URLs = {
	Provider.MistralAI: "https://api.mistral.ai/v1",
	Provider.OpenAI: "https://api.openai.com/v1",
	Provider.DeepSeek: "https://api.deepseek.com/v1",
	Provider.CustomOpenAI: "https://api.openai.com/v1",
	Provider.Ollama: "http://127.0.0.1:11434/v1",
	Provider.OpenRouter: "https://openrouter.ai/api/v1",
	Provider.Anthropic: "https://api.anthropic.com/v1",
	Provider.xAI: "https://api.x.ai/v1",
	Provider.Google: "https://generativelanguage.googleapis.com/v1beta",
}
# Translators: Default English system prompt text applied to new AI-Hub conversations until the user changes it.
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
LIBS_BASE = os.path.join(ADDON_DIR, "libs")
_is64bit = struct.calcsize("P") * 8 == 64
_maj, _min = sys.version_info.major, sys.version_info.minor
_suffix = "" if _is64bit else "_win32"
_candidates = [
	os.path.join(LIBS_BASE, f"lib_py{_maj}.{_min}{_suffix}"),
	os.path.join(LIBS_BASE, f"lib_py3.13{_suffix}" if _min >= 13 else f"lib_py3.11{_suffix}"),
]
ADDON_LIBS_DIR = next((p for p in _candidates if os.path.isdir(p)), LIBS_BASE)
