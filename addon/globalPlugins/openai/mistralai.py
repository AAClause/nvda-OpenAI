import json
import os
import urllib.request
import addonHandler
from logHandler import log

from .consts import DATA_DIR, Model, MODELS
from .model import Model

addonHandler.initTranslation()

_api_key = None


def set_api_key(key: str):
	"""
	Set the API key
	"""
	global _api_key
	_api_key = key
	with open(os.path.join(DATA_DIR, "mistralai.key"), "w") as f:
		f.write(key)


def get_api_key():
	global _api_key
	if _api_key:
		return _api_key
	try:
		with open(os.path.join(DATA_DIR, "mistralai.key"), "r") as f:
			return f.read().strip()
	except FileNotFoundError:
		return ''


BASE_URL = "https://api.mistral.ai/v1"
_api_key = get_api_key()
