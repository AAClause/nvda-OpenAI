"""Tools menu and registry for single-tool dialogs."""

import importlib
import addonHandler
import wx
from enum import StrEnum, auto
from logHandler import log

from . import apikeymanager
from .consts import Provider

addonHandler.initTranslation()


class ToolId(StrEnum):
	VOXTRAL_TTS = auto()
	MISTRAL_OCR = auto()
	MISTRAL_SPEECH_TO_TEXT = auto()
	LYRIA_3_PRO = auto()
	OPENAI_TTS = auto()
	OPENAI_TRANSCRIPTION = auto()
	OLLAMA_MODEL_MANAGER = auto()


# Submenu group label shown to the user. We don't reuse Provider.MistralAI
# because the user-facing brand is "Mistral", not "MistralAI".
_GROUP_MISTRAL = "Mistral"


# Dialog classes are loaded on first use (menu → open tool), not at addon startup.
# ``group_label`` is the visible submenu name; ``manager_provider`` is the
# Provider enum the dialog uses to look up API credentials.
TOOLS_REGISTRY = (
	{
		"id": ToolId.VOXTRAL_TTS,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("Voxtral TTS..."),
		"group_label": _GROUP_MISTRAL,
		"manager_provider": Provider.MistralAI,
		"dialog_module": ".tool_voxtral_tts_dialog",
		"dialog_class": "VoxtralTTSToolDialog",
	},
	{
		"id": ToolId.MISTRAL_OCR,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("OCR..."),
		"group_label": _GROUP_MISTRAL,
		"manager_provider": Provider.MistralAI,
		"dialog_module": ".tool_mistral_ocr_dialog",
		"dialog_class": "MistralOCRToolDialog",
	},
	{
		"id": ToolId.MISTRAL_SPEECH_TO_TEXT,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("Speech to Text..."),
		"group_label": _GROUP_MISTRAL,
		"manager_provider": Provider.MistralAI,
		"dialog_module": ".tool_mistral_transcription_dialog",
		"dialog_class": "MistralSpeechToTextToolDialog",
	},
	{
		"id": ToolId.LYRIA_3_PRO,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("Lyria 3 Pro..."),
		"group_label": Provider.Google,
		"manager_provider": Provider.Google,
		"dialog_module": ".tool_lyria_dialog",
		"dialog_class": "Lyria3ProToolDialog",
	},
	{
		"id": ToolId.OPENAI_TTS,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("TTS..."),
		"group_label": Provider.OpenAI,
		"manager_provider": Provider.OpenAI,
		"dialog_module": ".tool_openai_tts_dialog",
		"dialog_class": "OpenAITTSToolDialog",
	},
	{
		"id": ToolId.OPENAI_TRANSCRIPTION,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("Transcription / Translation..."),
		"group_label": Provider.OpenAI,
		"manager_provider": Provider.OpenAI,
		"dialog_module": ".tool_openai_transcription_dialog",
		"dialog_class": "OpenAITranscriptionToolDialog",
	},
	{
		"id": ToolId.OLLAMA_MODEL_MANAGER,
		# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
		"label": _("Model manager..."),
		"group_label": Provider.Ollama,
		"manager_provider": Provider.Ollama,
		"dialog_module": ".tool_ollama_models_dialog",
		"dialog_class": "OllamaModelManagerToolDialog",
	},
)


# Order of the per-vendor submenus in the Tools menu.
_GROUP_ORDER = (Provider.OpenAI, _GROUP_MISTRAL, Provider.Google, Provider.Ollama)

_OPEN_TOOL_DIALOGS = []


def _resolve_dialog_cls(tool_def):
	"""Return dialog class, importing the module on first use.

	Relative dialog modules (e.g. ``.tool_openai_tts_dialog``) live next to
	this file inside ``globalPlugins.AIHub``; passing ``__package__`` resolves
	them against this module's own package.
	"""
	cls = tool_def.get("dialog_cls")
	if cls is not None:
		return cls
	cached = tool_def.get("_resolved_dialog_cls")
	if cached is not None:
		return cached
	mod = importlib.import_module(tool_def["dialog_module"], package=__package__)
	cls = getattr(mod, tool_def["dialog_class"])
	tool_def["_resolved_dialog_cls"] = cls
	return cls


def _resolve_plugin(parent, plugin=None):
	if plugin is not None:
		return plugin
	if parent is not None:
		return getattr(parent, "_plugin", None)
	return None


def _populate_tools_provider_submenus(menu, parent, plugin):
	for group_label in _GROUP_ORDER:
		group_tools = [td for td in TOOLS_REGISTRY if td.get("group_label") == group_label]
		if not group_tools:
			continue
		submenu = wx.Menu()
		for tool_def in group_tools:
			item = submenu.Append(wx.ID_ANY, tool_def["label"])
			submenu.Bind(
				wx.EVT_MENU,
				lambda evt, td=tool_def: open_tool_dialog(parent, td, plugin=plugin),
				id=item.GetId(),
			)
		menu.AppendSubMenu(submenu, str(group_label))


def open_tool_dialog(parent, tool_def, conversationData=None, plugin=None):
	manager_provider = tool_def.get("manager_provider")
	group_label = tool_def.get("group_label")
	if manager_provider:
		try:
			manager = apikeymanager.get(manager_provider)
		except Exception:
			manager = None
		if manager and not manager.isReady():
			provider_label = group_label or manager_provider
			wx.MessageBox(
				# Translators: AI-Hub Tools menu: label for a submenu or action that opens a provider tool.
				_("No account configured for %s. Please add an account for this provider in AI-Hub settings.") % provider_label,
				"AI-Hub",
				wx.OK | wx.ICON_ERROR,
			)
			return
	dialog_cls = _resolve_dialog_cls(tool_def)
	plugin = _resolve_plugin(parent, plugin)
	dlg = dialog_cls(
		None,
		conversationData=conversationData,
		parentDialog=parent,
		plugin=plugin,
	)
	_OPEN_TOOL_DIALOGS.append(dlg)

	def _on_close(evt, dialog=dlg):
		try:
			if dialog in _OPEN_TOOL_DIALOGS:
				_OPEN_TOOL_DIALOGS.remove(dialog)
		except Exception:
			log.debug("Failed to remove closed tool dialog from tracking list", exc_info=True)
		evt.Skip()

	dlg.Bind(wx.EVT_CLOSE, _on_close)
	dlg.Show()
	dlg.Raise()


def open_tool_dialog_by_class(parent, dialog_cls, conversationData=None, plugin=None):
	tool_def = {"dialog_cls": dialog_cls}
	open_tool_dialog(parent, tool_def, conversationData=conversationData, plugin=plugin)


def append_tools_submenu(menu, parent=None, plugin=None, label=None):
	"""Append a Tools submenu to an existing menu."""
	tools_menu = wx.Menu()
	plugin = _resolve_plugin(parent, plugin)
	_populate_tools_provider_submenus(tools_menu, parent, plugin)
	# Translators: AI-Hub main window — Tools menu: entry in a context menu or submenu.
	menu.AppendSubMenu(tools_menu, label or _("&Tools"))


def show_tools_menu(parent, anchor_btn=None, plugin=None):
	menu = wx.Menu()
	plugin = _resolve_plugin(parent, plugin)
	_populate_tools_provider_submenus(menu, parent, plugin)
	if anchor_btn is not None:
		pos = anchor_btn.GetPosition()
		pos = (pos.x, pos.y + anchor_btn.GetSize().height)
		parent.PopupMenu(menu, pos)
	else:
		parent.PopupMenu(menu)
	menu.Destroy()
