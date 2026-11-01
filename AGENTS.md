# AGENTS.md

AI-Hub (internal add-on name `OpenAI`) is an NVDA add-on that connects the screen reader to several AI providers. Its users are blind or visually impaired: every feature must be fully usable with the keyboard and a screen reader.

## Language

- Code, identifiers, comments, docstrings, log messages, user-facing strings, commit messages and PR descriptions are always in **English**, whatever language the conversation is in.
- Translations live in `addon/locale/<lang>/LC_MESSAGES/nvda.po` and are maintained by translators. Do not edit a `.po` file unless explicitly asked.

## Tooling

The development environment is managed with **uv** and the checks with **prek** (not pip, not pre-commit).

- `uv sync` — create or update `.venv` from `pyproject.toml` and `uv.lock`.
- `uv run pytest` — unit tests.
- `uv run prek run --all-files` — code checks (configured in `prek.toml`; `uv run prek install` to run them on every commit).
- `uv run scons` — build the `.nvda-addon` package; `uv run scons pot` — translation template.

Add development dependencies with `uv add --group <test|build|lint> <package>` and commit the updated `uv.lock`. Never write a `requirements.txt` or call `pip install` directly. `pyproject.toml` only describes the dev environment: the add-on itself is not a Python package.

## Runtime constraints

- The code runs inside NVDA's own Python: 3.11 32-bit (NVDA 2024.1 to 2025.x, the minimum set in `buildVars.py`) or 3.13 64-bit (NVDA 2026.1+). It must work on both, and only on Windows.
- No third-party runtime dependency can be installed. The only ones available are vendored by `sconstruct` into `addon/globalPlugins/AIHub/libs/` (markdown-it-py, mdurl, Pillow), pinned there, and git-ignored. To bump or add one, edit `_VENDORED_WHEELS` in `sconstruct` and the list in `readme.md`.
- HTTP goes through `urllib` via the helpers in `apiclient/_http.py` (`http_request_json`, `_open_json`, `_open_streaming`...); do not add another HTTP stack. Validate user-supplied URLs with `url_safety.py`.
- Never block the UI thread: run network and disk work with `background.start_background`, return to the UI with `background.call_after_if_alive`, and stop workers with `thread_shutdown.stop_worker_thread`.
- Never log or display API keys or other secrets.

## Code style

- Indent with **tabs** (spaces only inside the vendored libraries).
- Follow the surrounding code: NVDA/wx names stay camelCase (`onCancel`, `getCurrentModel`, `scriptCategory`), new helpers are snake_case. Keep comments short and only where the code is not self-explanatory.
- Every user-facing string goes through `_()` and is preceded by a `# Translators:` comment that tells where it appears, in the style already used, for example `# Translators: NVDA Preferences — AI-Hub category: ...`. Use named placeholders with `.format()` (`_("Remove account {name}?").format(name=name)`), never f-strings or concatenation inside `_()`.
- A new subpackage with translatable strings must be added to `pythonSources` in `buildVars.py`, or its strings never reach the `.pot`.
- Settings belong in `configspec.py` (`config.conf["AIHub"]`); provider identifiers, models and defaults in `consts.py`.
- The bundled user guide is `readme.md` (copied to `addon/doc/en/` at build time). Update it when a user-visible feature or default changes.

## Tests

- Tests live in `tests/` and are written in plain pytest style (functions, `assert`, fixtures, `parametrize`).
- `tests/conftest.py` stubs the NVDA modules and registers the add-on as the `AIHub` package: import from `AIHub...` and add any missing NVDA stub there, never in a test file.
- Tests must not depend on the developer's machine: no real API keys (conftest strips `*_KEY` environment variables), no network (patch `AIHub.apiclient._client._open_bytes` and the like), files only under `tmp_path`.
- Test behaviour, not declarations: no test that only re-reads a constant, an enum member, a registry entry or a class hierarchy.

## Git

- Day-to-day work happens on `dev`; pull requests target `master`.
- Commit subjects are short imperative English sentences without a type prefix, e.g. `Keep the last SSE line when a stream ends without a line break`; the body explains why when it is not obvious.
- Do not commit build outputs (`*.nvda-addon`, the store `.json`, `manifest.ini`, `*.mo`, `*.pot`, `addon/doc/*/` HTML, `libs/`); they are git-ignored.
