"""Attachment file handling for the conversation window.

Holds the ``AttachmentFile`` data class and ``AttachmentFileTypes`` constants
used by the per-tab "Files" list (images and documents). Audio attachments
are kept in a separate ``audioPathList`` and modeled as plain path strings.

The module file is still named ``image_file.py`` for backward-compatibility
with conversation JSON loaders and any cached imports — only the in-code
identifiers were renamed.
"""
import os
import re
import mimetypes
import urllib.parse

from .imagehelper import get_image_dimensions

URL_PATTERN = re.compile(
	r"^(?:http)s?://(?:[A-Z0-9-]+\.)+[A-Z]{2,6}(?::\d+)?(?:/?|[/?]\S+)$",
	re.IGNORECASE
)


def get_display_size(size):
	if size < 1024:
		return f"{size} B"
	if size < 1024 * 1024:
		return f"{size / 1024:.2f} KB"
	return f"{size / 1024 / 1024:.2f} MB"


class AttachmentFileTypes:
	"""Type tag for ``AttachmentFile.type``.

	Distinguishes images vs. non-image documents and local-file vs. remote
	URL sources. Used to route attachments through the right provider request
	shape (image_url vs. input_file vs. native PDF, etc.).
	"""
	UNKNOWN = 0
	IMAGE_LOCAL = 1
	IMAGE_URL = 2
	DOCUMENT_LOCAL = 3
	DOCUMENT_URL = 4


class AttachmentFile:
	"""One entry in the per-tab "Files" attachment list.

	Despite the class living in ``image_file.py``, this represents either an
	image or a document (PDF, DOCX, TXT, …). The variant is recorded in
	``self.type`` (an :class:`AttachmentFileTypes` constant).
	"""

	def __init__(
		self,
		path: str,
		name: str = None,
		description: str = None,
		size: int = -1,
		dimensions: tuple = None
	):
		if not isinstance(path, str):
			raise TypeError("path must be a string")
		self.path = path
		self.type = self._get_type()
		self.name = name or self._get_name()
		self.description = description
		if size and size > 0:
			self.size = get_display_size(size)
		else:
			self.size = self._get_size()
		self.dimensions = dimensions or self._get_dimensions()
		# Provider → uploaded file id (OpenAI/xAI Responses). Reused so historical
		# documents keep a stable prompt prefix for prompt caching.
		self.providerFileIds = {}

	def _get_type(self):
		if os.path.exists(self.path):
			return AttachmentFileTypes.IMAGE_LOCAL if self._is_image_path(self.path) else AttachmentFileTypes.DOCUMENT_LOCAL
		if re.match(URL_PATTERN, self.path):
			return AttachmentFileTypes.IMAGE_URL if self._is_image_path(self.path) else AttachmentFileTypes.DOCUMENT_URL
		return AttachmentFileTypes.UNKNOWN

	def _is_image_path(self, value: str) -> bool:
		parsed = urllib.parse.urlparse(value)
		path = parsed.path if parsed.scheme else value
		mime, _ = mimetypes.guess_type(path)
		return bool(mime and mime.startswith("image/"))

	def _get_name(self):
		if self.type in (AttachmentFileTypes.IMAGE_LOCAL, AttachmentFileTypes.DOCUMENT_LOCAL):
			return os.path.basename(self.path)
		if self.type in (AttachmentFileTypes.IMAGE_URL, AttachmentFileTypes.DOCUMENT_URL):
			return self.path.split("/")[-1]
		return "N/A"

	def _get_size(self):
		if self.type == AttachmentFileTypes.IMAGE_LOCAL:
			return get_display_size(os.path.getsize(self.path))
		return "N/A"

	def _get_dimensions(self):
		if self.type == AttachmentFileTypes.IMAGE_LOCAL:
			try:
				return get_image_dimensions(self.path)
			except Exception:
				return None
		return None

	def __str__(self):
		return f"{self.name} ({self.path}, {self.size}, {self.dimensions}, {self.description})"
