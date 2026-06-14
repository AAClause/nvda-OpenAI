"""Base classes for provider-scoped conversation chrome sections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import wx

from ..consts import UI_SECTION_SPACING_PX


class ChromeSection(ABC):
	"""One logical group of generation controls (reasoning, tools, …)."""

	def __init__(self, dialog, parent: wx.Window):
		self.dialog = dialog
		self.parent = parent
		self._preserve: list[wx.Window] = []

	def _edited(self, evt):
		self.dialog._onConversationChromeEdited(evt)

	def _bind_preserve(self, ctrl: Optional[wx.Window]) -> None:
		if ctrl is not None:
			self._preserve.append(ctrl)

	def preserve_controls(self) -> list[wx.Window]:
		return list(self._preserve)

	@abstractmethod
	def build(self, sizer: wx.Sizer) -> None:
		...

	def update_for_model(self, model) -> None:
		pass

	def capture(self, st: dict, model) -> None:
		pass

	def apply(self, st: dict, model) -> None:
		pass

	def disable(self) -> None:
		for ctrl in self._preserve:
			try:
				ctrl.Disable()
			except Exception:
				pass


class ProviderToolsSection(ChromeSection):
	"""Base for provider-specific tool toggles under the Tools group."""

	provider_id: str = ""

	def supports(self, model) -> bool:
		if not model:
			return False
		return getattr(model, "provider", None) == self.provider_id
