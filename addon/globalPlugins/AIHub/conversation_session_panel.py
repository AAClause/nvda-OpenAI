"""Panel for one notebook page: system prompt, messages, prompt, and attachments."""

import wx

import addonHandler

from .consts import UI_SECTION_SPACING_PX

addonHandler.initTranslation()


class ConversationSessionPanelAccessible(wx.Accessible):
	"""Expose each notebook page as an accessible tab with the parent tab caption."""

	def __init__(self, window):
		super().__init__(window)

	def GetRole(self, childId):
		return (wx.ACC_OK, wx.ROLE_SYSTEM_PAGETAB)

	def GetName(self, childId):
		window = self.GetWindow()
		if not window:
			return (wx.ACC_OK, "")
		parent = window.GetParent()
		if isinstance(parent, wx.Notebook):
			index = parent.FindPage(window)
			if index != wx.NOT_FOUND:
				return (wx.ACC_OK, parent.GetPageText(index))
		# Keep a meaningful fallback if the panel is detached/reparented.
		label = window.GetLabel() if hasattr(window, "GetLabel") else ""
		return (wx.ACC_OK, label or "")


class ConversationSessionPanel(wx.Panel):
	"""One session tab page: system prompt, messages, prompt, file/audio lists, and worker slot."""

	def __init__(self, parent, host):
		super().__init__(parent)
		if hasattr(wx, "Accessible"):
			try:
				self.SetAccessible(ConversationSessionPanelAccessible(self))
			except Exception:
				pass
		self.host = host
		self.worker = None
		self.stopRequest = None
		self.firstBlock = None
		self.lastBlock = None
		# Per-tab "Files" attachment list (images + documents). The on-disk JSON
		# key is still ``pathList`` for backward compatibility, but the in-code
		# attribute uses the neutral ``filesList`` name.
		self.filesList = []
		self.audioPathList = []
		self._conversationId = None
		self._historyPath = None
		self.previousPrompt = None
		self.conversationModelHint = ""
		self.conversationAccountKey = ""
		self.conversationSystemText = ""
		self.conversationUiState = {}
		self.session_lazy_load = False
		# When True, this tab is not auto-saved, excluded from hub session, and any prior save is purged.
		self.ephemeral = False

		root = wx.BoxSizer(wx.VERTICAL)

		# Translators: Label for account selection list in a conversation tab.
		self.accountLabel = wx.StaticText(self, label=_("&Account:"))
		self.accountListCtrl = wx.ListBox(self, size=(700, 110))
		self.accountListCtrl.Bind(wx.EVT_LISTBOX, host.onAccountChange)
		root.Add(self.accountLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		root.Add(self.accountListCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		# Translators: Label for system prompt text field.
		self.systemPromptLabel = wx.StaticText(self, label=_("Sy&stem prompt:"))
		self.systemTextCtrl = wx.TextCtrl(self, size=(700, -1), style=wx.TE_MULTILINE)
		self.systemTextCtrl.Bind(wx.EVT_CONTEXT_MENU, host.onSystemContextMenu)
		self.systemTextCtrl.Bind(wx.EVT_TEXT, host._onSystemTextEdited)
		root.Add(self.systemPromptLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		root.Add(self.systemTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		exchange_col = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label for message history text area.
		self.messagesLabel = wx.StaticText(self, label=_("Me&ssages:"))
		exchange_col.Add(self.messagesLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		self.messagesTextCtrl = wx.TextCtrl(
			self,
			style=wx.TE_MULTILINE | wx.TE_READONLY,
			size=(700, -1),
		)
		exchange_col.Add(self.messagesTextCtrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		# Translators: Label for the user prompt input field.
		self.promptLabel = wx.StaticText(self, label=_("&Prompt:"))
		exchange_col.Add(self.promptLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		self.promptTextCtrl = wx.TextCtrl(
			self,
			size=(700, -1),
			style=wx.TE_MULTILINE,
		)
		exchange_col.Add(self.promptTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		root.Add(exchange_col, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)

		self.messagesTextCtrl.Bind(wx.EVT_CONTEXT_MENU, host.onHistoryContextMenu)
		self.messagesTextCtrl.Bind(wx.EVT_KEY_DOWN, host.onMessagesKeyDown)
		self.promptTextCtrl.Bind(wx.EVT_CONTEXT_MENU, host.onPromptContextMenu)
		self.promptTextCtrl.Bind(wx.EVT_KEY_DOWN, host.onPromptKeyDown)
		self.promptTextCtrl.Bind(wx.EVT_TEXT_PASTE, host.onPromptPasteSmart)

		# Attachments come right after the prompt (the things you've just typed
		# or pasted/dropped) and before the model picker, since they directly
		# affect which models are usable for the next submit.
		att_sz = wx.BoxSizer(wx.VERTICAL)
		# Translators: Section label for pending attachments in this tab.
		self.attachmentsSectionLabel = wx.StaticText(self, label=_("Attachments"))
		att_sz.Add(self.attachmentsSectionLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		# Translators: Label for attached files list (images/documents).
		self.filesLabel = wx.StaticText(
			self,
			# Translators: AI-Hub conversation tab (one notebook page): column title in a report-style list.
			label=_("&Files:"),
		)
		self.filesListCtrl = wx.ListCtrl(
			self,
			style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
			size=(700, 200),
		)
		# Translators: Column headers in attached files list.
		self.filesListCtrl.InsertColumn(0, _("name"))
		# Translators: AI-Hub conversation tab (one notebook page): read-only explanatory line next to controls.
		self.filesListCtrl.InsertColumn(1, _("path"))
		# Translators: AI-Hub conversation tab (one notebook page): read-only explanatory line next to controls.
		self.filesListCtrl.InsertColumn(2, _("size"))
		# Translators: AI-Hub conversation tab (one notebook page): read-only explanatory line next to controls.
		self.filesListCtrl.InsertColumn(3, _("Dimensions"))
		# Translators: AI-Hub conversation tab (one notebook page): read-only explanatory line next to controls.
		self.filesListCtrl.InsertColumn(4, _("description"))
		self.filesListCtrl.SetColumnWidth(0, 100)
		self.filesListCtrl.SetColumnWidth(1, 200)
		self.filesListCtrl.SetColumnWidth(2, 100)
		self.filesListCtrl.SetColumnWidth(3, 100)
		self.filesListCtrl.SetColumnWidth(4, 200)
		att_sz.Add(self.filesLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		att_sz.Add(self.filesListCtrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.filesListCtrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, host.onFilesListContextMenu)
		self.filesListCtrl.Bind(wx.EVT_KEY_DOWN, host.onFilesListKeyDown)
		self.filesListCtrl.Bind(wx.EVT_CONTEXT_MENU, host.onFilesListContextMenu)
		self.filesListCtrl.Bind(wx.EVT_RIGHT_UP, host.onFilesListContextMenu)

		# Translators: Label for attached audio files list.
		self.audioLabel = wx.StaticText(
			self,
			# Translators: AI-Hub conversation tab (one notebook page): column title in a report-style list.
			label=_("A&udio files:"),
		)
		self.audioListCtrl = wx.ListCtrl(
			self,
			style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
			size=(700, 120),
		)
		# Translators: Column headers in attached audio list.
		self.audioListCtrl.InsertColumn(0, _("File"))
		# Translators: AI-Hub conversation tab (one notebook page): read-only explanatory line next to controls.
		self.audioListCtrl.InsertColumn(1, _("Path"))
		self.audioListCtrl.SetColumnWidth(0, 150)
		self.audioListCtrl.SetColumnWidth(1, 450)
		att_sz.Add(self.audioLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		att_sz.Add(self.audioListCtrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)
		self.audioListCtrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, host.onAudioListContextMenu)
		self.audioListCtrl.Bind(wx.EVT_CONTEXT_MENU, host.onAudioListContextMenu)
		self.audioListCtrl.Bind(wx.EVT_KEY_DOWN, host.onAudioListKeyDown)

		self.attachmentsSectionLabel.Hide()
		self.filesLabel.Hide()
		self.filesListCtrl.Hide()
		self.audioLabel.Hide()
		self.audioListCtrl.Hide()

		root.Add(att_sz, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		# Translators: Label for model selection list in a conversation tab.
		self.modelsLabel = wx.StaticText(self, label=_("M&odel:"))
		self.modelsListCtrl = wx.ListBox(self, size=(700, 200))
		self.modelsListCtrl.Bind(wx.EVT_LISTBOX, host.onModelChange)
		self.modelsListCtrl.Bind(wx.EVT_KEY_DOWN, host.onModelKeyDown)
		self.modelsListCtrl.Bind(wx.EVT_CONTEXT_MENU, host.onModelContextMenu)
		self.modelsListCtrl.Bind(wx.EVT_RIGHT_UP, host.onModelContextMenu)
		root.Add(self.modelsLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, UI_SECTION_SPACING_PX)
		root.Add(self.modelsListCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_SECTION_SPACING_PX)

		self.SetSizer(root)
