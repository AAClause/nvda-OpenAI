"""Shared helpers to build model details text/HTML."""

from datetime import datetime
from html import escape
import locale
import re
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


_SLUG_TOKEN_LABELS = {
	"3d": "3D",
	"ascii": "ASCII",
	"html": "HTML",
	"svg": "SVG",
	"ui": "UI",
}
_SLUG_SPLIT_RE = re.compile(
	r"agentic|android|categories|category|native|slides|godot|website|component|"
	r"html|ascii|code|game|data|full|stack|web|apps|viz|art|dev|ui|svg|3d",
	re.I,
)


def _humanize_slug(value):
	raw = str(value or "").strip()
	if not raw:
		return ""
	raw = re.sub(r"[()]+", " ", raw)
	raw = raw.replace("_", " ").replace("-", " ")
	parts = []
	rest = raw
	while rest:
		if rest[0].isspace():
			rest = rest.lstrip()
			continue
		match = _SLUG_SPLIT_RE.match(rest)
		if match:
			token = match.group(0).lower()
			parts.append(_SLUG_TOKEN_LABELS.get(token, token))
			rest = rest[match.end():]
			continue
		next_split = _SLUG_SPLIT_RE.search(rest)
		chunk = rest[: next_split.start()] if next_split else rest
		chunk = chunk.strip()
		if chunk:
			parts.append(_SLUG_TOKEN_LABELS.get(chunk.lower(), chunk))
		rest = rest[next_split.start():] if next_split else ""
	if not parts:
		return raw
	out = []
	for part in parts:
		if part in _SLUG_TOKEN_LABELS.values() or part.isupper():
			out.append(part)
		else:
			out.append(part[:1].upper() + part[1:] if part else part)
	return " ".join(out)


def _aa_index_label(key):
	labels = {
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		"intelligence_index": _("Intelligence index"),
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		"coding_index": _("Coding index"),
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		"agentic_index": _("Agentic index"),
	}
	return labels.get(key, _humanize_slug(key) or key)


def _arena_type_label(arena):
	key = str(arena or "").strip().lower()
	if key == "agents":
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		return _("Agents")
	if key == "models":
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		return _("Models")
	return _humanize_slug(arena) or str(arena)


def _format_percent(value):
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	return "%s%%" % _format_number(number, 1)


def _append_design_arena(parts, rows):
	if not isinstance(rows, list) or not rows:
		return False
	grouped = {}
	for row in rows:
		if not isinstance(row, dict):
			continue
		arena = str(row.get("arena") or "").strip().lower() or "_"
		grouped.setdefault(arena, []).append(row)
	if not grouped:
		return False
	added = False
	arena_order = sorted(
		grouped,
		key=lambda name: (0 if name == "agents" else 1 if name == "models" else 2, name),
	)
	for arena in arena_order:
		entries = grouped[arena]
		entries.sort(key=lambda row: (row.get("rank") is None, row.get("rank") or 0, str(row.get("category") or "")))
		if arena == "_":
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			heading = _("Design Arena")
		else:
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report. {arena} is Agents or Models.
			heading = _("Design Arena ({arena})").format(arena=_arena_type_label(arena))
		parts.append("<h3>%s</h3>" % escape(heading))
		parts.append("<ul>")
		for row in entries:
			category = _humanize_slug(row.get("category")) or str(row.get("category") or "").strip()
			rank = row.get("rank")
			elo = row.get("elo")
			win = _format_percent(row.get("win_rate"))
			bits = []
			if isinstance(rank, (int, float)):
				# Translators: AI-Hub model details (browseable HTML): rank of a model on a design-arena leaderboard.
				bits.append(_("rank %s") % _format_number(int(rank)))
			if isinstance(elo, (int, float)):
				bits.append("Elo %s" % _format_number(int(elo)))
			if win:
				# Translators: AI-Hub model details (browseable HTML): win rate on a design-arena leaderboard.
				bits.append(_("win rate %s") % win)
			detail = ", ".join(bits)
			if not category and not detail:
				continue
			if not category:
				# Translators: AI-Hub model details (browseable HTML): fallback label when a design-arena row has no category name.
				category = _("Category")
			if detail:
				parts.append(_li(category, detail))
			else:
				parts.append("<li>%s</li>" % escape(category))
		parts.append("</ul>")
		added = True
	return added


def _append_benchmarks_section(parts, benchmarks):
	if not isinstance(benchmarks, dict):
		return
	aa = benchmarks.get("artificial_analysis")
	aa_items = []
	if isinstance(aa, dict):
		for key, value in aa.items():
			if isinstance(value, bool) or not isinstance(value, (int, float)):
				formatted = _format_value(value)
			else:
				formatted = _format_number(value, 1 if isinstance(value, float) else None)
			if formatted is None:
				continue
			aa_items.append((key, formatted))
	arena_rows = benchmarks.get("design_arena")
	has_arena = isinstance(arena_rows, list) and any(isinstance(row, dict) for row in arena_rows)
	if not aa_items and not has_arena:
		return
	parts.extend([
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		"<h2>%s</h2>" % escape(_("Benchmarks")),
	])
	if aa_items:
		parts.extend([
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			"<h3>%s</h3>" % escape(_("Artificial Analysis")),
			"<ul>",
		])
		for key, formatted in aa_items:
			parts.append(_li(_aa_index_label(key), formatted))
		parts.append("</ul>")
	_append_design_arena(parts, arena_rows)


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
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Reasoning"))
	if model.supports_web_search:
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Web search"))
	if getattr(model, "supports_x_search", False):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("X search"))
	if getattr(model, "supports_code_interpreter", False):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Code interpreter"))
	if getattr(model, "supports_collections_search", False):
		# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
		capabilities.append(_("Collections search"))
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
	])
	if getattr(model, "reasoning", False):
		if getattr(model, "reasoning_always_on", False):
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			_append_item(parts, _("Reasoning"), _("Required"))
		else:
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			_append_item(parts, _("Reasoning"), _("Optional"))
		effort_opts = list(getattr(model, "reasoning_effort_options", ()) or ())
		if effort_opts:
			# Translators: AI-Hub model details (browseable HTML): label, section heading, capability tag, or table cell in the generated report.
			_append_item(parts, _("Reasoning efforts"), ", ".join(label for _value, label in effort_opts))
	parts.append("</ul>")

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

	benchmarks = model.extraInfo.get("benchmarks") if isinstance(model.extraInfo, dict) else None
	_append_benchmarks_section(parts, benchmarks)

	extra = model.extraInfo if isinstance(model.extraInfo, dict) else {}
	if extra:
		extra = dict(extra)
		for key in ("pricing", "created", "supported_parameters", "reasoning", "benchmarks"):
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
