import wx
import addonHandler
import ui
from logHandler import log
from . import apikeymanager
from .model import Model

addonHandler.initTranslation()

class EditModelDialog(wx.Dialog):

	def __init__(
		self,
		parent,
		title: str,
		provider: apikeymanager.Provider
	):
		super(EditModelDialog, self).__init__(parent, title=title)
		self.provider = provider
		self.InitUI()
		self.CenterOnParent()
		self.SetSize((500, 200))

	def InitUI(self):
		pnl = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)
		fgs = wx.FlexGridSizer(3, 2, 9, 25)  # 3 rows, 2 columns, vertical and horizontal gap

		lblModelID = wx.StaticText(
			pnl,
			label=_("Model &ID:")
		)
		self.txtModelID = wx.TextCtrl(pnl)

		lblModelName = wx.StaticText(
			pnl,
			label=_("Model &name:")
		)
		self.txtModelName = wx.TextCtrl(pnl)

		self.chkMultimodal = wx.CheckBox(
			pnl,
			label=_("Multimodal")
		)



		# Adding Rows to the FlexGridSizer
		fgs.AddMany(
			[
				lblModelName, (self.txtModelName, 1, wx.EXPAND),
				lblModelID, (self.txtModelID, 1, wx.EXPAND),
				(self.chkMultimodal, 1, wx.EXPAND),
			])

		# Configure an expanding column for text controls
		fgs.AddGrowableCol(1, 1)

		btnsizer = wx.StdDialogButtonSizer()
		btnOK = wx.Button(pnl, wx.ID_OK)
		btnOK.SetDefault()
		btnsizer.AddButton(btnOK)
		btnsizer.AddButton(wx.Button(pnl, wx.ID_CANCEL))
		btnsizer.Realize()

		# Layout sizers
		vbox.Add(fgs, proportion=1, flag=wx.ALL|wx.EXPAND, border=10)
		vbox.Add(btnsizer, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=10)
		pnl.SetSizer(vbox)

	def getModelName(self):
		return self.txtModelName.GetValue()

	def getModelID(self):
		return self.txtModelID.GetValue()


