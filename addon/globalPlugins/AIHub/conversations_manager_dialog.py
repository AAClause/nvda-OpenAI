"""Conversation management dialog: list, open, rename, delete saved conversations."""
import datetime
import os
import re
import shlex

import addonHandler
import config
import gui
import wx
import ui

from . import conversations
from .conversations import ConversationFormat, normalize_conversation_format
from .list_dialog_utils import (
	bind_dialog_char_hook_space_opens_menu,
	listctrl_apply_context_menu_hit_selection,
	listctrl_menu_anchor_point,
)
from .tool_lyria_dialog import Lyria3ProToolDialog
from .tool_mistral_ocr_dialog import MistralOCRToolDialog
from .tool_mistral_transcription_dialog import MistralSpeechToTextToolDialog
from .tool_openai_transcription_dialog import OpenAITranscriptionToolDialog
from .tool_openai_tts_dialog import OpenAITTSToolDialog
from .toolsmenu import open_tool_dialog_by_class
from .tool_voxtral_tts_dialog import VoxtralTTSToolDialog

addonHandler.initTranslation()

def format_date(ts: int) -> str:
	if not ts:
		return ""
	try:
		dt = datetime.datetime.fromtimestamp(ts)
		return dt.strftime("%Y-%m-%d %H:%M")
	except Exception:
		return str(ts)


def format_size(size: int | None) -> str:
	if not isinstance(size, int) or size < 0:
		# Translators: Placeholder text when a file size cannot be determined.
		return _("unknown")
	units = ["B", "KB", "MB", "GB"]
	val = float(size)
	for unit in units:
		if val < 1024.0 or unit == units[-1]:
			if unit == "B":
				return f"{int(val)} {unit}"
			return f"{val:.1f} {unit}"
		val /= 1024.0
	return f"{size} B"


def _open_tool_dialog_from_conversation(plugin, dialog_cls, conversation_data):
	open_tool_dialog_by_class(
		None,
		dialog_cls,
		conversationData=conversation_data,
		plugin=plugin,
	)


