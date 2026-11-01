# AGENTS.md

AI-Hub (internal add-on name `OpenAI`) is an NVDA add-on that connects the screen reader to several AI providers. Its users are blind or visually impaired: every feature must be fully usable with the keyboard and a screen reader.

## Language

- Code, identifiers, comments, docstrings, log messages, user-facing strings, commit messages and PR descriptions are always in **English**, whatever language the conversation is in.
- Translations (`addon/locale/<lang>/LC_MESSAGES/nvda.po`, `addon/doc/<lang>/readme.md`) are maintained by translators: do not edit them unless explicitly asked.

## Tooling

The development environment is managed with **uv** and the checks with **prek** (not pip, not pre-commit).

- `uv sync` — create or update `.venv` from `pyproject.toml` and `uv.lock`.
- `uv run pytest` — unit tests; CI also runs them on Python 3.13 (`uv run --python 3.13 pytest`).
- `uv run prek run --all-files` — code checks (`prek.toml`), including ruff with bug-finding rules only (`[tool.ruff]` in `pyproject.toml`). Fix what ruff reports; `# noqa: <code>` only for intentional cases, with the reason.
- `uv run scons` — build the `.nvda-addon` package; `uv run scons pot` — translation template.

Tests and checks must pass before committing. Add tools with `uv add --group <test|build|lint> <package>` and commit `uv.lock`; never `pip install` or a `requirements.txt`.

## Runtime constraints

- The code runs inside NVDA's own Python: 3.11 32-bit (NVDA 2024.1 to 2025.x, the minimum set in `buildVars.py`) or 3.13 64-bit (NVDA 2026.1+). It must work on both, and only on Windows.
- No third-party runtime dependency can be installed. The only ones available are vendored by `sconstruct` into `addon/globalPlugins/AIHub/libs/` (markdown-it-py, mdurl, Pillow). To bump or add one, edit `_VENDORED_WHEELS` in `sconstruct` and the list in `readme.md`.
- Never block the UI thread: run network and disk work with `background.start_background`, return to the UI with `background.call_after_if_alive`, and stop workers with `thread_shutdown.stop_worker_thread`.

## Security

- All HTTP goes through the helpers in `apiclient/_http.py` (`http_request_json`, `_open_json`, `_open_streaming`...), whose opener drops credentials on cross-origin redirects. Never call `urllib.request.urlopen` or build another opener for authenticated requests.
- Validate user-supplied URLs with `url_safety.py` before fetching them.
- Never log or display API keys, and never send one provider's key to another endpoint (e.g. the OpenAI key to a custom OpenAI-compatible server).

## Code style

- Indent with **tabs** (spaces only inside the vendored libraries).
- Follow the surrounding code: NVDA/wx names stay camelCase (`onCancel`, `getCurrentModel`, `scriptCategory`), new helpers are snake_case. Keep comments short and only where the code is not self-explanatory.
- Every user-facing string goes through `_()` and is preceded by a `# Translators:` comment that tells where it appears, in the style already used, for example `# Translators: NVDA Preferences — AI-Hub category: ...`. Use named placeholders with `.format()` (`_("Remove account {name}?").format(name=name)`), never f-strings or concatenation inside `_()`.
- `_` is NVDA's gettext function: in a function that calls `_()`, never assign `_` as a throwaway variable (`x, _ = ...`, `for _ in ...`). Use another name such as `_unused`.
- Import a name from the module that defines it (`from .consts import CHAT_PROVIDERS`), never through another module that merely imports it (`apikeymanager.CHAT_PROVIDERS`): ruff removes imports that look unused in their own file. `tests/test_references.py` enforces this, along with the other references by name (`getattr(model, "...")`, the tools registry).
- A new subpackage with translatable strings must be added to `pythonSources` in `buildVars.py`, or its strings never reach the `.pot`.
- Settings belong in `configspec.py` (`config.conf["AIHub"]`), one spelling per key; renaming a key needs a migration of saved settings. Provider identifiers, models and defaults belong in `consts.py`.
- Label every input field (text, spin, choice, combo box, list) with a `wx.StaticText` created **immediately before** it, with the same parent. Screen readers take a field's name from the static text that precedes it in creation order (Windows z-order), not from the sizer layout: a field built before its label, or after another field, is announced with the wrong label. `MoveAfterInTabOrder()` and `SetName()` do not fix that. Use the label-first helpers, which take a factory for the field: NVDA's `guiHelper.BoxSizerHelper.addLabeledControl` in settings panels, `providertools_helpers.add_labeled_factory` in tool dialogs, `_labeled_control_row` in the conversation window. `tests/test_labels.py` enforces this.
- A control that does not apply to the current state (provider, model, no model yet) must be hidden, not only disabled, so that Tab never reaches it.
- The bundled user guide is `readme.md` (copied to `addon/doc/en/` at build time). Update it when a user-visible feature or default changes.

## Tests

- Tests live in `tests/` and are written in plain pytest style (functions, `assert`, fixtures, `parametrize`).
- `tests/conftest.py` stubs the NVDA modules and registers the add-on as the `AIHub` package: import from `AIHub...` and add any missing NVDA stub there, never in a test file. Use its `key_store` fixture for anything that touches API accounts.
- Tests must not depend on the developer's machine: no real API keys (conftest strips `*_KEY` environment variables), no network (patch `AIHub.apiclient._client._open_bytes` and the like, or use a local test server), files only under `tmp_path`.
- No GUI tests: never instantiate a dialog or drive wx controls, even with fakes. Test the logic those classes call (parsers, request building, accounts, transcription...), and move logic out of a dialog when it needs a test.
- Test behaviour, not declarations: no test that only re-reads a constant, an enum member, a registry entry or a class hierarchy. A bug fix comes with a test that fails without it.

## Git

- Day-to-day work happens on `dev`; pull requests target `master`.
- Commit subjects are short imperative English sentences without a type prefix, e.g. `Keep the last SSE line when a stream ends without a line break`; the body explains why when it is not obvious, and ends with `Fixes #N` when it closes an issue.
