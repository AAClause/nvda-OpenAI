If you want a dedicated desktop experience with additional workflows, see [BasiliskLLM](https://github.com/SigmaNight/basiliskLLM/) (standalone app plus a minimal NVDA add-on). AI-Hub remains a full-featured option inside NVDA.

# AI-Hub

**AI-Hub** is an NVDA add-on that connects your screen reader to multiple large-language-model (LLM) APIs. Use it for writing, summarizing, translation help, vision (images and screenshots), voice questions, transcription, and optional tool dialogs (TTS, OCR, and more)—without leaving NVDA.

The add-on’s **package name** in NVDA is still `openai` (for compatibility with existing installs). The **display name** you see in menus and settings is **AI-Hub**.

## Features at a glance

- **Chat** in a dedicated main dialog with history, system prompt, and model/account selection.
- **Images and documents** as attachments from files; **URLs** to remote files with type checks aligned to the **selected provider**.
- **Smart paste** in the prompt field: paste files from the clipboard, paths from text, or a single URL (also available from the prompt’s context menu). `Ctrl+V` uses the same logic when the prompt has focus.
- **Conversation save and history** with rename, delete, and reopen.
- **Ask a question** from anywhere (no default key): assign a gesture in **Input Gestures → AI-Hub** to record, send, and hear or read the answer.
- **Global describe**: screenshot (`NVDA+E`) or navigator object region (`NVDA+O`) sent into a chat session.
- **Tools** submenu (under NVDA → AI-Hub): provider-specific utilities such as TTS, OCR, speech-to-text, Lyria audio, Ollama model management, and Pangram AI detection / plagiarism check.
- **Reasoning / web search** options appear only when the **current model** supports them (varies by provider).

This add-on does **not** include its own update checker. **Updates** are handled through **NVDA’s official Add-on Store** when you install from there. If you install manually from the [releases page](https://github.com/aaclause/nvda-OpenAI/releases), install newer `.nvda-addon` builds the same way.

## Supported providers

Configure **one or more providers** in NVDA **Preferences → Settings → AI-Hub**. Each provider can hold **multiple named accounts** (API keys, optional organization or base URL where applicable).

| Provider | Role |
|----------|------|
| [OpenAI](https://platform.openai.com/) | GPT and related models; official transcription and TTS tool dialogs |
| [DeepSeek](https://www.deepseek.com/) | DeepSeek API (OpenAI-compatible) |
| **Custom OpenAI** | Any OpenAI-compatible HTTP API (custom base URL + key); transcription tool dialog |
| **Ollama** | Local models via OpenAI-compatible endpoint; model manager tool |
| [Mistral AI](https://mistral.ai/) | Mistral / Pixtral; Voxtral TTS, OCR, and speech-to-text tools |
| [OpenRouter](https://openrouter.ai/) | Many third-party models behind one key |
| [Anthropic](https://www.anthropic.com/) | Claude |
| [xAI](https://x.ai/) | Grok |
| [Google](https://ai.google.dev/) | Gemini; Lyria 3 Pro tool |
| [Pangram](https://www.pangram.com/) | Tool only (no chat models): AI-generated text detection, bulk detection, document detection, and plagiarism check |

The add-on can pick up API keys from **environment variables** when set (for example `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `PANGRAM_API_KEY`, and others). Custom OpenAI-compatible endpoints only use `OPENAI_COMPAT_API_KEY`, never `OPENAI_API_KEY`, so your OpenAI key is not sent to another server. The settings UI remains the main place to manage accounts.

### Speech-to-text (transcription) backends

For **microphone / file transcription** inside the main flow, you can choose among **whisper_cpp** (local), **openai** (Whisper API), **mistral**, and **custom_openai** (custom OpenAI-compatible endpoint), under the **Audio** section of AI-Hub settings.

## Installation

1. Open the [add-on releases page](https://github.com/aaclause/nvda-OpenAI/releases).
2. Download the latest `.nvda-addon` package.
3. Install it from NVDA’s **Tools → View/manage add-ons** (or open the file from Explorer and confirm installation).

## First-time configuration

1. Open **NVDA → Preferences → Settings**.
2. Select the **AI-Hub** category.
3. In **API Accounts**, choose **Add account...**.
4. In the account dialog, select a provider, enter an account name, and fill the required fields (API key for most providers; base URL for **Custom OpenAI** and **Ollama**, with Ollama defaulting to local `http://127.0.0.1:11434/v1` when empty).
5. Save, then optionally add more accounts, edit existing ones, or remove unused ones from the accounts list.
6. Optionally adjust **Audio**, **Chat feedback**, **Advanced** / temperature, and **Auto-save conversation** (enabled by default).

Until at least one provider account is ready, opening the main dialog will prompt you to add keys in AI-Hub settings.

## Upgrading from older “Open AI” builds

If you used an older version of this add-on:

- **Settings** are migrated from the legacy **`OpenAI`** config section to **`AIHub`**. You should not lose preferences.
- **Data files** (conversations, keys store, attachments) are migrated from the folder **`openai`** under your NVDA user configuration directory to **`aihub`**.

You do not need to move files manually unless you use a custom setup.

## NVDA menu: AI-Hub

Under the NVDA menu you will find **AI-Hub** (with the installed version in the label). Entries include:

- **Documentation** — opens the user guide in your browser (`doc\en\readme.html`).
- **Main dialog…** — open the chat window (`NVDA+G` by default).
- **Conversation history…** — manage saved chats.
- **Tools** — submenu grouping **OpenAI**, **Mistral**, **Google**, **Ollama**, and **Pangram** utilities (see below).
- **GitHub repository** / **BasiliskLLM** — quick links.

## Main dialog

Open with **`NVDA+G`** or **Main dialog…** from the AI-Hub menu.

### What you can do

- Chat with the selected model; review **Messages** with keyboard navigation and context menus (e.g. **j** / **k** to move between messages when focus is in the messages area—see on-screen hints for the active field).
- Attach **local images or documents** and add **file URLs** where the provider supports them. Unsupported types for the current provider may be warned before send.
- **Paste (file or text)** from the prompt’s context menu, or **`Ctrl+V`** in the prompt: the add-on may attach files, insert text paths, or treat a single URL as an attachment when appropriate.
- Record **audio** snippets, attach audio files, and use **TTS** for prompt text where the model supports it.
- **`Escape`** closes the main dialog (when no blocking modal is open).
- **`Ctrl+R`** toggles microphone recording (when applicable).
- **`F2`** renames the current saved conversation (after it exists in storage).
- **`Ctrl+N`** opens a **new** main dialog instance (session).

### Options that depend on the model

Some controls only appear or apply for certain models:

- **Reasoning** (“thinking”) for models that expose it; streamed reasoning is kept separate from the visible answer when the API provides that distinction.
- **Reasoning effort** and related controls where the provider supports levels.
- **Web search** only for models that advertise web search support.

Exact availability changes as providers update their APIs; the UI reflects the **currently selected model**.

### System prompt

The system prompt steers model behavior. A default suited to accessibility assistance is provided; you can edit it, reset it from the context menu, and optionally persist the last used prompt (configurable in settings). The system prompt consumes tokens like any other input.

## Conversation history

Use **Conversation history…** from the AI-Hub menu, or assign a gesture under **Input Gestures → AI-Hub**.

You can list, open, rename, delete, and create conversations. From the main dialog, **F2** and **Ctrl+N** help manage the current session.

### Auto-save

If **Auto-save conversation** is enabled in settings (default), the add-on saves (or updates) the stored conversation **after each completed assistant response**, and may persist state when you close the dialog if there is something to save. You can also save from the **Messages** field context menu. If auto-save is off, use manual save when you want to persist.

## Ask a question (voice)

This command has **no default key**. Assign one under **Input Gestures → AI-Hub**.

- First press: start recording.
- Second press while recording: stop and send.
- If the answer is played as audio, press again to stop playback.

**Modes:**

- **Direct audio** — if the selected model supports audio input, your recording can be sent as audio without a separate transcription step.
- **Transcribe then chat** — otherwise the configured transcription backend processes the recording, then the text is sent to the chat model.

If the main dialog is focused, its **current model** is used; otherwise the add-on chooses a suitable model among configured providers.

## Tools submenu

The **Tools** entry under the AI-Hub menu opens provider-grouped dialogs (each may require the corresponding API account):

| Menu area | Tool |
|-----------|------|
| Mistral | **Voxtral TTS…**, **OCR…**, **Speech to Text…** |
| Google | **Lyria 3 Pro…** |
| OpenAI | **TTS…**, **Transcription / Translation…** |
| Ollama | **Model manager…** |
| Pangram | **AI detection and plagiarism check…** |

If no account is configured for a tool’s provider, the add-on will tell you to add one in AI-Hub settings.

### Pangram

[Pangram](https://www.pangram.com/) detects AI-generated and AI-assisted text. Add a **Pangram** account (API key) in AI-Hub settings; Pangram accounts are used only by this tool and never appear in the chat model list. The dialog offers every option of the [official REST API](https://docs.pangram.com/api-reference/introduction):

- **AI detection (text)** — asynchronous task (`POST /task`, then polling). Choose the **model** selector (`default` follows Pangram’s current model; **Refresh model list** fetches the selectors enabled for your key) and optionally request a **public dashboard link**. The report gives the headline, prediction, AI-generated / AI-assisted / human fractions, segment counts, and each segment’s label, confidence, AI assistance score, humanizer result, word count, and character range.
- **AI detection (documents)** — uploads DOCX, PDF, or RTF files (several at once) to the file endpoint, with optional public dashboard links.
- **Bulk AI detection** — sends several texts as one asynchronous bulk job: separate items in the text field with a line containing only `---`, and/or add text files (each file is one item, identified by its name). Results are collected from every page once the job completes.
- **Plagiarism check** — compares the text with online sources and lists matched sources with their similarity score.
- **List available models** — shows the model selectors available to your API key.

Text can be typed, pasted from the clipboard, loaded from a text file, or taken from the last response of the conversation. Each run saves the report and raw JSON response and is stored in the conversation history so it can be reopened later. In the conversation window, the message history context menu has **Check with Pangram (AI detection)…** for the selected text or the current message, and a global command (no default gesture) opens the tool on the text selected in any application, or on the clipboard.

## Global commands

All default gestures can be changed in **NVDA → Preferences → Input Gestures → AI-Hub**.

| Gesture | Action |
|---------|--------|
| `NVDA+G` | Show the AI-Hub main dialog |
| `NVDA+E` | Screenshot and describe (adds image to a session) |
| `NVDA+O` | Describe the current navigator object region |
| *(no default gesture)* | Conversation history. Assign in Input Gestures → AI-Hub. |
| *(no default gesture)* | Ask a question (record / send / stop audio). Assign in Input Gestures → AI-Hub. |
| *(no default gesture)* | Toggle microphone recording and transcription. Assign in Input Gestures → AI-Hub. |
| *(no default gesture)* | Check the selected text (or the clipboard) with Pangram AI detection. Assign in Input Gestures → AI-Hub. |

## Where data is stored

Working files, saved conversations index, unified `accounts.json`, and attachments live under your NVDA **user configuration** directory, in the **`aihub`** folder (after migration from `openai`). Temporary files use a `tmp` subfolder and are cleaned up when reasonable (e.g. on add-on termination or dialog close).

## Required dependencies (auto-retrieved during build)

Builds use `scons` to populate runtime libs under:

`addon/globalPlugins/AIHub/libs/`

When a required lib is missing, `scons` downloads pinned wheels and extracts only what is needed into that folder. Current pinned dependencies are:

- **[markdown-it-py](https://pypi.org/project/markdown-it-py/)** `4.2.0` + **[mdurl](https://pypi.org/project/mdurl/)** `0.1.2` — extracted into `libs/` for chat Markdown rendering.
- **[Pillow](https://pypi.org/project/Pillow/)** `13.0.0` for:
  - Python `3.11` `win32` -> `libs/lib_py3.11_win32/`
  - Python `3.13` `win_amd64` -> `libs/lib_py3.13/`

The `libs` directory is intentionally git-ignored; contributors do not need to commit vendored artifacts.

## Development

The development environment is managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml`, `uv.lock`):

- `uv run pytest` — run the unit tests.
- `uv run scons` — build the `.nvda-addon` package (`uv run scons pot` for the translation template).
- `uv run prek run --all-files` — run the code checks (`uv run prek install` to run them on every commit).

## Troubleshooting (short)

- **“No account configured”** — Add an API key for the provider you selected in **AI-Hub** settings.
- **Provider rejects an attachment** — Check file type and size; try another model or provider that supports the media you need.

For issues and contributions, use the **GitHub repository** link from the AI-Hub menu.