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

from .consts import ADDON_DIR, DATA_DIR, LIBS_DIR, LIBS_DIR_PY

addonHandler.initTranslation()

ROOT_ADDON_DIR = "\\".join(ADDON_DIR.split(os.sep)[:-2])

ADDON_INFO = addonHandler.Addon(
	ROOT_ADDON_DIR
).manifest
URL_LATEST_RELEASE = "https://andreabc.net/projects/NVDA_addons/OpenAI/version.json"
NVDA_VERSION = "%d.%d.%d" % (
	versionInfo.version_year,
	versionInfo.version_major,
	versionInfo.version_minor,
)
PYTHON_VERSION = "%d.%d" % (sys.version_info.major, sys.version_info.minor)
conf = config.conf["OpenAI"]["update"]
LIB_REV =  conf["libRev"]


def check_hash(
		file_path: str,
		hash_: str
	) -> bool:
	"""Check the hash of a file."""
	with open(file_path, "rb") as file:
		file_hash = hashlib.sha256(file.read()).hexdigest()
	return file_hash == hash_

def check_data(data):
	if (
		not os.path.exists(LIBS_DIR)
		or LIB_REV != data["libs_rev"]
		or os.path.exists(LIBS_DIR_PY)
	):
		msg = _(
			"New Open AI dependencies revision available: %s. If you just installed the add-on you must install the dependencies to work properly. "
			"Do you want to update now?"
		) % data["libs_rev"]
		res = gui.messageBox(
			msg,
			_("Open AI dependencies update"),
			wx.YES_NO | wx.ICON_QUESTION
		)
		if res == wx.YES:
			ui.message(_("Updating Open AI dependencies... Please wait"))
			try:
				with urllib.request.urlopen(
					data["libs_download_url"]
				) as response:
					zip_file = response.read()
				zip_path = os.path.join(
					DATA_DIR,
					"lib_py3.11.zip"
				)
				with open(zip_path, "wb") as file:
					file.write(zip_file)
				if not check_hash(zip_path, data["libs_hash"]):
					msg = _("Libs hash mismatch")
					gui.messageBox(msg, _("Error"), wx.OK | wx.ICON_ERROR)
					return
				if os.path.exists(LIBS_DIR):
					shutil.rmtree(LIBS_DIR)
				with zipfile.ZipFile(zip_path, "r") as zip_file:
					zip_file.extractall(LIBS_DIR)
				conf["libRev"] = data["libs_rev"]
				os.remove(zip_path)
				msg = _("Open AI dependencies updated successfully")
				conf["lastCheck"] = time.time()
				gui.messageBox(msg, _("Success. Restart NVDA to apply changes"), wx.OK | wx.ICON_INFORMATION)
				core.restart()
			except Exception as e:
				log.error(e)
				msg = _("Error while updating Open AI dependencies. See log for details. Please retry later.")
				gui.messageBox(msg, _("Error"), wx.OK | wx.ICON_ERROR)
	else:
		conf["lastCheck"] = time.time()


def check_update():
	"""Check for updates to OpenAI plugin."""
	channel = conf["channel"]
	params = urllib.parse.urlencode({
		"nvda_version": NVDA_VERSION,
		"python_version": PYTHON_VERSION,
		"addon_version": ADDON_INFO["version"],
		"addon_channel": channel,
		"lib_rev": LIB_REV
	})
	with urllib.request.urlopen(
		f"{URL_LATEST_RELEASE}?{params}"
	) as response:
		data = json.loads(response.read())
		wx.CallAfter(check_data, data)

if (
	not os.path.exists(LIBS_DIR_PY)
	or (
		conf["check"]
		and conf["lastCheck"] + 86400 * 3 < time.time()
	)
):
		check_update()
