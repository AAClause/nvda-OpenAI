import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile
import gui
import wx

import addonHandler
import config
import core
import ui
import versionInfo
from logHandler import log

from .consts import ADDON_DIR, DATA_DIR, LIBS_DIR

addonHandler.initTranslation()

ROOT_ADDON_DIR = "\\".join(ADDON_DIR.split(os.sep)[:-2])

URL_LATEST_RELEASE = "https://andreabc.net/projects/NVDA_addons/OpenAI/version.json"

NVDA_VERSION = f"{versionInfo.version_year}.{versionInfo.version_major}.{versionInfo.version_minor}"
PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"

conf = config.conf["OpenAI"]["update"]
LIB_REV = conf["libRev"]


def ensure_dir_exists(directory: str):
	"""Ensure that the specified directory exists, creating it if necessary."""
	if not os.path.exists(directory):
		os.mkdir(directory)

ensure_dir_exists(DATA_DIR)

ADDON_INFO = addonHandler.Addon(ROOT_ADDON_DIR).manifest
LAST_CHECK_FP = os.path.join(DATA_DIR, "last_check")
_last_check = 0

def get_last_check():
	"""Return the timestamp for the latest update check"""
	global _last_check
	if _last_check:
		return _last_check
	if os.path.exists(LAST_CHECK_FP):
		try:
			with open(LAST_CHECK_FP) as file:
				_last_check = float(file.read().strip())
		except ValueError:
			pass
	return _last_check


def update_last_check():
	"""Update the timestamp for the last check in the configuration."""
	global _last_check
	_last_check = time.time()
	with open(LAST_CHECK_FP, "w") as file:
		file.write(str(_last_check))


def check_addon_version(data: dict, auto: bool):
	"""Check if the addon is up to date and notify the user accordingly."""
	local_version = ADDON_INFO["version"]
	remote_version = data["addon_version"]
	if local_version.split('-')[0] != remote_version:
		# Translators: This is the message displayed when a new version of the add-on is available.
		msg = _(
			"New version available: %s. "
			"You can update from the add-on store "
			"or from the GitHub repository."
		) % remote_version
		gui.messageBox(
			msg,
			# Translators: This is the title of the message displayed when a new version of the add-on is available.
			_("OpenAI update"),
			wx.OK | wx.ICON_INFORMATION
		)
	elif not auto:
		# Translators: This is the message displayed when the user checks for updates manually and there are no updates available.
		msg = _("You have the latest version of OpenAI add-on installed.")
		gui.messageBox(
			msg,
			# Translators: This is the title of the message displayed when the user checks for updates manually and there are no updates available.
			_("OpenAI update"),
			wx.OK | wx.ICON_INFORMATION
		)


def load_remote_data(auto: bool):
	"""Load and return the remote version and dependency information."""
	channel = conf["channel"]
	params = {
		"nvda_version": NVDA_VERSION,
		"python_version": PYTHON_VERSION,
		"addon_version": ADDON_INFO["version"],
		"addon_channel": channel,
		"lib_rev": LIB_REV
	}
	request_url = f"{URL_LATEST_RELEASE}?{urllib.parse.urlencode(params)}"
	with urllib.request.urlopen(request_url) as response:
		return json.loads(response.read())


def handle_data_update(response_data: dict, auto: bool):
	"""Check the addon and dependencies version and handle updates if necessary."""
	check_addon_version(response_data, auto)
	if (conf["libRev"] != response_data["libs_rev"]) or not os.path.exists(LIBS_DIR):
		if offer_data_update(response_data):
			update_dependency_files(response_data)
	else:
		update_last_check()

def check_file_hash(file_path: str, expected_hash: str) -> bool:
	"""Check that the SHA256 hash of the file at file_path matches expected_hash."""
	with open(file_path, "rb") as file:
		file_hash = hashlib.sha256(file.read()).hexdigest()
	return file_hash == expected_hash

def offer_data_update(data: dict) -> bool:
	"""Offer to update the OpenAI dependencies and return the user's choice."""
	# Translators: This is the message displayed when a new version of the OpenAI dependencies is available.
	msg = _("New OpenAI dependencies revision available: %s. Update now?") % data["libs_rev"]
	result = gui.messageBox(
		msg,
		# Translators: This is the title of the message displayed when a new version of the OpenAI dependencies is available.
		_("OpenAI dependencies update"),
		wx.YES_NO | wx.ICON_QUESTION
	)
	return result == wx.YES


def update_dependency_files(data: dict):
	"""Handle the downloading and extraction of updated dependencies."""
	ui.message(
		# Translators: This is the message emitted when updating the OpenAI dependencies.
		_("Updating OpenAI dependencies... Please wait")
	)
	try:
		with urllib.request.urlopen(data["libs_download_url"]) as response:
			zip_file_content = response.read()

		zip_path = os.path.join(DATA_DIR, "libs.zip")
		with open(zip_path, "wb") as file:
			file.write(zip_file_content)

		if not check_file_hash(zip_path, data["libs_hash"]):
			raise ValueError("Libs hash mismatch")

		if os.path.exists(LIBS_DIR):
			shutil.rmtree(LIBS_DIR)

		with zipfile.ZipFile(zip_path, "r") as zip_file:
			zip_file.extractall(LIBS_DIR)

		os.remove(zip_path)
		conf["libRev"] = data["libs_rev"]
		update_last_check()
		gui.messageBox(
			# Translators: This is the message displayed when the OpenAI dependencies are updated successfully.
			_("Dependencies updated successfully. NVDA will now restart to apply the changes."),
			# Translators: This is the title of the message displayed when the OpenAI dependencies are updated successfully.
			_("Success"),
			wx.OK | wx.ICON_INFORMATION
		)
		core.restart()

	except (
		urllib.error.URLError,
		zipfile.BadZipFile,
		ValueError,
		OSError
	) as e:
		log.error(f"Error updating OpenAI dependencies: {e}")
		gui.messageBox(
			# Translators: This is the message displayed when the OpenAI dependencies cannot be updated.
			_("Error updating dependencies. Please restart NVDA to try again."),
			# Translators: This is the title of the message displayed when the OpenAI dependencies cannot be updated.
			_("Error"),
			wx.OK | wx.ICON_ERROR
		)


def check_update(auto: bool = True):
	"""Check for updates to OpenAI addon, including new versions and dependencies."""
	log.info("Checking for Open AI updates (auto=%s)", auto)
	try:
		data = load_remote_data(auto)
		wx.CallAfter(handle_data_update, data, auto)
	except urllib.error.URLError as e:
		log.error(f"Error checking Open AI update: {e}")


if (
	(conf["check"] and get_last_check() + 86400 * 3 < time.time())
	or not os.path.exists(LIBS_DIR)
):
	check_update()
