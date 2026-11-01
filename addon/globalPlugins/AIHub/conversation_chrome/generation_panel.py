"""Generation chrome: reasoning + tools groups for the conversation dialog."""

from __future__ import annotations

import wx

import addonHandler

from .reasoning_section import ReasoningChromeSection
from .tools_section import ToolsChromeSection
from .verbosity_section import VerbosityChromeSection

addonHandler.initTranslation()


class GenerationChromePanel:
	"""Builds and manages grouped generation controls on the conversation dialog."""

	def __init__(self, dialog, parent: wx.Window):
		self.dialog = dialog
		self.parent = parent
		self._sections = [
			ReasoningChromeSection(dialog, parent),
			VerbosityChromeSection(dialog, parent),
			ToolsChromeSection(dialog, parent),
		]

	def build(self) -> wx.Sizer:
		# Translators: Outer section title wrapping reasoning and tool groups.
		gen_box = wx.StaticBox(self.parent, label=_("Generation"))
		gen_sz = wx.StaticBoxSizer(gen_box, wx.VERTICAL)
		for section in self._sections:
			section.build(gen_sz)
		return gen_sz

	def preserve_controls(self) -> list:
		controls = []
		for section in self._sections:
			controls.extend(section.preserve_controls())
		return controls

	def update_for_model(self, model) -> None:
		for section in self._sections:
			section.update_for_model(model)

	def capture(self, st: dict, model) -> None:
		for section in self._sections:
			section.capture(st, model)

	def apply(self, st: dict, model) -> None:
		for section in self._sections:
			section.apply(st, model)

	def disable_all(self) -> None:
		for section in self._sections:
			section.disable()
