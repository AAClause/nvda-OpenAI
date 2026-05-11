"""File-attachment list handlers for the conversation dialog.

The "Files" list (per session tab) holds both images and non-audio documents
(PDF, DOCX, TXT, …) modeled by :class:`AttachmentFile` from
``image_file.py``. Audio attachments are managed separately by
``audiohandlers.py``.
"""
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import wx

import addonHandler
import gui
from logHandler import log

from .imagehelper import get_image_dimensions, resize_image
from .image_file import AttachmentFile, AttachmentFileTypes, URL_PATTERN
from .consts import Provider, TEMP_DIR
from .mediastore import persist_local_file
from .url_safety import build_http_fetch_opener, validate_http_fetch_url

addonHandler.initTranslation()

IMAGE_EXTENSIONS = {
	".png", ".jpeg", ".jpg", ".gif", ".webp"
}

# OpenAI Chat Completions ``input_file`` content type accepts the broad
# document set on file-input-capable models (gpt-4.1, gpt-4o, …):
# https://developers.openai.com/api/docs/guides/file-inputs
OPENAI_DOCUMENT_EXTENSIONS = {
	".pdf", ".txt", ".md", ".json", ".html", ".xml", ".csv", ".tsv",
	".doc", ".docx", ".rtf", ".odt", ".ppt", ".pptx", ".xls", ".xlsx",
}

# xAI Grok files API accepts a very broad MIME range — PDFs, all common
# Office formats, code/text formats, EPUB, Jupyter notebooks, and any
# UTF-8 text file: https://docs.x.ai/developers/files/collections
XAI_DOCUMENT_EXTENSIONS = {
	".pdf", ".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".xml",
	".doc", ".docx", ".rtf", ".odt",
	".ppt", ".pptx",
	".xls", ".xlsx",
	".epub", ".ipynb",
}

