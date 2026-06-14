"""Tools group: native web search, OpenRouter, and xAI built-in tools."""

from __future__ import annotations

import wx

import addonHandler

from ..consts import UI_SECTION_SPACING_PX
from ._base import ChromeSection
from .native_tools import NativeWebSearchSection
from .openrouter_tools import OpenRouterToolsSection
from .xai_tools import XaiToolsSection

addonHandler.initTranslation()


class ToolsChromeSection(ChromeSection):
	def __init__(self, dialog, parent: wx.Window):
		super().__init__(dialog, parent)
		self._native = NativeWebSearchSection(dialog, parent)
		self._openrouter = OpenRouterToolsSection(dialog, parent)
		self._xai = XaiToolsSection(dialog, parent)
		self._children = [self._native, self._openrouter, self._xai]

	def build(self, sizer: wx.Sizer) -> None:
		# Translators: Section title for model tool toggles (web search, code interpreter, etc.).
		box = wx.StaticBox(self.parent, label=_("Tools"))
		inner = wx.StaticBoxSizer(box, wx.VERTICAL)
		for child in self._children:
			child.build(inner)
		sizer.Add(inner, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

	def preserve_controls(self) -> list:
		items = list(super().preserve_controls())
		for child in self._children:
			items.extend(child.preserve_controls())
		return items

	def update_for_model(self, model) -> None:
		for child in self._children:
			child.update_for_model(model)

	def capture(self, st: dict, model) -> None:
		for child in self._children:
			child.capture(st, model)

	def apply(self, st: dict, model) -> None:
		for child in self._children:
			child.apply(st, model)

	def disable(self) -> None:
		for child in self._children:
			child.disable()
