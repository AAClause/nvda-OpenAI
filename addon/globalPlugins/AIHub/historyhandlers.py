"""History and message handlers for ConversationDialog."""
import datetime
import os
import re
import time
import wx

import addonHandler
import api
import ui
from logHandler import log

from .history import TextSegment, get_textctrl_selected_text, update_textctrl_saved_selection
from .image_file import AttachmentFile, AttachmentFileTypes, URL_PATTERN
from .propertiesutils import aggregate_blocks_usage, build_message_properties_html
from .usage_ledger import build_conversation_usage_lines
from .detached_branch import (
	clear_detached_branch,
	detach_tail_for_regenerate,
	detached_branch_summary,
	has_detached_branch,
	restore_detached_branch,
)

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
		page = self._conversation_scope()
		ledger = getattr(page, "usageLedger", None)
		if not isinstance(ledger, list):
			ledger = []
		lines = [
			# Translators: Text in history navigation and context-menu messages.
			_("Conversation properties"),
			"",
		]
		lines.extend(
			build_conversation_usage_lines(
				blocks=blocks,
				ledger=ledger,
				unknown_model_label=_("unknown"),
				message_count=len(blocks),
			)
		)
		session_models = {}
		for entry in ledger:
			if not isinstance(entry, dict):
				continue
			model_name = entry.get("model") or _("unknown")
			session_models[model_name] = session_models.get(model_name, 0) + 1
		if session_models:
			lines.append("")
			# Translators: Text in history navigation and context-menu messages.
			lines.append(_("Models used (session):"))
			for model_name, count in sorted(session_models.items(), key=lambda x: x[1], reverse=True):
				lines.append(f"- {model_name}: {count}")
		elif blocks:
			agg = aggregate_blocks_usage(blocks, _("unknown"))
			if agg.get("model_counts"):
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

	def _setMessagesInsertionPoint(self, pos: int) -> None:
		self.messagesTextCtrl.SetInsertionPoint(pos)
		update_textctrl_saved_selection(self.messagesTextCtrl)

	def _contentSegmentForKind(self, block, kind):
		if kind == "prompt":
			return block.segmentPrompt
		if kind == "response":
			return block.segmentResponse
		if kind == "reasoning":
			return block.segmentReasoning
		return None

	def _isAtContentStart(self, block, kind) -> bool:
		content = self._contentSegmentForKind(block, kind)
		if content is None:
			return True
		return self.messagesTextCtrl.GetInsertionPoint() <= content.start

	def onPreviousMessage(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		try:
			kind = self._segmentKind(block, segment)
			if kind not in ("prompt", "response", "reasoning"):
				return
			if not self._isAtContentStart(block, kind):
				content = self._contentSegmentForKind(block, kind)
				if content is not None:
					self._setMessagesInsertionPoint(content.start)
					label, text = self._getBlockTextByKind(block, kind)
					self.message(label + text, speechOnly=True)
					return
			if kind == "prompt":
				prev = block.previous
				if prev is None:
					wx.Bell()
					return
				if prev.segmentResponse is None or prev.segmentResponseLabel is None:
					return
				start = prev.segmentResponseLabel.start
				label, text = self._getBlockTextByKind(prev, "response")
			elif kind in ("response", "reasoning"):
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
		self._setMessagesInsertionPoint(start)
		self.message(label + text, speechOnly=True)

	def onNextMessage(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		try:
			kind = self._segmentKind(block, segment)
			if kind in ("response", "reasoning"):
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
		self._setMessagesInsertionPoint(start)
		self.message(label + text, speechOnly=True)

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
		self._setMessagesInsertionPoint(target)
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
		self._setMessagesInsertionPoint(think_segment.start)
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
		self._setMessagesInsertionPoint(target_segment.start)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Moved to beginning of content."), speechOnly=True)

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
		self._setMessagesInsertionPoint(target)
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Moved to end of content."), speechOnly=True)

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

	def _syncMessagesSelectionCache(self, evt=None):
		update_textctrl_saved_selection(self.messagesTextCtrl)
		if evt is not None:
			evt.Skip()

	def onCopyMessage(self, evt, isHtml=False):
		from .conversation_dialog import copyToClipAsHTML, render_markdown_html
		text = get_textctrl_selected_text(self.messagesTextCtrl)
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

	def _block_has_submittable_content(self, block):
		if not block:
			return False
		if (getattr(block, "prompt", "") or "").strip():
			return True
		if getattr(block, "filesList", None):
			return True
		if getattr(block, "audioPathList", None):
			return True
		tlist = getattr(block, "audioTranscriptList", None)
		return bool(tlist and any(t for t in tlist))

	def _detachBlocksAfterForRegenerate(self, block):
		"""Archive the focused block's prior response and later turns for optional restore."""
		page = self._conversation_scope()
		detach_tail_for_regenerate(page, block)

	def _announceDetachedBranchIfAny(self):
		page = self._conversation_scope()
		branch = getattr(page, "detachedBranch", None)
		if not isinstance(branch, dict):
			return
		tail_count, had_response = detached_branch_summary(branch)
		if tail_count:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(
				_("Previous branch archived (%d later messages). Use Restore previous branch to undo.") % tail_count
			)
		elif had_response:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("Previous response archived. Use Restore previous branch to undo."))

	def _resetBlockForRegenerate(self, block):
		"""Clear assistant output on ``block`` so a new response can stream in."""
		block.responseText = ""
		block.reasoningText = ""
		block.responseTerminated = False
		block.displayHeader = True
		block.lastLen = 0
		block.lastReasoningLen = 0
		block.usage = None
		block.timing = {"startedAt": time.time()}
		block.segmentBreakLine = None
		block.segmentPromptLabel = None
		block.segmentPrompt = None
		block.segmentResponseLabel = None
		block.segmentResponse = None
		block.segmentReasoningLabel = None
		block.segmentReasoning = None
		block.segmentReasoningSuffix = None

	def onRegenerateBlock(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None or block is None:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("No message selected."))
			return
		if self.worker:
			return
		if not self._block_has_submittable_content(block):
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("This message has no prompt or attachments to regenerate."))
			return
		page = self._conversation_scope()
		page._regenerateBlock = block
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Regenerating response..."))
		self._onSubmitImpl(evt)

	def onRestoreDetachedBranch(self, evt=None):
		if self.worker:
			return
		page = self._conversation_scope()
		if not has_detached_branch(page):
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("No archived branch to restore."))
			return
		anchor, new_last = restore_detached_branch(page, self.firstBlock, self.lastBlock)
		if anchor is None:
			# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
			self.message(_("Archived branch is no longer available."))
			return
		self.lastBlock = new_last
		self._rerenderMessages(anchor_block=anchor, anchor_part="response")
		# Translators: AI-Hub conversation — message history area: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Previous branch restored."))
		if hasattr(self, "_autoSaveConversation"):
			self._autoSaveConversation()

	def onDeleteBlock(self, evt):
		segment, block = self._getCurrentSegmentBlock()
		if segment is None:
			return
		page = self._conversation_scope()
		branch = getattr(page, "detachedBranch", None)
		if isinstance(branch, dict):
			anchor_uid = branch.get("anchorBlockId")
			tail = branch.get("tailFirstBlock")
			if getattr(block, "uid", None) == anchor_uid or block is tail:
				clear_detached_branch(page)
			else:
				while tail is not None:
					if tail is block:
						clear_detached_branch(page)
						break
					tail = tail.next
		if block.previous is not None:
			anchor_block = block.previous
			anchor_part = "response"
		elif block.next is not None:
			anchor_block = block.next
			anchor_part = "prompt"
		else:
			anchor_block = None
			anchor_part = "prompt"
		if block.previous is not None:
			block.previous.next = block.next
		else:
			self.firstBlock = block.next
		if block.next is not None:
			block.next.previous = block.previous
		else:
			self.lastBlock = block.previous
		# Rebuild the read-only history view from block data so removal stays
		# correct (wx stores \\n as CRLF) without touching streaming.
		self._rerenderMessages(anchor_block=anchor_block, anchor_part=anchor_part)
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
		regenerate_item = menu.Append(item_id, _("Regenerate response") + " (Ctrl+Shift+R)")
		self.Bind(wx.EVT_MENU, self.onRegenerateBlock, id=item_id)
		if self.worker:
			regenerate_item.Enable(False)
		page = self._conversation_scope()
		if has_detached_branch(page):
			item_id = wx.NewIdRef()
			branch = getattr(page, "detachedBranch", None)
			tail_count, _had_response = detached_branch_summary(branch)
			if tail_count:
				# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
				label = _("Restore previous branch (%d messages)") % tail_count
			else:
				# Translators: AI-Hub conversation — message history area: entry in a context menu or submenu.
				label = _("Restore previous branch")
			restore_item = menu.Append(item_id, label)
			self.Bind(wx.EVT_MENU, self.onRestoreDetachedBranch, id=item_id)
			if self.worker:
				restore_item.Enable(False)
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
