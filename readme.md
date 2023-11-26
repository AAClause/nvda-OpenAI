# Open AI NVDA add-on

This add-on designed to seamlessly integrate the capabilities of the Open AI API into your workflow. Whether you're looking to craft comprehensive text, translate passages with precision, concisely summarize documents, or even interpret and describe visual content, this add-on does it all with ease.

## Installation Steps

1. Navigate to the [releases page](https://github.com/aaclause/nvda-OpenAI/releases) to find the latest version of the add-on.
2. Download the latest release from the provided link.
3. Execute the installer to add the add-on to your NVDA environment.

## Prerequisites for Use

In order to utilize the full functionality of the OpenAI NVDA add-on, an API key from OpenAI is required. Follow these steps to set it up:

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

### Accessing Main Features

The functionality of the add-on is housed within a central dialog that can be opened using the shortcut `NVDA+g`. This dialog provides access to the majority of the add-on's features, enabling you to:

- Engage in conversation with the AI model.
- Get descriptions of images from image files.
- Transcribe spoken content from audio files or through a microphone.
- Use the text-to-speech feature to vocalize written text in the prompt.

#### Commands from main dialog

Some commands are available in the main dialog for different elements.

- When the Prompt Field is focused:
	- `Ctrl+Enter`: Submit the text you've entered.
	- `Ctrl+Up Arrow`: Retrieve and place the most recently entered prompt into the current field for review or re-use.
- When the History Field is focused:
	- `Alt+Right Arrow`: Copy the user's text to the prompt.
	- `Alt+Left Arrow`: Copy the assistant's response to the system.
	- `Ctrl+C`: Copy the assistant's response or the user's text depending on the cursor's position.
	- `Ctrl+Shift+Up Arrow`: Move to the text block of the user or assistant above the current block.
	- `Ctrl+Shift+Down Arrow`: Move to the text block of the user or assistant below the current block.

### Global Commands

These commands can be used to trigger actions from anywhere on your computer. It is possible to reassign them from *Input Gestures* dialog under *Open AI* category.

- `NVDA+e`: Take a screenshot and describe it.
- `NVDA+o`: Grab the current navigator object and describe it.

## Included Dependencies

The add-on comes bundled with the following essential dependencies:

- [OpenAI](https://pypi.org/project/openai/): The official Python library for the openai API.
- [MSS](https://pypi.org/project/mss/): An ultra fast cross-platform multiple screenshots module in pure python using ctypes.
- [sounddevice](https://pypi.org/project/sounddevice/): Play and Record Sound with Python.
