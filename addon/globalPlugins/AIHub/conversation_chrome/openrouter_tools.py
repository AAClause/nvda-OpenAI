"""OpenRouter server-side web search tool."""

from __future__ import annotations

import wx

import addonHandler

from ..consts import Provider, UI_SECTION_SPACING_PX
from ._base import ProviderToolsSection
from ._widgets import bind_checkbox

addonHandler.initTranslation()


class OpenRouterToolsSection(ProviderToolsSection):
	provider_id = Provider.OpenRouter

	def build(self, sizer: wx.Sizer) -> None:
		self._panel = wx.Panel(self.parent)
		panel_sz = wx.BoxSizer(wx.HORIZONTAL)
		self.dialog.openRouterWebSearchCheckBox = bind_checkbox(
			self._panel,
			self.dialog,
			"openRouterWebSearchCheckBox",
			_("OpenRouter &web search"),
			self._edited,
		)
		panel_sz.Add(self.dialog.openRouterWebSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._panel.SetSizer(panel_sz)
		sizer.Add(self._panel, 0, wx.EXPAND, 0)
		self._bind_preserve(self.dialog.openRouterWebSearchCheckBox)

	def update_for_model(self, model) -> None:
		cb = self.dialog.openRouterWebSearchCheckBox
		show = bool(model and model.supports_openrouter_web_search)
		self._panel.Show(show)
		if show:
			cb.Enable(True)
			cb.Show(True)
		else:
			cb.Enable(False)
			cb.Show(False)
			cb.SetValue(False)

	def capture(self, st: dict, model) -> None:
		cb = self.dialog.openRouterWebSearchCheckBox
		if cb.IsShown():
			st["openRouterWebSearch"] = cb.IsChecked()

	def apply(self, st: dict, model) -> None:
		if model and model.supports_openrouter_web_search and "openRouterWebSearch" in st:
			self.dialog.openRouterWebSearchCheckBox.SetValue(bool(st["openRouterWebSearch"]))
