"""Image utilities: screenshots, dimensions, resize. Requires Pillow in libs/."""
import base64
import os
import sys

from .consts import ADDON_DIR, ADDON_LIBS_DIR

sys.path.insert(0, ADDON_LIBS_DIR)
from PIL import Image, ImageGrab  # noqa: E402
sys.path.remove(ADDON_LIBS_DIR)

RESAMPLE = (
	getattr(getattr(Image, "Resampling", None), "LANCZOS", None)
	or getattr(Image, "LANCZOS", None)
	or getattr(Image, "ANTIALIAS", None)
	or 1
)


def get_image_dimensions(path_or_file):
	"""Return (width, height) from image path or file-like object."""
	with Image.open(path_or_file) as img:
		return img.size


def save_screenshot(path: str, bbox=None) -> bool:
	"""Capture screen or region, save as PNG. Returns True on success."""
	try:
		img = ImageGrab.grab(bbox=bbox)
		img.save(path, "PNG")
		return True
	except OSError:
		return False


def resize_image(src: str, max_width: int = 0, max_height: int = 0, quality: int = 85, target: str = "Compressed.PNG"):
	"""Resize image to fit within max dimensions. Returns True on success."""
	if max_width <= 0 and max_height <= 0:
		return False
	image = Image.open(src)
	orig_w, orig_h = image.size
	if max_width > 0 and max_height > 0:
		ratio = min(max_width / orig_w, max_height / orig_h)
	elif max_width > 0:
		ratio = max_width / orig_w
	else:
		ratio = max_height / orig_h
	new_w, new_h = int(orig_w * ratio), int(orig_h * ratio)
	image.resize((new_w, new_h), RESAMPLE).save(target, optimize=True, quality=quality)
	return True


def encode_image(image_path):
	with open(image_path, "rb") as f:
		return base64.b64encode(f.read()).decode("utf-8")

