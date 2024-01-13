import json
import urllib.request
import addonHandler

addonHandler.initTranslation()

_OpenRouterModels = None

class Model:

	def __init__(
		self,
		id_: str,
		description: str='',
		contextWindow: int=32768,
		maxOutputToken: int=-1,
		maxTemperature: float=2.0,
		defaultTemperature: float=1.0,
		vision: bool=False,
		preview=False,
		name: str='',
		**kwargs
	):
		self.id = id_
		self.name = name or id_
		self.description = description
		self.contextWindow = contextWindow
		self.maxOutputToken = maxOutputToken
		self.maxTemperature = maxTemperature
		self.defaultTemperature = defaultTemperature
		self.vision = vision
		self.preview = preview

	def getDescription(self):
		description = self.description.rstrip('.')
		if self.preview:
			description += " (" + _("preview: not yet suited for production traffic") + ")"
		return description

	def __repr__(self):
		return f"Model(id={self.id}, name={self.name}, description={self.description}, contextWindow={self.contextWindow}, maxOutputToken={self.maxOutputToken}, maxTemperature={self.maxTemperature}, defaultTemperature={self.defaultTemperature})"

	def __str__(self):
		id_ = self.id
		contextWindow = self.contextWindow
		maxOutputToken = self.maxOutputToken
		s = f"{self.name} ({id_}"
		if contextWindow > 0:
			label = _("Context window:")
			s += f". {label} {contextWindow}"
		if maxOutputToken > 0:
			label = _("max output token:")
			s += f", {label} {maxOutputToken}"
		s += ')'
		return s

	def __hash__(self):
		return hash((self.name, self.contextWindow, self.maxOutputToken, self.maxTemperature, self.defaultTemperature))


def getOpenRouterModels():
	global _OpenRouterModels
	if _OpenRouterModels:
		return _OpenRouterModels
	url = "https://openrouter.ai/api/v1/models"
	req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
	with urllib.request.urlopen(req) as response:
		data = json.loads(response.read())
		models = []
		for model in sorted(
			data["data"],
			key=lambda m: m["name"].lower()
		):
			models.append(Model(
				id_=model['id'],
				name=model['name'],
				description=model['description'],
				contextWindow=int(model['context_length']),
				maxOutputToken=model["top_provider"]['max_completion_tokens'] or -1,
				maxTemperature=2,
				defaultTemperature=0.7,
				vision="#multimodal" in model['description'],
				preview="-preview" in model['id']
			))
		_OpenRouterModels = models
	return models
