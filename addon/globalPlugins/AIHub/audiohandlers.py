"""Audio list handlers for ConversationDialog."""
import os
import wx

import addonHandler
from logHandler import log

from .consts import stop_progress_sound
from .history import TextSegment
from .mediastore import persist_local_file

addonHandler.initTranslation()


class AudioHandlersMixin:
	def persistAudioPath(self, path):
		"""Persist temporary audio to add-on data folder."""
		return persist_local_file(path, "audio", prefix="audio", fallback_ext=".wav")

	def _playBlockAudio(self, path: str):
		if not path or not os.path.exists(path):
			return
		self._audioPlayingPath = path
		try:
			os.startfile(path)
			# Translators: AI-Hub — audio capture and playback: brief status feedback (speech/braille), not a full dialog.
			self.message(_("Playing audio"))
		except Exception as e:
			log.error(f"Failed to play audio: {e}", exc_info=True)
			self._audioPlayingPath = None
			# Translators: AI-Hub — audio capture and playback: brief status feedback (speech/braille), not a full dialog.
			self.message(_("An error occurred. More information is in the NVDA log."))

	def _stopBlockAudio(self):
		stop_progress_sound()
		self._audioPlayingPath = None
		# Translators: AI-Hub — audio capture and playback: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Audio stopped"))

	def onAudioPlayPause(self, evt):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		if segment is None:
			return
		block = segment.owner
		if segment != block.segmentResponseLabel and segment != block.segmentResponse:
			return
		path = getattr(block, "audioPath", None)
		if not path or not os.path.exists(path):
			# Translators: AI-Hub — audio capture and playback: brief status feedback (speech/braille), not a full dialog.
			self.message(_("No audio in this message"))
			return
		if self._audioPlayingPath == path:
			self._stopBlockAudio()
		else:
			self._playBlockAudio(path)

	def onAudioStop(self, evt):
		self._stopBlockAudio()

	def getDefaultAudioPrompt(self):
		# Translators: Text in audio processing status and error messages.
		return _("Transcribe and describe the content of this audio.")

	def ensureModelAudioSelected(self):
		model = self.getCurrentModel()
		if model and getattr(model, "audioInput", False):
			return
		audio_models = [m for m in self._models if getattr(m, "audioInput", False)]
		if not audio_models:
			return
		self._selectModelById(audio_models[0].id)

	def updateAudioList(self, focusPrompt=True):
		# Mirror updateFilesList: read/write the visible active page directly so
		# new attachments cannot land on the wrong page when a worker is running.
		page = self.get_active_page()
		audio_list = getattr(page, "audioPathList", None) or []
		audio_label = page.audioLabel
		audio_ctrl = page.audioListCtrl
		audio_ctrl.DeleteAllItems()
		if not audio_list:
			audio_label.Hide()
			audio_ctrl.Hide()
			self._sync_attachments_section_header()
			self._relayout_attachments(anchor=audio_ctrl)
			if focusPrompt:
				page.promptTextCtrl.SetFocus()
			return
		audio_label.Show()
		audio_ctrl.Show()
		self._sync_attachments_section_header()
		for path in audio_list:
			path_str = path if isinstance(path, str) else getattr(path, "path", str(path))
			name = os.path.basename(path_str) if path_str else "?"
			audio_ctrl.Append([name, path_str or ""])
		self._attachment_list_end_refresh(audio_ctrl, focus_prompt_if_empty=False)

	def onAddAudioFromFile(self, evt):
		dlg = wx.FileDialog(
			None,
			# Translators: Text in audio processing status and error messages.
			message=_("Select audio files"),
			defaultFile="",
			# Translators: Text in audio processing status and error messages.
			wildcard=_("Audio files (*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.wav;*.webm)|*.mp3;*.mp4;*.mpeg;*.mpga;*.m4a;*.wav;*.webm"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		paths = dlg.GetPaths()
		if not paths:
			return
		if not self.audioPathList:
			self.audioPathList = []
		for path in paths:
			stored = self.persistAudioPath(path)
			if stored not in self.audioPathList:
				self.audioPathList.append(stored)
		self.ensureModelAudioSelected()
		if not self.promptTextCtrl.GetValue().strip():
			self.promptTextCtrl.SetValue(self.getDefaultAudioPrompt())
		self.updateAudioList()
		# Translators: AI-Hub — audio capture and playback: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Audio file(s) added. Enter your prompt and submit."))

	def onAudioListContextMenu(self, evt):
		menu = wx.Menu()
		if self.audioPathList:
			if self.audioListCtrl.GetItemCount() > 0 and self.audioListCtrl.GetSelectedItemCount() > 0:
				item_id = wx.NewIdRef()
				# Translators: AI-Hub — audio capture and playback: entry in a context menu or submenu.
				menu.Append(item_id, _("&Remove selected") + " (Del)")
				self.Bind(wx.EVT_MENU, self.onRemoveSelectedAudio, id=item_id)
			item_id = wx.NewIdRef()
			# Translators: AI-Hub — audio capture and playback: entry in a context menu or submenu.
			menu.Append(item_id, _("Remove &all"))
			self.Bind(wx.EVT_MENU, self.onRemoveAllAudio, id=item_id)
			menu.AppendSeparator()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub — audio capture and playback: entry in a context menu or submenu.
		menu.Append(item_id, _("Add from &file path..."))
		self.Bind(wx.EVT_MENU, self.onAddAudioFromFile, id=item_id)
		self.PopupMenu(menu)
		menu.Destroy()

	def onAudioListKeyDown(self, evt):
		if evt.GetKeyCode() == wx.WXK_DELETE and self.audioPathList:
			self.onRemoveSelectedAudio(evt)
		else:
			evt.Skip()

	def onRemoveSelectedAudio(self, evt):
		if not self.audioPathList:
			return
		remove_idx = frozenset(self._list_ctrl_selected_indices(self.audioListCtrl))
		if not remove_idx:
			return
		self.audioPathList = [p for i, p in enumerate(self.audioPathList) if i not in remove_idx]
		self.updateAudioList()

	def onRemoveAllAudio(self, evt):
		self.audioPathList.clear()
		self.updateAudioList()
