"""Shared UI helpers for attachment list controls (files and audio)."""

import wx


class AttachmentListUIMixin:
	"""Attachment list UI helpers."""

	def _sync_attachments_section_header(self):
		try:
			page = self.get_active_page()
		except Exception:
			return
		lbl = getattr(page, "attachmentsSectionLabel", None)
		if lbl is None:
			return
		if page.filesList or page.audioPathList:
			lbl.Show()
		else:
			lbl.Hide()

	def _relayout_attachments(self, anchor=None):
		"""Force a full relayout after attachment list/label visibility changed.

		The attachment label and list controls live inside the active session
		panel's own sizer. ``Show()``/``Hide()`` on those controls only takes
		visible effect once their parent panel's sizer is told to relayout —
		``dialog.Layout()`` alone does not descend into nested panel sizers.
		"""
		panel = None
		if anchor is not None:
			try:
				panel = anchor.GetParent()
			except Exception:
				panel = None
		if panel is None:
			try:
				panel = self.get_active_page()
			except Exception:
				panel = None
		if panel is not None:
			try:
				panel.Layout()
			except Exception:
				pass
		try:
			self.Layout()
		except Exception:
			pass

	def _attachment_list_end_refresh(self, list_ctrl, *, focus_prompt_if_empty=True):
		self._relayout_attachments(anchor=list_ctrl)
		if list_ctrl.GetItemCount() == 0:
			if focus_prompt_if_empty:
				self.promptTextCtrl.SetFocus()
			return
		list_ctrl.SetFocus()
		list_ctrl.Select(0)

	def _list_ctrl_selected_indices(self, list_ctrl):
		out = []
		i = list_ctrl.GetFirstSelected()
		while i != wx.NOT_FOUND:
			out.append(i)
			i = list_ctrl.GetNextSelected(i)
		return out
