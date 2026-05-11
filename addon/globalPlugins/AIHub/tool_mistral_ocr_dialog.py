"""Dedicated dialog for Mistral OCR."""

import base64
import json
import mimetypes
import os
import threading
import urllib.request
import winsound

import addonHandler
import wx
from logHandler import log

from .conversations import ConversationFormat
from .consts import (
	Provider,
	SND_CHAT_RESPONSE_RECEIVED,
	SND_PROGRESS,
	stop_progress_sound,
	UI_DIALOG_BORDER_PX,
	UI_FORM_ROW_BORDER_PX,
	UI_SECTION_SPACING_PX,
)
from .mediastore import build_media_path
from .providertools_helpers import add_labeled_factory, extract_ocr_text, safe_int
from .thread_shutdown import stop_worker_thread
from .tool_dialog_base import ToolDialogBase

addonHandler.initTranslation()

class MistralOCRToolDialog(ToolDialogBase):
	def __init__(self, parent, conversationData=None, parentDialog=None, plugin=None):
		super().__init__(
			parent,
			# Translators: Window title of the AI-Hub Mistral OCR tool dialog.
			title=_("Tool: Mistral OCR"),
			provider=Provider.MistralAI,
			size=(800, 760),
			parentDialog=parentDialog,
			plugin=plugin,
		)
		self._worker = None
		self._restoredTextPath = ""
		self._restoredJsonPath = ""
		dialogSizer = wx.BoxSizer(wx.VERTICAL)
		self.formPanel = wx.Panel(self)
		main = wx.BoxSizer(wx.VERTICAL)

		self.accountChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the Mistral account drop-down in the Mistral OCR tool.
			_("&Account:"),
			lambda: self.build_account_choice(self.formPanel),
		)
		self.modelText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the OCR model id text field (default mistral-ocr-latest).
			_("&Model:"),
			lambda: wx.TextCtrl(self.formPanel, value="mistral-ocr-latest"),
		)
		self.sourceText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the path or https URL of the image or PDF to send to Mistral OCR.
			_("Source file or &URL:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.sourceText.Bind(wx.EVT_TEXT, lambda evt: (self._syncOpenButtons(), evt.Skip()))
		# Translators: Button that opens a file picker for the local image or PDF used as OCR input.
		self.browseSourceBtn = wx.Button(self.formPanel, label=_("Browse OCR source..."))
		self.browseSourceBtn.Bind(wx.EVT_BUTTON, self.onBrowseSource)
		main.Add(self.browseSourceBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the current source path or URL in the default application.
		self.openSourceBtn = wx.Button(self.formPanel, label=_("Open source"))
		self.openSourceBtn.Bind(wx.EVT_BUTTON, self.onOpenSource)
		main.Add(self.openSourceBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the plain-text OCR output file from the last successful run.
		self.openTextResultBtn = wx.Button(self.formPanel, label=_("Open OCR text result"))
		self.openTextResultBtn.Bind(wx.EVT_BUTTON, self.onOpenTextResult)
		main.Add(self.openTextResultBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		# Translators: Button that opens the structured JSON OCR output file from the last successful run.
		self.openJsonResultBtn = wx.Button(self.formPanel, label=_("Open OCR JSON result"))
		self.openJsonResultBtn.Bind(wx.EVT_BUTTON, self.onOpenJsonResult)
		main.Add(self.openJsonResultBtn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		self.pagesText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional PDF page index list sent to Mistral OCR (comma-separated page numbers).
			_("&Pages (e.g. 0,1,2):"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.imageLimitText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional maximum number of images Mistral should process from the document.
			_("Image &limit:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.imageMinSizeText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional minimum image dimension filter for OCR preprocessing.
			_("Image min si&ze:"),
			lambda: wx.TextCtrl(self.formPanel, value=""),
		)
		self.tableFormatChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the table output format drop-down (markdown, html, or none) for Mistral OCR.
			_("Table f&ormat:"),
			lambda: wx.Choice(self.formPanel, choices=["", "markdown", "html"]),
		)
		self.docAnnotationChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the document-level annotation output format drop-down for Mistral OCR.
			_("Document annotation for&mat:"),
			lambda: wx.Choice(self.formPanel, choices=["", "text", "json", "json_schema"]),
		)
		self.bboxAnnotationChoice = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the bounding-box annotation output format drop-down for Mistral OCR.
			_("BBo&x annotation format:"),
			lambda: wx.Choice(self.formPanel, choices=["", "text", "json"]),
		)
		# Translators: Checkbox asking Mistral to embed base64 image data in the OCR JSON response.
		self.includeImageB64Check = wx.CheckBox(self.formPanel, label=_("Include image base64 in response"))
		# Translators: Checkbox enabling extraction of document header regions in Mistral OCR.
		self.extractHeaderCheck = wx.CheckBox(self.formPanel, label=_("Extract header"))
		# Translators: Checkbox enabling extraction of document footer regions in Mistral OCR.
		self.extractFooterCheck = wx.CheckBox(self.formPanel, label=_("Extract footer"))
		self.annotationPromptText = add_labeled_factory(
			self.formPanel,
			main,
			# Translators: Label before the optional multiline instructions for document-level annotations in Mistral OCR.
			_("Document annotation &prompt:"),
			lambda: wx.TextCtrl(self.formPanel, style=wx.TE_MULTILINE, size=(-1, 100)),
		)
		for choice in (self.tableFormatChoice, self.docAnnotationChoice, self.bboxAnnotationChoice):
			choice.SetSelection(0)
		main.Add(self.includeImageB64Check, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		main.Add(self.extractHeaderCheck, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)
		main.Add(self.extractFooterCheck, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, UI_FORM_ROW_BORDER_PX)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that sends the configured document to the Mistral OCR API and writes result files.
		self.runBtn = wx.Button(self.formPanel, label=_("Run OCR"))
		self.runBtn.Bind(wx.EVT_BUTTON, self.onRun)
		self.bind_ctrl_enter_submit(self.onRun)
		self.closeBtn = wx.Button(self.formPanel, id=wx.ID_CLOSE)
		self.closeBtn.Bind(wx.EVT_BUTTON, self.onClose)
		buttons.Add(self.runBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		buttons.Add(self.closeBtn, 0, wx.ALL, UI_SECTION_SPACING_PX)
		main.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, UI_SECTION_SPACING_PX)

		self.formPanel.SetSizer(main)
		dialogSizer.Add(self.formPanel, 1, wx.EXPAND | wx.ALL, UI_DIALOG_BORDER_PX)
		self.SetSizer(dialogSizer)
		if parent:
			self.CentreOnParent(wx.BOTH)
		else:
			self.Centre(wx.BOTH)
		self._applyConversationData(conversationData)
		self._syncOpenButtons()

	def onBrowseSource(self, evt):
		dlg = wx.FileDialog(
			self,
			# Translators: Title of the file picker for the source image or PDF in the Mistral OCR tool.
			message=_("Select image or document file"),
			defaultFile="",
			# Translators: File-type filter in the Mistral OCR source file picker.
			wildcard=_("Supported files (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp;*.pdf)|*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp;*.pdf"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		if dlg.ShowModal() == wx.ID_OK:
			self.sourceText.SetValue(dlg.GetPath())

	def _setBusy(self, busy: bool):
		for ctrl in (
			self.accountChoice,
			self.modelText,
			self.sourceText,
			self.browseSourceBtn,
			self.openSourceBtn,
			self.openTextResultBtn,
			self.openJsonResultBtn,
			self.pagesText,
			self.imageLimitText,
			self.imageMinSizeText,
			self.tableFormatChoice,
			self.docAnnotationChoice,
			self.bboxAnnotationChoice,
			self.includeImageB64Check,
			self.extractHeaderCheck,
			self.extractFooterCheck,
			self.annotationPromptText,
			self.runBtn,
			self.closeBtn,
		):
			ctrl.Enable(not busy)

	def _run_thread(self, api_key, body):
		err = None
		text = ""
		txt_path = ""
		json_path = ""
		try:
			req = urllib.request.Request(
				"https://api.mistral.ai/v1/ocr",
				data=json.dumps(body).encode("utf-8"),
				headers={"Content-Type": "application/json", "x-api-key": api_key.strip()},
				method="POST",
			)
			with urllib.request.urlopen(req, timeout=180) as resp:
				payload = json.loads(resp.read().decode("utf-8", errors="replace"))
			text = extract_ocr_text(payload)
			json_path = build_media_path("documents", ".json", prefix="mistral_ocr")
			txt_path = build_media_path("documents", ".txt", prefix="mistral_ocr")
			with open(json_path, "w", encoding="utf-8") as f:
				json.dump(payload, f, ensure_ascii=False, indent=2)
			with open(txt_path, "w", encoding="utf-8") as f:
				f.write(text or "")
		except Exception as e:
			err = e
		wx.CallAfter(self._onThreadDone, text, txt_path, json_path, err)

	def _onThreadDone(self, text, txt_path, json_path, err):
		stop_progress_sound()
		if not self._isDialogAlive():
			self._worker = None
			return
		if self.conf["chatFeedback"]["sndResponseReceived"]:
			winsound.PlaySound(SND_CHAT_RESPONSE_RECEIVED, winsound.SND_ASYNC)
		if not self.end_long_task(focus_ctrl=self.openTextResultBtn):
			self._worker = None
			return
		self._worker = None
		if err is not None:
			log.error(f"Mistral OCR failed: {err}", exc_info=True)
			# Translators: Error body for an unexpected Mistral OCR failure (title is «OpenAI»; technical details only in the NVDA log).
			wx.MessageBox(_("Mistral OCR failed. See NVDA log for details."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		self._restoredTextPath = txt_path
		self._restoredJsonPath = json_path
		self._syncOpenButtons()
		self.open_local_path(txt_path, err_title="OpenAI")
		preview = text[:4000] if isinstance(text, str) else ""
		if preview:
			response_text = preview
		else:
			# Translators: Short completion line stored with the tool run when Mistral OCR produced files but no inline text preview.
			response_text = _("OCR completed. See attached output files.")
		self.save_tool_conversation(
			# Translators: Title stored on the synthetic «tool output» conversation tab after Mistral OCR finishes successfully.
			title=_("Tool output: Mistral OCR"),
			conversation_format=ConversationFormat.TOOL_MISTRAL_OCR,
			prompt=self.sourceText.GetValue().strip(),
			response_text=response_text,
			model=self.modelText.GetValue().strip() or "mistral-ocr-latest",
			format_data={
				"source": self.sourceText.GetValue().strip(),
				"model": self.modelText.GetValue().strip() or "mistral-ocr-latest",
				"text_path": txt_path,
				"json_path": json_path,
				"text_preview": preview,
				"options": {
					"pages": self.pagesText.GetValue().strip(),
					"image_limit": safe_int(self.imageLimitText.GetValue(), default=None),
					"image_min_size": safe_int(self.imageMinSizeText.GetValue(), default=None),
					"table_format": self.tableFormatChoice.GetStringSelection().strip(),
					"document_annotation_format": self.docAnnotationChoice.GetStringSelection().strip(),
					"bbox_annotation_format": self.bboxAnnotationChoice.GetStringSelection().strip(),
					"include_image_base64": self.includeImageB64Check.IsChecked(),
					"extract_header": self.extractHeaderCheck.IsChecked(),
					"extract_footer": self.extractFooterCheck.IsChecked(),
					"document_annotation_prompt": self.annotationPromptText.GetValue().strip(),
				},
			},
		)

	def _syncOpenButtons(self):
		self.openSourceBtn.Enable(bool(self.sourceText.GetValue().strip()))
		self.openTextResultBtn.Show(bool(self._restoredTextPath))
		self.openJsonResultBtn.Show(bool(self._restoredJsonPath))
		self.formPanel.Layout()
		self.Layout()

	def _applyConversationData(self, conversationData):
		if not isinstance(conversationData, dict):
			return
		fd = conversationData.get("formatData", {})
		if not isinstance(fd, dict):
			return
		self.sourceText.SetValue(fd.get("source", ""))
		self.modelText.SetValue(fd.get("model", self.modelText.GetValue()))
		options = fd.get("options", {})
		if isinstance(options, dict):
			if options.get("pages"):
				self.pagesText.SetValue(str(options.get("pages")))
			if options.get("image_limit") is not None:
				self.imageLimitText.SetValue(str(options.get("image_limit")))
			if options.get("image_min_size") is not None:
				self.imageMinSizeText.SetValue(str(options.get("image_min_size")))
			table_fmt = options.get("table_format", "")
			if isinstance(table_fmt, str) and table_fmt:
				idx = self.tableFormatChoice.FindString(table_fmt)
				if idx != wx.NOT_FOUND:
					self.tableFormatChoice.SetSelection(idx)
			doc_fmt = options.get("document_annotation_format", "")
			if isinstance(doc_fmt, str) and doc_fmt:
				idx = self.docAnnotationChoice.FindString(doc_fmt)
				if idx != wx.NOT_FOUND:
					self.docAnnotationChoice.SetSelection(idx)
			bbox_fmt = options.get("bbox_annotation_format", "")
			if isinstance(bbox_fmt, str) and bbox_fmt:
				idx = self.bboxAnnotationChoice.FindString(bbox_fmt)
				if idx != wx.NOT_FOUND:
					self.bboxAnnotationChoice.SetSelection(idx)
			self.includeImageB64Check.SetValue(bool(options.get("include_image_base64", False)))
			self.extractHeaderCheck.SetValue(bool(options.get("extract_header", False)))
			self.extractFooterCheck.SetValue(bool(options.get("extract_footer", False)))
			if isinstance(options.get("document_annotation_prompt"), str):
				self.annotationPromptText.SetValue(options.get("document_annotation_prompt"))
		text_path = fd.get("text_path", "")
		json_path = fd.get("json_path", "")
		if isinstance(text_path, str) and text_path:
			self._restoredTextPath = text_path
		if isinstance(json_path, str) and json_path:
			self._restoredJsonPath = json_path

	def onOpenSource(self, evt):
		self.open_local_path(self.sourceText.GetValue().strip(), err_title="OpenAI")

	def onOpenTextResult(self, evt):
		self.open_local_path(self._restoredTextPath, err_title="OpenAI")

	def onOpenJsonResult(self, evt):
		self.open_local_path(self._restoredJsonPath, err_title="OpenAI")

	def onClose(self, evt):
		self._markClosing()
		stop_progress_sound()
		self.end_long_task()
		stop_worker_thread(self._worker)
		self._worker = None
		if isinstance(evt, wx.CloseEvent):
			evt.Skip()
			return
		self.Close()

	def onRun(self, evt):
		if self._worker and self._worker.is_alive():
			return
		acc_id = self.require_account(self.accountChoice)
		if not acc_id:
			return
		source = self.sourceText.GetValue().strip()
		if not source:
			# Translators: Error body when Run is pressed without a local path or http(s) URL in the Mistral OCR source field (title is «OpenAI»).
			wx.MessageBox(_("Please provide a file path or URL for OCR."), "OpenAI", wx.OK | wx.ICON_ERROR)
			self.sourceText.SetFocus()
			return
		model = self.modelText.GetValue().strip() or "mistral-ocr-latest"
		api_key = self.manager.get_api_key(account_id=acc_id)
		if not api_key:
			# Translators: Error body when Mistral OCR cannot run because the selected Mistral account has no API key (title is «OpenAI»).
			wx.MessageBox(_("No API key available for the selected Mistral account."), "OpenAI", wx.OK | wx.ICON_ERROR)
			return
		if source.startswith("http://") or source.startswith("https://"):
			document = {"type": "document_url", "document_url": source}
		else:
			if not os.path.exists(source):
				# Translators: Error body when the entered local OCR path is not found on disk (title is «OpenAI»).
				wx.MessageBox(_("File does not exist."), "OpenAI", wx.OK | wx.ICON_ERROR)
				return
			try:
				with open(source, "rb") as f:
					data = f.read()
				mime = mimetypes.guess_type(source)[0] or "application/octet-stream"
				data_url = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
				document = {"type": "image_url", "image_url": data_url} if mime.startswith("image/") else {"type": "document_url", "document_url": data_url}
			except Exception as err:
				# Translators: Error body when the local image or PDF cannot be read for upload; placeholder is the OS error (title is «OpenAI»).
				wx.MessageBox(_("Unable to read source file: %s") % err, "OpenAI", wx.OK | wx.ICON_ERROR)
				return
		body = {
			"model": model,
			"document": document,
			"include_image_base64": self.includeImageB64Check.IsChecked(),
			"extract_header": self.extractHeaderCheck.IsChecked(),
			"extract_footer": self.extractFooterCheck.IsChecked(),
		}
		pages_raw = self.pagesText.GetValue().strip()
		if pages_raw:
			items = [p.strip() for p in pages_raw.split(",") if p.strip()]
			if items:
				body["pages"] = items
		image_limit = safe_int(self.imageLimitText.GetValue(), default=None)
		if image_limit is not None:
			body["image_limit"] = image_limit
		image_min_size = safe_int(self.imageMinSizeText.GetValue(), default=None)
		if image_min_size is not None:
			body["image_min_size"] = image_min_size
		table_format = self.tableFormatChoice.GetStringSelection().strip()
		if table_format:
			body["table_format"] = table_format
		doc_annotation = self.docAnnotationChoice.GetStringSelection().strip()
		if doc_annotation:
			body["document_annotation_format"] = doc_annotation
		bbox_annotation = self.bboxAnnotationChoice.GetStringSelection().strip()
		if bbox_annotation:
			body["bbox_annotation_format"] = bbox_annotation
		annotation_prompt = self.annotationPromptText.GetValue().strip()
		if annotation_prompt:
			body["document_annotation_prompt"] = annotation_prompt
		if self.conf["chatFeedback"]["sndTaskInProgress"]:
			winsound.PlaySound(SND_PROGRESS, winsound.SND_ASYNC | winsound.SND_LOOP)
		# Translators: Status line on the modal progress window while Mistral OCR is processing the selected file.
		self.begin_long_task(_("OCR in progress..."), self._setBusy)
		self._worker = threading.Thread(
			target=self._run_thread,
			args=(api_key, body),
			daemon=True,
		)
		self._worker.start()
