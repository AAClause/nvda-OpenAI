"""Conversation dialog controls while no model is available (issue #142)."""

from AIHub.conversation_chrome.generation_panel import GenerationChromePanel
from AIHub.modelhandlers import ModelHandlersMixin


class FakeCtrl:
	"""Stands for any wx control; shown and enabled by default, like wx."""

	def __init__(self):
		self.shown = True
		self.enabled = True
		self.value = None

	def Show(self, show=True):
		self.shown = bool(show)

	def Hide(self):
		self.shown = False

	def IsShown(self):
		return self.shown

	def Enable(self, enable=True):
		self.enabled = bool(enable)

	def Disable(self):
		self.enabled = False

	def SetValue(self, value):
		self.value = value

	def __getattr__(self, name):
		# Layout(), GetParent()... are irrelevant here.
		return lambda *args, **kwargs: None


class FakeDialog(ModelHandlersMixin):
	"""The conversation dialog without wx: every control is a FakeCtrl."""

	def __init__(self):
		self._controls = {}
		self._models = []
		self.data = {}
		self.captured = False
		self._generation_chrome = GenerationChromePanel(self, FakeCtrl())
		# Sections that group their controls in a panel of their own.
		tools = self._generation_chrome._sections[2]
		for child in (tools._openrouter, tools._xai):
			child._panel = FakeCtrl()

	def __getattr__(self, name):
		if name.startswith("__"):
			raise AttributeError(name)
		return self._controls.setdefault(name, FakeCtrl())

	def getCurrentModel(self):
		return None

	def _effective_advanced_mode(self):
		return True

	def _update_advanced_controls_visibility(self):
		pass

	def _captureConversationChromeToPage(self, page):
		self.captured = True

	def Layout(self):
		pass


def test_no_model_hides_every_model_dependent_control():
	dlg = FakeDialog()
	dlg.onModelChange(None)
	# A control is reachable with Tab when it is both shown and enabled.
	reachable = sorted(name for name, ctrl in dlg._controls.items() if ctrl.shown and ctrl.enabled)
	assert reachable == []
	# Provider tool rows (xAI search, OpenRouter web search...) included.
	for name in ("xSearchCheckBox", "codeInterpreterCheckBox", "webSearchCheckBox", "openRouterWebSearchCheckBox",
		"verbosityRow", "maxTokensSpinCtrl", "reasoningModeChoice", "temperatureSpinCtrl"):
		assert not getattr(dlg, name).shown, name
	tools = dlg._generation_chrome._sections[2]
	assert not tools._openrouter._panel.shown
	assert not tools._xai._panel.shown


def test_no_model_does_not_overwrite_the_tab_saved_options():
	dlg = FakeDialog()
	dlg.onModelChange(None)
	assert dlg.captured is False
