import wx

EVT_RESULT_ID = wx.NewId()


class ResultEvent(wx.PyEvent):

	def __init__(self, data=None):
		wx.PyEvent.__init__(self)
		self.SetEventType(EVT_RESULT_ID)
		self.data = data
