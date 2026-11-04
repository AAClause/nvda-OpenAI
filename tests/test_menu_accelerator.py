"""Tests verifying the AI Hub submenu accelerator key in the NVDA menu.

Ensures that the AI Hub submenu accelerator key is set to 'A' (&AI Hub)
and does not conflict with NVDA's built-in Help menu accelerator ('H').
"""

import ast
import importlib.util
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import MagicMock

import enum
if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        @staticmethod
        def _generate_next_value_(name, start, count, last_values):
            return name.lower()
        def __str__(self):
            return str(self.value)
    enum.StrEnum = StrEnum

try:
    import conftest  # noqa: F401
except ImportError:
    from . import conftest  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MIXINS_PATH = REPO_ROOT / "addon" / "globalPlugins" / "AIHub" / "plugin_mixins.py"
LOCALE_DIR = REPO_ROOT / "addon" / "locale"

# NVDA top-level menu accelerators
STANDARD_NVDA_ACCELERATORS = {
    "H": "Help",
    "P": "Preferences",
    "T": "Tools",
    "X": "Exit",
}


def extract_accelerator(label: str) -> str:
    """Extract accelerator letter indicated by ampersand prefix (e.g. '&AI Hub' -> 'A')."""
    match = re.search(r"&([A-Za-z0-9])", label)
    return match.group(1).upper() if match else ""


class TestMenuAcceleratorAST(unittest.TestCase):
    """Verify submenu accelerator in source AST of plugin_mixins.py."""

    def setUp(self):
        self.assertTrue(PLUGIN_MIXINS_PATH.is_file(), f"File not found: {PLUGIN_MIXINS_PATH}")
        self.tree = ast.parse(PLUGIN_MIXINS_PATH.read_text(encoding="utf-8"))

    def test_insert_menu_uses_ampersand_a(self):
        """Verify InsertMenu call formats '&AI Hub {addon_version}' instead of 'AI &Hub'."""
        menu_templates = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "InsertMenu":
                for arg in node.args:
                    # Pattern 1: _("&AI Hub ...".format(...))
                    if isinstance(arg, ast.Call) and getattr(arg.func, "id", None) == "_":
                        if arg.args:
                            inner = arg.args[0]
                            if isinstance(inner, ast.Call) and getattr(inner.func, "attr", None) == "format":
                                val = inner.func.value
                                if isinstance(val, ast.Constant):
                                    menu_templates.append(val.value)
                            elif isinstance(inner, ast.Constant):
                                menu_templates.append(inner.value)
                    # Pattern 2: _("&AI Hub ...").format(...)
                    elif isinstance(arg, ast.Call) and getattr(arg.func, "attr", None) == "format":
                        callee = arg.func.value
                        if isinstance(callee, ast.Call) and getattr(callee.func, "id", None) == "_":
                            if callee.args and isinstance(callee.args[0], ast.Constant):
                                menu_templates.append(callee.args[0].value)

        self.assertEqual(len(menu_templates), 1, "Expected exactly one InsertMenu call with format template")
        template = menu_templates[0]

        # Accelerator must be 'A'
        accelerator = extract_accelerator(template)
        self.assertEqual(accelerator, "A", f"Expected accelerator 'A' in '{template}', got '{accelerator}'")
        self.assertNotEqual(accelerator, "H", "Accelerator 'H' conflicts with NVDA Help menu shortcut")

        # Must not conflict with standard NVDA top-level accelerators
        self.assertNotIn(
            accelerator,
            STANDARD_NVDA_ACCELERATORS,
            f"Accelerator '{accelerator}' conflicts with NVDA standard menu: {STANDARD_NVDA_ACCELERATORS.get(accelerator)}"
        )

        # Template must start with '&AI Hub'
        self.assertTrue(
            template.startswith("&AI Hub"),
            f"Expected template to start with '&AI Hub', got '{template}'"
        )


