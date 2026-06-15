"""Reasoning controls (unified mode/effort/adaptive combo, xAI encrypted reasoning)."""

from __future__ import annotations

import wx

import addonHandler

from ..consts import Provider, UI_SECTION_SPACING_PX
from ._base import ChromeSection
from ._widgets import bind_checkbox

addonHandler.initTranslation()


class ReasoningChromeSection(ChromeSection):
	def build(self, sizer: wx.Sizer) -> None:
		# Single combo for all reasoning configuration: disabled / effort levels (Low/Medium/
		# High/...) / adaptive. Built per model and hidden when the model offers no real choice.
		# No surrounding StaticBox: a lone combo does not warrant its own group.
		self.dialog.reasoningModeRow = wx.Panel(self.parent)
		mode_sz = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label for the combo box selecting reasoning (disabled/effort level/adaptive).
		self.dialog.reasoningModeLabel = wx.StaticText(self.dialog.reasoningModeRow, label=_("&Reasoning:"))
		self.dialog.reasoningModeChoice = wx.Choice(self.dialog.reasoningModeRow, choices=[])
		self.dialog.reasoningModeChoice.Bind(wx.EVT_CHOICE, self.dialog._onReasoningModeChange)
		mode_sz.Add(self.dialog.reasoningModeLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		mode_sz.Add(self.dialog.reasoningModeChoice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.dialog.reasoningModeRow.SetSizer(mode_sz)
		sizer.Add(self.dialog.reasoningModeRow, 0, wx.EXPAND, 0)
		self._bind_preserve(self.dialog.reasoningModeChoice)

		# Manual extended-thinking token budget (Anthropic models that use
		# ``thinking.budget_tokens``). Shown only for those models; 0 = automatic.
		self.dialog.reasoningBudgetRow = wx.Panel(self.parent)
		budget_sz = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label for the Anthropic extended-thinking token budget spin control.
		self.dialog.reasoningBudgetLabel = wx.StaticText(
			self.dialog.reasoningBudgetRow, label=_("Thinking &budget (tokens, 0 = automatic):")
		)
		self.dialog.reasoningBudgetSpinCtrl = wx.SpinCtrl(self.dialog.reasoningBudgetRow, min=0, max=200000)
		self.dialog.reasoningBudgetSpinCtrl.Bind(wx.EVT_SPINCTRL, self._edited)
		budget_sz.Add(self.dialog.reasoningBudgetLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		budget_sz.Add(
			self.dialog.reasoningBudgetSpinCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX
		)
		self.dialog.reasoningBudgetRow.SetSizer(budget_sz)
		sizer.Add(self.dialog.reasoningBudgetRow, 0, wx.EXPAND, 0)
		self._bind_preserve(self.dialog.reasoningBudgetSpinCtrl)

		self.dialog.xaiEncryptedReasoningCheckBox = bind_checkbox(
			self.parent,
			self.dialog,
			"xaiEncryptedReasoningCheckBox",
			_("Encrypted reasoning &content"),
			self._edited,
		)
		sizer.Add(self.dialog.xaiEncryptedReasoningCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.xaiEncryptedReasoningCheckBox)

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
		d = self.dialog
		mode_opts = getattr(d, "_reasoningModeOptions", ())
		if mode_opts and d.reasoningModeChoice.IsShown():
			idx = d.reasoningModeChoice.GetSelection()
			if 0 <= idx < len(mode_opts):
				mode, effort, _label = mode_opts[idx]
				st["reasoningMode"] = mode != "disabled"
				st["reasoningSelectionMode"] = mode
				if mode == "enabled" and effort is not None:
					st["reasoningEffort"] = effort
				if getattr(model, "adaptive_choice_visible", False):
					st["adaptiveThinking"] = mode == "adaptive"
		cb = d.xaiEncryptedReasoningCheckBox
		if cb.IsShown():
			st["xaiEncryptedReasoning"] = cb.IsChecked()
		spn = getattr(d, "reasoningBudgetSpinCtrl", None)
		if spn is not None and spn.IsShown():
			st["thinkingBudget"] = spn.GetValue()

	def apply(self, st: dict, model) -> None:
		d = self.dialog
		if "xaiEncryptedReasoning" in st:
			if model and model.provider == Provider.xAI and getattr(model, "reasoning", False):
				d.xaiEncryptedReasoningCheckBox.SetValue(bool(st["xaiEncryptedReasoning"]))
		spn = getattr(d, "reasoningBudgetSpinCtrl", None)
		if spn is not None and "thinkingBudget" in st and getattr(model, "thinking_budget_supported", False):
			try:
				spn.SetValue(int(st["thinkingBudget"]))
			except (TypeError, ValueError):
				pass
		self.update_for_model(model)
