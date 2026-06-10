"""Model/account list handlers for ConversationDialog."""

import wx

import addonHandler
import gui
import ui

from . import apikeymanager
from .consts import Provider
from .modeldetailsutils import build_model_details_html
from .model import clearModelCache, getModels


# Providers whose model listing depends on a per-account base URL.
_USER_ENDPOINT_PROVIDERS = (Provider.CustomOpenAI, Provider.Ollama)

addonHandler.initTranslation()

MODEL_SORT_OPTIONS = {
	"created": (lambda m: getattr(m, "created", 0), True),
	"created_asc": (lambda m: getattr(m, "created", 0), False),
	"name": (lambda m: (m.name or m.id).lower(), False),
	"name_desc": (lambda m: (m.name or m.id).lower(), True),
	"context": (lambda m: m.contextWindow, True),
	"context_asc": (lambda m: m.contextWindow, False),
	"max_tokens": (lambda m: (m.maxOutputToken if m.maxOutputToken > 0 else 0), True),
	"max_tokens_asc": (lambda m: (m.maxOutputToken if m.maxOutputToken > 0 else 0), False),
}
MODEL_SORT_DEFAULT = "created"


class ModelHandlersMixin:
	def _effective_advanced_mode(self):
		"""Per-session advanced sampling UI (temperature, top-p, stream, debug)."""
		cb = getattr(self, "advancedSamplingCheckBox", None)
		return bool(cb is not None and cb.IsChecked())

	def _supported_param_set(self, model) -> set[str]:
		return {
			p.lower()
			for p in (getattr(model, "supportedParameters", None) or [])
			if isinstance(p, str)
		}

	def _updateWebSearchCheckbox(self, model):
		"""Show/enable web search chrome in sync with model.supports_web_search."""
		if model and model.supports_web_search:
			self.webSearchCheckBox.Enable(True)
			self.webSearchCheckBox.Show(True)
		else:
			self.webSearchCheckBox.Enable(False)
			self.webSearchCheckBox.Show(False)
			self.webSearchCheckBox.SetValue(False)

	def _set_labeled_visibility(self, label, ctrl, visible: bool, enabled: bool | None = None):
		if enabled is None:
			enabled = visible
		label.Show(visible)
		ctrl.Show(visible)
		label.Enable(enabled)
		ctrl.Enable(enabled)

	def _modelKey(self, model):
		return f"{model.provider}:{model.id}"

	def _accountKey(self, account):
		return f"{account['provider'].lower()}/{account['id']}"

	def _accountLabel(self, account):
		# Translators: Fallback account name when no custom name is set.
		name = account.get("name") or _("Account")
		label = f"{account['provider']} / {name}"
		if account.get("provider") in _USER_ENDPOINT_PROVIDERS and account.get("base_url"):
			label = f"{label} - {account['base_url']}"
		return label

	def _loadAccounts(self):
		accounts = []
		for provider in apikeymanager.AVAILABLE_PROVIDERS:
			manager = apikeymanager.get(provider)
			for acc in manager.list_accounts(include_env=True):
				if not manager.isReady(account_id=acc["id"]):
					continue
				account = {
					"provider": provider,
					"id": acc["id"],
					# Translators: Fallback account name when loading account list.
					"name": acc.get("name") or _("Account"),
					"base_url": acc.get("base_url", ""),
				}
				account["key"] = self._accountKey(account)
				accounts.append(account)
		self._accounts = sorted(accounts, key=lambda a: (a["provider"].lower(), (a.get("name") or "").lower()))

	def getCurrentAccount(self):
		if not hasattr(self, "accountListCtrl"):
			return None
		idx = self.accountListCtrl.GetSelection()
		if idx == wx.NOT_FOUND:
			return None
		return self.accountListCtrl.GetClientData(idx)

	def _requireAccount(self, modal=False):
		account = self.getCurrentAccount()
		if account:
			return account
		# Translators: Error message shown when no account is selected.
		msg = _("Please select an account.")
		if modal:
			gui.messageBox(msg, "OpenAI", wx.OK | wx.ICON_ERROR)
		else:
			ui.message(msg)
		return None

	def _selectAccountOnList(self, lst, account_key):
		if not account_key:
			return False
		account_key_norm = account_key.lower()
		for i in range(lst.GetCount()):
			acc = lst.GetClientData(i)
			if acc and acc.get("key", "").lower() == account_key_norm:
				lst.SetSelection(i)
				return True
		return False

	def _selectAccount(self, account_key):
		return self._selectAccountOnList(self.accountListCtrl, account_key)

	def _refreshAccountsList(self, account_to_select=None):
		self._loadAccounts()
		notebook = getattr(self, "notebook", None)
		if notebook is None or notebook.GetPageCount() <= 0:
			return
		active_idx = notebook.GetSelection()
		if active_idx < 0:
			active_idx = 0
		fallback = account_to_select or self.data.get("lastAccountKey")
		for ti in range(notebook.GetPageCount()):
			page = notebook.GetPage(ti)
			lst = page.accountListCtrl
			lst.Clear()
			for account in self._accounts:
				lst.Append(self._accountLabel(account), account)
			if ti == active_idx:
				sel = fallback
			else:
				sel = (getattr(page, "conversationAccountKey", None) or "").strip() or fallback
			if not self._selectAccountOnList(lst, sel) and lst.GetCount():
				lst.SetSelection(0)

	def _reload_models_for_current_account(self, model_to_select=None):
		"""Load `self._models` for the selected account and fill the active tab's model list."""
		account = self.getCurrentAccount()
		if not account:
			self._models = []
			self._refreshModelsList(model_to_select=model_to_select)
			return
		self._models = getModels(account["provider"], account_id=account.get("id"))
		self._refreshModelsList(model_to_select=model_to_select)

	def onAccountChange(self, evt):
		account = self.getCurrentAccount()
		if not account:
			self._models = []
			self._refreshModelsList()
			return
		if self.data.get("lastAccountKey") != account["key"]:
			self.data["lastAccountKey"] = account["key"]
			self.saveData(True)
		self._reload_models_for_current_account()
		self.onModelChange(None)

	def getCurrentModel(self):
		if not hasattr(self, "modelsListCtrl"):
			return None
		try:
			lst = self.modelsListCtrl
		except Exception:
			return None
		idx = lst.GetSelection()
		if idx == wx.NOT_FOUND:
			return self._models[0] if self._models else None
		return lst.GetClientData(idx)

	def _requireModel(self, modal=False):
		model = self.getCurrentModel()
		if model:
			return model
		# Translators: Error message shown when no model is selected.
		msg = _("Please select a model.")
		if modal:
			gui.messageBox(msg, "OpenAI", wx.OK | wx.ICON_ERROR)
		else:
			ui.message(msg)
		return None

	def _getCurrentModelKey(self):
		model = self.getCurrentModel()
		return self._modelKey(model) if model else None

	def _selectModel(self, selector):
		if not selector:
			return False
		lst = self.modelsListCtrl
		selector_norm = selector.lower() if isinstance(selector, str) else selector
		for i in range(lst.GetCount()):
			model = lst.GetClientData(i)
			if not model:
				continue
			if selector_norm == model.id.lower() or selector == self._modelKey(model):
				lst.SetSelection(i)
				return True
			if isinstance(selector_norm, str) and "/" in selector_norm and selector_norm.endswith("/" + model.id.lower()):
				lst.SetSelection(i)
				return True
		return False

	def _getFavoriteModels(self):
		fav = self.data.get("favorite_models", [])
		return fav if isinstance(fav, list) else []

	def _favoriteKey(self, model):
		return self._modelKey(model)

	def _isModelFavorite(self, model):
		fav = self._getFavoriteModels()
		key = self._favoriteKey(model)
		return key in fav or model.id in fav

	def _getModelSortOrder(self):
		return self.data.get("modelSort", MODEL_SORT_DEFAULT)

	def _sortModelsBySetting(self, models):
		sort_key = self._getModelSortOrder()
		key_fn, reverse = MODEL_SORT_OPTIONS.get(sort_key, MODEL_SORT_OPTIONS[MODEL_SORT_DEFAULT])
		by_key = sorted(models, key=key_fn, reverse=reverse)
		return sorted(by_key, key=lambda m: not self._isModelFavorite(m))

	def _formatModelLabel(self, model):
		# Translators: Capability tags shown in model list labels.
		capabilities = [_("text")]
		if model.vision:
			# Translators: Text in model/account selection UI and model context menus.
			capabilities.append(_("image"))
		if getattr(model, "audioInput", False) or getattr(model, "audioOutput", False):
			# Translators: Text in model/account selection UI and model context menus.
			capabilities.append(_("audio"))
		if model.reasoning:
			# Translators: Text in model/account selection UI and model context menus.
			capabilities.append(_("reasoning"))
		if model.supports_web_search:
			# Translators: Text in model/account selection UI and model context menus.
			capabilities.append(_("web search"))
		cap_str = ", ".join(capabilities)
		ctx_k = model.contextWindow // 1000
		suffix = " *" if self._isModelFavorite(model) else ""
		return f"{model.name}{suffix}  |  {cap_str}  |  {ctx_k}k"

	def _getDefaultSelection(self):
		account = self.getCurrentAccount()
		if account:
			account_key = account["key"]
			last_by_account = self.data.get("lastModelByAccount", {})
			if isinstance(last_by_account, dict):
				model_id = last_by_account.get(account_key)
				if isinstance(model_id, str) and model_id:
					return model_id
			last_selection = self.data.get("lastModelSelection")
			prefix = f"{account_key}/"
			if isinstance(last_selection, str) and last_selection.lower().startswith(prefix):
				parts = last_selection.split("/", 2)
				if len(parts) == 3 and parts[2]:
					return parts[2]
		last_model = self.data.get("lastModel")
		if isinstance(last_model, str) and last_model:
			return last_model
		return self.conf["modelVision" if self.filesList else "model"]

	def _persistCurrentModelSelection(self, model):
		if not model:
			return
		changed = False
		account = self.getCurrentAccount()
		if account:
			account_key = account["key"]
			if not isinstance(self.data.get("lastModelByAccount"), dict):
				self.data["lastModelByAccount"] = {}
				changed = True
			if self.data["lastModelByAccount"].get(account_key) != model.id:
				self.data["lastModelByAccount"][account_key] = model.id
				changed = True
			if self.data.get("lastAccountKey") != account_key:
				self.data["lastAccountKey"] = account_key
				changed = True
			new_sel = f"{account_key}/{model.id}"
			if self.data.get("lastModelSelection") != new_sel:
				self.data["lastModelSelection"] = new_sel
				changed = True
		if self.data.get("lastModel") != model.id:
			self.data["lastModel"] = model.id
			changed = True
		if changed:
			self.saveData(True)

	def _refreshModelsList(self, model_to_select=None):
		lst = self.modelsListCtrl
		lst.Clear()
		if not self._models:
			return
		for model in self._sortModelsBySetting(self._models):
			lst.Append(self._formatModelLabel(model), model)
		selector = model_to_select or self._getDefaultSelection()
		if not self._selectModel(selector) and lst.GetCount():
			lst.SetSelection(0)

	def onModelChange(self, evt):
		model = self.getCurrentModel()
		if not model:
			return
		self._persistCurrentModelSelection(model)
		supported = self._supported_param_set(model)
		supports_max_tokens = (
			"max_tokens" in supported
			or "max_completion_tokens" in supported
			or (getattr(model, "maxOutputToken", -1) > 0)
		)
		self._set_labeled_visibility(self.maxTokensLabel, self.maxTokensSpinCtrl, supports_max_tokens)
		if hasattr(self, "maxTokensRow"):
			self.maxTokensRow.Show(supports_max_tokens)
		if supports_max_tokens:
			max_cap = model.maxOutputToken if model.maxOutputToken > 1 else model.contextWindow
			self.maxTokensSpinCtrl.SetRange(0, max_cap)
		key_maxTokens = "maxTokens_%s" % model.id
		defaultMaxOutputToken = self.data.get(key_maxTokens, 0) if isinstance(self.data.get(key_maxTokens, 0), int) else 0
		if supports_max_tokens:
			try:
				self.maxTokensSpinCtrl.SetValue(defaultMaxOutputToken)
			except Exception:
				self.maxTokensSpinCtrl.SetValue(0)

		if model.reasoning:
			self.reasoningModeCheckBox.Enable(True)
			self.reasoningModeCheckBox.Show(True)
			reasoning_on = self.reasoningModeCheckBox.IsChecked()
			opts = model.reasoning_effort_options
			if opts and reasoning_on:
				labels = [o[1] for o in opts]
				self._reasoningEffortOptions = opts
				self.reasoningEffortChoice.Set(labels)
				saved = self.conf.get("reasoningEffort", "medium")
				idx = next((i for i, (v, _) in enumerate(opts) if v == saved), 0)
				self.reasoningEffortChoice.SetSelection(idx)
				self._set_labeled_visibility(self.reasoningEffortLabel, self.reasoningEffortChoice, True)
				if hasattr(self, "reasoningEffortRow"):
					self.reasoningEffortRow.Show(True)
			else:
				self._reasoningEffortOptions = ()
				self.reasoningEffortChoice.Clear()
				self._set_labeled_visibility(self.reasoningEffortLabel, self.reasoningEffortChoice, False)
				if hasattr(self, "reasoningEffortRow"):
					self.reasoningEffortRow.Show(False)
			if model.adaptive_choice_visible and reasoning_on:
				self.adaptiveThinkingCheckBox.Enable(True)
				self.adaptiveThinkingCheckBox.Show(True)
				self.adaptiveThinkingCheckBox.SetValue(self.conf.get("adaptiveThinking", True))
			else:
				self.adaptiveThinkingCheckBox.Enable(False)
				self.adaptiveThinkingCheckBox.Show(False)
				if not model.adaptive_choice_visible:
					self.adaptiveThinkingCheckBox.SetValue(False)
		else:
			self.reasoningModeCheckBox.Enable(False)
			self.reasoningModeCheckBox.Show(False)
			self.reasoningModeCheckBox.SetValue(False)
			self.reasoningEffortChoice.Clear()
			self._set_labeled_visibility(self.reasoningEffortLabel, self.reasoningEffortChoice, False)
			if hasattr(self, "reasoningEffortRow"):
				self.reasoningEffortRow.Show(False)
			self.adaptiveThinkingCheckBox.Enable(False)
			self.adaptiveThinkingCheckBox.Show(False)
			self.adaptiveThinkingCheckBox.SetValue(False)

		self._updateWebSearchCheckbox(model)

		if self._effective_advanced_mode():
			if "temperature" in supported:
				self._set_labeled_visibility(self.temperatureLabel, self.temperatureSpinCtrl, True)
				self.temperatureSpinCtrl.SetRange(0, int(model.maxTemperature * 100))
				key_temperature = "temperature_%s" % model.id
				if key_temperature in self.data:
					self.temperatureSpinCtrl.SetValue(int(self.data[key_temperature]))
				else:
					self.temperatureSpinCtrl.SetValue(int(model.defaultTemperature * 100))
			else:
				self._set_labeled_visibility(self.temperatureLabel, self.temperatureSpinCtrl, False)
			if "top_p" in supported:
				self._set_labeled_visibility(self.topPLabel, self.topPSpinCtrl, True)
			else:
				self._set_labeled_visibility(self.topPLabel, self.topPSpinCtrl, False)
			if "seed" in supported:
				self._set_labeled_visibility(self.advancedSeedLabel, self.advancedSeedSpinCtrl, True)
				key_seed = "seed_%s" % model.id
				if key_seed in self.data:
					try:
						self.advancedSeedSpinCtrl.SetValue(int(self.data[key_seed]))
					except Exception:
						self.advancedSeedSpinCtrl.SetValue(-1)
				else:
					self.advancedSeedSpinCtrl.SetValue(-1)
			else:
				self._set_labeled_visibility(self.advancedSeedLabel, self.advancedSeedSpinCtrl, False)
			if "top_k" in supported:
				self._set_labeled_visibility(self.advancedTopKLabel, self.advancedTopKSpinCtrl, True)
				key_tk = "top_k_%s" % model.id
				if key_tk in self.data:
					try:
						self.advancedTopKSpinCtrl.SetValue(int(self.data[key_tk]))
					except Exception:
						self.advancedTopKSpinCtrl.SetValue(0)
				else:
					self.advancedTopKSpinCtrl.SetValue(0)
			else:
				self._set_labeled_visibility(self.advancedTopKLabel, self.advancedTopKSpinCtrl, False)
			if "stop" in supported:
				self._set_labeled_visibility(self.advancedStopLabel, self.advancedStopTextCtrl, True)
				key_stop = "stop_%s" % model.id
				if key_stop in self.data:
					self.advancedStopTextCtrl.SetValue(str(self.data[key_stop]))
				else:
					self.advancedStopTextCtrl.SetValue("")
			else:
				self._set_labeled_visibility(self.advancedStopLabel, self.advancedStopTextCtrl, False)
			if "frequency_penalty" in supported:
				self._set_labeled_visibility(self.advancedFreqPenaltyLabel, self.advancedFreqPenaltySpinCtrl, True)
				key_fp = "frequency_penalty_%s" % model.id
				if key_fp in self.data:
					try:
						self.advancedFreqPenaltySpinCtrl.SetValue(int(self.data[key_fp]))
					except Exception:
						self.advancedFreqPenaltySpinCtrl.SetValue(0)
				else:
					self.advancedFreqPenaltySpinCtrl.SetValue(0)
			else:
				self._set_labeled_visibility(self.advancedFreqPenaltyLabel, self.advancedFreqPenaltySpinCtrl, False)
			if "presence_penalty" in supported:
				self._set_labeled_visibility(self.advancedPresPenaltyLabel, self.advancedPresPenaltySpinCtrl, True)
				key_pp = "presence_penalty_%s" % model.id
				if key_pp in self.data:
					try:
						self.advancedPresPenaltySpinCtrl.SetValue(int(self.data[key_pp]))
					except Exception:
						self.advancedPresPenaltySpinCtrl.SetValue(0)
				else:
					self.advancedPresPenaltySpinCtrl.SetValue(0)
			else:
				self._set_labeled_visibility(self.advancedPresPenaltyLabel, self.advancedPresPenaltySpinCtrl, False)
		self._update_advanced_controls_visibility()
		self.Layout()
		if not getattr(self, "_sync_suppress_tab_capture", False):
			if getattr(self, "notebook", None):
				try:
					self._captureConversationChromeToPage(self.get_active_page())
				except Exception:
					pass
	def showModelDetails(self, evt=None, model=None):
		if model is None:
			model = self._requireModel()
		if not model:
			return
		html = build_model_details_html(model)
		# Translators: Title for the browseable model details view.
		ui.browseableMessage(html, _("Model details"), isHtml=True)

	def _reloadModels(self):
		account = self.getCurrentAccount()
		if not account:
			self._models = []
			self._refreshModelsList()
			return
		clearModelCache(account["provider"])
		self._models = getModels(account["provider"], account_id=account.get("id"))
		self._refreshModelsList(model_to_select=self._getCurrentModelKey())
		wx.CallAfter(self.onModelChange, None)

	def onFavoriteModel(self, evt=None, model=None):
		if model is None:
			model = self._requireModel()
		if not model:
			return
		fav = self._getFavoriteModels()
		key = self._favoriteKey(model)
		if self._isModelFavorite(model):
			self.data["favorite_models"] = [x for x in fav if x != key and x != model.id]
		else:
			self.data["favorite_models"] = fav + [key]
		self.saveData(True)
		self._refreshModelsList(model_to_select=self._modelKey(model))
		wx.CallAfter(self.onModelChange, None)

	def onModelKeyDown(self, evt):
		if evt.GetKeyCode() == wx.WXK_SPACE:
			if evt.GetModifiers() == wx.MOD_SHIFT:
				self.onFavoriteModel(evt)
			elif evt.GetModifiers() == wx.MOD_NONE:
				self.showModelDetails()
		elif evt.GetKeyCode() == wx.WXK_RETURN:
			self.onSubmit(evt)
		else:
			evt.Skip()

	def _onModelSortChoice(self, evt, sort_key):
		self.data["modelSort"] = sort_key
		self.saveData(True)
		current_key = self._getCurrentModelKey()
		self._refreshModelsList(model_to_select=current_key)
		wx.CallAfter(self.onModelChange, None)

	def onModelContextMenu(self, evt):
		lst = evt.GetEventObject()
		idx = lst.GetSelection() if lst is not None else wx.NOT_FOUND
		model = lst.GetClientData(idx) if lst is not None and idx != wx.NOT_FOUND else None
		if not model:
			model = self._requireModel()
		if not model:
			return
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		# Translators: Context-menu item for opening model details.
		menu.Append(item_id, _("Show model &details") + " (Space)")
		self.Bind(wx.EVT_MENU, lambda e, m=model: self.showModelDetails(e, m), id=item_id)
		isFavorite = self._isModelFavorite(model)
		item_id = wx.NewIdRef()
		# Translators: Context-menu item labels to favorite/unfavorite a model.
		label = _("Add to &favorites") if not isFavorite else _("Remove from &favorites")
		menu.Append(item_id, f"{label} (Shift+Space)")
		self.Bind(wx.EVT_MENU, lambda e, m=model: self.onFavoriteModel(e, model=m), id=item_id)
		menu.AppendSeparator()
		sort_menu = wx.Menu()
		current_sort = self._getModelSortOrder()
		# Translators: Labels for model sorting choices.
		sort_labels = {
			# Translators: Text in model/account selection UI and model context menus.
			"created": _("Most recent first"),
			# Translators: Text in model/account selection UI and model context menus.
			"created_asc": _("Oldest first"),
			# Translators: Text in model/account selection UI and model context menus.
			"name": _("Name (A–Z)"),
			# Translators: Text in model/account selection UI and model context menus.
			"name_desc": _("Name (Z–A)"),
			# Translators: Text in model/account selection UI and model context menus.
			"context": _("Context window (largest first)"),
			# Translators: Text in model/account selection UI and model context menus.
			"context_asc": _("Context window (smallest first)"),
			# Translators: Text in model/account selection UI and model context menus.
			"max_tokens": _("Max output tokens (highest first)"),
			# Translators: AI-Hub conversation — model list: entry in a context menu or submenu.
			"max_tokens_asc": _("Max output tokens (lowest first)"),
		}
		for key in MODEL_SORT_OPTIONS:
			item_id = wx.NewIdRef()
			label = sort_labels.get(key, key)
			sort_menu.AppendRadioItem(item_id, label)
			if key == current_sort:
				sort_menu.Check(item_id, True)
			self.Bind(wx.EVT_MENU, lambda e, k=key: self._onModelSortChoice(e, k), id=item_id)
		# Translators: Submenu label for model sort options.
		menu.AppendSubMenu(sort_menu, _("&Sort by"))
		menu.AppendSeparator()
		item_id = wx.NewIdRef()
		# Translators: Context-menu item to refresh provider model list.
		menu.Append(item_id, _("&Refresh model list"))
		self.Bind(wx.EVT_MENU, lambda e: self._reloadModels(), id=item_id)
		menu.AppendSeparator()
		lst.PopupMenu(menu)
		menu.Destroy()
