from unittest.mock import MagicMock

import pytest

from AIHub.tool_dialog_base import ToolDialogBase


@pytest.mark.parametrize("cancelled", [True, False])
def test_progress_dialog_cancel_button_stops_the_task(cancelled):
	dlg = ToolDialogBase.__new__(ToolDialogBase)
	dlg._taskProgressDialog = MagicMock()
	# wx.ProgressDialog.Pulse returns (continue, skip); Cancel makes continue False.
	dlg._taskProgressDialog.Pulse.return_value = (not cancelled, False)
	dlg._request_task_cancel = MagicMock()
	dlg._onTaskProgressTimer(None)
	dlg._taskProgressDialog.Pulse.assert_called_once_with("Task in progress...")
	assert dlg._request_task_cancel.called is cancelled
