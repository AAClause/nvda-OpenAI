"""Small wx layout helpers for conversation generation chrome."""

from __future__ import annotations

import wx

from ..consts import UI_SECTION_SPACING_PX


def add_vertical_labeled(
	parent: wx.Window,
	sizer: wx.Sizer,
	label: str,
	control: wx.Window,
	border: int = UI_SECTION_SPACING_PX,
) -> None:
	label_ctrl = wx.StaticText(parent, label=label)
	sizer.Add(label_ctrl, 0, wx.LEFT | wx.RIGHT | wx.TOP, border)
	sizer.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border)


def set_controls_visible(controls: list, visible: bool, enabled: bool | None = None) -> None:
	if enabled is None:
		enabled = visible
	for ctrl in controls:
		if ctrl is None:
			continue
		ctrl.Show(visible)
		ctrl.Enable(enabled)


def bind_checkbox(parent, dialog, attr: str, label: str, handler) -> wx.CheckBox:
	cb = wx.CheckBox(parent, label=label)
	cb.Bind(wx.EVT_CHECKBOX, handler)
	setattr(dialog, attr, cb)
	return cb


def bind_text(parent, dialog, attr: str, handler=None) -> wx.TextCtrl:
	ctrl = wx.TextCtrl(parent)
	if handler is not None:
		ctrl.Bind(wx.EVT_TEXT, handler)
	setattr(dialog, attr, ctrl)
	return ctrl
