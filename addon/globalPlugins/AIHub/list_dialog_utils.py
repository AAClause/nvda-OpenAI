"""Shared helpers for wx.ListCtrl-based management dialogs (context menu placement, Space key)."""
import wx


def listctrl_selected_indices(list_ctrl):
	idxs = []
	idx = list_ctrl.GetFirstSelected()
	while idx != -1:
		idxs.append(idx)
		idx = list_ctrl.GetNextSelected(idx)
	return idxs


def listctrl_menu_anchor_point(list_ctrl):
	"""Return client coordinates to anchor a context menu near the focused or first selected row."""
	idx = list_ctrl.GetFocusedItem()
	if idx in (-1, wx.NOT_FOUND):
		idx = list_ctrl.GetFirstSelected()
	if idx < 0 or idx >= list_ctrl.GetItemCount():
		sz = list_ctrl.GetClientSize()
		return wx.Point(max(8, sz.width // 3), max(8, min(sz.height // 2, 40)))
	r = list_ctrl.GetItemRect(idx)
	cw = list_ctrl.GetClientSize().width
	x = min(max(r.x + 24, 8), cw - 8)
	y = r.y + max(4, r.height // 2)
	return wx.Point(x, y)


def listctrl_apply_context_menu_hit_selection(list_ctrl, hit, n_entries):
	"""Explorer-style selection when right-clicking a row; returns True if ``hit`` is a valid row index."""
	if hit < 0 or hit >= n_entries:
		return False
	selected_idxs = listctrl_selected_indices(list_ctrl)
	if hit not in selected_idxs:
		for i in range(list_ctrl.GetItemCount()):
			list_ctrl.Select(i, False)
		list_ctrl.Select(hit)
	list_ctrl.Focus(hit)
	list_ctrl.EnsureVisible(hit)
	return True


def bind_dialog_char_hook_space_opens_menu(dialog, list_ctrl, show_menu_callback):
	"""
	Plain Space on ``list_ctrl``: if the focused row is already selected, open the context menu
	(``show_menu_callback``). Otherwise let the event propagate so the native control selects
	the focused row as usual.
	"""
	def on_char_hook(evt):
		if (
			evt.GetKeyCode() == wx.WXK_SPACE
			and evt.GetModifiers() == 0
			and wx.Window.FindFocus() is list_ctrl
		):
			focused = list_ctrl.GetFocusedItem()
			if focused not in (-1, wx.NOT_FOUND) and list_ctrl.IsSelected(focused):
				show_menu_callback()
				return
		evt.Skip()

	dialog.Bind(wx.EVT_CHAR_HOOK, on_char_hook)
