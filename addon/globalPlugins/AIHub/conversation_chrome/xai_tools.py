"""xAI Responses API built-in tools and filter chrome."""

from __future__ import annotations

import wx

import addonHandler

from ..consts import Provider, UI_SECTION_SPACING_PX
from ._base import ProviderToolsSection
from ._widgets import bind_checkbox, bind_text, set_controls_visible

addonHandler.initTranslation()


class XaiToolsSection(ProviderToolsSection):
	provider_id = Provider.xAI

	STATE_TEXT = (
		("xaiWebAllowedDomainsTextCtrl", "xaiWebAllowedDomains"),
		("xaiWebExcludedDomainsTextCtrl", "xaiWebExcludedDomains"),
		("xaiXAllowedHandlesTextCtrl", "xaiXAllowedHandles"),
		("xaiXExcludedHandlesTextCtrl", "xaiXExcludedHandles"),
		("xaiXFromDateTextCtrl", "xaiXFromDate"),
		("xaiXToDateTextCtrl", "xaiXToDate"),
	)
	STATE_CHECKS = (
		("xaiWebImageSearchCheckBox", "xaiWebImageSearch"),
		("xaiWebImageUnderstandingCheckBox", "xaiWebImageUnderstanding"),
		("xaiXImageUnderstandingCheckBox", "xaiXImageUnderstanding"),
		("xaiXVideoUnderstandingCheckBox", "xaiXVideoUnderstanding"),
	)

	def build(self, sizer: wx.Sizer) -> None:
		self._panel = wx.Panel(self.parent)
		panel_sz = wx.BoxSizer(wx.VERTICAL)

		toggles = wx.BoxSizer(wx.HORIZONTAL)
		self.dialog.xSearchCheckBox = bind_checkbox(
			self._panel, self.dialog, "xSearchCheckBox", _("&X search"), self._on_x_search_toggled,
		)
		toggles.Add(self.dialog.xSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.xSearchCheckBox)

		self.dialog.codeInterpreterCheckBox = bind_checkbox(
			self._panel, self.dialog, "codeInterpreterCheckBox", _("Code &interpreter"), self._edited,
		)
		toggles.Add(self.dialog.codeInterpreterCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.codeInterpreterCheckBox)

		self.dialog.collectionsSearchCheckBox = bind_checkbox(
			self._panel, self.dialog, "collectionsSearchCheckBox", _("Collections &search"), self._on_collections_toggled,
		)
		toggles.Add(self.dialog.collectionsSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self._bind_preserve(self.dialog.collectionsSearchCheckBox)
		panel_sz.Add(toggles, 0, wx.EXPAND, 0)

		self.dialog.xaiCollectionIdsRow = wx.Panel(self._panel)
		coll_sz = wx.BoxSizer(wx.VERTICAL)
		self.dialog.xaiCollectionIdsLabel = wx.StaticText(self.dialog.xaiCollectionIdsRow, label=_("Collection &IDs:"))
		self.dialog.xaiCollectionIdsTextCtrl = bind_text(self.dialog.xaiCollectionIdsRow, self.dialog, "xaiCollectionIdsTextCtrl", self._edited)
		coll_sz.Add(self.dialog.xaiCollectionIdsLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		coll_sz.Add(self.dialog.xaiCollectionIdsTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiCollectionsMaxResultsLabel = wx.StaticText(self.dialog.xaiCollectionIdsRow, label=_("Max &results:"))
		self.dialog.xaiCollectionsMaxResultsSpinCtrl = wx.SpinCtrl(self.dialog.xaiCollectionIdsRow, min=0, max=50)
		self.dialog.xaiCollectionsMaxResultsSpinCtrl.Bind(wx.EVT_SPINCTRL, self._edited)
		coll_sz.Add(self.dialog.xaiCollectionsMaxResultsLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		coll_sz.Add(self.dialog.xaiCollectionsMaxResultsSpinCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.dialog.xaiCollectionIdsRow.SetSizer(coll_sz)
		panel_sz.Add(self.dialog.xaiCollectionIdsRow, 0, wx.EXPAND, 0)
		for ctrl in (
			self.dialog.xaiCollectionIdsRow,
			self.dialog.xaiCollectionIdsLabel,
			self.dialog.xaiCollectionIdsTextCtrl,
			self.dialog.xaiCollectionsMaxResultsLabel,
			self.dialog.xaiCollectionsMaxResultsSpinCtrl,
		):
			self._bind_preserve(ctrl)

		self.dialog.xaiWebSearchOptionsRow = wx.Panel(self._panel)
		web_sz = wx.BoxSizer(wx.VERTICAL)
		web_sz.Add(wx.StaticText(self.dialog.xaiWebSearchOptionsRow, label=_("Web search filters")), 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.dialog.xaiWebAllowedDomainsLabel = wx.StaticText(self.dialog.xaiWebSearchOptionsRow, label=_("Allowed &domains (max 5):"))
		self.dialog.xaiWebAllowedDomainsTextCtrl = bind_text(self.dialog.xaiWebSearchOptionsRow, self.dialog, "xaiWebAllowedDomainsTextCtrl", self._edited)
		web_sz.Add(self.dialog.xaiWebAllowedDomainsLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		web_sz.Add(self.dialog.xaiWebAllowedDomainsTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiWebExcludedDomainsLabel = wx.StaticText(self.dialog.xaiWebSearchOptionsRow, label=_("Excluded dom&ains (max 5):"))
		self.dialog.xaiWebExcludedDomainsTextCtrl = bind_text(self.dialog.xaiWebSearchOptionsRow, self.dialog, "xaiWebExcludedDomainsTextCtrl", self._edited)
		web_sz.Add(self.dialog.xaiWebExcludedDomainsLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		web_sz.Add(self.dialog.xaiWebExcludedDomainsTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiWebImageSearchCheckBox = bind_checkbox(
			self.dialog.xaiWebSearchOptionsRow, self.dialog, "xaiWebImageSearchCheckBox", _("Enable &image search"), self._edited,
		)
		web_sz.Add(self.dialog.xaiWebImageSearchCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.dialog.xaiWebImageUnderstandingCheckBox = bind_checkbox(
			self.dialog.xaiWebSearchOptionsRow, self.dialog, "xaiWebImageUnderstandingCheckBox", _("Enable image &understanding"), self._edited,
		)
		web_sz.Add(self.dialog.xaiWebImageUnderstandingCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.dialog.xaiWebSearchOptionsRow.SetSizer(web_sz)
		panel_sz.Add(self.dialog.xaiWebSearchOptionsRow, 0, wx.EXPAND, 0)

		self.dialog.xaiXSearchOptionsRow = wx.Panel(self._panel)
		x_sz = wx.BoxSizer(wx.VERTICAL)
		x_sz.Add(wx.StaticText(self.dialog.xaiXSearchOptionsRow, label=_("X search filters")), 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.dialog.xaiXAllowedHandlesLabel = wx.StaticText(self.dialog.xaiXSearchOptionsRow, label=_("Allowed X &handles (max 20):"))
		self.dialog.xaiXAllowedHandlesTextCtrl = bind_text(self.dialog.xaiXSearchOptionsRow, self.dialog, "xaiXAllowedHandlesTextCtrl", self._edited)
		x_sz.Add(self.dialog.xaiXAllowedHandlesLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		x_sz.Add(self.dialog.xaiXAllowedHandlesTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiXExcludedHandlesLabel = wx.StaticText(self.dialog.xaiXSearchOptionsRow, label=_("Excluded X hand&les (max 20):"))
		self.dialog.xaiXExcludedHandlesTextCtrl = bind_text(self.dialog.xaiXSearchOptionsRow, self.dialog, "xaiXExcludedHandlesTextCtrl", self._edited)
		x_sz.Add(self.dialog.xaiXExcludedHandlesLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		x_sz.Add(self.dialog.xaiXExcludedHandlesTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiXFromDateLabel = wx.StaticText(self.dialog.xaiXSearchOptionsRow, label=_("From date (&YYYY-MM-DD):"))
		self.dialog.xaiXFromDateTextCtrl = bind_text(self.dialog.xaiXSearchOptionsRow, self.dialog, "xaiXFromDateTextCtrl", self._edited)
		x_sz.Add(self.dialog.xaiXFromDateLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		x_sz.Add(self.dialog.xaiXFromDateTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiXToDateLabel = wx.StaticText(self.dialog.xaiXSearchOptionsRow, label=_("To date (YYYY-MM-&DD):"))
		self.dialog.xaiXToDateTextCtrl = bind_text(self.dialog.xaiXSearchOptionsRow, self.dialog, "xaiXToDateTextCtrl", self._edited)
		x_sz.Add(self.dialog.xaiXToDateLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		x_sz.Add(self.dialog.xaiXToDateTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, UI_SECTION_SPACING_PX)
		self.dialog.xaiXImageUnderstandingCheckBox = bind_checkbox(
			self.dialog.xaiXSearchOptionsRow, self.dialog, "xaiXImageUnderstandingCheckBox", _("X image &understanding"), self._edited,
		)
		x_sz.Add(self.dialog.xaiXImageUnderstandingCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.dialog.xaiXVideoUnderstandingCheckBox = bind_checkbox(
			self.dialog.xaiXSearchOptionsRow, self.dialog, "xaiXVideoUnderstandingCheckBox", _("X video underst&anding"), self._edited,
		)
		x_sz.Add(self.dialog.xaiXVideoUnderstandingCheckBox, 0, wx.ALL, UI_SECTION_SPACING_PX)
		self.dialog.xaiXSearchOptionsRow.SetSizer(x_sz)
		panel_sz.Add(self.dialog.xaiXSearchOptionsRow, 0, wx.EXPAND, 0)

		for name in (
			"xaiWebSearchOptionsRow", "xaiWebAllowedDomainsLabel", "xaiWebAllowedDomainsTextCtrl",
			"xaiWebExcludedDomainsLabel", "xaiWebExcludedDomainsTextCtrl",
			"xaiWebImageSearchCheckBox", "xaiWebImageUnderstandingCheckBox",
			"xaiXSearchOptionsRow", "xaiXAllowedHandlesLabel", "xaiXAllowedHandlesTextCtrl",
			"xaiXExcludedHandlesLabel", "xaiXExcludedHandlesTextCtrl",
			"xaiXFromDateLabel", "xaiXFromDateTextCtrl", "xaiXToDateLabel", "xaiXToDateTextCtrl",
			"xaiXImageUnderstandingCheckBox", "xaiXVideoUnderstandingCheckBox",
		):
			self._bind_preserve(getattr(self.dialog, name))

		self._panel.SetSizer(panel_sz)
		sizer.Add(self._panel, 0, wx.EXPAND, 0)

	def _on_x_search_toggled(self, evt):
		self.update_for_model(self.dialog.getCurrentModel())
		self._edited(evt)

	def _on_collections_toggled(self, evt):
		self._update_collections_row()
		self._edited(evt)

	def _supported(self, model) -> bool:
		return bool(model and getattr(model, "supports_xai_builtin_tools", False))

	def _update_collections_row(self, model=None) -> None:
		model = model or self.dialog.getCurrentModel()
		supported = bool(model and getattr(model, "supports_collections_search", False))
		cb = self.dialog.collectionsSearchCheckBox
		if supported:
			cb.Enable(True)
			cb.Show(True)
		else:
			cb.Enable(False)
			cb.Show(False)
			cb.SetValue(False)
		show_ids = supported and cb.IsChecked()
		set_controls_visible([
			self.dialog.xaiCollectionIdsRow,
			self.dialog.xaiCollectionIdsLabel,
			self.dialog.xaiCollectionIdsTextCtrl,
			self.dialog.xaiCollectionsMaxResultsLabel,
			self.dialog.xaiCollectionsMaxResultsSpinCtrl,
		], show_ids)
		if not show_ids:
			self.dialog.xaiCollectionIdsTextCtrl.SetValue("")
			self.dialog.xaiCollectionsMaxResultsSpinCtrl.SetValue(0)

	def update_for_model(self, model) -> None:
		show = self._supported(model)
		self._panel.Show(show)
		if not show:
			for name in ("xSearchCheckBox", "codeInterpreterCheckBox", "collectionsSearchCheckBox"):
				cb = getattr(self.dialog, name)
				cb.Enable(False)
				cb.Show(False)
				cb.SetValue(False)
			set_controls_visible([self.dialog.xaiWebSearchOptionsRow, self.dialog.xaiXSearchOptionsRow], False)
			self._update_collections_row(model)
			return

		for name, attr in (
			("xSearchCheckBox", "supports_x_search"),
			("codeInterpreterCheckBox", "supports_code_interpreter"),
		):
			cb = getattr(self.dialog, name)
			on = getattr(model, attr, False)
			cb.Enable(on)
			cb.Show(on)
			if not on:
				cb.SetValue(False)

		self._update_collections_row(model)

		web_on = model.supports_web_search and self.dialog.webSearchCheckBox.IsShown() and self.dialog.webSearchCheckBox.IsChecked()
		x_on = getattr(model, "supports_x_search", False) and self.dialog.xSearchCheckBox.IsShown() and self.dialog.xSearchCheckBox.IsChecked()

		set_controls_visible([self.dialog.xaiWebSearchOptionsRow], web_on)
		set_controls_visible([
			self.dialog.xaiWebAllowedDomainsLabel, self.dialog.xaiWebAllowedDomainsTextCtrl,
			self.dialog.xaiWebExcludedDomainsLabel, self.dialog.xaiWebExcludedDomainsTextCtrl,
			self.dialog.xaiWebImageSearchCheckBox, self.dialog.xaiWebImageUnderstandingCheckBox,
		], web_on)
		if not web_on:
			self.dialog.xaiWebAllowedDomainsTextCtrl.SetValue("")
			self.dialog.xaiWebExcludedDomainsTextCtrl.SetValue("")
			self.dialog.xaiWebImageSearchCheckBox.SetValue(False)
			self.dialog.xaiWebImageUnderstandingCheckBox.SetValue(False)

		set_controls_visible([self.dialog.xaiXSearchOptionsRow], x_on)
		set_controls_visible([
			self.dialog.xaiXAllowedHandlesLabel, self.dialog.xaiXAllowedHandlesTextCtrl,
			self.dialog.xaiXExcludedHandlesLabel, self.dialog.xaiXExcludedHandlesTextCtrl,
			self.dialog.xaiXFromDateLabel, self.dialog.xaiXFromDateTextCtrl,
			self.dialog.xaiXToDateLabel, self.dialog.xaiXToDateTextCtrl,
			self.dialog.xaiXImageUnderstandingCheckBox, self.dialog.xaiXVideoUnderstandingCheckBox,
		], x_on)
		if not x_on:
			self.dialog.xaiXAllowedHandlesTextCtrl.SetValue("")
			self.dialog.xaiXExcludedHandlesTextCtrl.SetValue("")
			self.dialog.xaiXFromDateTextCtrl.SetValue("")
			self.dialog.xaiXToDateTextCtrl.SetValue("")
			self.dialog.xaiXImageUnderstandingCheckBox.SetValue(False)
			self.dialog.xaiXVideoUnderstandingCheckBox.SetValue(False)

	def capture(self, st: dict, model) -> None:
		if self.dialog.xSearchCheckBox.IsShown():
			st["xSearch"] = self.dialog.xSearchCheckBox.IsChecked()
		if self.dialog.codeInterpreterCheckBox.IsShown():
			st["codeInterpreter"] = self.dialog.codeInterpreterCheckBox.IsChecked()
		if self.dialog.collectionsSearchCheckBox.IsShown():
			st["collectionsSearch"] = self.dialog.collectionsSearchCheckBox.IsChecked()
		if self.dialog.xaiCollectionIdsTextCtrl.IsShown():
			st["xaiCollectionIds"] = self.dialog.xaiCollectionIdsTextCtrl.GetValue()
			st["xaiCollectionsMaxResults"] = self.dialog.xaiCollectionsMaxResultsSpinCtrl.GetValue()
		for ctrl_name, key in self.STATE_TEXT:
			ctrl = getattr(self.dialog, ctrl_name)
			if ctrl.IsShown():
				st[key] = ctrl.GetValue()
		for ctrl_name, key in self.STATE_CHECKS:
			ctrl = getattr(self.dialog, ctrl_name)
			if ctrl.IsShown():
				st[key] = ctrl.IsChecked()

	def apply(self, st: dict, model) -> None:
		if not self._supported(model):
			return
		if "xSearch" in st:
			self.dialog.xSearchCheckBox.SetValue(bool(st["xSearch"]))
		if "codeInterpreter" in st:
			self.dialog.codeInterpreterCheckBox.SetValue(bool(st["codeInterpreter"]))
		if "collectionsSearch" in st:
			self.dialog.collectionsSearchCheckBox.SetValue(bool(st["collectionsSearch"]))
		if "xaiCollectionIds" in st:
			self.dialog.xaiCollectionIdsTextCtrl.SetValue(st["xaiCollectionIds"])
		if "xaiCollectionsMaxResults" in st:
			try:
				self.dialog.xaiCollectionsMaxResultsSpinCtrl.SetValue(int(st["xaiCollectionsMaxResults"]))
			except (TypeError, ValueError):
				pass
		for ctrl_name, key in self.STATE_TEXT:
			if key in st:
				getattr(self.dialog, ctrl_name).SetValue(st[key])
		for ctrl_name, key in self.STATE_CHECKS:
			if key in st:
				getattr(self.dialog, ctrl_name).SetValue(bool(st[key]))
		self.update_for_model(model)
