"""Load the add-on's pure modules outside NVDA.

NVDA-only modules (addonHandler, logHandler, globalVars, wx, winsound...) are
replaced by small stubs, and the ``AIHub`` package is registered without
running its ``__init__`` (which needs a live NVDA), so tests can import
``AIHub.apiclient``, ``AIHub.pricingutils`` and the like.
"""

import builtins
import enum
import logging
import os
import sys
import tempfile
import types
from unittest import mock

if not hasattr(enum, "StrEnum"):
	class StrEnum(str, enum.Enum):
		def _generate_next_value_(name, start, count, last_values):
			return name.lower()

		def __str__(self):
			return str(self.value)

	enum.StrEnum = StrEnum

# The add-on falls back to API keys and hosts from the environment; drop them so
# the developer's real accounts never reach the tests (nor their output).
for _name in list(os.environ):
	if _name.upper().endswith("_KEY") or _name.upper() == "OLLAMA_HOST":
		del os.environ[_name]

ADDON_PACKAGE_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "addon", "globalPlugins", "AIHub")


def _stub(name, **attrs):
	module = types.ModuleType(name)
	module.__dict__.update(attrs)
	sys.modules.setdefault(name, module)
	return module


builtins._ = lambda text: text
builtins.ngettext = lambda singular, plural, count: singular if count == 1 else plural
_stub("addonHandler", initTranslation=lambda: None, getCodeAddon=mock.MagicMock(), Addon=mock.MagicMock())
_stub("logHandler", log=logging.getLogger("AIHub"))
_stub("globalVars", appArgs=types.SimpleNamespace(configPath=tempfile.mkdtemp(prefix="aihub-tests-")))
_stub("config", conf={})
if "winsound" not in sys.modules:
	try:
		import winsound  # noqa: F401
	except ImportError:
		_stub("winsound", PlaySound=lambda *a, **k: None, SND_ASYNC=1, SND_LOOP=8)
sys.modules.setdefault("wx", mock.MagicMock(name="wx"))
sys.modules.setdefault("markdown_it", mock.MagicMock(name="markdown_it"))
if "PIL" not in sys.modules:
	pil_mock = mock.MagicMock(name="PIL")
	sys.modules["PIL"] = pil_mock
	sys.modules["PIL.Image"] = pil_mock.Image
	sys.modules["PIL.ImageGrab"] = pil_mock.ImageGrab

package = types.ModuleType("AIHub")
package.__path__ = [os.path.abspath(ADDON_PACKAGE_DIR)]
sys.modules.setdefault("AIHub", package)
