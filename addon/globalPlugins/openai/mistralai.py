import json
import os
import urllib.request
import addonHandler
from logHandler import log

from .consts import DATA_DIR, Model, MODELS
from .model import Model

addonHandler.initTranslation()


class MistralResponse:

	def __init__(self, success: bool, content: str):
		self.success = success
		self.content = content

	def __bool__(self):
		return self.success

	def __str__(self):
		return self.content

	def __repr__(self):
		return "<MistralResponse success=%s content=%s>" % (self.success, repr(self.content))
MISTRAL_API_KEY = None

def set_api_key(key: str):
	"""
	Set the API key
	"""
	global MISTRAL_API_KEY
	MISTRAL_API_KEY = key
	with open(os.path.join(DATA_DIR, "mistralai.key"), "w") as f:
		f.write(key)


def get_api_key():
	global MISTRAL_API_KEY
	if MISTRAL_API_KEY:
		return MISTRAL_API_KEY
	try:
		with open(os.path.join(DATA_DIR, "mistralai.key"), "r") as f:
			return f.read().strip()
	except FileNotFoundError:
		return ''


def get_available_models():
	return [model.name for model in AVAILABLE_MODELS]


def make_request(
	model: str,
	messages: list,
	temperature: float=0.7,
	top_p: float=1,
	max_tokens: int=1024,
	stream: bool=False,
	safe_mode: bool=False,
) -> (bool, str):
	"""
	Make a request to the Mistral AI API
	"""
	url = "https://api.mistral.ai/v1/chat/completions"

	data = {
		"model": model,
		"messages": messages,
		"temperature": temperature,
		"top_p": top_p,
		"max_tokens": max_tokens,
		"stream": False,
		"safe_mode": False,
		"random_seed": None
	}

	data = json.dumps(data).encode()
	req = urllib.request.Request(
		url,
		method="POST",
		data=data
	)
	req.add_header("Content-Type", "application/json")
	req.add_header("accept", "application/json")
	req.add_header("Authorization", "Bearer " + MISTRAL_API_KEY)
	# The default user agent is blocked by the API, so we need to set a custom one
	req.add_header(
		"User-Agent",
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0"
	)

	try:
		response = urllib.request.urlopen(req, timeout=30)
		response_data = json.load(response)
		if "choices" not in response_data:
			log.error(response_data)
			return MistralResponse(False, _("Invalid response from the API"))
		content = ""
		for choice in response_data["choices"]:
			content += choice["message"]["content"]
		return MistralResponse(True, content)
	except urllib.error.HTTPError as e:
		content = e.read().decode()
		log.error(content)
		return MistralResponse(False, _("HTTP error: %s. See log for details.") % e.code)

NO_STREAM_MODE_STR = _("No stream mode")

AVAILABLE_MODELS = [
	Model(
		"mistral-tiny",
		_("Used for large batch processing tasks where cost is a significant factor but reasoning capabilities are not crucial.") + " " + NO_STREAM_MODE_STR,
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"mistral-small",
		_("Higher reasoning capabilities and more capabilities.") + " " + NO_STREAM_MODE_STR,
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"mistral-medium",
		_("Internal prototype model.") + " " + NO_STREAM_MODE_STR,
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
]

MODELS.extend(AVAILABLE_MODELS)
MISTRAL_API_KEY = get_api_key()
