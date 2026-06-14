"""Reasoning controls (mode, effort, adaptive thinking, xAI encrypted reasoning)."""

from __future__ import annotations

import wx

import addonHandler

from ..consts import Provider, UI_SECTION_SPACING_PX
from ._base import ChromeSection
from ._widgets import bind_checkbox, set_controls_visible

addonHandler.initTranslation()


class ReasoningChromeSection(ChromeSection):
	def build(self, sizer: wx.Sizer) -> None:
		# Translators: Section title for reasoning-related generation options.
		box = wx.StaticBox(self.parent, label=_("Reasoning"))
		inner = wx.StaticBoxSizer(box, wx.VERTICAL)
		self.dialog.reasoningModeCheckBox = wx.CheckBox(
			self.parent,
			label=_("&Reasoning mode"),
		)
		self.dialog.reasoningModeCheckBox.Bind(wx.EVT_CHECKBOX, self.dialog._onReasoningModeChange)
		inner.Add(self.dialog.reasoningModeCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.reasoningModeCheckBox)

		self.dialog.adaptiveThinkingCheckBox = wx.CheckBox(
			self.parent,
			label=_("&Adaptive thinking"),
		)
		self.dialog.adaptiveThinkingCheckBox.SetValue(self.dialog.conf.get("adaptiveThinking", True))
		self.dialog.adaptiveThinkingCheckBox.Bind(wx.EVT_CHECKBOX, self.dialog._onAdaptiveThinkingChange)
		inner.Add(self.dialog.adaptiveThinkingCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.adaptiveThinkingCheckBox)

		self.dialog.reasoningEffortRow = wx.Panel(self.parent)
		row_sz = wx.BoxSizer(wx.VERTICAL)
		self.dialog.reasoningEffortLabel = wx.StaticText(self.dialog.reasoningEffortRow, label=_("Reasoning &effort:"))
		self.dialog.reasoningEffortChoice = wx.Choice(self.dialog.reasoningEffortRow, choices=[])
		self.dialog.reasoningEffortChoice.Bind(wx.EVT_CHOICE, self.dialog._onReasoningEffortChange)
		row_sz.Add(self.dialog.reasoningEffortLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		row_sz.Add(self.dialog.reasoningEffortChoice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.dialog.reasoningEffortRow.SetSizer(row_sz)
		inner.Add(self.dialog.reasoningEffortRow, 0, wx.EXPAND, 0)
		self._bind_preserve(self.dialog.reasoningEffortChoice)

		self.dialog.xaiEncryptedReasoningCheckBox = bind_checkbox(
			self.parent,
			self.dialog,
			"xaiEncryptedReasoningCheckBox",
			_("Encrypted reasoning &content"),
			self._edited,
		)
		inner.Add(self.dialog.xaiEncryptedReasoningCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.xaiEncryptedReasoningCheckBox)

		sizer.Add(inner, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

	def update_for_model(self, model) -> None:
		show_enc = bool(model and model.provider == Provider.xAI and getattr(model, "reasoning", False))
		cb = self.dialog.xaiEncryptedReasoningCheckBox
		if show_enc:
			cb.Show(True)
			cb.Enable(True)
		else:
			cb.Show(False)
			cb.Enable(False)
			cb.SetValue(False)

	def capture(self, st: dict, model) -> None:
		if self.dialog.reasoningModeCheckBox.IsShown():
			st["reasoningMode"] = self.dialog.reasoningModeCheckBox.IsChecked()
		opts = getattr(self.dialog, "_reasoningEffortOptions", ())
		idx = self.dialog.reasoningEffortChoice.GetSelection()
		if opts and 0 <= idx < len(opts):
			st["reasoningEffort"] = opts[idx][0]
		if self.dialog.adaptiveThinkingCheckBox.IsShown():
			st["adaptiveThinking"] = self.dialog.adaptiveThinkingCheckBox.IsChecked()
		cb = self.dialog.xaiEncryptedReasoningCheckBox
		if cb.IsShown():
			st["xaiEncryptedReasoning"] = cb.IsChecked()

	def apply(self, st: dict, model) -> None:
		if "xaiEncryptedReasoning" in st:
			if model and model.provider == Provider.xAI and getattr(model, "reasoning", False):
				self.dialog.xaiEncryptedReasoningCheckBox.SetValue(bool(st["xaiEncryptedReasoning"]))
		opts = getattr(self.dialog, "_reasoningEffortOptions", ())
		if opts and "reasoningEffort" in st:
			want = st["reasoningEffort"]
			idx = next((i for i, (v, _) in enumerate(opts) if v == want), None)
			if idx is not None:
				self.dialog.reasoningEffortChoice.SetSelection(idx)
				self.dialog.conf["reasoningEffort"] = want
		if self.dialog.adaptiveThinkingCheckBox.IsShown() and "adaptiveThinking" in st:
			v = bool(st["adaptiveThinking"])
			self.dialog.adaptiveThinkingCheckBox.SetValue(v)
			self.dialog.conf["adaptiveThinking"] = v
		if opts and self.dialog.reasoningModeCheckBox.IsShown() and self.dialog.reasoningModeCheckBox.IsChecked():
			self.dialog._ensure_reasoning_effort_selection(opts)
		self.update_for_model(model)
