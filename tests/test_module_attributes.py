"""Every `module.name` read in the add-on must exist in that module.

ruff only sees names inside one file: removing an import that looked unused
in apikeymanager broke `apikeymanager.CHAT_PROVIDERS` in five other modules.
This walks the add-on's source (without importing it) and checks each
attribute read on one of its own modules.
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


def _defined_names(parts, tree):
	"""Names a module defines at its top level, or through `global`."""
	names = {child[-1] for child in MODULES if child[:-1] == parts and child}

	def visit(nodes):
		for node in nodes:
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
				names.add(node.name)
				continue
			if isinstance(node, (ast.Import, ast.ImportFrom)):
				names.update((a.asname or a.name).split(".")[0] for a in node.names)
			for target in ast.walk(node):
				if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
					names.add(target.id)
			visit([n for n in ast.iter_child_nodes(node) if isinstance(n, ast.stmt)])

	visit(tree.body)
	for node in ast.walk(tree):
		if isinstance(node, ast.Global):
			names.update(node.names)
	return names


def _imported_modules(parts, tree, is_package):
	"""Local aliases bound to the add-on's own modules: {alias: module parts}."""
	package = parts if is_package else parts[:-1]
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


def test_attributes_read_on_addon_modules_exist():
	defined = {parts: _defined_names(parts, tree) for parts, tree in MODULES.items()}
	missing = []
	for parts, tree in MODULES.items():
		is_package = (PACKAGE_DIR.joinpath(*parts) / "__init__.py").exists()
		aliases = _imported_modules(parts, tree, is_package)
		for node in ast.walk(tree):
			if (
				isinstance(node, ast.Attribute)
				and isinstance(node.ctx, ast.Load)
				and isinstance(node.value, ast.Name)
				and node.value.id in aliases
			):
				target = aliases[node.value.id]
				if node.attr not in defined[target]:
					missing.append(f"{'.'.join(parts) or 'AIHub'}:{node.lineno}: {'.'.join(target)}.{node.attr}")
	assert missing == []
