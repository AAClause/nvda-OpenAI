import json
import os
import urllib.request
import addonHandler
from logHandler import log

from .consts import DATA_DIR, Model, MODELS
from .model import Model

addonHandler.initTranslation()

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


BASE_URL = "https://api.mistral.ai/v1"
SUFFIX = ' ' + _("Use the Mistral API.")
AVAILABLE_MODELS = [
	Model(
		"mistral-tiny",
		_("Used for large batch processing tasks where cost is a significant factor but reasoning capabilities are not crucial.") + SUFFIX,
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"mistral-small",
		_("Higher reasoning capabilities and more capabilities.") + SUFFIX,
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
	Model(
		"mistral-medium",
		_("Internal prototype model.") + SUFFIX,
		32000,
		maxTemperature=1.0,
		defaultTemperature=0.7
	),
]
MODELS.extend(AVAILABLE_MODELS)
MISTRAL_API_KEY = get_api_key()
