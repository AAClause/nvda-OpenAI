"""History and message handlers for ConversationDialog."""
import datetime
import os
import re
import wx

import addonHandler
import api
import ui
from logHandler import log

from .history import TextSegment
from .image_file import AttachmentFile, AttachmentFileTypes, URL_PATTERN
from .propertiesutils import aggregate_blocks_usage, build_message_properties_html, format_token_usage_lines

addonHandler.initTranslation()


class HistoryHandlersMixin:
	def _getCurrentSegmentBlock(self):
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		if segment is None or not getattr(segment, "owner", None):
			return None, None
		return segment, segment.owner

	def _getCurrentBlock(self):
		segment, block = self._getCurrentSegmentBlock()
		return block

	def _segmentKind(self, block, segment):
		if segment in (block.segmentPromptLabel, block.segmentPrompt):
			return "prompt"
		if segment in (block.segmentResponseLabel, block.segmentResponse):
			return "response"
		if segment in (block.segmentReasoningLabel, block.segmentReasoning):
			return "reasoning"
		if segment == block.segmentBreakLine:
			return "break"
		return None

	def _getBlockTextByKind(self, block, kind):
		"""Return ``(label, text)`` for the requested kind on ``block``.

		``text`` is always read from the underlying HistoryBlock data
		(``block.prompt`` / ``block.responseText`` / ``block.reasoningText``),
		never from the rendered messagesTextCtrl segment, so callers get the
		canonical assistant/user text without UI artifacts (trailing newlines,
		``<think>`` wrappers, label prefixes, etc.). ``label`` is still derived
		from the rendered segment label because it's only used for speech
		announcements.
		"""
		if kind == "prompt":
			label = block.segmentPromptLabel.getText() if block.segmentPromptLabel else ""
			return label, (block.prompt or "")
		if kind == "response":
			label = block.segmentResponseLabel.getText() if block.segmentResponseLabel else ""
			return label, (block.responseText or "")
		if kind == "reasoning":
			label = block.segmentReasoningLabel.getText() if block.segmentReasoningLabel else ""
			return label, (getattr(block, "reasoningText", "") or "")
		return "", ""

	def _assistantClipboardPlainText(self, block):
		"""Assistant-side text for clipboard / system-prompt copy.

		Always uses raw object data; when ``_showThinkingInHistory`` is on we
		also prepend the reasoning so the clipboard mirrors what the user
		hears in the history pane (reasoning before the visible answer).
		"""
		response_text = block.responseText or ""
		if not getattr(self, "_showThinkingInHistory", False):
			return response_text
		reasoning = (getattr(block, "reasoningText", "") or "").strip()
		if not reasoning:
			return response_text
		return f"{self._formatThinkingForHistory(block.reasoningText)}{response_text}"

	def _formatTokenUsage(self, usage: dict) -> list:
		return format_token_usage_lines(usage, include_unavailable=True)

	def onMessageProperties(self, evt=None):
		block = self._getCurrentBlock()
		if not block:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			ui.message(_("No message selected."))
			return
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		html = build_message_properties_html(block, _("unknown"))
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		ui.browseableMessage(html, _("Message properties"), True)

	def onConversationProperties(self, evt=None):
		blocks = []
		b = self.firstBlock
		while b:
			blocks.append(b)
			b = b.next
		if not blocks:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			ui.message(_("No conversation messages yet."))
			return
		# Translators: Placeholder model name in the «Conversation properties» token summary when a message block has no model id.
		agg = aggregate_blocks_usage(blocks, _("unknown"))
		lines = [
			# Translators: Text in history navigation and context-menu messages.
			_("Conversation properties"),
			"",
			# Translators: Text in history navigation and context-menu messages.
			_("Messages: %d") % len(blocks),
			# Translators: Text in history navigation and context-menu messages.
			_("Total input tokens: %d") % agg["total_input"],
			# Translators: Text in history navigation and context-menu messages.
			_("Total output tokens: %d") % agg["total_output"],
			# Translators: Text in history navigation and context-menu messages.
			_("Total tokens: %d") % agg["total_tokens"],
		]
		if agg["total_reasoning"]:
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Total reasoning tokens: %d") % agg["total_reasoning"])
		if agg["total_cached"]:
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Total cached input tokens: %d") % agg["total_cached"])
		if agg["total_cache_write"]:
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Total cache write tokens: %d") % agg["total_cache_write"])
		if agg["total_input_audio"]:
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Total input audio tokens: %d") % agg["total_input_audio"])
		if agg["total_output_audio"]:
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Total output audio tokens: %d") % agg["total_output_audio"])
		if agg["has_cost"]:
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Total cost: $%.6f") % agg["total_cost"])
		lines.append("")
		# Translators: Text in history navigation and context-menu messages.
		lines.append(_("Models used:"))
		for model_name, count in sorted(agg["model_counts"].items(), key=lambda x: x[1], reverse=True):
			lines.append(f"- {model_name}: {count}")
		# Translators: Text in history navigation and context-menu messages.
		ui.browseableMessage("\n".join(lines), _("Conversation properties"), False)

	def onMessagesKeyDown(self, evt):
		"""Handle j/k for prev/next message; TextCtrl consumes single keys before AcceleratorTable."""
		key = evt.GetKeyCode()
		if key == ord("j") or key == ord("J"):
			self.onPreviousMessage(evt)
			return
		if key == ord("k") or key == ord("K"):
			self.onNextMessage(evt)
			return
		if key == ord("b") or key == ord("B"):
			if evt.ShiftDown():
				self.onMoveToStartOfThinking(evt)
			else:
				self.onMoveToBeginOfContent(evt)
			return
		if key == ord("n") or key == ord("N"):
			if evt.ShiftDown():
				self.onMoveToEndOfThinking(evt)
			else:
				self.onMoveToEndOfContent(evt)
			return
		if key == ord("r") or key == ord("R"):
			self.onToggleThinkingInHistory(evt)
			return
		evt.Skip()

	def onPromptKeyDown(self, evt):
		key = evt.GetKeyCode()
		if evt.ControlDown() and (key == ord("V") or key == ord("v")):
			self.onPromptPasteSmart(evt)
			return
		evt.Skip()

	def _insertPromptText(self, text: str):
		if not text:
			return
		start, end = self.promptTextCtrl.GetSelection()
		if start != end:
			self.promptTextCtrl.Replace(start, end, text)
		else:
			self.promptTextCtrl.WriteText(text)

	def _attachFilesFromPaths(self, paths) -> bool:
		clean_paths = []
		for path in paths or []:
			if not isinstance(path, str):
				continue
			p = path.strip().strip('"')
			if p and os.path.exists(p):
				clean_paths.append(p)
		if not clean_paths:
			return False
		added_count = 0
		added_image = False
		rejected = []
		model = self.getCurrentModel() if hasattr(self, "getCurrentModel") else None
		provider = getattr(model, "provider", "") if model else ""
		for path in clean_paths:
			if self.fileExists(path):
				continue
			attachment = AttachmentFile(path)
			unsupported = self.getUnsupportedAttachments(provider=provider, filesList=[attachment])
			if unsupported:
				rejected.append(path)
				continue
			self.filesList.append(attachment)
			added_count += 1
			if attachment.type in (AttachmentFileTypes.IMAGE_LOCAL, AttachmentFileTypes.IMAGE_URL):
				added_image = True
		if rejected:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			ui.message(_("Some files were skipped because they are not supported by the selected provider."))
		if added_count <= 0:
			return False
		if added_image:
			self.ensureModelVisionSelected()
		self.updateFilesList()
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		ui.message(_("%d file(s) attached.") % added_count)
		return True

	def onPromptPasteSmart(self, evt=None):
		if not wx.TheClipboard.Open():
			if evt is not None and hasattr(evt, "Skip"):
				evt.Skip()
			return
		try:
			if wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_FILENAME)):
				file_data = wx.FileDataObject()
				if wx.TheClipboard.GetData(file_data):
					if self._attachFilesFromPaths(file_data.GetFilenames()):
						return
			if wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_TEXT)):
				text_data = wx.TextDataObject()
				if wx.TheClipboard.GetData(text_data):
					text = text_data.GetText()
					lines = [line.strip().strip('"') for line in text.splitlines() if line.strip()]
					if lines and all(os.path.exists(line) for line in lines):
						if self._attachFilesFromPaths(lines):
							return
					if len(lines) == 1 and re.match(URL_PATTERN, lines[0]):
						try:
							self.filesList = self.filesList or []
							url_file = AttachmentFile(lines[0])
							model = self.getCurrentModel() if hasattr(self, "getCurrentModel") else None
							provider = getattr(model, "provider", "") if model else ""
							if self.getUnsupportedAttachments(provider=provider, filesList=[url_file]):
								self._insertPromptText(text)
								return
							if not self.fileExists(lines[0]):
								self.filesList.append(url_file)
								if url_file.type in (AttachmentFileTypes.IMAGE_LOCAL, AttachmentFileTypes.IMAGE_URL):
									self.ensureModelVisionSelected()
								self.updateFilesList()
								# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
								ui.message(_("URL attached."))
								return
						except Exception as err:
							log.error(f"URL paste attach failed: {err}", exc_info=True)
					self._insertPromptText(text)
					return
		except Exception as err:
			log.error(f"Smart paste failed: {err}", exc_info=True)
		finally:
			wx.TheClipboard.Close()
		if evt is not None and hasattr(evt, "Skip"):
			evt.Skip()

	def onPreviousPrompt(self, event):
		value = self.previousPrompt
		if value:
			self.promptTextCtrl.SetValue(value)

	def onPreviousMessage(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		try:
			kind = self._segmentKind(block, segment)
			if kind == "prompt":
				prev = block.previous
				if prev is None:
					wx.Bell()
					return
				if prev.segmentResponse is None or prev.segmentResponseLabel is None:
					return
				start = prev.segmentResponseLabel.start
				label, text = self._getBlockTextByKind(prev, "response")
			elif kind in ("response", "break", "reasoning"):
				if block.segmentPrompt is None or block.segmentPromptLabel is None:
					return
				start = block.segmentPromptLabel.start
				label, text = self._getBlockTextByKind(block, "prompt")
			else:
				return
		except Exception as e:
			log.error(f"onPreviousMessage: {e}", exc_info=True)
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("An error occurred. More information is in the NVDA log."))
			return
		self.messagesTextCtrl.SetInsertionPoint(start)
		self.message(label + text)

	def onNextMessage(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		try:
			kind = self._segmentKind(block, segment)
			if kind in ("response", "break", "reasoning"):
				next = block.next
				if next is None:
					wx.Bell()
					return
				if next.segmentPrompt is None or next.segmentPromptLabel is None:
					return
				start = next.segmentPromptLabel.start
				label, text = self._getBlockTextByKind(next, "prompt")
			elif kind == "prompt":
				if block.segmentResponse is not None and block.segmentResponseLabel is not None:
					start = block.segmentResponseLabel.start
					label, text = self._getBlockTextByKind(block, "response")
				elif block.segmentReasoning is not None:
					start = block.segmentReasoning.start
					label, text = self._getBlockTextByKind(block, "reasoning")
				else:
					return
			else:
				return
		except Exception as e:
			log.error(f"onNextMessage: {e}", exc_info=True)
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("An error occurred. More information is in the NVDA log."))
			return
		self.messagesTextCtrl.SetInsertionPoint(start)
		self.message(label + text)

	def onCurrentMessage(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		try:
			kind = self._segmentKind(block, segment)
			if kind not in ("prompt", "response", "reasoning"):
				return
			label_text, text = self._getBlockTextByKind(block, kind)
		except Exception as e:
			log.error(f"onCurrentMessage: {e}", exc_info=True)
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("An error occurred. More information is in the NVDA log."))
			return
		self.message(text)

	def onMoveToEndOfThinking(self, evt=None):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		think_segment = getattr(block, "segmentReasoning", None)
		if think_segment is None:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("No thinking block in the current message."))
			return
		target = max(think_segment.start, think_segment.end - 1)
		self.messagesTextCtrl.SetInsertionPoint(target)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Moved to end of thinking block."))

	def onMoveToStartOfThinking(self, evt=None):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		think_segment = getattr(block, "segmentReasoning", None)
		if think_segment is None:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("No thinking block in the current message."))
			return
		self.messagesTextCtrl.SetInsertionPoint(think_segment.start)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Moved to start of thinking block."))

	def onMoveToBeginOfContent(self, evt=None):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		if segment in (block.segmentPromptLabel, block.segmentPrompt):
			target_segment = block.segmentPrompt
		else:
			target_segment = block.segmentResponse or block.segmentPrompt
		if target_segment is None:
			return
		self.messagesTextCtrl.SetInsertionPoint(target_segment.start)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Moved to beginning of content."))

	def onMoveToEndOfContent(self, evt=None):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		if segment in (block.segmentPromptLabel, block.segmentPrompt):
			target_segment = block.segmentPrompt
		else:
			target_segment = block.segmentResponse or block.segmentPrompt
		if target_segment is None:
			return
		target = max(target_segment.start, target_segment.end - 1)
		self.messagesTextCtrl.SetInsertionPoint(target)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Moved to end of content."))

	def onCopyResponseToSystem(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		label_text, text = self._getBlockTextByKind(block, "response")
		if not text:
			return
		self.systemTextCtrl.SetValue(text)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Response copied to system: %s") % text)

	def onCopyPromptToPrompt(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		label_text, text = self._getBlockTextByKind(block, "prompt")
		self.promptTextCtrl.SetValue(text)
		self.promptTextCtrl.SetFocus()
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Copied to prompt"))

	def onCopyMessage(self, evt, isHtml=False):
		from .conversation_dialog import copyToClipAsHTML, render_markdown_html
		text = self.messagesTextCtrl.GetStringSelection()
		# Translators: Text in history navigation and context-menu messages.
		msg = _("Copy")
		if not text:
			segment, block = self._getCurrentSegmentBlock()
			if segment is None:
				return
			kind = self._segmentKind(block, segment)
			if kind == "prompt":
				label_text, text = self._getBlockTextByKind(block, "prompt")
				# Translators: Text in history navigation and context-menu messages.
				msg = _("Copy prompt")
			else:
				text = self._assistantClipboardPlainText(block)
				has_reasoning = bool((getattr(block, "reasoningText", "") or "").strip())
				# Translators: Text in history navigation and context-menu messages.
				msg = _("Copy assistant message") if getattr(self, "_showThinkingInHistory", False) and has_reasoning else _("Copy response")
		if isHtml:
			text = render_markdown_html(text)
			copyToClipAsHTML(text)
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			msg += ' ' + _("as formatted HTML")
		else:
			api.copyToClip(text)
		self.message(msg)

	def onDeleteBlock(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		if block.segmentBreakLine is not None:
			block.segmentBreakLine.delete()
		if block.segmentPromptLabel is not None:
			block.segmentPromptLabel.delete()
		if block.segmentPrompt is not None:
			block.segmentPrompt.delete()
		if block.segmentResponseLabel is not None:
			block.segmentResponseLabel.delete()
		if block.segmentResponse is not None:
			block.segmentResponse.delete()
		if block.segmentReasoningLabel is not None:
			block.segmentReasoningLabel.delete()
		if block.segmentReasoning is not None:
			block.segmentReasoning.delete()
		if block.previous is not None:
			block.previous.next = block.next
		else:
			self.firstBlock = block.next
		if block.next is not None:
			block.next.previous = block.previous
		else:
			self.lastBlock = block.previous
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Block deleted"))

	def onWebviewMessage(self, evt, isHtml=False):
		from .conversation_dialog import render_markdown_html
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		try:
			kind = self._segmentKind(block, segment)
			if kind not in ("prompt", "response", "reasoning"):
				return
			label_text, text = self._getBlockTextByKind(block, kind)
		except Exception as e:
			log.error(f"onWebviewMessage: {e}", exc_info=True)
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("An error occurred. More information is in the NVDA log."))
			return
		html = render_markdown_html(text)
		ui.browseableMessage(
			html,
			title="OpenAI",
			isHtml=isHtml
		)

	def onSaveHistory(self, evt):
		path = None
		if self._historyPath and os.path.exists(self._historyPath):
			path = self._historyPath
		else:
			now = datetime.datetime.now()
			now_str = now.strftime("%Y-%m-%d_%H-%M-%S")
			defaultFile = "openai_history_%s.txt" % now_str
			dlg = wx.FileDialog(
				None,
				# Translators: Text in history navigation and context-menu messages.
				message=_("Save history"),
				defaultFile=defaultFile,
				# Translators: Text in history navigation and context-menu messages.
				wildcard=_("Text file") + " (*.txt)|*.txt",
				style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
			)
			if dlg.ShowModal() != wx.ID_OK:
				return
			path = dlg.GetPath()
		if not path:
			return
		self._historyPath = path
		with open(path, "w", encoding="utf-8") as f:
			f.write(self.messagesTextCtrl.GetValue())
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("History saved"))

	def onSystemContextMenu(self, event):
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a right-click or application context menu.
		resetItem = menu.Append(item_id, _("Reset to default"))
		self.Bind(wx.EVT_MENU, self.onResetSystemPrompt, id=item_id)
		menu.AppendSeparator()
		self.addStandardMenuOptions(menu)
		self.systemTextCtrl.PopupMenu(menu)
		menu.Destroy()

	def onHistoryContextMenu(self, evt):
		menu = wx.Menu()
		segment = TextSegment.getCurrentSegment(self.messagesTextCtrl)
		has_audio = False
		if segment and segment.owner:
			b = segment.owner
			if segment == b.segmentResponseLabel or segment == b.segmentResponse:
				has_audio = getattr(b, "audioPath", None) and os.path.exists(b.audioPath)
		if has_audio:
			item_id = wx.NewIdRef()
			# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
			menu.Append(item_id, _("&Play / Pause audio") + " (Ctrl+P)")
			self.Bind(wx.EVT_MENU, self.onAudioPlayPause, id=item_id)
			item_id = wx.NewIdRef()
			# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
			menu.Append(item_id, _("S&top audio"))
			self.Bind(wx.EVT_MENU, self.onAudioStop, id=item_id)
			menu.AppendSeparator()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Show message in web view as formatted HTML") + " (Space)")
		self.Bind(wx.EVT_MENU, lambda e: self.onWebviewMessage(e, True), id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Show message in web view as HTML source") + " (Shift+Space)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onWebviewMessage(evt, False), id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Copy message as plain text") + " (Ctrl+C)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onCopyMessage(evt, False), id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Copy message as formatted HTML") + " (Ctrl+Shift+C)")
		self.Bind(wx.EVT_MENU, lambda evt: self.onCopyMessage(evt, True), id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		toggle_label = _("Hide thinking in history") if getattr(self, "_showThinkingInHistory", True) else _("Show thinking in history")
		menu.Append(item_id, toggle_label + " (R)")
		self.Bind(wx.EVT_MENU, self.onToggleThinkingInHistory, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Copy response to system") + " (Alt+Left)")
		self.Bind(wx.EVT_MENU, self.onCopyResponseToSystem, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Copy prompt to prompt") + " (Alt+Right)")
		self.Bind(wx.EVT_MENU, self.onCopyPromptToPrompt, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Delete block") + " (Ctrl+D)")
		self.Bind(wx.EVT_MENU, self.onDeleteBlock, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Save history as text file") + " (Ctrl+Shift+S)")
		self.Bind(wx.EVT_MENU, self.onSaveHistory, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		save_item = menu.Append(item_id, _("Save conversation"))
		self.Bind(wx.EVT_MENU, self._onManualSaveRequested, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		rename_item = menu.Append(item_id, _("Rename conversation"))
		self.Bind(wx.EVT_MENU, self._renameConversation, id=item_id)
		if getattr(self.get_active_page(), "ephemeral", False):
			save_item.Enable(False)
			rename_item.Enable(False)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Message properties") + " (Alt+Enter)")
		self.Bind(wx.EVT_MENU, self.onMessageProperties, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Conversation properties") + " (Ctrl+Alt+Enter)")
		self.Bind(wx.EVT_MENU, self.onConversationProperties, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Say message") + " (M)")
		self.Bind(wx.EVT_MENU, self.onCurrentMessage, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Move to previous message") + " (j)")
		self.Bind(wx.EVT_MENU, self.onPreviousMessage, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a right-click or application context menu.
		menu.Append(item_id, _("Move to next message") + " (k)")
		self.Bind(wx.EVT_MENU, self.onNextMessage, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a right-click or application context menu.
		menu.Append(item_id, _("Move to start of thinking block") + " (Shift+B)")
		self.Bind(wx.EVT_MENU, self.onMoveToStartOfThinking, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a right-click or application context menu.
		menu.Append(item_id, _("Move to end of thinking block") + " (Shift+N)")
		self.Bind(wx.EVT_MENU, self.onMoveToEndOfThinking, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a right-click or application context menu.
		menu.Append(item_id, _("Move to beginning of content") + " (B)")
		self.Bind(wx.EVT_MENU, self.onMoveToBeginOfContent, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a right-click or application context menu.
		menu.Append(item_id, _("Move to end of content") + " (N)")
		self.Bind(wx.EVT_MENU, self.onMoveToEndOfContent, id=item_id)
		menu.AppendSeparator()
		self.addStandardMenuOptions(menu)
		self.messagesTextCtrl.PopupMenu(menu)
		menu.Destroy()

	def onPromptContextMenu(self, evt):
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Dictate") + " (Ctrl+R)")
		self.Bind(wx.EVT_MENU, self.onRecord, id=item_id)
		menu.AppendSeparator()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Attach image from f&ile...") + " (Ctrl+I)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromFilePath, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Attach image from &URL...") + " (Ctrl+U)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromURL, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Attach image from &screenshot") + " (Ctrl+E)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromScreenshot, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Attach audio from f&ile..."))
		self.Bind(wx.EVT_MENU, self.onAddAudioFromFile, id=item_id)
		menu.AppendSeparator()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
		menu.Append(item_id, _("Paste (file or text)") + "\tCtrl+V")
		self.Bind(wx.EVT_MENU, self.onPromptPasteSmart, id=item_id)
		if self.previousPrompt:
			menu.AppendSeparator()
			item_id = wx.NewIdRef()
			# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
			menu.Append(item_id, _("Insert previous prompt") + " (Ctrl+Up)")
			self.Bind(wx.EVT_MENU, self.onPreviousPrompt, id=item_id)
		menu.AppendSeparator()
		self.addStandardMenuOptions(menu, include_paste=False)
		self.promptTextCtrl.PopupMenu(menu)
		menu.Destroy()
