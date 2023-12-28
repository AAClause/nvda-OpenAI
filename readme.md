# Open AI NVDA add-on

This add-on designed to seamlessly integrate the capabilities of the Open AI API into your workflow. Whether you're looking to craft comprehensive text, translate passages with precision, concisely summarize documents, or even interpret and describe visual content, this add-on does it all with ease.

## Installation Steps

1. Navigate to the [releases page](https://github.com/aaclause/nvda-OpenAI/releases) to find the latest version of the add-on.
2. Download the latest release from the provided link.
3. Execute the installer to add the add-on to your NVDA environment.

## Prerequisites for Use

To fully unlock the capabilities of the OpenAI NVDA add-on, you must obtain an API key from OpenAI. Here's how to configure it for use:

1. Acquire an API key by registering for an OpenAI account at [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. With the API key ready, you have two options for configuration:
	- Through the NVDA settings dialog:
		1. Access the NVDA menu and navigate to the "Preferences" submenu.
		2. Open the "Settings" dialog and select the "Open AI" category.
		3. Input your API key in the provided field and click "OK" to confirm.
	- Using environment variables:
		1. Press `Windows+Pause` to open System Properties.
		2. Click on "Advanced system settings" and select "Environment Variables".
		3. Create a new variable under "User variables":
			1. Click on "New".
			2. Enter `OPENAI_API_KEY` as the variable name and paste your API key as the value.
		4. Click "OK" to save your changes.

You are now equipped to explore the features of the OpenAI NVDA add-on!

## How to Use the Add-on

### The Main Dialog

The majority of the add-on's features can be easily accessed via a dialog box, which can be launched by pressing `NVDA+G`.  
As an alternative, navigate to the "Open AI" submenu under the NVDA menu and select the "Main Dialog…" item.  
Within this dialog, you will be able to:

- Initiate interactive conversations with the AI models for assistance or information gathering.
- Get descriptions of images from image files.
- Transcribe spoken content from audio files or through a microphone.
- Use the text-to-speech feature to vocalize written text in the prompt.

To further improve your interaction with the interface, please take note of the following:

- The multiline "System", "History", and "Prompt" fields come equipped with context menus filled with commands that can be quickly executed using keyboard shortcuts.
  These shortcuts are active when the relevant field is in focus.
  For example, the keys 'j' and 'k' allow you to navigate to the previous and next messages, respectively, when the focus is on the History field.

- Additionally, the interface includes keyboard shortcuts that are effective across the entire window. For instance, `CTRL + R` starts or stops a recording.

All keyboard shortcuts are displayed next to the labels of their corresponding elements.

### Global Commands

These commands can be used to trigger actions from anywhere on your computer. It is possible to reassign them from *Input Gestures* dialog under *Open AI* category.

- `NVDA+e`: Take a screenshot and describe it.
- `NVDA+o`: Grab the current navigator object and describe it.
- Commands not assigned to any gesture by default:
	- Toggle the microphone recording and transcribe the audio from anywhere.

## Included Dependencies

The add-on comes bundled with the following essential dependencies:

- [openai](https://pypi.org/project/openai/): The official Python library for the openai API.
- [markdown2](https://pypi.org/project/markdown2/): A fast and complete Python implementation of Markdown.
- [MSS](https://pypi.org/project/mss/): An ultra fast cross-platform multiple screenshots module in pure python using ctypes.
- [Pillow](https://pypi.org/project/Pillow/): The user-friendly fork of the Python Imaging Library, used for image resizing.
- [sounddevice](https://pypi.org/project/sounddevice/): Play and Record Sound with Python.
