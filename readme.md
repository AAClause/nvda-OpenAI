# Open AI NVDA add-on

This add-on designed to seamlessly integrate the capabilities of the Open AI API into your workflow. Whether you're looking to craft comprehensive text, translate passages with precision, concisely summarize documents, or even interpret and describe visual content, this add-on does it all with ease.

The add-on also supports integration with Mistral and OpenRouter services, thanks to their shared API format.

## Installation Steps

1. Navigate to the [releases page](https://github.com/aaclause/nvda-OpenAI/releases) to find the latest version of the add-on.
2. Download the latest release from the provided link.
3. Execute the installer to add the add-on to your NVDA environment.

## API Key Configuration

To use this add-on, you need to configure it with an API key from your selected service provider(s) ([OpenAI](https://platform.openai.com/), [Mistral AI](https://mistral.ai/), and/or [OpenRouter](https://openrouter.ai/). Each provider offers a straightforward process for API key acquisition and integration.

Once you have your API key, the next step is to integrate it with the add-on:

- Navigate through the NVDA menu to 'Preferences' and then 'Settings'. In the 'Settings' dialog, find the "Open AI" category.
- In this category, you will notice a group labeled 'API Keys' which contains buttons named after the supported service providers (e.g., "OpenAI API keys...").
- Click on the relevant button for your service. A dialogue will appear, prompting not only for your API key but also for an organization key if you have one. This is particularly useful for integrating with services that differentiate between personal and organizational usage.
- Fill in your API key and, if applicable, your organization key in the respective fields and click 'OK' to save your settings.

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

#### Increase your productivity with shortcuts

To further improve your interaction with the interface, please take note of the following:

- The multiline "System prompt", "Messages", and "Prompt" fields come equipped with context menus filled with commands that can be quickly executed using keyboard shortcuts. It is the same for the models list.
  These shortcuts are active when the relevant field is in focus.
  For instance, within the Messages area, pressing 'j' moves to the previous message, and 'k' to the next one.)

- Additionally, the interface includes keyboard shortcuts that are effective across the entire window.
  For instance, `CTRL + R` starts or stops a recording.

All keyboard shortcuts are displayed next to the labels of their corresponding elements.

#### About Conversation Mode checkbox

The conversation mode checkbox is designed to enhance your chat experience and save input tokens.

When activated (the default setting), the add-on delivers the entirety of the conversation history to the AI model, thereby granting it improved contextual understanding and resulting in more coherent responses. This comprehensive mode does result in higher consumption of input tokens.

Conversely, when the checkbox is left unticked, only the current user prompt is sent to the AI model. Select this mode to direct specific questions or acquire discrete responses, bypassing the need for contextual comprehension and conserving input tokens when the dialogue's history isn't necessary.

You can switch between the two modes at any time during a session.

#### About the "System prompt" Field

The "System prompt" field is designed to fine-tune the AI model's behavior and personality to match your specific expectations.

- **Default System Prompt**: Upon installation, the add-on includes a default system prompt ready to use.
- **Customization**: You have the freedom to personalize the system prompt by modifying the text directly within the field. The add-on will remember the last system prompt you used and automatically load it the next time you launch the dialog. This behavior can be disabled in settings.
- **Reset Option**: Want to go back to the standard configuration? Simply use the context menu to reset the "System promt" field to its default value effortlessly.

Please be aware that the system prompt is included in the AI model's input data, consuming tokens accordingly.

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
