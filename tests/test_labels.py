"""Every input field gets the label created just before it.

Screen readers name a Windows control after the static text that precedes it
in creation order (z-order), not after what is next to it on screen. A field
created before its label, or after another field, is read with the wrong
label (or none). This reads the add-on's source, without creating any window.
"""

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "addon" / "globalPlugins" / "AIHub"

# Fields NVDA names from the preceding static text (buttons and check boxes carry their own label).
NEEDS_LABEL = {"TextCtrl", "SpinCtrl", "SpinCtrlDouble", "Choice", "ComboBox", "ListBox", "ListCtrl", "CheckListBox", "Slider"}
# Helpers that create the label, then call a factory for the field.
LABEL_FIRST_HELPERS = {"add_labeled_factory", "_labeled_control_row"}
# Not windows, or windows that never label the next field.
NOT_WINDOWS = {
	"BoxSizer", "StaticBoxSizer", "GridBagSizer", "FlexGridSizer", "GridSizer", "WrapSizer", "StdDialogButtonSizer",
	"SizerFlags", "Menu", "Timer", "Font", "Colour", "Size", "Point", "AcceleratorTable", "AcceleratorEntry",
	"FileDialog", "DirDialog", "MessageDialog", "TextEntryDialog", "SingleChoiceDialog", "ProgressDialog",
	"Bitmap", "Image", "Icon", "Cursor", "ImageList", "ClientDC", "TextDataObject", "ListItem",
}


def _created(node):
	"""Kind of window (or label-first helper) a call creates, else None."""
	if not isinstance(node, ast.Call):
		return None
	func = node.func
	if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "wx":
		if func.attr[:1].isupper() and func.attr not in NOT_WINDOWS:
			return func.attr
	name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
	if name in LABEL_FIRST_HELPERS or name == "bind_text":
		return "TextCtrl" if name == "bind_text" else name
	return None


def test_fields_are_created_right_after_their_label():
	wrong = []
	for path in sorted(PACKAGE_DIR.rglob("*.py")):
		if "libs" in path.relative_to(PACKAGE_DIR).parts:
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for fn in ast.walk(tree):
			if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
				continue
			calls = sorted((n.lineno, n.col_offset, kind, n) for n in ast.walk(fn) if (kind := _created(n)))
			for i, (line, _col, kind, node) in enumerate(calls):
				if kind not in NEEDS_LABEL:
					continue
				if i == 0:
					# A builder (bind_text, build_account_choice...): its caller labels it.
					continue
				previous, previous_node = calls[i - 1][2], calls[i - 1][3]
				if previous != "StaticText" and previous not in LABEL_FIRST_HELPERS:
					wrong.append(f"{path.relative_to(PACKAGE_DIR)}:{line}: {kind} created after a {previous}")
				elif previous == "StaticText" and node.args and previous_node.args and ast.unparse(node.args[0]) != ast.unparse(previous_node.args[0]):
					# Different parents are not siblings: the label does not name the field.
					wrong.append(f"{path.relative_to(PACKAGE_DIR)}:{line}: {kind} and its label have different parents")
	assert wrong == []


def test_label_helpers_build_the_field_after_the_label():
	"""Passing an already built field to a label helper creates the label after it."""
	wrong = []
	for path in sorted(PACKAGE_DIR.rglob("*.py")):
		if "libs" in path.relative_to(PACKAGE_DIR).parts:
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"))
		functions = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
		for node in ast.walk(tree):
			if _created(node) not in LABEL_FIRST_HELPERS:
				continue
			factory = node.args[3] if len(node.args) > 3 else None
			if not (isinstance(factory, ast.Lambda) or (isinstance(factory, ast.Name) and factory.id in functions)):
				wrong.append(f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}: {ast.unparse(node.func)} without a factory")
	assert wrong == []
