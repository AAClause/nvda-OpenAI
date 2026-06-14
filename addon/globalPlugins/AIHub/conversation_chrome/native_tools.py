"""Native provider web search toggle (Anthropic, Google, OpenAI, xAI)."""

from __future__ import annotations

import wx

import addonHandler

from ..consts import UI_SECTION_SPACING_PX
from ._base import ProviderToolsSection
from ._widgets import bind_checkbox

addonHandler.initTranslation()


class NativeWebSearchSection(ProviderToolsSection):
	def build(self, sizer: wx.Sizer) -> None:
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.dialog.webSearchCheckBox = bind_checkbox(
			self.parent,
			self.dialog,
			"webSearchCheckBox",
			_("&Web search"),
			self._on_toggled,
		)
		row.Add(self.dialog.webSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.webSearchCheckBox)
		sizer.Add(row, 0, wx.ALL, 0)

	def _on_toggled(self, evt):
		try:
			model = self.dialog.getCurrentModel()
			if model:
				self.dialog._generation_chrome.update_for_model(model)
		except Exception:
			pass
		self._edited(evt)

	def update_for_model(self, model) -> None:
		cb = self.dialog.webSearchCheckBox
		if model and model.supports_web_search:
			cb.Enable(True)
			cb.Show(True)
		else:
			cb.Enable(False)
			cb.Show(False)
			cb.SetValue(False)

	def capture(self, st: dict, model) -> None:
		if self.dialog.webSearchCheckBox.IsShown():
			st["webSearch"] = self.dialog.webSearchCheckBox.IsChecked()

	def apply(self, st: dict, model) -> None:
		if model and model.supports_web_search and "webSearch" in st:
			self.dialog.webSearchCheckBox.SetValue(bool(st["webSearch"]))
