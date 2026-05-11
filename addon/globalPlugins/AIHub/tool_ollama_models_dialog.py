"""Dedicated dialog for Ollama model management actions."""

import json
import threading
import urllib.error
import urllib.request
import winsound

import addonHandler
import wx

from .consts import (
	BASE_URLs,
	Provider,
	SND_CHAT_RESPONSE_RECEIVED,
	SND_PROGRESS,
	stop_progress_sound,
	UI_DIALOG_BORDER_PX,
	UI_FORM_ROW_BORDER_PX,
	UI_SECTION_SPACING_PX,
)
from .model import clearModelCache
from .providertools_helpers import add_labeled_factory
from .thread_shutdown import stop_worker_thread
from .tool_dialog_base import ToolDialogBase

addonHandler.initTranslation()

class OllamaModelManagerToolDialog(ToolDialogBase):
	ACTIONS = (
		# Translators: Ollama model manager — label in the Action drop-down for listing models installed on the local Ollama server.
		("list_models", _("List installed models")),
		# Translators: Ollama model manager — label in the Action drop-down for listing models currently loaded in Ollama memory.
		("list_running", _("List running models")),
		# Translators: Ollama model manager — label in the Action drop-down for fetching JSON details of one named model.
		("show", _("Show model details")),
		# Translators: Ollama model manager — label in the Action drop-down for downloading a model from a registry into Ollama.
		("pull", _("Pull model (add)")),
		# Translators: Ollama model manager — label in the Action drop-down for uploading a local model to a registry.
		("push", _("Push model")),
		# Translators: Ollama model manager — label in the Action drop-down for duplicating a model under a new name on the same machine.
		("copy", _("Copy model")),
		# Translators: Ollama model manager — label in the Action drop-down for creating a model from pasted Modelfile text.
		("create", _("Create model from Modelfile")),
		# Translators: Ollama model manager — label in the Action drop-down for removing one installed model from Ollama.
		("delete", _("Delete model (remove)")),
	)

	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub tool dialog for running Ollama admin commands (list, pull, delete, etc.).
			title=_("Tool: Ollama model manager"),
			provider=Provider.Ollama,
			size=(860, 760),
			parentDialog=parentDialog,
			plugin=plugin,
		)
		self._worker = None
		self._rawResult = ""

		dialogSizer = wx.BoxSizer(wx.VERTICAL)
		self.formPanel = wx.Panel(self)
		main = wx.BoxSizer(wx.VERTICAL)

		self.accountChoice = add_labeled_factory(
			# Translators: Label before the account drop-down choosing which saved Ollama endpoint/credentials to use.
			self.formPanel, main, _("&Account:"), lambda: self.build_account_choice(self.formPanel)
		)
		self.actionChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the drop-down that selects which Ollama admin action to run (list, pull, delete, etc.).
			_("&Action:"),
			lambda: wx.Choice(self.formPanel, choices=[label for _, label in self.ACTIONS]),
		)
		self.actionChoice.SetSelection(0)
		self.actionChoice.Bind(wx.EVT_CHOICE, self.onActionChanged)

		self.modelText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the primary model name or id text field in the Ollama models tool.
			_("&Model name:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.secondaryModelText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the destination model name field used only for the Copy model action.
			_("Destination model (for copy):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.modelfileText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the multiline Modelfile editor used only for the Create model from Modelfile action.
			_("&Modelfile (for create):"),
			lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 140)),
		)
		# Translators: Checkbox allowing non-TLS or otherwise insecure registry connections when pulling or pushing models.
		self.insecureCheck = wx.CheckBox(self.formPanel, label=_("Allow insecure registry (pull/push)"))
		main.Add(self.insecureCheck, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)

		self.resultText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the read-only multiline area showing formatted command output from Ollama.
			_("&Result:"),
			lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 280)),
		)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that runs the Ollama HTTP action selected in the Action drop-down.
		self.runBtn = wx.Button(self.formPanel, label=_("Run action"))
		self.runBtn.Bind(wx.EVT_BUTTON, self.onRun)
		self.bind_ctrl_enter_submit(self.onRun)
		# Translators: Button opening a dialog with the raw JSON or text response from the last Ollama command.
		self.openRawBtn = wx.Button(self.formPanel, label=_("Show raw result"))
		self.openRawBtn.Bind(wx.EVT_BUTTON, self.onShowRaw)
		self.closeBtn = wx.Button(self.formPanel, id=wx.ID_CLOSE)
		self.closeBtn.Bind(wx.EVT_BUTTON, self.onClose)
		buttons.Add(self.runBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		buttons.Add(self.openRawBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		buttons.Add(self.closeBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		main.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, UI_SECTION_SPACING_PX)

		self.formPanel.SetSizer(main)
		dialogSizer.Add(self.formPanel, 1, wx.EXPAND | wx.ALL, UI_DIALOG_BORDER_PX)
		self.SetSizer(dialogSizer)
		if parent:
			self.CentreOnParent(wx.BOTH)
		else:
			self.Centre(wx.BOTH)
		self.onActionChanged(None)

	def _setBusy(self, busy: bool):
		for ctrl in (
			self.accountChoice,
			self.actionChoice,
			self.modelText,
			self.secondaryModelText,
			self.modelfileText,
			self.insecureCheck,
			self.runBtn,
			self.openRawBtn,
			self.closeBtn,
		):
			ctrl.Enable(not busy)

	def _native_base_url(self, account_id: str) -> str:
		base = self.manager.get_base_url(account_id=account_id) or BASE_URLs.get(Provider.Ollama, "http://127.0.0.1:11434/v1")
		base = base.rstrip("/")
		if base.lower().endswith("/v1"):
			base = base[:-3]
		return base

	def _headers(self, account_id: str, with_json: bool = True) -> dict:
		api_key = self.manager.get_api_key(account_id=account_id)
		headers = {"User-Agent": "Mozilla/5.0 (compatible; NVDA-OpenAI-Addon/1.0)"}
		if with_json:
			headers["Content-Type"] = "application/json"
		if api_key and str(api_key).strip():
			headers["Authorization"] = f"Bearer {api_key}"
		return headers

	def _endpoint_for_action(self, action: str) -> tuple[str, str]:
		if action == "list_models":
			return "GET", "/api/tags"
		if action == "list_running":
			return "GET", "/api/ps"
		if action == "show":
			return "POST", "/api/show"
		if action == "pull":
			return "POST", "/api/pull"
		if action == "push":
			return "POST", "/api/push"
		if action == "copy":
			return "POST", "/api/copy"
		if action == "create":
			return "POST", "/api/create"
		if action == "delete":
			return "DELETE", "/api/delete"
		raise ValueError("Unsupported Ollama action")

	def _build_payload(self, action: str) -> dict | None:
		model_name = self.modelText.GetValue().strip()
		if action in ("show", "pull", "push", "delete", "create") and not model_name:
			# Translators: Validation error when the Ollama models tool action needs a model name but the field is empty.
			raise ValueError(_("Model name is required for this action."))
		if action == "copy":
			source = model_name
			dest = self.secondaryModelText.GetValue().strip()
			if not source or not dest:
				# Translators: Validation error when the copy action is chosen but source or destination model name is missing.
				raise ValueError(_("Source and destination model names are required for copy."))
			return {"source": source, "destination": dest}
		if action == "create":
			modelfile = self.modelfileText.GetValue().strip()
			if not modelfile:
				# Translators: Validation error when create-from-Modelfile is chosen but the Modelfile text box is empty.
				raise ValueError(_("Modelfile content is required for create action."))
			return {"model": model_name, "modelfile": modelfile, "stream": False}
		if action in ("show",):
			return {"model": model_name}
		if action in ("pull", "push"):
			payload = {"model": model_name, "stream": False}
			if self.insecureCheck.GetValue():
				payload["insecure"] = True
			return payload
		if action == "delete":
			return {"model": model_name}
		return None

	def onActionChanged(self, evt):
		action = self.ACTIONS[self.actionChoice.GetSelection()][0]
		needs_model = action not in ("list_models", "list_running")
		needs_secondary = action == "copy"
		needs_modelfile = action == "create"
		needs_insecure = action in ("pull", "push")
		self.modelText.Show(needs_model)
		self.secondaryModelText.Show(needs_secondary)
		self.modelfileText.Show(needs_modelfile)
		self.insecureCheck.Show(needs_insecure)
		self.formPanel.Layout()
		self.Layout()

	def _format_result(self, action: str, data: dict | list | str) -> str:
		if action == "list_models" and isinstance(data, dict):
			models = data.get("models", [])
			if not isinstance(models, list):
				models = []
			if not models:
				# Translators: Result text when list_models succeeds but Ollama reports no locally installed models.
				return _("No installed models found.")
			# Translators: Heading line before the bullet list of installed Ollama models in the tool result view.
			lines = [_("Installed models (%d):") % len(models)]
			for item in models:
				if isinstance(item, dict):
					name = item.get("model") or item.get("name") or ""
					size = item.get("size")
					if name:
						lines.append(f"- {name}" + (f" ({size} bytes)" if isinstance(size, int) else ""))
			return "\n".join(lines)
		if action == "list_running" and isinstance(data, dict):
			models = data.get("models", [])
			if not isinstance(models, list):
				models = []
			if not models:
				# Translators: Result text when list_running succeeds but no model is currently loaded in Ollama.
				return _("No running models.")
			# Translators: Heading line before the bullet list of models currently loaded in Ollama.
			lines = [_("Running models (%d):") % len(models)]
			for item in models:
				if isinstance(item, dict):
					name = item.get("model") or item.get("name") or ""
					if name:
						lines.append(f"- {name}")
			return "\n".join(lines)
		return json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else str(data)

	def _run_worker(self, account_id: str, action: str):
		err = None
		formatted = ""
		raw = ""
		try:
			method, endpoint = self._endpoint_for_action(action)
			payload = self._build_payload(action)
			url = self._native_base_url(account_id) + endpoint
			data = None
			if payload is not None:
				data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
			req = urllib.request.Request(url, data=data, headers=self._headers(account_id), method=method)
			with urllib.request.urlopen(req, timeout=120) as resp:
				raw = resp.read().decode("utf-8", errors="replace")
			try:
				parsed = json.loads(raw) if raw else {}
			except Exception:
				parsed = raw
			formatted = self._format_result(action, parsed)
			if action in ("pull", "delete", "create", "copy"):
				clearModelCache(Provider.Ollama)
		except urllib.error.HTTPError as e:
			try:
				body = e.read().decode("utf-8", errors="replace")
			except Exception:
				body = str(e)
			err = f"HTTP {e.code}: {body}"
		except Exception as e:
			err = str(e)
		wx.CallAfter(self._onWorkerDone, formatted, raw, err)

	def _onWorkerDone(self, formatted: str, raw: str, err: str | None):
		stop_progress_sound()
		if not self._isDialogAlive():
			self._worker = None
			return
		if self.conf["chatFeedback"]["sndResponseReceived"]:
			winsound.PlaySound(SND_CHAT_RESPONSE_RECEIVED, winsound.SND_ASYNC)
		if not self.end_long_task(focus_ctrl=self.resultText):
			self._worker = None
			return
		self._worker = None
		if err:
			# Translators: Error body after an Ollama HTTP call fails in the Ollama models tool; placeholder is the error text (title is «OpenAI»).
			wx.MessageBox(_("Ollama action failed: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._rawResult = raw or formatted or ""
		# Translators: Single line shown in the Ollama models tool result text box when the action succeeded but returned no printable output.
		self.resultText.SetValue(formatted or _("Done."))
		self.resultText.SetFocus()

	def onRun(self, evt):
		if self._worker is not None:
			return
		account_id = self.require_account(self.accountChoice)
		if not account_id:
			return
		action = self.ACTIONS[self.actionChoice.GetSelection()][0]
		try:
			self._build_payload(action)
		except Exception as e:
			wx.MessageBox(str(e), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		# Translators: Message on the modal progress bar while the Ollama models tool waits for the local Ollama HTTP action to finish.
		self.begin_long_task(_("Running Ollama action..."), self._setBusy)
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		self._worker = threading.Thread(target=self._run_worker, args=(account_id, action), daemon=True)
		self._worker.start()

	def onShowRaw(self, evt):
		text = self._rawResult or self.resultText.GetValue()
		if not text.strip():
			# Translators: Information message when «Show raw» is used before any Ollama command has produced a response body (title is «OpenAI»).
			wx.MessageBox(_("No raw result available yet."), "OpenAI", wx.OK | wx.ICON_INFORMATION)
			return
		# Translators: Title of the read-only dialog that shows up to the first 20 000 characters of the last Ollama JSON response (body is technical text).
		wx.MessageBox(text[:20000], _("Raw result"), wx.OK | wx.ICON_INFORMATION)

	def onClose(self, evt):
		stop_progress_sound()
		self._markClosing()
		stop_worker_thread(self._worker)
		self._worker = None
		self._destroy_task_progress_dialog()
		try:
			self.Destroy()
		except Exception:
			pass
