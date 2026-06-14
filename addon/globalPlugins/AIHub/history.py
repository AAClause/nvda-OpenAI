"""Chat history: TextSegment and HistoryBlock for the messages control."""


def _shift_index_after_insert(pos: int, insert_at: int, insert_len: int) -> int:
	return pos + insert_len if pos >= insert_at else pos


def sync_textctrl_saved_selection_after_insert(control, insert_at: int, insert_len: int) -> None:
	"""Keep a cached messages-field selection aligned when text is appended upstream."""
	saved = getattr(control, "_aihub_saved_selection", None)
	if not saved or saved[0] == saved[1]:
		return
	control._aihub_saved_selection = (
		_shift_index_after_insert(saved[0], insert_at, insert_len),
		_shift_index_after_insert(saved[1], insert_at, insert_len),
	)


def update_textctrl_saved_selection(control) -> None:
	"""Record or clear the user's current selection on a messages TextCtrl."""
	start, end = control.GetSelection()
	if start != end:
		control._aihub_saved_selection = (start, end)
	else:
		control._aihub_saved_selection = None


def get_textctrl_selected_text(control) -> str:
	"""Return selected text from the control, using a cached range if wx cleared it."""
	start, end = control.GetSelection()
	if start != end:
		return control.GetRange(start, end)
	saved = getattr(control, "_aihub_saved_selection", None)
	if saved and saved[0] != saved[1]:
		s_start, s_end = saved
		last = control.GetLastPosition()
		if 0 <= s_start < s_end <= last:
			text = control.GetRange(s_start, s_end)
			if text:
				return text
	return ""


class TextSegment:
	previous = None
	next = None
	originalText = ""
	start = 0
	end = 0
	owner = None

	def __init__(self, control, text, owner):
		self.control = control
		self.originalText = text
		self.owner = owner
		if not hasattr(control, "lastSegment") or control.lastSegment is None:
			control.firstSegment = self
			control.lastSegment = self
		else:
			control.lastSegment.next = self
			self.previous = control.lastSegment
			control.lastSegment = self
		p = control.GetInsertionPoint()
		control.SetInsertionPointEnd()
		self.start = control.GetInsertionPoint()
		control.AppendText(text)
		self.end = control.GetInsertionPoint()
		control.SetInsertionPoint(p)

	def appendText(self, text):
		if not text:
			return
		ctrl = self.control
		caret_pos = ctrl.GetInsertionPoint()
		sel_start, sel_end = ctrl.GetSelection()
		had_selection = sel_start != sel_end
		ctrl.SetInsertionPoint(self.end)
		pos_before = ctrl.GetInsertionPoint()
		ctrl.AppendText(text)
		pos_after = ctrl.GetInsertionPoint()
		insert_len = pos_after - pos_before
		self.end = pos_after
		segment = self.next
		while segment is not None:
			segment.start += insert_len
			segment.end += insert_len
			segment = segment.next
		if had_selection:
			new_sel_start = _shift_index_after_insert(sel_start, pos_before, insert_len)
			new_sel_end = _shift_index_after_insert(sel_end, pos_before, insert_len)
			ctrl.SetSelection(new_sel_start, new_sel_end)
			ctrl._aihub_saved_selection = (new_sel_start, new_sel_end)
		elif caret_pos >= pos_before:
			ctrl.SetInsertionPoint(caret_pos + insert_len)
		else:
			ctrl.SetInsertionPoint(caret_pos)
		if not had_selection:
			sync_textctrl_saved_selection_after_insert(ctrl, pos_before, insert_len)

	def getText(self):
		return self.control.GetRange(self.start, self.end)

	@staticmethod
	def getCurrentSegment(control):
		p = control.GetInsertionPoint()
		if not hasattr(control, "firstSegment"):
			return None
		segment = control.firstSegment
		while segment is not None:
			if segment.start <= p and segment.end > p:
				return segment
			segment = segment.next
		return control.lastSegment

	def delete(self):
		self.control.Remove(self.start, self.end)
		if self.previous is not None:
			self.previous.next = self.next
		else:
			self.control.firstSegment = self.next
		if self.next is not None:
			self.next.previous = self.previous
		else:
			self.control.lastSegment = self.previous
		segment = self.next
		while segment is not None:
			segment.start -= (self.end - self.start)
			segment.end -= (self.end - self.start)
			segment = segment.next


class HistoryBlock:
	previous = None
	next = None
	prompt = ""
	responseText = ""
	reasoningText = ""
	segmentBreakLine = None
	segmentPromptLabel = None
	segmentPrompt = None
	segmentResponseLabel = None
	segmentResponse = None
	segmentReasoningLabel = None
	segmentReasoning = None
	lastLen = 0
	lastReasoningLen = 0
	model = ""
	temperature = 0
	topP = 0
	seed = None
	topK = None
	stopText = ""
	frequencyPenalty = None
	presencePenalty = None
	displayHeader = True
	focused = False
	responseTerminated = False
	# In-code attribute uses the neutral name; on-disk JSON key is still
	# ``pathList`` for backward compatibility with previously saved conversations.
	filesList = None
	audioPathList = None
	audioTranscriptList = None
	audioPath = None
	usage = None
	timing = None
