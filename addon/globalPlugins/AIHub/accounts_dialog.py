"""Modal dialog for API account management (add / edit / remove)."""

import re

import addonHandler
import gui
import ui
import wx

from . import apikeymanager
from .consts import BASE_URLs, Provider
from .list_dialog_utils import (
	bind_dialog_char_hook_space_opens_menu,
	listctrl_apply_context_menu_hit_selection,
	listctrl_menu_anchor_point,
)
from .providertools_helpers import add_labeled_factory


# Providers whose configuration UI exposes the custom-base-URL field.
_USER_ENDPOINT_PROVIDERS = (Provider.CustomOpenAI, Provider.Ollama)

addonHandler.initTranslation()

class AccountDialog(wx.Dialog):
	def __init__(self, parent, title, account=None):
		super().__init__(parent, title=title)
		self.account = account or {}
		self._buildUI()
		self.CenterOnParent()
		self.SetSize((620, 470))

	def _buildUI(self):
		panel = wx.Panel(self)
		rootSizer = wx.BoxSizer(wx.VERTICAL)
		formPanel = wx.Panel(panel)
		formSizer = wx.BoxSizer(wx.VERTICAL)

		self.providerChoice = add_labeled_factory(
			formPanel,
			formSizer,
			# Translators: Label before the provider drop-down in the Add/Edit API account form.
			_("&Provider:"),
			lambda: wx.Choice(formPanel, choices=apikeymanager.AVAILABLE_PROVIDERS),
		)
		provider = self.account.get("provider", apikeymanager.AVAILABLE_PROVIDERS[0])
		self.providerChoice.SetSelection(
			apikeymanager.AVAILABLE_PROVIDERS.index(provider) if provider in apikeymanager.AVAILABLE_PROVIDERS else 0
		)
		self.nameText = add_labeled_factory(
			formPanel,
			formSizer,
			# Translators: Label before the optional friendly account name in the Add/Edit API account form.
			_("Account &name:"),
			lambda: wx.TextCtrl(formPanel, value=self.account.get("name", "")),
		)
		self.apiKeyText = add_labeled_factory(
			formPanel,
			formSizer,
			# Translators: Label before the secret API key field in the Add/Edit API account form.
			_("&API key:"),
			lambda: wx.TextCtrl(formPanel, value=self.account.get("api_key", "")),
		)
		self.customBaseUrlText = add_labeled_factory(
			formPanel,
			formSizer,
			# Translators: Label before the custom endpoint URL for Ollama or CustomOpenAI in the Add/Edit API account form.
			_("Custom base &URL:"),
			lambda: wx.TextCtrl(formPanel, value=self.account.get("base_url") or ""),
		)
		self.orgNameText = add_labeled_factory(
			formPanel,
			formSizer,
			# Translators: Label before the optional OpenAI organization display name in the Add/Edit API account form.
			_("Organization &name:"),
			lambda: wx.TextCtrl(formPanel, value=self.account.get("org_name", "")),
		)
		self.orgKeyText = add_labeled_factory(
			formPanel,
			formSizer,
			# Translators: Label for the optional OpenAI organization secret key field in the Add/Edit API account form.
			_("Organization &key:"),
			lambda: wx.TextCtrl(formPanel, value=self.account.get("org_key", "")),
		)
		self.providerChoice.Bind(wx.EVT_CHOICE, self.onProviderChoice)
		formPanel.SetSizer(formSizer)

		btnsizer = wx.StdDialogButtonSizer()
		btnOK = wx.Button(panel, id=wx.ID_OK)
		btnOK.SetDefault()
		btnsizer.AddButton(btnOK)
		btnsizer.AddButton(wx.Button(panel, id=wx.ID_CANCEL))
		btnsizer.Realize()

		rootSizer.Add(formPanel, proportion=1, flag=wx.ALL | wx.EXPAND, border=10)
		rootSizer.Add(btnsizer, flag=wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, border=10)
		panel.SetSizer(rootSizer)
		self.onProviderChoice(None)
		self.providerChoice.SetFocus()

	def onProviderChoice(self, evt):
		provider = self.providerChoice.GetStringSelection()
		uses_custom_url = provider in _USER_ENDPOINT_PROVIDERS
		self.customBaseUrlText.Enable(uses_custom_url)
		self.orgNameText.Enable(not uses_custom_url)
		self.orgKeyText.Enable(not uses_custom_url)
		if provider == Provider.Ollama and not self.customBaseUrlText.GetValue().strip():
			self.customBaseUrlText.SetValue(BASE_URLs.get(Provider.Ollama, "http://127.0.0.1:11434/v1"))

	def _normalize_custom_base_url(self, value: str) -> str:
		url = (value or "").strip()
		if not url:
			return ""
		if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", url):
			url = f"http://{url}"
		return url.rstrip("/")

	def getData(self):
		provider = self.providerChoice.GetStringSelection()
		if provider in _USER_ENDPOINT_PROVIDERS:
			base_url = self._normalize_custom_base_url(self.customBaseUrlText.GetValue())
			if provider == Provider.Ollama and not base_url:
				base_url = BASE_URLs.get(Provider.Ollama, "http://127.0.0.1:11434/v1")
		else:
			base_url = ""
		return {
			"provider": provider,
			"name": self.nameText.GetValue().strip(),
			"api_key": self.apiKeyText.GetValue().strip(),
			"base_url": base_url,
			"org_name": self.orgNameText.GetValue().strip(),
			"org_key": self.orgKeyText.GetValue().strip(),
		}