class ProviderDialog(wx.Dialog):

	def __init__(
		self,
		parent,
		title: str,
		provider: apikeymanager.Provider,
	):
		super(ProviderDialog, self).__init__(parent, title=title)
		self.provider = provider
		self.initUI()
		self.CenterOnParent()
		self.SetSize((500, 200))

	def initUI(self):
		pnl = wx.Panel(self)
		vbox = wx.BoxSizer(wx.VERTICAL)
		fgs = wx.FlexGridSizer(3, 2, 9, 25)  # 3 rows, 2 columns, vertical and horizontal gap

		if self.provider.require_api_key:
			lblAPIKey = wx.StaticText(
				pnl,
				# Translators: This is a label for the API key field in the provider dialog.
				label=_("API &Key:")
			)
			self.txtAPIKey = wx.TextCtrl(pnl)

			lblOrgName = wx.StaticText(
				pnl,
				# Translators: This is a label for the organization name field in the provider dialog.
				label=_("Organization &name:")
			)
			self.txtOrgName = wx.TextCtrl(pnl)

			lblOrgKey = wx.StaticText(
				pnl,
				# Translators: This is a label for the organization key field in the provider dialog.
				label="Organization ke&y:"
			)
			self.txtOrgKey = wx.TextCtrl(pnl)

			# Adding Rows to the FlexGridSizer
			fgs.AddMany(
				[
					lblAPIKey, (self.txtAPIKey, 1, wx.EXPAND),
					lblOrgName, (self.txtOrgName, 1, wx.EXPAND),
					lblOrgKey, (self.txtOrgKey, 1, wx.EXPAND),
				])

			# Configure an expanding column for text controls
			fgs.AddGrowableCol(1, 1)

			APIKey = self.provider.get_api_key()
			if APIKey:
				self.txtAPIKey.SetValue(
					APIKey
				)
			orgKey = self.provider.get_organization_key()
			orgName = self.provider.get_organization_name()
			if orgKey and orgName:
				self.txtOrgName.SetValue(
					orgName
				)
				self.txtOrgKey.SetValue(
					orgKey
				)
		if self.provider.custom:
			lblBaseURL = wx.StaticText(
				pnl,
				label=_("&Base URL:")
			)
			self.txtBaseURL = wx.TextCtrl(pnl)
			self.txtBaseURL.SetValue(self.provider.base_url)

			lblModels = wx.StaticText(
				pnl,
				label=_("&Models:")
			)
			self.modelList = wx.ListCtrl(pnl, style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
			self.modelList.InsertColumn(0, _("Model name"))
			self.modelList.InsertColumn(1, _("Model ID"))
			self.modelList.InsertColumn(2, _("Model type"))
			self.modelList.Bind(wx.EVT_CONTEXT_MENU, self.onModelListContextMenu)

			btnAddModel = wx.Button(pnl, label=_("Add model"))
			btnAddModel.Bind(wx.EVT_BUTTON, self.onAddModel)

			# Adding Rows to the FlexGridSizer
			fgs.AddMany(
				[
					(self.modelList, 1, wx.EXPAND),
					(btnAddModel, 1, wx.EXPAND),
				]
			)
		if self.provider.require_api_key:
			self.txtAPIKey.SetFocus()
		else:
			self.txtBaseURL.SetFocus()

		btnsizer = wx.StdDialogButtonSizer()
		btnOK = wx.Button(pnl, wx.ID_OK)
		btnOK.SetDefault()
		btnsizer.AddButton(btnOK)
		btnsizer.AddButton(wx.Button(pnl, wx.ID_CANCEL))
		btnsizer.Realize()

		# Layout sizers
		vbox.Add(fgs, proportion=1, flag=wx.ALL|wx.EXPAND, border=10)
		vbox.Add(btnsizer, flag=wx.ALIGN_CENTER|wx.TOP|wx.BOTTOM, border=10)
		pnl.SetSizer(vbox)

	def onAddModel(self, event):
		dlg = EditModelDialog(self, _("Add model"), self.provider)
		if dlg.ShowModal() == wx.ID_OK:
			modelName = dlg.getModelName()
			modelID = dlg.getModelID()
			self.modelList.InsertItem(self.modelList.GetItemCount(), modelName)
			self.modelList.SetItem(self.modelList.GetItemCount() - 1, 1, modelID)
			self.modelList.SetItem(self.modelList.GetItemCount() - 1, 2, "Custom")
		dlg.Destroy()

	def onModelListContextMenu(self, event):
		item = self.modelList.GetFirstSelected()
		if item == -1:
			return
		menu = wx.Menu()

		item_id = wx.NewIdRef()
		menu.Append(item_id, _("&Info"))
		self.Bind(wx.EVT_MENU, self.onInfo, id=item_id)

		item_id = wx.NewIdRef()
		menu.Append(item_id, _("&Edit"))
		self.Bind(wx.EVT_MENU, self.onEdit, id=item_id)

		item_id = wx.NewIdRef()
		menu.Append(item_id, _("&Delete"))
		self.Bind(wx.EVT_MENU, self.onDelete, id=item_id)

		self.PopupMenu(menu)
		menu.Destroy()

	def loadModels(self):
		for model in self.provider.get_models():
			self.modelList.InsertItem(self.modelList.GetItemCount(), model["name"])
			self.modelList.SetItem(self.modelList.GetItemCount() - 1, 1, model["id"])
			self.modelList.SetItem(self.modelList.GetItemCount() - 1, 2, model["type"])

	def onInfo(self, event):
		pass

	def onEdit(self, event):
		item = self.modelList.GetFirstSelected()
		if item == -1:
			return
		dlg = EditModelDialog(self, _("Edit model"), self.provider)
		dlg.txtModelName.SetValue(self.modelList.GetItemText(item))
		dlg.txtModelID.SetValue(self.modelList.GetItem(item, 1).GetText())
		if dlg.ShowModal() == wx.ID_OK:
			self.modelList.SetItem(item, 0, dlg.getModelName())
			self.modelList.SetItem(item, 1, dlg.getModelID())
		dlg.Destroy()

	def onDelete(self, event):
		item = self.modelList.GetFirstSelected()
		if item == -1:
			return
		self.modelList.DeleteItem(item)
