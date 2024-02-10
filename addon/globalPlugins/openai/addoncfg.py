import json
import os
import sys
from logHandler import log
from .consts import DATA_JSON_FP

_orig_data = {}
_data = {}

def load():
	global _data
	_orig_data = {}
	_data = {}
	if os.path.exists(DATA_JSON_FP):
		with open(DATA_JSON_FP, 'r') as f:
			try:
				data = json.loads(f.read())
				if "providers" not in data:
					data["providers"] = {}
				_orig_data = data
				_data = data
			except Exception as e:
				log.error(f"Failed to load data from {DATA_JSON_FP}: {e}")
	return _data

def get():
	if _data:
		return _data
	return load()


def save(force=False):
	if not force and _orig_data == _data:
		return
	with open(DATA_JSON_FP, "w") as f:
		f.write(json.dumps(_data, indent=2, sort_keys=True))
