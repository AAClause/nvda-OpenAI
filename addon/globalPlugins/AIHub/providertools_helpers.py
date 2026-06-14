"""Helpers for provider tools dialogs."""

import wx

from .consts import UI_FORM_ROW_BORDER_PX


def safe_int(value, default=None):
	try:
		return int(str(value).strip())
	except Exception:
		return default


def safe_float(value, default=None):
	try:
		return float(str(value).strip())
	except Exception:
		return default


def extract_audio_b64(data):
	"""Best effort extractor for base64 audio payload."""
	if isinstance(data, dict):
		for key in ("audio", "audioBytes", "audio_bytes", "data", "inlineData"):
			value = data.get(key)
			if isinstance(value, str) and len(value) > 128:
				return value
			if isinstance(value, dict):
				found = extract_audio_b64(value)
				if found:
					return found
		for value in data.values():
			found = extract_audio_b64(value)
			if found:
				return found
	elif isinstance(data, list):
		for item in data:
			found = extract_audio_b64(item)
			if found:
				return found
	return None


def extract_ocr_text(payload):
	if not isinstance(payload, dict):
		return ""
	lines = []
	pages = payload.get("pages")
	if isinstance(pages, list):
		for page in pages:
			if not isinstance(page, dict):
				continue
			markdown = page.get("markdown")
			text = page.get("text")
			if isinstance(markdown, str) and markdown.strip():
				lines.append(markdown.strip())
			elif isinstance(text, str) and text.strip():
				lines.append(text.strip())
	if not lines:
		for key in ("markdown", "text", "content"):
			val = payload.get(key)
			if isinstance(val, str) and val.strip():
				lines.append(val.strip())
				break
	return "\n\n".join(lines).strip()


def add_labeled_factory(parent, sizer, label_text, control_factory, border=UI_FORM_ROW_BORDER_PX, expand=True):
	"""
	Create label first, then control, then add to sizer.
	Useful when SR association depends on creation order.
	"""
	label = wx.StaticText(parent, label=label_text)
	sizer.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, border)
	control = control_factory()
	flags = wx.LEFT | wx.RIGHT | wx.BOTTOM
	if expand:
		flags |= wx.EXPAND
	proportion = 1 if expand and isinstance(control, wx.TextCtrl) and control.IsMultiLine() else 0
	sizer.Add(control, proportion, flags, border)
	return control