_OPENAI_DOC_MIME = {
	"application/pdf",
	"text/plain",
	"text/markdown",
	"application/json",
	"text/html",
	"application/xml",
	"text/xml",
	"text/csv",
	"text/tab-separated-values",
	"application/msword",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	"application/rtf",
	"application/vnd.oasis.opendocument.text",
	"application/vnd.ms-powerpoint",
	"application/vnd.openxmlformats-officedocument.presentationml.presentation",
	"application/vnd.ms-excel",
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_XAI_DOC_MIME = _OPENAI_DOC_MIME | {
	"application/epub+zip",
	"application/x-ipynb+json",
	"application/vnd.jupyter",
}

_STANDARD_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Per-provider supported attachment extensions. Sourced from each provider's
# official docs (see comments) — be conservative, errors surface as user-
# visible "unsupported attachment" warnings before the request is sent.
#
# * OpenAI / Custom-OpenAI: full document set via Chat Completions ``input_file``
#   on file-input-capable models.
# * Anthropic: PDF + plain text are native ``document`` blocks. Other formats
#   (.csv, .md, .docx, .xlsx) must be inlined as text by the caller — we don't
#   advertise them here so the user is told to convert.
#   Source: https://platform.claude.com/docs/en/docs/build-with-claude/files
# * Google Gemini: images + PDF + plain text supported as inline data.
#   Source: https://firebase.google.com/docs/ai-logic/input-file-requirements
# * Mistral chat-vision: images + PDF (chat completions; OCR endpoint is wider
#   but lives in ``tool_mistral_ocr_dialog``).
# * OpenRouter: images + PDF (PDF parsing is normalized by OpenRouter for any
#   underlying model). Source: https://openrouter.ai/docs/guides/overview/multimodal/pdfs
# * xAI Grok: very broad document set via the Files API.
# * Ollama vision models: images only.
# * DeepSeek: no attachments.
PROVIDER_SUPPORTED_FILES = {
	Provider.OpenAI: {"images": IMAGE_EXTENSIONS, "documents": OPENAI_DOCUMENT_EXTENSIONS},
	Provider.CustomOpenAI: {"images": IMAGE_EXTENSIONS, "documents": OPENAI_DOCUMENT_EXTENSIONS},
	Provider.DeepSeek: {"images": set(), "documents": set()},
	Provider.Ollama: {"images": IMAGE_EXTENSIONS, "documents": set()},
	Provider.MistralAI: {"images": IMAGE_EXTENSIONS, "documents": {".pdf"}},
	Provider.OpenRouter: {"images": IMAGE_EXTENSIONS, "documents": {".pdf"}},
	Provider.Anthropic: {"images": {".jpeg", ".jpg", ".png", ".gif", ".webp"}, "documents": {".pdf", ".txt"}},
	Provider.xAI: {"images": IMAGE_EXTENSIONS, "documents": XAI_DOCUMENT_EXTENSIONS},
	Provider.Google: {"images": IMAGE_EXTENSIONS, "documents": {".pdf", ".txt"}},
}

PROVIDER_SUPPORTED_URL_MIME = {
	Provider.OpenAI: {"images": _STANDARD_IMAGE_MIME, "documents": _OPENAI_DOC_MIME},
	Provider.CustomOpenAI: {"images": _STANDARD_IMAGE_MIME, "documents": _OPENAI_DOC_MIME},
	Provider.DeepSeek: {"images": set(), "documents": set()},
	Provider.Ollama: {"images": _STANDARD_IMAGE_MIME, "documents": set()},
	Provider.MistralAI: {"images": _STANDARD_IMAGE_MIME, "documents": {"application/pdf"}},
	Provider.OpenRouter: {"images": _STANDARD_IMAGE_MIME, "documents": {"application/pdf"}},
	Provider.Anthropic: {"images": _STANDARD_IMAGE_MIME, "documents": {"application/pdf", "text/plain"}},
	Provider.xAI: {"images": _STANDARD_IMAGE_MIME, "documents": _XAI_DOC_MIME},
	Provider.Google: {"images": _STANDARD_IMAGE_MIME, "documents": {"application/pdf", "text/plain"}},
}


class FileHandlersMixin:
	"""Mixin: per-tab "Files" attachment list management for the conversation dialog."""

	def _get_provider_file_support(self, provider: str):
		return PROVIDER_SUPPORTED_FILES.get(provider) or {"images": IMAGE_EXTENSIONS, "documents": {".pdf"}}

	def _get_file_extension(self, attachment: AttachmentFile) -> str:
		path = (getattr(attachment, "path", "") or "").strip()
		if not path:
			return ""
		if re.match(URL_PATTERN, path):
			parsed = urllib.parse.urlparse(path)
			path = parsed.path or ""
		return os.path.splitext(path)[1].lower()

	def _get_attachment_mime(self, attachment: AttachmentFile) -> str:
		desc = getattr(attachment, "description", "") or ""
		if isinstance(desc, str) and "/" in desc:
			mime = desc.split(",", 1)[0].strip().lower()
			if ";" in mime:
				mime = mime.split(";", 1)[0].strip()
			if "/" in mime:
				return mime
		path = (getattr(attachment, "path", "") or "").strip()
		if not path:
			return ""
		guess_target = urllib.parse.urlparse(path).path if re.match(URL_PATTERN, path) else path
		mime, _ = mimetypes.guess_type(guess_target)
		return (mime or "").lower()

	def _is_url_attachment_supported_by_mime(self, provider: str, attachment: AttachmentFile) -> bool:
		mime = self._get_attachment_mime(attachment)
		if not mime:
			return False
		mime_support = PROVIDER_SUPPORTED_URL_MIME.get(provider) or {"images": set(), "documents": {"application/pdf"}}
		if attachment.type == AttachmentFileTypes.IMAGE_URL:
			return mime in mime_support["images"]
		if attachment.type == AttachmentFileTypes.DOCUMENT_URL:
			return mime in mime_support["documents"]
		return False

	def getUnsupportedAttachments(self, provider: str = None, filesList=None):
		files = self.filesList if filesList is None else filesList
		if not files:
			return []
		if provider is None:
			model = self.getCurrentModel() if hasattr(self, "getCurrentModel") else None
			provider = getattr(model, "provider", "") if model else ""
		support = self._get_provider_file_support(provider)
		unsupported = []
		for attachment in files:
			ext = self._get_file_extension(attachment)
			if attachment.type in (AttachmentFileTypes.IMAGE_LOCAL, AttachmentFileTypes.IMAGE_URL):
				if attachment.type == AttachmentFileTypes.IMAGE_URL and self._is_url_attachment_supported_by_mime(provider, attachment):
					continue
				if ext not in support["images"]:
					# Translators: Text in attachment/file handling messages.
					unsupported.append((attachment.path, ext or _("unknown"), _("image")))
			elif attachment.type in (AttachmentFileTypes.DOCUMENT_LOCAL, AttachmentFileTypes.DOCUMENT_URL):
				if attachment.type == AttachmentFileTypes.DOCUMENT_URL and self._is_url_attachment_supported_by_mime(provider, attachment):
					continue
				if ext not in support["documents"]:
					# Translators: Text in attachment/file handling messages.
					unsupported.append((attachment.path, ext or _("unknown"), _("document")))
		return unsupported

	def validateAttachmentsForProvider(self, provider: str = None, filesList=None):
		unsupported = self.getUnsupportedAttachments(provider=provider, filesList=filesList)
		if not unsupported:
			return True, ""
		# Translators: Text in attachment/file handling messages.
		provider_name = provider or _("selected provider")
		details = "\n".join(
			f"- {os.path.basename(path) or path} ({kind}, {ext})"
			for path, ext, kind in unsupported
		)
		# Translators: Text in attachment/file handling messages.
		msg = _(
			"The following attachments are not supported by {provider} and cannot be sent:\n{details}"
		).format(**{
			"provider": provider_name,
			"details": details,
		})
		return False, msg

	def addFileToList(self, path, removeAfter=False):
		"""Append one image/document attachment to the active tab's Files list.

		Accepts:
		* an :class:`AttachmentFile` instance (already-built attachment),
		* a ``str`` path (local file or URL),
		* a ``(path, name)`` tuple (e.g. screenshot capture with a friendly label).
		"""
		if not path:
			return
		page = self.get_active_page()
		fl = page.filesList
		if isinstance(path, AttachmentFile):
			path.path = persist_local_file(path.path, "images", prefix="image", fallback_ext=".png")
			fl.append(path)
		elif isinstance(path, str):
			stored = persist_local_file(path, "images", prefix="image", fallback_ext=".png")
			fl.append(AttachmentFile(stored))
		elif isinstance(path, tuple) and len(path) == 2:
			location, name = path
			stored = persist_local_file(location, "images", prefix="image", fallback_ext=".png")
			fl.append(AttachmentFile(stored, name=name))
			if removeAfter and location != stored:
				self._fileToRemoveAfter.append(location)
		else:
			raise ValueError(f"Invalid path: {path}")

	def getDefaultFilesDescriptionPrompt(self):
		if self.conf["images"]["useCustomPrompt"]:
			return self.conf["images"]["customPromptText"]
		# Translators: AI-Hub conversation — attachments and files: entry in a right-click or application context menu.
		return _("Describe the images in as much detail as possible.")

	def onFileDescription(self, evt):
		menu = wx.Menu()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — attachments and files: entry in a right-click or application context menu.
		menu.Append(item_id, _("From f&ile path...") + " (Ctrl+I)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromFilePath, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — attachments and files: entry in a right-click or application context menu.
		menu.Append(item_id, _("From &URL...") + " (Ctrl+U)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromURL, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — attachments and files: entry in a right-click or application context menu.
		menu.Append(item_id, _("From &screenshot") + " (Ctrl+E)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromScreenshot, id=item_id)
		self.PopupMenu(menu)
		menu.Destroy()

	def onFilesListKeyDown(self, evt):
		key_code = evt.GetKeyCode()
		if key_code == wx.WXK_DELETE:
			self.onRemoveSelectedFiles(evt)
		elif key_code == ord('A') and evt.ControlDown():
			self.onFilesListSelectAll(evt)
		evt.Skip()

	def onFilesListContextMenu(self, evt):
		menu = wx.Menu()
		if self.filesList:
			if self.filesListCtrl.GetItemCount() > 0 and self.filesListCtrl.GetSelectedItemCount() > 0:
				item_id = wx.NewIdRef()
				# Translators: AI-Hub conversation — attachments and files: entry in a context menu or submenu.
				menu.Append(item_id, _("&Remove selected files") + " (Del)")
				self.Bind(wx.EVT_MENU, self.onRemoveSelectedFiles, id=item_id)
			item_id = wx.NewIdRef()
			# Translators: AI-Hub conversation — attachments and files: entry in a context menu or submenu.
			menu.Append(item_id, _("Remove &all files"))
			self.Bind(wx.EVT_MENU, self.onRemoveAllFiles, id=item_id)
			menu.AppendSeparator()
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — attachments and files: entry in a context menu or submenu.
		menu.Append(item_id, _("Add from f&ile path...") + " (Ctrl+I)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromFilePath, id=item_id)
		item_id = wx.NewIdRef()
		# Translators: AI-Hub conversation — attachments and files: entry in a context menu or submenu.
		menu.Append(item_id, _("Add from &URL...") + " (Ctrl+U)")
		self.Bind(wx.EVT_MENU, self.onFileDescriptionFromURL, id=item_id)
		self.PopupMenu(menu)
		menu.Destroy()

	def onFilesListSelectAll(self, evt):
		for i in range(self.filesListCtrl.GetItemCount()):
			self.filesListCtrl.Select(i)

	def onRemoveSelectedFiles(self, evt):
		if not self.filesList:
			return
		focused_item = self.filesListCtrl.GetFocusedItem()
		remove_idx = frozenset(self._list_ctrl_selected_indices(self.filesListCtrl))
		if not remove_idx:
			return
		self.filesList = [path for i, path in enumerate(self.filesList) if i not in remove_idx]
		self.updateFilesList()
		if focused_item == wx.NOT_FOUND:
			return
		if focused_item > self.filesListCtrl.GetItemCount() - 1:
			focused_item -= 1
		self.filesListCtrl.Focus(focused_item)
		self.filesListCtrl.Select(focused_item)

	def onRemoveAllFiles(self, evt):
		self.filesList.clear()
		self.updateFilesList()

	def fileExists(self, path, filesList=None):
		"""True if ``path`` is already in the current Files list or any history block."""
		if not filesList:
			filesList = self.filesList
		for attachment in filesList:
			if attachment.path.lower() == path.lower():
				return True
		block = self.firstBlock
		while block is not None:
			if block.filesList:
				for attachment in block.filesList:
					if attachment.path.lower() == path.lower():
						return True
			block = block.next
		return False

	def updateFilesList(self, focusPrompt=True):
		# Always read/write the visible (active) page directly. Going through the
		# scope-aware ``self.filesList``/``self.filesListCtrl`` properties can
		# resolve to a different page when a worker is running, which would put
		# the attachment data on one page and refresh the list ctrl on another.
		page = self.get_active_page()
		files = getattr(page, "filesList", None) or []
		files_label = page.filesLabel
		files_ctrl = page.filesListCtrl
		files_ctrl.DeleteAllItems()
		if not files:
			files_label.Hide()
			files_ctrl.Hide()
			self._sync_attachments_section_header()
			self._relayout_attachments(anchor=files_ctrl)
			if focusPrompt:
				page.promptTextCtrl.SetFocus()
			return
		files_label.Show()
		files_ctrl.Show()
		self._sync_attachments_section_header()
		for attachment in files:
			files_ctrl.Append([
				attachment.name,
				attachment.path,
				attachment.size,
				f"{attachment.dimensions[0]}x{attachment.dimensions[1]}" if isinstance(attachment.dimensions, tuple) else "N/A",
				attachment.description or "N/A"
			])
		self._attachment_list_end_refresh(files_ctrl, focus_prompt_if_empty=False)

	def ensureModelVisionSelected(self):
		model = self.getCurrentModel()
		if model and model.vision:
			return
		vision_id = self.conf.get("modelVision")
		if vision_id and self._selectModelById(vision_id):
			return
		vision_models = [m for m in self._models if m.vision]
		if vision_models:
			self._selectModelById(vision_models[0].id)

	def focusLastFile(self):
		index = self.filesListCtrl.GetItemCount() - 1
		self.filesListCtrl.SetItemState(
			index,
			wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
			wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED
		)
		self.filesListCtrl.EnsureVisible(index)

	def onFileDescriptionFromFilePath(self, evt):
		if not self.filesList:
			self.filesList = []
		# Wildcard advertises the union of every provider's supported extensions.
		# The per-provider validation in ``getUnsupportedAttachments`` then rejects
		# anything the currently selected model cannot accept.
		all_exts = "*.png;*.jpeg;*.jpg;*.gif;*.webp;*.bmp;*.pdf;*.txt;*.md;*.json;*.html;*.htm;*.xml;*.csv;*.tsv;*.doc;*.docx;*.rtf;*.odt;*.ppt;*.pptx;*.xls;*.xlsx;*.epub;*.ipynb"
		dlg = wx.FileDialog(
			None,
			# Translators: Text in attachment/file handling messages.
			message=_("Select files"),
			defaultFile="",
			# Translators: Text in attachment/file handling messages.
			wildcard=_("Supported files") + f" ({all_exts})|{all_exts}",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		paths = dlg.GetPaths()
		if not paths:
			return
		added_image = False
		rejected = []
		model = self.getCurrentModel() if hasattr(self, "getCurrentModel") else None
		provider = getattr(model, "provider", "") if model else ""
		for path in paths:
			if not self.fileExists(path):
				attachment = AttachmentFile(path)
				unsupported = self.getUnsupportedAttachments(provider=provider, filesList=[attachment])
				if unsupported:
					rejected.append(path)
					continue
				self.filesList.append(attachment)
				if attachment.type in (AttachmentFileTypes.IMAGE_LOCAL, AttachmentFileTypes.IMAGE_URL):
					added_image = True
			else:
				gui.messageBox(
					# Translators: Error body when the user adds a local file that is already in the conversation’s attachment list (placeholder is the path).
					_("The following file has already been added and will be ignored:\n%s") % path,
					"OpenAI",
					wx.OK | wx.ICON_ERROR
				)
		if rejected:
			gui.messageBox(
				# Translators: Warning body listing attachment paths skipped because the current model’s provider cannot use them (newline-separated list).
				_("Some files are not supported by the selected provider and were ignored:\n%s") % "\n".join(rejected),
				# Translators: Title of the warning dialog when some dropped or chosen files were skipped as unsupported.
				_("Unsupported files"),
				wx.OK | wx.ICON_WARNING
			)
		if added_image:
			self.ensureModelVisionSelected()
		if not self.promptTextCtrl.GetValue().strip():
			self.promptTextCtrl.SetValue(self.getDefaultFilesDescriptionPrompt())
		self.updateFilesList()
		self.focusLastFile()

	def onFileDescriptionFromURL(self, evt):
		dlg = wx.TextEntryDialog(
			None,
			# Translators: Text in attachment/file handling messages.
			message=_("Enter file URL"),
			caption="OpenAI",
			style=wx.OK | wx.CANCEL
		)
		if dlg.ShowModal() != wx.ID_OK:
			return
		url = dlg.GetValue().strip()
		if not url:
			return
		if not re.match(URL_PATTERN, url):
			gui.messageBox(
				# Translators: Error body when the user entered an attachment URL that does not match the expected URL pattern.
				_("Invalid URL, bad format."),
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return
		try:
			validate_http_fetch_url(url)
		except ValueError:
			gui.messageBox(
				# Translators: Error message when adding an attachment URL that fails security or protocol checks in the conversation window.
				_("This URL cannot be opened (unsupported scheme or blocked address)."),
				# Translators: Title of the error dialog when the attachment URL failed security or protocol validation.
				_("Invalid URL"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		try:
			req = urllib.request.Request(
				url,
				headers={"User-Agent": "AI-Hub/NVDA (image URL fetch)"},
				method="GET",
			)
			with build_http_fetch_opener().open(req, timeout=15) as r:
				if not self.filesList:
					self.filesList = []
				content_type = (r.headers.get_content_type() or "").lower().strip()
				if not content_type:
					content_type = "application/octet-stream"
				description = content_type
				size = r.headers.get("Content-Length")
				if size and size.isdigit():
					size = int(size)
				is_image = content_type.startswith("image/")
				attachment = AttachmentFile(
					url,
					description=description,
					size=size or -1,
					dimensions=None
				)
				if attachment.type == AttachmentFileTypes.UNKNOWN:
					attachment.type = AttachmentFileTypes.IMAGE_URL if is_image else AttachmentFileTypes.DOCUMENT_URL
				model = self.getCurrentModel() if hasattr(self, "getCurrentModel") else None
				provider = getattr(model, "provider", "") if model else ""
				unsupported = self.getUnsupportedAttachments(provider=provider, filesList=[attachment])
				if unsupported:
					gui.messageBox(
						# Translators: Error body when a fetched URL’s content type is not allowed for the current provider’s models.
						_("This URL file type is not supported by the selected provider."),
						# Translators: Title of the error dialog when the URL attachment’s MIME type is rejected.
						_("Unsupported file type"),
						wx.OK | wx.ICON_ERROR
					)
					return
				if is_image:
					try:
						attachment.dimensions = get_image_dimensions(r)
					except Exception as err:
						log.error(f"get_image_dimensions: {err}", exc_info=True)
						gui.messageBox(
							# Translators: Error body when NVDA could not read image width/height from a URL attachment (placeholder is the technical error).
							_("Failed to get image dimensions. %s") % err,
							"OpenAI",
							wx.OK | wx.ICON_ERROR
						)
						return
				self.filesList.append(attachment)
				if is_image:
					self.ensureModelVisionSelected()
					if not self.promptTextCtrl.GetValue().strip():
						self.promptTextCtrl.SetValue(self.getDefaultFilesDescriptionPrompt())
				self.updateFilesList()
				self.focusLastFile()
				return
		except urllib.error.HTTPError as err:
			gui.messageBox(
				# Translators: AI-Hub conversation — attachments and files: brief status feedback (speech/braille), not a full dialog.
				_("HTTP error %s.") % err,
				"OpenAI",
				wx.OK | wx.ICON_ERROR
			)
			return

	def onFileDescriptionFromScreenshot(self, evt):
		from . import conversation_dialog
		if conversation_dialog.addToSession and conversation_dialog.addToSession is self:
			conversation_dialog.addToSession = None
			# Translators: AI-Hub conversation — attachments and files: brief status feedback (speech/braille), not a full dialog.
			self.message(_("Screenshot reception disabled"))
			return
		conversation_dialog.addToSession = self
		# Translators: AI-Hub conversation — attachments and files: brief status feedback (speech/braille), not a full dialog.
		self.message(_("Screenshot reception enabled"))