class AccountsManagementDialog(wx.Dialog):
	def __init__(self, parent):
		# Translators: Window title of the modal dialog that lists every AI-Hub API account (NVDA → AI-Hub → API accounts).
		super().__init__(parent, title=_("API accounts"))
		self._account_entries = []
		self._build_ui()
		self.CenterOnParent()
		self.SetSize((560, 440))
		self.SetEscapeId(wx.ID_CLOSE)
		bind_dialog_char_hook_space_opens_menu(
			self,
			self.accounts_list,
			lambda: self._show_accounts_context_menu(listctrl_menu_anchor_point(self.accounts_list)),
		)
		self._refresh_list()

	def _build_ui(self):
		panel = wx.Panel(self)
		root = wx.BoxSizer(wx.VERTICAL)
		hint = wx.StaticText(
			panel,
			# Translators: Static help sentence above the account list in the API accounts management dialog.
			label=_("Stored provider accounts. Space selects the focused row or opens the menu if it is already selected; Enter edits."),
		)
		root.Add(hint, 0, wx.ALL, 10)
		self.accounts_list = wx.ListCtrl(
			panel,
			style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
			size=(520, 240),
		)
		# Translators: Column header for the combined provider / account list in API accounts management.
		self.accounts_list.InsertColumn(0, _("Account"), width=500)
		self.accounts_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
		self.accounts_list.Bind(wx.EVT_LIST_KEY_DOWN, self.on_accounts_list_key_down)
		self.accounts_list.Bind(wx.EVT_CONTEXT_MENU, self.on_accounts_context_menu)
		self.Bind(wx.EVT_MENU, self.on_accounts_menu_command)
		root.Add(self.accounts_list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
		btn_row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button in API accounts management — opens the form to register a new provider account.
		self.add_btn = wx.Button(panel, label=_("&Add..."))
		btn_row.Add(self.add_btn, 0, wx.RIGHT, 8)
		root.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
		self.add_btn.Bind(wx.EVT_BUTTON, self.on_add)
		btns = wx.StdDialogButtonSizer()
		# Translators: Button that closes the API accounts management dialog without further changes.
		close_btn = wx.Button(panel, id=wx.ID_CLOSE)
		btns.AddButton(close_btn)
		btns.Realize()
		root.Add(btns, 0, wx.ALIGN_CENTER | wx.BOTTOM, 12)
		panel.SetSizer(root)
		close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))

	def _refresh_list(self, select_key=None):
		prev_selected_keys = []
		if getattr(self, "accounts_list", None) and self.accounts_list.GetItemCount() > 0 and self._account_entries:
			idx = self.accounts_list.GetFirstSelected()
			while idx != -1:
				if 0 <= idx < len(self._account_entries):
					prev_selected_keys.append(self._account_entries[idx]["key"])
				idx = self.accounts_list.GetNextSelected(idx)

		self._account_entries = []
		self.accounts_list.DeleteAllItems()
		for provider in apikeymanager.AVAILABLE_PROVIDERS:
			manager = apikeymanager.get(provider)
			active_id = manager.get_active_account_id()
			for acc in manager.list_accounts(include_env=True):
				entry = {
					"provider": provider,
					"id": acc["id"],
					# Translators: Fallback display name in the API accounts list when the stored account has no custom name.
					"name": acc.get("name") or _("Account"),
					"api_key": acc.get("api_key", ""),
					"base_url": acc.get("base_url") or "",
					"org_name": acc.get("org_name", ""),
					"org_key": acc.get("org_key", ""),
				}
				entry["key"] = f"{provider}/{entry['id']}"
				label = f"{provider} / {entry['name']}"
				if provider in _USER_ENDPOINT_PROVIDERS and entry["base_url"]:
					label = f"{label} - {entry['base_url']}"
				if entry["id"] == active_id:
					# Translators: Suffix in the combined provider/account list marking the account that is currently active for that provider.
					label = f"{label} ({_('default')})"
				self._account_entries.append(entry)
				self.accounts_list.Append([label])
		if not self._account_entries:
			return

		target_keys = []
		if select_key:
			target_keys = [select_key]
		elif prev_selected_keys:
			target_keys = prev_selected_keys
		else:
			for i, entry in enumerate(self._account_entries):
				manager = apikeymanager.get(entry["provider"])
				if manager.get_active_account_id() == entry["id"]:
					self.accounts_list.Select(i)
					self.accounts_list.Focus(i)
					self.accounts_list.EnsureVisible(i)
					return
			self.accounts_list.Select(0)
			self.accounts_list.Focus(0)
			self.accounts_list.EnsureVisible(0)
			return

		want = frozenset(target_keys)
		any_sel = False
		for i, entry in enumerate(self._account_entries):
			if entry["key"] in want:
				self.accounts_list.Select(i)
				any_sel = True
		if any_sel:
			for i, entry in enumerate(self._account_entries):
				if entry["key"] in want:
					self.accounts_list.Focus(i)
					self.accounts_list.EnsureVisible(i)
					break
			return
		for i, entry in enumerate(self._account_entries):
			manager = apikeymanager.get(entry["provider"])
			if manager.get_active_account_id() == entry["id"]:
				self.accounts_list.Select(i)
				self.accounts_list.Focus(i)
				self.accounts_list.EnsureVisible(i)
				return
		self.accounts_list.Select(0)
		self.accounts_list.Focus(0)
		self.accounts_list.EnsureVisible(0)

	def _get_selected_entries(self):
		selected = []
		idx = self.accounts_list.GetFirstSelected()
		while idx != -1:
			if 0 <= idx < len(self._account_entries):
				selected.append(self._account_entries[idx])
			idx = self.accounts_list.GetNextSelected(idx)
		return selected

	def on_accounts_list_key_down(self, evt):
		key = evt.GetKeyCode()
		if key == wx.WXK_DELETE:
			self.on_remove(evt)
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.on_edit(evt)
		else:
			evt.Skip()

	def on_accounts_context_menu(self, evt):
		pos = evt.GetPosition()
		if pos == wx.DefaultPosition or pos.x < 0:
			client_pt = listctrl_menu_anchor_point(self.accounts_list)
		else:
			client_pt = pos
			hit, _flags = self.accounts_list.HitTest(client_pt)
			listctrl_apply_context_menu_hit_selection(self.accounts_list, hit, len(self._account_entries))
		self._show_accounts_context_menu(client_pt)

	def _show_accounts_context_menu(self, client_pt):
		n = len(self._get_selected_entries())
		menu = wx.Menu()
		# wx auto-IDs from NewControlId() are released when the menu is destroyed; reusing them on a later
		# PopupMenu triggers wx assertions. Map each wx.ID_ANY item to its action for the current menu only.
		cmd_by_id = {}
		self._accounts_ctx_cmd_by_id = cmd_by_id

		def add_cmd(label, action):
			item = menu.Append(wx.ID_ANY, label)
			cmd_by_id[item.GetId()] = action

		if n >= 2:
			# Translators: API accounts list context menu — delete several accounts; Del when the list has focus.
			add_cmd(_("&Remove %(count)d accounts (Del)") % {"count": n}, "remove")
			menu.AppendSeparator()
			# Translators: API accounts list context menu — create a new stored account.
			add_cmd(_("&Add account..."), "add")
		elif n == 1:
			# Translators: API accounts list context menu — edit the selected account; Enter when the list has focus.
			add_cmd(_("&Edit account (Enter)"), "edit")
			# Translators: API accounts list context menu — remove the selected account; Del when the list has focus.
			add_cmd(_("&Remove account (Del)"), "remove")
			menu.AppendSeparator()
			add_cmd(_("&Add account..."), "add")
		else:
			add_cmd(_("&Add account..."), "add")
		self.accounts_list.PopupMenu(menu, client_pt.x, client_pt.y)
		self._accounts_ctx_cmd_by_id = None
		menu.Destroy()

	def on_accounts_menu_command(self, evt):
		mapping = getattr(self, "_accounts_ctx_cmd_by_id", None)
		if not mapping:
			evt.Skip()
			return
		action = mapping.get(evt.GetId())
		if action == "edit":
			self.on_edit(evt)
		elif action == "remove":
			self.on_remove(evt)
		elif action == "add":
			self.on_add(evt)
		else:
			evt.Skip()

	def on_add(self, evt):
		# Translators: Title bar of the modal form for creating a new API account.
		dlg = AccountDialog(self, _("Add account"))
		if dlg.ShowModal() != wx.ID_OK:
			dlg.Destroy()
			return
		data = dlg.getData()
		dlg.Destroy()
		if data["provider"] == Provider.CustomOpenAI:
			if not data["base_url"]:
				# Translators: Error message body when saving a new CustomOpenAI account without a base URL (title is «AI-Hub»).
				gui.messageBox(_("Custom base URL is required for CustomOpenAI accounts."), _("AI-Hub"), wx.OK | wx.ICON_ERROR)
				return
		elif data["provider"] == Provider.Ollama:
			pass
		elif not data["api_key"]:
			# Translators: Error message body when saving a new account for a provider that requires an API key (title is «AI-Hub»).
			gui.messageBox(_("API key is required."), _("AI-Hub"), wx.OK | wx.ICON_ERROR)
			return
		manager = apikeymanager.get(data["provider"])
		acc_id = manager.add_account(
			# Translators: Stored display name for a new account when the user left the account name field empty (same word as list fallback).
			name=data["name"] or _("Account"),
			api_key=data["api_key"],
			base_url=data.get("base_url", ""),
			org_name=data["org_name"],
			org_key=data["org_key"],
			set_active=True,
		)
		self._refresh_list(select_key=f"{data['provider']}/{acc_id}")

	def on_edit(self, evt):
		selected = self._get_selected_entries()
		if not selected:
			return
		if len(selected) > 1:
			# Translators: Shown when several API accounts are selected but edit allows only one.
			ui.message(_("Select only one account to edit."))
			return
		entry = selected[0]
		# Translators: Title bar of the modal form for editing the selected API account.
		dlg = AccountDialog(self, _("Edit account"), account=entry)
		if dlg.ShowModal() != wx.ID_OK:
			dlg.Destroy()
			return
		updated = dlg.getData()
		dlg.Destroy()
		if updated["provider"] == Provider.CustomOpenAI:
			if not updated["base_url"]:
				# Translators: Error message body when saving an edited CustomOpenAI account without a base URL (title is «AI-Hub»).
				gui.messageBox(_("Custom base URL is required for CustomOpenAI accounts."), _("AI-Hub"), wx.OK | wx.ICON_ERROR)
				return
		elif updated["provider"] == Provider.Ollama:
			pass
		elif not updated["api_key"]:
			# Translators: Error message body when saving an edited account for a provider that requires an API key (title is «AI-Hub»).
			gui.messageBox(_("API key is required."), _("AI-Hub"), wx.OK | wx.ICON_ERROR)
			return
		if updated["provider"] == entry["provider"]:
			manager = apikeymanager.get(entry["provider"])
			manager.update_account(
				entry["id"],
				# Translators: Stored display name after edit when the user cleared the account name field (same word as list fallback).
				name=updated["name"] or _("Account"),
				api_key=updated["api_key"],
				base_url=updated.get("base_url", ""),
				org_name=updated["org_name"],
				org_key=updated["org_key"],
			)
			manager.set_active_account(entry["id"])
			self._refresh_list(select_key=f"{entry['provider']}/{entry['id']}")
			return
		old_manager = apikeymanager.get(entry["provider"])
		new_manager = apikeymanager.get(updated["provider"])
		new_id = new_manager.add_account(
			# Translators: Stored display name when moving an account to another provider and the name field was left blank.
			name=updated["name"] or _("Account"),
			api_key=updated["api_key"],
			base_url=updated.get("base_url", ""),
			org_name=updated["org_name"],
			org_key=updated["org_key"],
			set_active=True,
		)
		old_manager.remove_account(entry["id"])
		self._refresh_list(select_key=f"{updated['provider']}/{new_id}")

	def on_remove(self, evt):
		entries = self._get_selected_entries()
		if not entries:
			return
		if len(entries) == 1:
			entry = entries[0]
			res = gui.messageBox(
				# Translators: Confirmation prompt before deleting one stored API account (placeholders are account label and provider id).
				_("Remove account {name} from provider {provider}?").format(**{
					"name": entry["name"],
					"provider": entry["provider"],
				}),
				# Translators: Title of the confirmation dialog for removing an API account.
				_("Remove account"),
				wx.YES_NO | wx.ICON_QUESTION,
			)
		else:
			res = gui.messageBox(
				# Translators: Confirmation before permanently deleting several stored API accounts.
				_("Remove %(count)d selected accounts? This cannot be undone.") % {"count": len(entries)},
				# Translators: Title of the confirmation dialog for removing several API accounts at once.
				_("Remove accounts"),
				wx.YES_NO | wx.ICON_WARNING,
			)
		if res != wx.YES:
			return
		for entry in entries:
			manager = apikeymanager.get(entry["provider"])
			manager.remove_account(entry["id"])
		self._refresh_list()


def show_accounts_management(parent=None):
	"""Show the API accounts dialog modally. ``parent`` defaults to the NVDA main frame."""
	parent = parent or gui.mainFrame
	dlg = AccountsManagementDialog(parent)
	dlg.ShowModal()
	dlg.Destroy()