class TestMenuAcceleratorExecution(unittest.TestCase):
    """Verify runtime behavior of createMenu with mocked NVDA modules."""

    def test_create_menu_accelerator_runtime(self):
        mock_modules = [
            "addonHandler", "api", "config", "gui", "ui", "wx", "logHandler",
        ]
        saved_modules = {mod: sys.modules.get(mod) for mod in mock_modules}

        try:
            for mod in mock_modules:
                sys.modules[mod] = MagicMock()

            # Mock AIHub submodules needed for relative imports in plugin_mixins
            aihub_mock = MagicMock()
            sys.modules["AIHub"] = aihub_mock
            sys.modules["AIHub.apikeymanager"] = MagicMock()
            consts_mock = MagicMock()
            consts_mock.ADDON_DIR = str(PLUGIN_MIXINS_PATH.parent)
            consts_mock.TEMP_DIR = "/tmp/aihub_test"
            sys.modules["AIHub.consts"] = consts_mock
            sys.modules["AIHub.thread_shutdown"] = MagicMock()
            sys.modules["AIHub.toolsmenu"] = MagicMock()

            import builtins
            orig_gettext = getattr(builtins, "_", None)
            builtins._ = lambda s: s

            spec = importlib.util.spec_from_file_location("AIHub.plugin_mixins", str(PLUGIN_MIXINS_PATH))
            mixins_mod = importlib.util.module_from_spec(spec)
            sys.modules["AIHub.plugin_mixins"] = mixins_mod
            mixins_mod.ADDON_INFO = {"version": "2024.1"}
            spec.loader.exec_module(mixins_mod)

            class DummyPlugin(mixins_mod.MenuMixin, MagicMock):
                pass

            plugin = DummyPlugin()
            plugin.createMenu()

            tray_menu = sys.modules["gui"].mainFrame.sysTrayIcon
            self.assertTrue(tray_menu.menu.InsertMenu.called, "InsertMenu was not called")

            call_args = tray_menu.menu.InsertMenu.call_args[0]
            menu_title = call_args[2]

            self.assertTrue(isinstance(menu_title, str), f"Menu title should be string, got {type(menu_title)}")
            accelerator = extract_accelerator(menu_title)
            self.assertEqual(accelerator, "A", f"Runtime accelerator must be 'A', got '{accelerator}'")
            self.assertNotEqual(accelerator, "H", "Runtime accelerator conflicts with Help menu shortcut")

        finally:
            for mod, orig in saved_modules.items():
                if orig is not None:
                    sys.modules[mod] = orig
                else:
                    sys.modules.pop(mod, None)
            for m in list(sys.modules.keys()):
                if m.startswith("AIHub"):
                    sys.modules.pop(m, None)
            if orig_gettext is not None:
                builtins._ = orig_gettext


class TestMenuAcceleratorLocalization(unittest.TestCase):
    """Verify localization files use &AI Hub accelerator."""

    def _check_po_file(self, po_path: Path):
        content = po_path.read_text(encoding="utf-8")
        self.assertNotIn(
            'msgid "AI &Hub',
            content,
            f"Found obsolete 'msgid \"AI &Hub\"' in {po_path}"
        )
        self.assertNotIn(
            'msgstr "AI &Hub',
            content,
            f"Found obsolete 'msgstr \"AI &Hub\"' in {po_path}"
        )

        # Must have updated string
        self.assertIn(
            'msgid "&AI Hub',
            content,
            f"Missing 'msgid \"&AI Hub\"' in {po_path}"
        )
        self.assertIn(
            'msgstr "&AI Hub',
            content,
            f"Missing 'msgstr \"&AI Hub\"' in {po_path}"
        )

    def test_ukrainian_translation(self):
        po_path = LOCALE_DIR / "uk" / "LC_MESSAGES" / "nvda.po"
        self._check_po_file(po_path)

    def test_french_translation(self):
        po_path = LOCALE_DIR / "fr" / "LC_MESSAGES" / "nvda.po"
        self._check_po_file(po_path)


if __name__ == "__main__":
    unittest.main()
