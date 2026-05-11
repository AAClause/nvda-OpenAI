"""Base class for single-tool dialogs."""

import os

import addonHandler
import config
import wx

from . import apikeymanager
from .apiclient import configure_client_for_provider

addonHandler.initTranslation()


class ToolDialogBase(wx.Dialog):
	def __init__(self, parent, title, provider, size=(760, 620), parentDialog=None, plugin=None):
		super().__init__(
			parent,
			title=title,
			size=size,
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
		)
		self.parentDialog = parentDialog or parent
		self.plugin = plugin or getattr(self.parentDialog, "_plugin", None)
		self.conf = config.conf["AIHub"]
		if self.parentDialog and hasattr(self.parentDialog, "conf"):
			self.conf = self.parentDialog.conf
		self.client = None
		if self.parentDialog and hasattr(self.parentDialog, "client"):
			self.client = self.parentDialog.client
		elif self.plugin and hasattr(self.plugin, "getClient"):
			self.client = self.plugin.getClient()
		self.provider = provider
		self.manager = apikeymanager.get(provider)
		self._accounts = self.manager.list_accounts(include_env=True)
		self._isClosing = False
		self._ctrlEnterHandler = None
		self._taskProgressDialog = None
		self._taskProgressTimer = None
		self._taskBusySetter = None
		self._taskCancelRequested = False
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.Bind(wx.EVT_CHAR_HOOK, self._onCharHook)

	def _markClosing(self):
		self._isClosing = True

	def _isDialogAlive(self) -> bool:
		if self._isClosing:
			return False
		try:
			return not self.IsBeingDeleted()
		except Exception:
			return False

	def bind_ctrl_enter_submit(self, handler):
		"""Bind Ctrl+Enter to a submit/generate handler for this dialog."""
		self._ctrlEnterHandler = handler

	def begin_long_task(self, status_message: str, set_busy):
		self._taskCancelRequested = False
		self._taskBusySetter = set_busy
		if callable(set_busy):
			set_busy(True)
		self._destroy_task_progress_dialog()
		if not self._isDialogAlive():
			return
		try:
			self._taskProgressDialog = wx.ProgressDialog(
				# Translators: Title bar of the modal progress window shown during long AI-Hub tool requests (label kept as «OpenAI» for compatibility).
				_("OpenAI"),
				status_message,
				parent=self,
				style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME,
			)
			self._taskProgressDialog.Pulse(status_message)
			self._taskProgressTimer = wx.Timer(self)
			self.Bind(wx.EVT_TIMER, self._onTaskProgressTimer, self._taskProgressTimer)
			self._taskProgressTimer.Start(250)
		except Exception:
			self._destroy_task_progress_dialog()

	def end_long_task(self, *, focus_ctrl=None):
		self._destroy_task_progress_dialog()
		if callable(self._taskBusySetter):
			self._taskBusySetter(False)
		self._taskBusySetter = None
		if self._taskCancelRequested:
			return False
		if focus_ctrl and self._isDialogAlive():
			try:
				focus_ctrl.SetFocus()
			except Exception:
				pass
		return True

	def is_task_cancel_requested(self) -> bool:
		return bool(self._taskCancelRequested)

	def _onTaskProgressTimer(self, evt):
		if not self._taskProgressDialog:
			return
		try:
			# Translators: Short status text repeatedly pulsed in the tool progress dialog while the background HTTP task is still running.
			keep_going, _ = self._taskProgressDialog.Pulse(_("Task in progress..."))
		except Exception:
			keep_going = True
		if not keep_going:
			self._request_task_cancel()

	def _request_task_cancel(self):
		if self._taskCancelRequested:
			return
		self._taskCancelRequested = True
		self._destroy_task_progress_dialog()
		if callable(self._taskBusySetter):
			self._taskBusySetter(False)
		self._taskBusySetter = None
		if self._isDialogAlive():
			wx.MessageBox(
				# Translators: Information message after the user stops a long tool task from the progress dialog; the tool window then closes.
				_("Cancellation requested. The dialog will now close."),
				"OpenAI",
				wx.OK | wx.ICON_INFORMATION,
			)
		self._markClosing()
		if self._isDialogAlive():
			self.Close()

	def _destroy_task_progress_dialog(self):
		timer = self._taskProgressTimer
		if timer is not None:
			try:
				timer.Stop()
			except Exception:
				pass
			self._taskProgressTimer = None
		dlg = self._taskProgressDialog
		if dlg is not None:
			try:
				dlg.Destroy()
			except Exception:
				pass
			self._taskProgressDialog = None

	def _onCharHook(self, evt):
		key = evt.GetKeyCode()
		if (
			key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
			and evt.ControlDown()
			and callable(self._ctrlEnterHandler)
		):
			self._ctrlEnterHandler(None)
			return
		evt.Skip()

	def build_account_choice(self, parent):
		labels = [acc.get("name", "Account") for acc in self._accounts]
		# Translators: Sole visible entry in the tool’s account drop-down when no accounts exist for this provider (control is disabled).
		choice = wx.Choice(parent, choices=labels or [_("No account configured")])
		if labels:
			active_id = self.manager.get_active_account_id()
			idx = 0
			for i, acc in enumerate(self._accounts):
				if acc.get("id") == active_id:
					idx = i
					break
			choice.SetSelection(idx)
		else:
			choice.SetSelection(0)
			choice.Disable()
		return choice

	def get_selected_account_id(self, choice_ctrl):
		if not self._accounts:
			return None
		idx = choice_ctrl.GetSelection()
		if idx < 0 or idx >= len(self._accounts):
			idx = 0
		return self._accounts[idx].get("id")

	def require_account(self, choice_ctrl):
		acc_id = self.get_selected_account_id(choice_ctrl)
		if not acc_id:
			# Translators: Error message when the user starts a tool action but no usable account id is selected (OK-only message box).
			wx.MessageBox(_("No account configured for this tool/provider."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return None
		return acc_id

	def configure_client(self, account_id):
		if self.client is None:
			# Translators: Internal error message when a tool tries to use the API client but the parent conversation has no active client (user should configure accounts).
			raise RuntimeError(_("No API client available. Configure at least one account first."))
		self.client = configure_client_for_provider(self.client, self.provider, account_id=account_id, clone=True)

	def suggest_open_audio(self, path):
		self.open_local_path(path, err_title="OpenAI")

	def append_prompt_text(self, text):
		if not text:
			return
		parent = self.parentDialog
		if not parent or not hasattr(parent, "promptTextCtrl"):
			return
		prev = parent.promptTextCtrl.GetValue().strip()
		parent.promptTextCtrl.SetValue((prev + "\n\n" + text) if prev else text)
		parent.promptTextCtrl.SetFocus()
		parent.promptTextCtrl.SetInsertionPointEnd()
		try:
			parent._autoSaveConversation()
		except Exception:
			pass

	def attach_audio_to_conversation(self, path):
		parent = self.parentDialog
		if not parent or not hasattr(parent, "audioPathList"):
			return
		if not getattr(parent, "audioPathList", None):
			parent.audioPathList = []
		if path not in parent.audioPathList:
			parent.audioPathList.append(path)
			parent.updateAudioList(focusPrompt=False)
		try:
			parent._autoSaveConversation()
		except Exception:
			pass

	def open_local_path(self, path: str, err_title: str = "OpenAI") -> bool:
		if not isinstance(path, str) or not path:
			return False
		if path.startswith("http://") or path.startswith("https://"):
			try:
				os.startfile(path)
				return True
			except Exception as err:
				# Translators: Error message when a tool tries to open an http(s) link in the browser and the OS reports a failure (placeholder is the system error).
				wx.MessageBox(_("Unable to open URL: %s") % err, err_title, wx.OK | wx.ICON_ERROR)
				return False
		if not os.path.exists(path):
			# Translators: Error message when a tool tries to open a local file path that no longer exists (placeholder is the full path).
			wx.MessageBox(_("File not found:\n%s") % path, err_title, wx.OK | wx.ICON_ERROR)
			return False
		try:
			os.startfile(path)
			return True
		except Exception as err:
			# Translators: Error message when a tool tries to open an existing local file and the OS shell reports a failure (placeholder is the system error).
			wx.MessageBox(_("Unable to open file: %s") % err, err_title, wx.OK | wx.ICON_ERROR)
			return False

	def save_tool_conversation(
		self,
		*,
		title: str,
		conversation_format: str,
		prompt: str = "",
		response_text: str = "",
		model: str = "",
		audio_paths=None,
		image_paths=None,
		format_data: dict | None = None,
	):
		"""Create a dedicated conversation entry for a tool output."""
		from . import conversations
		from .history import HistoryBlock
		block = HistoryBlock()
		block.prompt = prompt or ""
		block.responseText = response_text or ""
		block.model = model or ""
		block.system = ""
		block.filesList = list(image_paths or [])
		block.audioPathList = list(audio_paths or [])
		block.audioTranscriptList = []
		conversations.save_conversation(
			[block],
			system="",
			model=model or "",
			name=title,
			conv_id=None,
			draftPrompt="",
			draftPathList=[],
			draftAudioPathList=[],
			conversation_format=conversation_format,
			format_data=format_data or {},
		)
