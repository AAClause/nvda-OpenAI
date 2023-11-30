import base64
import os
import sys
from logHandler import log
from .consts import ADDON_DIR

additionalLibsPath = os.path.join(ADDON_DIR, "lib")
sys.path.insert(0, additionalLibsPath)
from openai import OpenAI
sys.path.remove(additionalLibsPath)

# Borrowed from <https://platform.openai.com/docs/guides/vision>

# Function to encode the image
def encode_image(image_path):
	with open(image_path, "rb") as image_file:
		return base64.b64encode(image_file.read()).decode('utf-8')


def describeFromImageFileList(
	client,
	messages: list,
	max_tokens: int = 700,
):
	"""
	Describe a list of images from a list of file paths.
	@param client: OpenAI client
	@param messages: list of messages
	@param max_tokens: max tokens to use
	@return: description
	"""
	if not messages:
		return None
	response = client.chat.completions.create(
		model="gpt-4-vision-preview",
		messages=messages,
		max_tokens=max_tokens
	)
	return response.choices[0]
