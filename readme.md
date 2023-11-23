# Open AI NVDA add-on

This add-on designed to seamlessly integrate the capabilities of the Open AI API into your workflow. Whether you're looking to craft comprehensive text, translate passages with precision, concisely summarize documents, or even interpret and describe visual content, this add-on does it all with ease.

## Installation Steps

1. Navigate to the [releases page](https://github.com/aaclause/nvda-OpenAI/releases) to find the latest version of the add-on.
2. Download the latest release from the provided link.
3. Execute the installer to add the add-on to your NVDA environment.

## Prerequisites for Use

In order to utilize the full functionality of the OpenAI NVDA add-on, an API key from OpenAI is required. Follow these steps to set it up:

1. Acquire an API key by registering for an OpenAI account at [https://openai.com/](https://openai.com/).
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
- Obtain descriptions of images from image files.
- Transscribe spoken content from audio files.
- Use the text-to-speech feature to vocalize written text in the prompt.

### Quick Commands

- `NVDA+e`: Take a screenshot and describe it.
- `NVDA+o`: Grab the current navigator object and describe it.

TO BE CONTINUED