class ConversationsManagerDialog(wx.Dialog):
	# Translators: Title of the conversation management dialog
	title = _("Conversation history")

	@staticmethod
	def _sort_menu_entries():
		# Translators: Labels for the "Sort by" submenu in conversation history (one label per sort order).
		return (
			("date_desc", _("Last modified (newest &first)")),
			("date_asc", _("Last modified (&oldest first)")),
			("name_asc", _("&Name (A to Z)")),
			("name_desc", _("Na&me (Z to A)")),
			("tokens_desc", _("&Tokens (highest first)")),
			("tokens_asc", _("To&kens (lowest first)")),
			("messages_desc", _("&Messages (most first)")),
			("messages_asc", _("Mes&sages (fewest first)")),
			("format_asc", _("&Format (A to Z)")),
			("format_desc", _("For&mat (Z to A)")),
		)

	def __init__(self, parent, plugin):
		super().__init__(parent, title=self.title)
		self._plugin = plugin
		self._all_entries = []
		self._entries = []
		main = wx.BoxSizer(wx.VERTICAL)
		main.Add(
			wx.StaticText(
				self,
				# Translators: Static label above the list of saved conversations in the Manage saved conversations dialog.
				label=_("Saved conversations:"),
			),
			0, wx.ALL, 5
		)
		filter_sz = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Label for the conversation-history filter input.
		self._filterLabel = wx.StaticText(self, label=_("&Filter (F1 for help):"))
		self._filterTextCtrl = wx.TextCtrl(self)
		self._filterTextCtrl.Bind(wx.EVT_TEXT, self.onFilterChanged)
		self._filterTextCtrl.Bind(wx.EVT_KEY_DOWN, self.onFilterKeyDown)
		filter_sz.Add(self._filterLabel, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
		filter_sz.Add(self._filterTextCtrl, 1, wx.ALL | wx.EXPAND, 5)
		main.Add(filter_sz, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 0)

		self._list = wx.ListCtrl(
			self,
			style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
			size=(600, 320)
		)
		# Translators: List column header: saved conversation display title in Manage saved conversations.
		self._list.InsertColumn(0, _("Name"), width=300)
		# Translators: List column header: stored conversation format id (e.g. generic, tool output).
		self._list.InsertColumn(1, _("Format"), width=180)
		# Translators: List column header: last modification timestamp of the conversation file.
		self._list.InsertColumn(2, _("Last modified"), width=140)
		# Translators: List column header: number of chat messages in the saved conversation.
		self._list.InsertColumn(3, _("Messages"), width=80)
		# Translators: List column header: total token count when usage metadata is available.
		self._list.InsertColumn(4, _("Tokens"), width=90)
		# Translators: List column header: primary model id used in that conversation when known.
		self._list.InsertColumn(5, _("Main model"), width=180)
		self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onOpen)
		self._list.Bind(wx.EVT_LIST_KEY_DOWN, self.onListKeyDown)
		self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onSelectionChanged)
		self._list.Bind(wx.EVT_CONTEXT_MENU, self.onListContextMenu)
		self._sort_mode = "date_desc"
		self.Bind(wx.EVT_MENU, self.onListContextMenuCommand)
		main.Add(self._list, 1, wx.EXPAND | wx.ALL, 5)
		self._propertiesText = wx.TextCtrl(
			self,
			style=wx.TE_MULTILINE | wx.TE_READONLY,
			size=(600, 120)
		)
		main.Add(
			wx.StaticText(
				self,
				# Translators: Static label above the read-only summary of the currently selected saved conversation.
				label=_("Selected conversation properties:"),
			),
			0, wx.LEFT | wx.RIGHT | wx.TOP, 5
		)
		main.Add(self._propertiesText, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that removes every saved conversation that has no messages and no draft text.
		delete_empty_btn = wx.Button(self, label=_("Delete &empty"))
		delete_empty_btn.Bind(wx.EVT_BUTTON, self.onDeleteEmpty)
		# Translators: Button that deletes every conversation in the list after confirmation.
		delete_all_btn = wx.Button(self, label=_("Delete a&ll"))
		delete_all_btn.Bind(wx.EVT_BUTTON, self.onDeleteAll)
		# Translators: Button that closes the Manage saved conversations dialog without opening a chat.
		close_btn = wx.Button(self, id=wx.ID_CLOSE)
		close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
		for b in (delete_empty_btn, delete_all_btn, close_btn):
			btn_sizer.Add(b, 0, wx.ALL, 5)
		main.Add(btn_sizer, 0, wx.ALL, 5)
		self.SetSizerAndFit(main)
		self.CentreOnParent(wx.BOTH)
		self.SetEscapeId(wx.ID_CLOSE)
		bind_dialog_char_hook_space_opens_menu(
			self,
			self._list,
			lambda: self._show_list_context_menu(listctrl_menu_anchor_point(self._list)),
		)
		self.refresh_list()

	def refresh_list(self):
		self._all_entries = conversations.list_conversations()
		self._applyFilterAndRender()

	def _selected_entry_ids_from_list(self, entry_list):
		ids = []
		idx = self._list.GetFirstSelected()
		while idx != -1:
			if 0 <= idx < len(entry_list):
				cid = entry_list[idx].get("id")
				if cid is not None:
					ids.append(cid)
			idx = self._list.GetNextSelected(idx)
		return ids

	def _entry_summary(self, entry):
		"""Lightweight summary for list/filtering; falls back to full props for legacy entries."""
		summary = entry.get("summary")
		if isinstance(summary, dict) and "messages" in summary:
			return summary
		props = conversations.get_conversation_properties(entry.get("id")) or {}
		summary = conversations.summary_from_properties(props)
		entry["summary"] = summary
		return summary

	def _sort_entries(self):
		if len(self._entries) <= 1:
			return
		mode = self._sort_mode

		def _props(e):
			p = e.get("summary")
			return p if isinstance(p, dict) else {}

		if mode == "date_desc":
			self._entries.sort(key=lambda e: int(e.get("updated") or 0), reverse=True)
		elif mode == "date_asc":
			self._entries.sort(key=lambda e: int(e.get("updated") or 0))
		elif mode == "name_asc":
			self._entries.sort(key=lambda e: (e.get("name") or "").lower())
		elif mode == "name_desc":
			self._entries.sort(key=lambda e: (e.get("name") or "").lower(), reverse=True)
		elif mode == "tokens_desc":
			self._entries.sort(
				key=lambda e: int(_props(e).get("total_tokens", 0) or 0),
				reverse=True,
			)
		elif mode == "tokens_asc":
			self._entries.sort(key=lambda e: int(_props(e).get("total_tokens", 0) or 0))
		elif mode == "messages_desc":
			self._entries.sort(
				key=lambda e: int(_props(e).get("messages", 0) or 0),
				reverse=True,
			)
		elif mode == "messages_asc":
			self._entries.sort(key=lambda e: int(_props(e).get("messages", 0) or 0))
		elif mode == "format_asc":
			self._entries.sort(key=lambda e: str(e.get("format", "")).lower())
		elif mode == "format_desc":
			self._entries.sort(
				key=lambda e: str(e.get("format", "")).lower(),
				reverse=True,
			)

	def _set_sort_mode(self, mode):
		if self._sort_mode == mode:
			return
		self._sort_mode = mode
		self._applyFilterAndRender(focus_list=True)

	def _applyFilterAndRender(self, *, focus_list=True):
		old_entries = list(self._entries)
		prev_selected_ids = self._selected_entry_ids_from_list(old_entries)
		filter_text = self._filterTextCtrl.GetValue().strip() if hasattr(self, "_filterTextCtrl") else ""
		keyed_terms, plain_terms = self._parse_filter_query(filter_text)
		has_keyed = bool(keyed_terms)
		filtered = []
		for entry in list(self._all_entries):
			summary = self._entry_summary(entry)
			if self._entry_matches_filter(entry, summary, keyed_terms, plain_terms, has_keyed):
				filtered.append(entry)
		self._entries = filtered
		self._sort_entries()
		self._list.DeleteAllItems()
		for e in self._entries:
			summary = self._entry_summary(e)
			# Translators: Default conversation title when no name exists.
			name = e.get("name", _("Untitled conversation"))
			date_str = format_date(e.get("updated", 0))
			model_counts = summary.get("model_counts", {}) if isinstance(summary, dict) else {}
			# Translators: Placeholder model label when no model is known.
			main_model = _("unknown")
			if model_counts:
				main_model = max(model_counts.items(), key=lambda item: item[1])[0]
			conv_format = e.get("format", "generic")
			total_tokens_display = str(summary.get("total_tokens", 0))
			if not summary.get("has_usage"):
				total_tokens_display = "—"
			self._list.Append([
				name,
				str(conv_format),
				date_str,
				str(summary.get("messages", 0)),
				total_tokens_display,
				main_model,
			])
		if self._entries:
			focus_idx = 0
			if prev_selected_ids:
				want = frozenset(prev_selected_ids)
				for i, e in enumerate(self._entries):
					if e.get("id") in want:
						focus_idx = i
						break
			self._list.Select(focus_idx)
			self._list.SetItemState(focus_idx, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
			if focus_list:
				self._list.SetFocus()
			self._updatePropertiesPanel(self._entries[focus_idx])
		else:
			self._propertiesText.SetValue("")

	def onFilterChanged(self, evt):
		# Keep keyboard focus in the filter field while typing; still refresh list selection and properties below.
		self._applyFilterAndRender(focus_list=False)
		# Reassert focus after list rebuild — some wx builds shift focus when the ListCtrl is cleared/repopulated.
		wx.CallAfter(self._filterTextCtrl.SetFocus)
		evt.Skip()

	def onFilterKeyDown(self, evt):
		key = evt.GetKeyCode()
		if key == wx.WXK_F1:
			self.showFilterHelp()
			return
		if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			if not self._entries:
				wx.Bell()
				return
			idx = self._list.GetFirstSelected()
			if idx < 0 or idx >= len(self._entries):
				idx = 0
			self._list.Select(idx)
			self._list.SetItemState(idx, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
			self._list.SetFocus()
			self._updatePropertiesPanel(self._entries[idx])
			return
		evt.Skip()

	def showFilterHelp(self):
		operator_literals = "`=`, `>`, `<`, `>=`, `<=`"
		bool_literals = "`true`/`false`, `yes`/`no`, `1`/`0`"
		has_literals = "`draft`, `messages`, `usage`, `cost`, `files`"
		# Translators: Help text describing filter query syntax for conversation history.
		# Keep literal filter tokens (e.g. model:gpt-5, operators, true/false) unchanged.
		lines = [
			# Translators: Heading at the top of the F1 filter-help text in the Manage saved conversations dialog.
			_("Filter syntax"),
			"",
			# Translators: Help line explaining that several key:value filters can be combined with spaces in the history search box.
			_("You can combine key:value terms separated by spaces."),
			# Translators: Help line explaining that plain words (without key:) only match the saved conversation title.
			_("If no key:value term is present, search is done in the conversation title only."),
			"",
			# Translators: Label before the example filter queries in the F1 help text.
			_("Examples:"),
			"- quadratic equation",
			"- model:gpt-5 format:generic",
			"- name:meeting messages:>0",
			"- tokens:>=1000 has:usage",
			"- empty:true",
			"- has:draft",
			"",
			# Translators: Label before the list of allowed filter field names in the F1 help text.
			_("Supported keys:"),
			# Translators: Comma-separated list of filter keys as shown in F1 help; keep English key names so they match the search box.
			_("name/title, model, format/type, id, date/updated, messages/msg/msgs, tokens/token, draft/draftlen, empty, has"),
			"",
			# Translators: Help line listing comparison operators allowed for numeric filters; placeholder is backtick-wrapped operator list.
			_("Numeric operators: %s (for messages/tokens/draft).") % operator_literals,
			# Translators: Help line listing accepted true/false spellings for the empty: filter; placeholder is literal token list.
			_("Boolean values: %s (for empty).") % bool_literals,
			# Translators: Help line listing allowed values after has: in the filter box; placeholder is literal token list.
			_("has values: %s.") % has_literals,
		]
		ui.browseableMessage(
			"\n".join(lines),
			# Translators: Title of the read-only NVDA browseable window opened with F1 for the saved-conversation filter field.
			_("Filter help"),
			False,
		)

	def _parse_filter_query(self, query: str):
		if not query:
			return [], []
		try:
			tokens = shlex.split(query)
		except ValueError:
			tokens = query.split()
		keyed = []
		plain = []
		for token in tokens:
			if ":" in token:
				key, value = token.split(":", 1)
				key = key.strip().lower()
				value = value.strip()
				if key and value:
					keyed.append((key, value))
					continue
			if token.strip():
				plain.append(token.strip().lower())
		return keyed, plain

	def _match_numeric_expr(self, actual: int, expr: str) -> bool:
		m = re.match(r"^(<=|>=|=|<|>)?\s*(-?\d+)$", (expr or "").strip())
		if not m:
			return False
		op = m.group(1) or "="
		expected = int(m.group(2))
		if op == "=":
			return actual == expected
		if op == "<":
			return actual < expected
		if op == ">":
			return actual > expected
		if op == "<=":
			return actual <= expected
		return actual >= expected

	def _parse_bool(self, value: str):
		v = (value or "").strip().lower()
		if v in ("1", "true", "yes", "y", "on"):
			return True
		if v in ("0", "false", "no", "n", "off"):
			return False
		return None

	def _entry_matches_filter(self, entry, props, keyed_terms, plain_terms, has_keyed: bool) -> bool:
		name = (entry.get("name", "") or "").lower()
		conv_format = str(entry.get("format", "") or "").lower()
		model_counts = props.get("model_counts", {}) if isinstance(props, dict) else {}
		main_model = max(model_counts.items(), key=lambda item: item[1])[0] if model_counts else ""
		main_model = (main_model or "").lower()
		conv_id = (entry.get("id", "") or "").lower()
		date_text = format_date(entry.get("updated", 0)).lower()
		messages = int(props.get("messages", 0) or 0)
		total_tokens = int(props.get("total_tokens", 0) or 0)
		draft_len = int(props.get("draft_len", 0) or 0)
		is_empty = messages == 0 and draft_len == 0
		has_usage = bool(props.get("has_usage"))
		has_cost = bool(props.get("has_cost"))
		has_files = bool(props.get("has_files") or props.get("files")) if isinstance(props, dict) else False

		for key, value in keyed_terms:
			val = value.lower()
			if key in ("name", "title"):
				if val not in name:
					return False
			elif key in ("model",):
				if val not in main_model:
					return False
			elif key in ("format", "type"):
				if val not in conv_format:
					return False
			elif key in ("id",):
				if val not in conv_id:
					return False
			elif key in ("date", "updated"):
				if val not in date_text:
					return False
			elif key in ("messages", "msg", "msgs"):
				if not self._match_numeric_expr(messages, val):
					return False
			elif key in ("tokens", "token"):
				if not self._match_numeric_expr(total_tokens, val):
					return False
			elif key in ("draft", "draftlen"):
				if not self._match_numeric_expr(draft_len, val):
					return False
			elif key == "empty":
				b = self._parse_bool(val)
				if b is None or is_empty != b:
					return False
			elif key == "has":
				flag = val
				if flag == "draft" and draft_len <= 0:
					return False
				if flag in ("message", "messages") and messages <= 0:
					return False
				if flag == "usage" and not has_usage:
					return False
				if flag == "cost" and not has_cost:
					return False
				if flag == "files" and not has_files:
					return False
			else:
				# Unknown keys: treat as plain term for flexible matching.
				if val not in name and val not in conv_format and val not in main_model:
					return False

		if plain_terms:
			if has_keyed:
				haystack = f"{name} {main_model} {conv_format} {conv_id}"
			else:
				# No key:value terms -> search in title only.
				haystack = name
			for term in plain_terms:
				if term not in haystack:
					return False
		return True

	def _buildPropertiesLines(self, entry, props):
		props = props or {}
		# Translators: Property lines shown for one conversation in the history dialog.
		lines = [
			# Translators: First line of the read-only properties text for one saved conversation: display name (placeholder is title or «Untitled conversation»).
			_("Name: %s") % entry.get("name", _("Untitled conversation")),
			# Translators: Properties line: stored conversation format id (placeholder is format string, often «generic»).
			_("Format: %s") % entry.get("format", "generic"),
			# Translators: Properties line: number of chat messages saved in this conversation.
			_("Messages: %d") % int(props.get("messages", 0)),
			# Translators: Properties line: character count of the system prompt text.
			_("System prompt length: %d characters") % int(props.get("system_len", 0)),
			# Translators: Properties line: character count of the user’s draft prompt area when saved.
			_("Draft prompt length: %d characters") % int(props.get("draft_len", 0)),
		]
		if props.get("has_usage"):
			ledger_entries = int(props.get("ledger_entries", 0) or 0)
			if ledger_entries:
				# Translators: Property line: number of billable API calls recorded for this conversation.
				lines.append(_("API calls recorded: %d") % ledger_entries)
				lines.extend([
					"",
					# Translators: Section heading — cumulative API usage including deleted/regenerated turns.
					_("Session (all API calls)"),
					# Translators: Property line: billed input tokens summed across every API call.
					_("Billed input tokens: %d") % int(props.get("total_input", 0)),
					# Translators: Property line: billed output tokens summed across every API call.
					_("Billed output tokens: %d") % int(props.get("total_output", 0)),
					# Translators: Property line: billed input plus output tokens across every API call.
					_("Billed total tokens: %d") % int(props.get("total_tokens", 0)),
				])
				if props.get("has_cost"):
					# Translators: Property line: cumulative API dollar cost for all recorded calls.
					lines.append(_("API spend: $%.6f") % float(props.get("total_cost", 0.0)))
				thread_has_usage = int(props.get("thread_total_tokens", 0) or 0) > 0
				session_tokens = int(props.get("total_tokens", 0) or 0)
				thread_tokens = int(props.get("thread_total_tokens", 0) or 0)
				if thread_has_usage and thread_tokens != session_tokens:
					lines.extend([
						"",
						# Translators: Section heading — usage for messages still in the saved history.
						_("Active thread (remaining messages)"),
						# Translators: Property line: billed input tokens for remaining message blocks only.
						_("Billed input tokens: %d") % int(props.get("thread_total_input", 0)),
						# Translators: Property line: billed output tokens for remaining message blocks only.
						_("Billed output tokens: %d") % int(props.get("thread_total_output", 0)),
						# Translators: Property line: billed total tokens for remaining message blocks only.
						_("Billed total tokens: %d") % thread_tokens,
					])
					if props.get("thread_has_cost"):
						# Translators: Property line: API dollar cost attributable to remaining messages only.
						lines.append(_("API spend: $%.6f") % float(props.get("thread_total_cost", 0.0)))
			else:
				lines.extend([
					# Translators: Property line: sum of input tokens across all turns (shown only when usage was recorded).
					_("Billed input tokens: %d") % int(props.get("total_input", 0)),
					# Translators: Property line: sum of output tokens across all turns.
					_("Billed output tokens: %d") % int(props.get("total_output", 0)),
					# Translators: Property line: combined input plus output token total.
					_("Billed total tokens: %d") % int(props.get("total_tokens", 0)),
				])
				if props.get("has_cost"):
					# Translators: Property line: total estimated API dollar cost for one saved conversation.
					lines.append(_("API spend: $%.6f") % float(props.get("total_cost", 0.0)))
		else:
			# Translators: Property line in «Manage saved conversations» when the conversation file has no stored token totals.
			lines.append(_("Token usage: unavailable"))
		if int(props.get("total_reasoning", 0)):
			# Translators: Property line: aggregate reasoning-token count for one saved conversation.
			lines.append(_("Reasoning tokens: %d") % int(props.get("total_reasoning", 0)))
		if int(props.get("total_cached", 0)):
			# Translators: Property line: aggregate cached prompt-token count for one saved conversation.
			lines.append(_("Cached input tokens: %d") % int(props.get("total_cached", 0)))
		if int(props.get("total_cache_write", 0)):
			# Translators: Property line: aggregate cache-write token count for one saved conversation.
			lines.append(_("Cache write tokens: %d") % int(props.get("total_cache_write", 0)))
		if int(props.get("total_input_audio", 0)):
			# Translators: Property line: aggregate input-audio token count for one saved conversation.
			lines.append(_("Input audio tokens: %d") % int(props.get("total_input_audio", 0)))
		if int(props.get("total_output_audio", 0)):
			# Translators: Property line: aggregate output-audio token count for one saved conversation.
			lines.append(_("Output audio tokens: %d") % int(props.get("total_output_audio", 0)))
		model_counts = props.get("model_counts", {})
		if model_counts:
			# Translators: Section heading before the per-model message counts in the saved-conversation properties list.
			lines.extend(["", _("Models used:")])
			for model_name, count in sorted(model_counts.items(), key=lambda x: x[1], reverse=True):
				lines.append(f"- {model_name}: {count}")
		files = props.get("files", [])
		if isinstance(files, list) and files:
			# Translators: Section heading before listing attachment paths referenced in one saved conversation.
			lines.extend(["", _("Files (input/output):")])
			for item in files:
				if not isinstance(item, dict):
					continue
				role = item.get("role", "input")
				kind = item.get("kind", "file")
				path = item.get("path", "")
				# Translators: Placeholder when a file entry has no stored path.
				name = os.path.basename(path) if isinstance(path, str) and path else _("(no path)")
				size = format_size(item.get("size"))
				# Translators: One file line in the conversation properties list.
				lines.append(_("- [{role}] {name} ({kind}, {size})").format(**{
					"role": role,
					"name": name,
					"kind": kind,
					"size": size,
				}))
		return lines

	def _updatePropertiesPanel(self, entry):
		if not entry:
			self._propertiesText.SetValue("")
			return
		props = entry.get("properties")
		if not isinstance(props, dict):
			props = conversations.get_conversation_properties(entry.get("id")) or {}
			entry["properties"] = props
		self._propertiesText.SetValue("\n".join(self._buildPropertiesLines(entry, props)))

	def onSelectionChanged(self, evt):
		idx = evt.GetIndex()
		if 0 <= idx < len(self._entries):
			self._updatePropertiesPanel(self._entries[idx])
		evt.Skip()

	def onListContextMenu(self, evt):
		pos = evt.GetPosition()
		if pos == wx.DefaultPosition or pos.x < 0:
			client_pt = listctrl_menu_anchor_point(self._list)
		else:
			client_pt = pos
			hit, _flags = self._list.HitTest(client_pt)
			if listctrl_apply_context_menu_hit_selection(self._list, hit, len(self._entries)):
				self._updatePropertiesPanel(self._entries[hit])
		self._show_list_context_menu(client_pt)

	def _show_list_context_menu(self, client_pt):
		n = len(self._get_selected_entries())
		menu = wx.Menu()
		# wx auto-IDs from NewControlId() are released when the menu is destroyed; reusing them on a later
		# PopupMenu triggers wx assertions. Map wx.ID_ANY items to actions for this menu only.
		cmd_by_id = {}
		sort_mode_by_id = {}
		self._list_ctx_cmd_by_id = cmd_by_id
		self._list_ctx_sort_mode_by_id = sort_mode_by_id

		def add_cmd(label, action):
			item = menu.Append(wx.ID_ANY, label)
			cmd_by_id[item.GetId()] = action

		if n >= 2:
			# Translators: Conversation history context menu — opens every selected chat; Enter with focus on the list.
			add_cmd(_("Open &these %(count)d conversations (Enter)") % {"count": n}, "open")
			menu.AppendSeparator()
			# Translators: Conversation history context menu — deletes every selected chat; Del with focus on the list.
			add_cmd(_("&Delete %(count)d conversations (Del)") % {"count": n}, "delete")
			menu.AppendSeparator()
			# Translators: Conversation history list context menu — starts a new AI-Hub chat (no list shortcut).
			add_cmd(_("&New conversation"), "new")
		else:
			# Translators: Conversation history list context menu — opens selected chat; Enter opens when the list has focus.
			add_cmd(_("&Open (Enter)"), "open")
			# Translators: Conversation history list context menu — rename; F2 when the list has focus.
			add_cmd(_("&Rename (F2)"), "rename")
			# Translators: Conversation history list context menu — metadata window; Alt+Enter when the list has focus.
			add_cmd(_("&Properties (Alt+Enter)"), "props")
			menu.AppendSeparator()
			sort_menu = wx.Menu()
			checked_sort_id = None
			for mode, label in self._sort_menu_entries():
				item = sort_menu.AppendRadioItem(wx.ID_ANY, label)
				sort_mode_by_id[item.GetId()] = mode
				if mode == self._sort_mode:
					checked_sort_id = item.GetId()
			if checked_sort_id is not None:
				sort_menu.Check(checked_sort_id, True)
			# Translators: Conversation history list — parent item for the sort-order submenu.
			menu.AppendSubMenu(sort_menu, _("Sort &by"))
			menu.AppendSeparator()
			# Translators: Conversation history list context menu — delete; Del when the list has focus.
			add_cmd(_("&Delete (Del)"), "delete")
			menu.AppendSeparator()
			# Translators: Conversation history list context menu — starts a new AI-Hub chat (no list shortcut).
			add_cmd(_("&New conversation"), "new")
		self._list.PopupMenu(menu, client_pt.x, client_pt.y)
		self._list_ctx_cmd_by_id = None
		self._list_ctx_sort_mode_by_id = None
		menu.Destroy()

	def onListContextMenuCommand(self, evt):
		eid = evt.GetId()
		sort_map = getattr(self, "_list_ctx_sort_mode_by_id", None)
		if sort_map and eid in sort_map:
			self._set_sort_mode(sort_map[eid])
			return
		cmd_map = getattr(self, "_list_ctx_cmd_by_id", None)
		if not cmd_map:
			evt.Skip()
			return
		cmd = cmd_map.get(eid)
		if cmd == "open":
			self.onOpen(evt)
		elif cmd == "rename":
			self.onRename(evt)
		elif cmd == "props":
			self.onProperties(evt)
		elif cmd == "delete":
			self.onDelete(evt)
		elif cmd == "new":
			self.onNew(evt)
		else:
			evt.Skip()

	def _get_selected_entries(self):
		selected = []
		idx = self._list.GetFirstSelected()
		while idx != -1:
			if 0 <= idx < len(self._entries):
				selected.append(self._entries[idx])
			idx = self._list.GetNextSelected(idx)
		return selected

	def onListKeyDown(self, evt):
		key = evt.GetKeyCode()
		# EVT_LIST_KEY_DOWN delivers wx.ListEvent, which has no GetModifiers(); use live key state instead.
		alt_held = wx.GetKeyState(wx.WXK_ALT)
		if key == wx.WXK_DELETE:
			self.onDelete(evt)
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and alt_held:
			self.onProperties(evt)
		elif key in (wx.WXK_NUMPAD_ENTER, wx.WXK_RETURN):
			self.onOpen(evt)
		elif key == wx.WXK_F2:
			self.onRename(evt)
		else:
			evt.Skip()

	def onProperties(self, evt):
		selected = self._get_selected_entries()
		if not selected:
			wx.Bell()
			return
		if len(selected) > 1:
			# Translators: Message shown when properties require exactly one selected conversation.
			ui.message(_("Select only one conversation to show properties."))
			return
		entry = selected[0]
		props = entry.get("properties")
		if not isinstance(props, dict):
			props = conversations.get_conversation_properties(entry.get("id")) or {}
			entry["properties"] = props
		# Translators: First heading line inside the read-only properties text for one saved conversation (duplicated as window title below).
		lines = [_("Conversation properties"), ""] + self._buildPropertiesLines(entry, props)
		ui.browseableMessage(
			"\n".join(lines),
			# Translators: Title of NVDA’s read-only browseable window showing every line of metadata for the selected saved conversation.
			_("Conversation properties"),
			False,
		)

	def _open_selected_entries(self, selected_entries):
		"""Load selected conversations, close this dialog, then open each (generic chat or tool dialog)."""
		loaded = []
		for entry in selected_entries:
			data = conversations.load_conversation(entry.get("id"))
			if data:
				loaded.append(data)
		if not loaded:
			gui.messageBox(
				# Translators: Error shown when a selected conversation cannot be loaded from disk.
				_("Unable to load conversation."),
				self.title,
				wx.OK | wx.ICON_ERROR
			)
			return
		if len(loaded) < len(selected_entries):
			# Translators: Shown when opening several chats but at least one file failed to load.
			ui.message(_("Some conversations could not be loaded."))
		plugin = self._plugin
		client = plugin.getClient()
		conf = config.conf.get("AIHub", {})
		self.EndModal(wx.ID_OK)
		if not client or not conf:
			return
		tool_dialog_map = {
			ConversationFormat.TOOL_MISTRAL_OCR: MistralOCRToolDialog,
			ConversationFormat.TOOL_MISTRAL_SPEECH_TO_TEXT: MistralSpeechToTextToolDialog,
			ConversationFormat.TOOL_MISTRAL_VOXTRAL_TTS: VoxtralTTSToolDialog,
			ConversationFormat.TOOL_GOOGLE_LYRIA_PRO: Lyria3ProToolDialog,
			ConversationFormat.TOOL_OPENAI_TTS: OpenAITTSToolDialog,
			ConversationFormat.TOOL_OPENAI_TRANSCRIPTION: OpenAITranscriptionToolDialog,
		}
		for data in loaded:
			conv_format = normalize_conversation_format(data.get("format", ConversationFormat.GENERIC.value))
			if conv_format in tool_dialog_map:
				dialog_cls = tool_dialog_map[conv_format]
				wx.CallAfter(_open_tool_dialog_from_conversation, plugin, dialog_cls, data)
			else:
				wx.CallAfter(plugin._openMainDialog, None, data, False, True)

	def onOpen(self, evt):
		selected = self._get_selected_entries()
		if not selected:
			return
		self._open_selected_entries(selected)

	def onRename(self, evt):
		selected = self._get_selected_entries()
		if not selected:
			wx.Bell()
			return
		if len(selected) > 1:
			# Translators: Message shown when renaming requires exactly one selected conversation.
			ui.message(_("Select only one conversation to rename."))
			return
		entry = selected[0]
		current_name = entry.get("name", "")
		dlg = wx.TextEntryDialog(
			self,
			# Translators: Prompt in the rename conversation dialog.
			_("Enter new name for this conversation:"),
			# Translators: Title of the rename conversation dialog.
			_("Rename conversation"),
			value=current_name
		)
		if dlg.ShowModal() != wx.ID_OK:
			dlg.Destroy()
			return
		new_name = dlg.GetValue().strip()
		dlg.Destroy()
		if not new_name:
			return
		if conversations.rename_conversation(entry["id"], new_name):
			self.refresh_list()

	def onDelete(self, evt):
		selected = self._get_selected_entries()
		if not selected:
			wx.Bell()
			return
		count = len(selected)
		confirm_msg = (
			# Translators: Yes/No confirmation question before deleting exactly one saved conversation from the history list.
			_("Delete this conversation? This cannot be undone.")
			if count == 1
			# Translators: Yes/No confirmation question before deleting several selected conversations; placeholder is the count.
			else _("Delete %d conversations? This cannot be undone.") % count
		)
		self._confirm_and_delete_entries(
			selected,
			confirm_msg=confirm_msg,
			# Translators: Title of the Yes/No confirmation dialog for deleting one or more saved conversations.
			confirm_title=_("Delete conversation"),
		)

	def onDeleteAll(self, evt):
		if not self._entries:
			wx.Bell()
			return
		total = len(self._entries)
		self._confirm_and_delete_entries(
			list(self._entries),
			# Translators: Yes/No confirmation question before wiping the entire saved-conversation list; placeholder is how many will be removed.
			confirm_msg=_("Delete all %d conversations? This cannot be undone.") % total,
			# Translators: Title of the Yes/No confirmation dialog for deleting every saved conversation at once.
			confirm_title=_("Delete all conversations"),
			confirm_style=wx.YES_NO | wx.ICON_WARNING,
		)

	def _entry_is_empty_conversation(self, entry):
		summary = self._entry_summary(entry)
		return int(summary.get("messages", 0) or 0) == 0 and int(summary.get("draft_len", 0) or 0) == 0

	def _confirm_and_delete_entries(self, entries, *, confirm_msg, confirm_title, confirm_style=wx.YES_NO | wx.ICON_QUESTION):
		res = gui.messageBox(confirm_msg, confirm_title, confirm_style)
		if res != wx.YES:
			return
		deleted = 0
		deleted_ids = []
		for entry in list(entries):
			cid = entry["id"]
			if conversations.delete_conversation(cid):
				deleted_ids.append(cid)
				deleted += 1
		if deleted_ids:
			conversations.prune_hub_session_references(deleted_ids)
		self.refresh_list()
		ui.message(
			# Translators: Spoken status after deleting one or many conversations.
			_("Deleted %d conversation.") % deleted
			if deleted == 1
			# Translators: AI-Hub conversation history dialog: brief status feedback (speech/braille), not a full dialog.
			else _("Deleted %d conversations.") % deleted
		)

	def onDeleteEmpty(self, evt):
		if not self._entries:
			wx.Bell()
			return
		empty_entries = [e for e in self._entries if self._entry_is_empty_conversation(e)]
		if not empty_entries:
			# Translators: Spoken message when no empty conversations match delete-empty action.
			ui.message(_("No empty conversations to delete."))
			return
		count = len(empty_entries)
		self._confirm_and_delete_entries(
			empty_entries,
			# Translators: AI-Hub conversation history dialog: brief status feedback (speech/braille), not a full dialog.
			confirm_msg=_("Delete all %d empty conversations? This cannot be undone.") % count,
			# Translators: AI-Hub conversation history dialog: brief status feedback (speech/braille), not a full dialog.
			confirm_title=_("Delete empty conversations"),
			confirm_style=wx.YES_NO | wx.ICON_WARNING,
		)

	def onNew(self, evt):
		plugin = self._plugin
		client = plugin.getClient()
		conf = config.conf.get("AIHub", {})
		self.EndModal(wx.ID_OK)
		if not client or not conf:
			return
		wx.CallAfter(plugin._openMainDialog, None, None, False)


def show_conversations_manager(plugin):
	"""Show the conversation management dialog."""
	from . import __init__ as init_mod
	client = plugin.getClient()
	conf = config.conf.get("AIHub", {})
	if not client or not conf:
		# Translators: Fallback message when no API key is configured.
		ui.message(getattr(init_mod, "NO_AUTHENTICATION_KEY_PROVIDED_MSG", _("No API key provided")))
		return
	gui.mainFrame.prePopup()
	dlg = None
	try:
		dlg = ConversationsManagerDialog(gui.mainFrame, plugin)
		dlg.ShowModal()
	finally:
		try:
			if dlg is not None:
				dlg.Destroy()
		except Exception:
			pass
		gui.mainFrame.postPopup()
