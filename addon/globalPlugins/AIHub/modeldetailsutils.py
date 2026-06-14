"""Shared helpers to build model details text/HTML."""

from datetime import datetime
from html import escape
import locale
import addonHandler

addonHandler.initTranslation()


def _parse_price(value):
	if isinstance(value, (int, float)):
		return float(value)
	if not isinstance(value, str):
		return None
	try:
		return float(value.strip())
	except ValueError:
		return None


def _format_price_per_million(value):
	price = _parse_price(value)
	if price is None:
		return None
	return f"${_format_number(price * 1000000, 3)}/Mtok"


def _format_number(value, decimals=None):
	"""Format numbers using the currently active locale."""
	try:
		if decimals is not None:
			return locale.format_string(f"%.{decimals}f", float(value), grouping=True)
		if isinstance(value, int):
			return locale.format_string("%d", value, grouping=True)
		return locale.format_string("%g", float(value), grouping=True)
	except Exception:
		if decimals is not None:
			return f"{float(value):.{decimals}f}"
		return str(value)


def _li(label, value):
	return f"<li><strong>{escape(str(label))}:</strong> {escape(str(value))}</li>"


def _clean_value(value):
	"""Remove None/empty values recursively for cleaner display."""
	if value is None:
		return None
	if isinstance(value, dict):
		cleaned = {}
		for key, item in value.items():
			cleaned_item = _clean_value(item)
			if cleaned_item is not None:
				cleaned[key] = cleaned_item
		return cleaned or None
	if isinstance(value, (list, tuple, set)):
		cleaned = [_clean_value(item) for item in value]
		cleaned = [item for item in cleaned if item is not None]
		return cleaned or None
	return value


def _format_datetime(value):
	"""Format epoch timestamp as local date/time using active locale."""
	try:
		ts = int(value)
		if ts <= 0:
			return None
		return datetime.fromtimestamp(ts).strftime("%x %X")
	except Exception:
		return None


def _format_value(value):
	"""Format values for user-facing model details."""
	value = _clean_value(value)
	if value is None:
		return None
	if isinstance(value, bool):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		return _("Yes") if value else _("No")
	if isinstance(value, int):
		return _format_number(value)
	if isinstance(value, float):
		return _format_number(value)
	if isinstance(value, list):
		return ", ".join(str(_format_value(item)) for item in value)
	if isinstance(value, dict):
		items = []
		for key, item in value.items():
			formatted_item = _format_value(item)
			if formatted_item is None:
				continue
			items.append(f"{key}: {formatted_item}")
		return "; ".join(items) if items else None
	return str(value)


def _append_item(parts, label, value):
	"""Append one list item when value is meaningful."""
	formatted = _format_value(value)
	if formatted is not None:
		parts.append(_li(label, formatted))


def build_model_details_html(model):
	"""Build user-facing model details HTML for browseable message."""
	# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
	unknown = _("unknown")
	max_output = _format_number(model.maxOutputToken) if model.maxOutputToken > 0 else unknown
	created = _format_datetime(getattr(model, "created", 0))

	parts = [
		f"<h1>{escape(str(model.name))}</h1>",
		f"<p><strong>{escape(str(model.id))}</strong></p>",
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		"<h2>%s</h2>" % escape(_("Overview")),
		"<ul>",
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		_li(_("Provider"), model.provider),
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		_li(_("Context window"), _("%s tokens") % _format_number(model.contextWindow)),
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		_li(_("Max output tokens"), max_output),
	]
	# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
	_append_item(parts, _("Created"), created)
	# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
	_append_item(parts, _("Max temperature"), model.maxTemperature)
	# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
	_append_item(parts, _("Default temperature"), model.defaultTemperature)
	parts.append("</ul>")

	# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
	capabilities = [_("Text")]
	if model.vision:
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Image input"))
	if getattr(model, "audioInput", False):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Audio input"))
	if getattr(model, "audioOutput", False):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Audio output"))
	if model.reasoning:
		if getattr(model, "reasoning_always_on", False):
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			capabilities.append(_("Reasoning (required)"))
		else:
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			capabilities.append(_("Reasoning"))
	if model.supports_web_search:
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Web search"))
	if getattr(model, "supports_openrouter_web_search", False):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("OpenRouter web search"))

	parts.extend([
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		"<h2>%s</h2>" % escape(_("Capabilities and parameters")),
		"<ul>",
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		_li(_("Capabilities"), ", ".join(capabilities)),
		_li(
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			_("Supported parameters"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			", ".join(model.supportedParameters) if model.supportedParameters else _("none")
		),
		"</ul>",
	])

	if model.description:
		parts.extend([
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"<h2>%s</h2>" % escape(_("Description")),
			f"<p>{escape(str(model.description))}</p>",
		])

	pricing = model.extraInfo.get("pricing", {}) if isinstance(model.extraInfo, dict) else {}
	if isinstance(pricing, dict) and pricing:
		parts.extend([
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"<h2>%s</h2>" % escape(_("Pricing")),
			"<ul>",
		])
		price_labels = {
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"prompt": _("Input tokens"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"completion": _("Output tokens"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"input_cache_read": _("Input cache read"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"input_cache_write": _("Input cache write"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"audio": _("Audio tokens"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"image": _("Image"),
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"request": _("Request"),
		}
		for key, value in pricing.items():
			label = price_labels.get(key, key)
			if key == "request":
				price = _parse_price(value)
				if price is not None:
					parts.append(_li(label, f"${_format_number(price, 6)}/request"))
				else:
					parts.append(_li(label, value))
			else:
				per_m = _format_price_per_million(value)
				if per_m:
					parts.append(_li(label, per_m))
				else:
					parts.append(_li(label, value))
		parts.append("</ul>")

	extra = model.extraInfo if isinstance(model.extraInfo, dict) else {}
	if extra:
		extra = dict(extra)
		for key in ("pricing", "created", "supported_parameters"):
			extra.pop(key, None)
		extra = _clean_value(extra) or {}
	if extra:
		parts.extend([
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"<h2>%s</h2>" % escape(_("Additional information")),
			"<ul>",
		])
		for key, value in extra.items():
			_append_item(parts, key, value)
		parts.append("</ul>")

	return "".join(parts)
