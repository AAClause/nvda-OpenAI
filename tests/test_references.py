"""References the linter cannot check: names read through modules, by string.

`module.name` in the add-on must read a name that module defines itself.

ruff checks names one file at a time: when apikeymanager stopped importing
CHAT_PROVIDERS, which it did not use, the five modules that read
`apikeymanager.CHAT_PROVIDERS` broke. Names are therefore imported from the
module that defines them, never read through another module that merely
imports them; only a package's __init__ may re-export (its public API).
This walks the add-on's source, without importing it, and enforces that.
"""

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "addon" / "globalPlugins" / "AIHub"


def _module_files():
	for path in PACKAGE_DIR.rglob("*.py"):
		rel = path.relative_to(PACKAGE_DIR)
		if rel.parts[0] == "libs":
			continue
		parts = rel.with_suffix("").parts
		if parts[-1] == "__init__":
			parts = parts[:-1]
		yield parts, path


MODULES = {parts: ast.parse(path.read_text(encoding="utf-8")) for parts, path in _module_files()}


def _is_package(parts):
	return (PACKAGE_DIR.joinpath(*parts) / "__init__.py").exists()


def _defined_names(parts, tree):
	"""Names a module defines at its top level, or through `global`.

	Imported names only count for a package's __init__.
	"""
	names = {child[-1] for child in MODULES if child[:-1] == parts and child}
	reexports = _is_package(parts)

	def visit(nodes):
		for node in nodes:
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
				names.add(node.name)
				continue
			if isinstance(node, (ast.Import, ast.ImportFrom)):
				if reexports:
					names.update((a.asname or a.name).split(".")[0] for a in node.names)
				continue
			for target in ast.walk(node):
				if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
					names.add(target.id)
			visit([n for n in ast.iter_child_nodes(node) if isinstance(n, ast.stmt)])

	visit(tree.body)
	for node in ast.walk(tree):
		if isinstance(node, ast.Global):
			names.update(node.names)
	return names


def _imported_modules(parts, tree):
	"""Local aliases bound to the add-on's own modules: {alias: module parts}."""
	package = parts if _is_package(parts) else parts[:-1]
	aliases = {}
	for node in ast.walk(tree):
		if not isinstance(node, ast.ImportFrom) or not node.level:
			continue
		base = package[: len(package) - (node.level - 1)]
		if node.module:
			base = base + tuple(node.module.split("."))
		for alias in node.names:
			target = base + (alias.name,)
			if target in MODULES:
				aliases[alias.asname or alias.name] = target
	return aliases


def test_module_attributes_are_defined_where_they_are_read():
	defined = {parts: _defined_names(parts, tree) for parts, tree in MODULES.items()}
	wrong = []
	for parts, tree in MODULES.items():
		aliases = _imported_modules(parts, tree)
		for node in ast.walk(tree):
			if (
				isinstance(node, ast.Attribute)
				and isinstance(node.ctx, ast.Load)
				and isinstance(node.value, ast.Name)
				and node.value.id in aliases
			):
				target = aliases[node.value.id]
				if node.attr not in defined[target]:
					wrong.append(f"{'.'.join(parts) or 'AIHub'}:{node.lineno}: {'.'.join(target)}.{node.attr}")
	assert wrong == []



def test_names_are_imported_from_the_module_that_defines_them():
	"""`from .module import name` breaks if module only imported `name` itself."""
	defined = {parts: _defined_names(parts, tree) for parts, tree in MODULES.items()}
	wrong = []
	for parts, tree in MODULES.items():
		package = parts if _is_package(parts) else parts[:-1]
		for node in ast.walk(tree):
			if not isinstance(node, ast.ImportFrom) or not node.level:
				continue
			base = package[: len(package) - (node.level - 1)]
			if node.module:
				base = base + tuple(node.module.split("."))
			if base not in MODULES:
				continue
			for alias in node.names:
				if base + (alias.name,) not in MODULES and alias.name not in defined[base]:
					wrong.append(f"{'.'.join(parts) or 'AIHub'}:{node.lineno}: from {'.'.join(base)} import {alias.name}")
	assert wrong == []

def test_model_attributes_read_with_getattr_exist():
	"""`getattr(model, "name", default)` silently returns the default on a typo."""
	from AIHub.model import Model

	known = set(dir(Model))
	for node in ast.walk(MODULES[("model",)]):
		if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
			known.add(node.attr)
	wrong = []
	for parts, tree in MODULES.items():
		for node in ast.walk(tree):
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "getattr"
				and len(node.args) >= 2
				and isinstance(node.args[0], ast.Name)
				and node.args[0].id in ("model", "model_obj")
				and isinstance(node.args[1], ast.Constant)
				and node.args[1].value not in known
			):
				wrong.append(f"{'.'.join(parts)}:{node.lineno}: model.{node.args[1].value}")
	assert wrong == []


def test_every_tool_dialog_resolves():
	"""Tool dialogs are loaded by module and class name on first use."""
	from AIHub.toolsmenu import TOOLS_REGISTRY, _resolve_dialog_cls

	for tool in TOOLS_REGISTRY:
		cls = _resolve_dialog_cls(dict(tool))
		assert cls.__name__ == tool["dialog_class"], tool["id"]


def test_config_keys_exist_in_the_spec():
	"""A misspelled config key reads the default and writes a key nobody reads."""
	from AIHub.configspec import confSpecs

	sections = {name for name, spec in confSpecs.items() if isinstance(spec, dict)}

	def section_of(receiver):
		if isinstance(receiver, ast.Subscript) and isinstance(receiver.slice, ast.Constant) and receiver.slice.value in sections:
			return receiver.slice.value
		if isinstance(receiver, ast.Name) and receiver.id == "audio_conf":
			return "audio"
		return None

	wrong = []
	for parts, tree in MODULES.items():
		for node in ast.walk(tree):
			if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
				receiver, key = node.value, node.slice.value
			elif (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "get"
				and node.args
				and isinstance(node.args[0], ast.Constant)
			):
				receiver, key = node.func.value, node.args[0].value
			else:
				continue
			section = section_of(receiver)
			if section and key not in confSpecs[section]:
				wrong.append(f"{'.'.join(parts)}:{node.lineno}: [{section!r}][{key!r}]")
	assert wrong == []
