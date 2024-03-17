import json
import urllib.request
import addonHandler
from logHandler import log
from . import addoncfg

addonHandler.initTranslation()

_models = {}
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
		provider='',
		extraInfo=None,
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
		self.provider = provider
		self.extraInfo = extraInfo or {}

	def getDescription(self):
		description = self.description.rstrip('.')
		if self.preview:
			description += " (" + _("preview: not yet suited for production traffic") + ")"
		return description

	def __repr__(self):
		return f"Model(id={self.id}, name={self.name}, description={self.description}, contextWindow={self.contextWindow}, maxOutputToken={self.maxOutputToken}, maxTemperature={self.maxTemperature}, defaultTemperature={self.defaultTemperature})"

	def __str__(self):
		name = self.name
		id_ = self.id
		contextWindow = self.contextWindow
		maxOutputToken = self.maxOutputToken
		s = name + " ["
		l = []
		l.append(
			_("provider: {provider}").format(
				provider=self.provider
			)
		)
		if id_ != name:
			l.append(_("ID: {id}").format(id=id_) )
		if contextWindow > 0:
			l.append(
				_("context window: {contextWindow}").format(
					contextWindow=contextWindow
				)
			)
		if maxOutputToken > 0:
			l.append(
				_("max output tokens: {maxOutputToken}").format(
					maxOutputToken=maxOutputToken
				)
			)
		s += ". ".join(l)
		s += ']'
		return s

	def __hash__(self):
		return hash((self.provider, self.id))

	def to_dict(self):
		return {
			"id": self.id,
			"name": self.name,
			"description": self.description,
			"contextWindow": self.contextWindow,
			"maxOutputToken": self.maxOutputToken,
			"maxTemperature": self.maxTemperature,
			"defaultTemperature": self.defaultTemperature,
			"vision": self.vision,
			"preview": self.preview
		}


def getOpenRouterModels():
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
				maxOutputToken=model.get("top_provider", {}).get("max_completion_tokens") or -1,
				maxTemperature=2,
				defaultTemperature=0.7,
				vision="#multimodal" in model['description'],
				preview="-preview" in model['id'],
				extraInfo={k: v for k, v in model.items() if k not in ("id", "name", "description", "context_length", "top_provider")})
			)
		return models
	return models


# References:
# - https://platform.openai.com/docs/models/
# - https://docs.mistral.ai/platform/endpoints/
# - https://openrouter.ai/api/v1/models
def get_models(provider_name):
	models = []
	if provider_name in _models:
		return _models[provider_name]
	if provider_name == "OpenAI":
		models = [
			Model(
				"gpt-3.5-turbo",
				# Translators: This is a model description
				_("Points to one of the most recent iterations of gpt-3.5 model."),
				16385,
				4096
			),
			Model(
				"gpt-3.5-turbo-0125",
				# Translators: This is a model description
				_("The latest GPT-3.5 Turbo model with higher accuracy at responding in requested formats and a fix for a bug which caused a text encoding issue for non-English language function calls."),
				16385,
				4096
			),
			Model(
				"gpt-3.5-turbo-1106",
				# Translators: This is a model description
				_("Updated GPT 3.5 Turbo. The latest GPT-3.5 Turbo model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."),
				16385,
				4096
			),
			Model(
				"gpt-3.5-turbo-0613",
				# Translators: This is a model description
				_("Same capabilities as the standard gpt-3.5-turbo model but with 4 times the context"),
				16384,
				4096
			),
			Model(
				"gpt-4-turbo-preview",
				# Translators: This is a model description
				_("Points to one of the most recent iterations of gpt-4 model."),
				128000,
				4096
			),
			Model(
				"gpt-4-0125-preview",
				# Translators: This is a model description
				_("The latest GPT-4 model intended to reduce cases of “laziness” where the model doesn’t complete a task."),
				128000,
				4096
			),
			Model(
				"gpt-4-1106-preview",
				# Translators: This is a model description
				_("GPT-4 Turbo model featuring improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more."),
				128000,
				4096,
				preview=True
			),
			Model(
				"gpt-4-vision-preview",
				# Translators: This is a model description
				_("GPT-4 Turbo with vision. Ability to understand images, in addition to all other GPT-4 Turbo capabilities."),
				128000,
				4096,
				vision=True,
				preview=True
			),
			Model(
				"gpt-4-0613",
				# Translators: This is a model description
				_("More capable than any GPT-3.5 model, able to do more complex tasks, and optimized for chat"),
				8192
			),
			Model(
				"gpt-4-32k-0613",
				# Translators: This is a model description
				_("Same capabilities as the standard gpt-4 mode but with 4x the context length."),
				32768,
				8192
			)
		]
	elif provider_name == "MistralAI":
		models = [
			Model(
				"open-mistral-7b",
				# Translators: This is a model description
				_("aka %s") % "mistral-tiny-2312",
				32000,
				maxTemperature=1.0,
				defaultTemperature=0.7
			),
			Model(
				"open-mixtral-8x7b",
				# Translators: This is a model description
				_("aka %s") % "mistral-small-2312",
				32000,
				maxTemperature=1.0,
				defaultTemperature=0.7
			),
			Model(
				"mistral-small-latest",
				# Translators: This is a model description
				_("Simple tasks (Classification, Customer Support, or Text Generation)"),
				32000,
				maxTemperature=1.0,
				defaultTemperature=0.7
			),
			Model(
				"mistral-medium-latest",
				# Translators: This is a model description
				_("Intermediate tasks that require moderate reasoning (Data extraction, Summarizing a Document, Writing emails, Writing a Job Description, or Writing Product Descriptions)"),
				32000,
				maxTemperature=1.0,
				defaultTemperature=0.7
			),
			Model(
				"mistral-large-latest",
				# Translators: This is a model description
				_("Complex tasks that require large reasoning capabilities or are highly specialized (Synthetic Text Generation, Code Generation, RAG, or Agents)"),
				32000,
				maxTemperature=1.0,
				defaultTemperature=0.7
			)
		]
	elif provider_name == "OpenRouter":
		models = getOpenRouterModels()
	else:
		data = addoncfg.get()
		models_ = data.get("providers", {}).get(provider_name, {}).get("models", [])
		for model in models_:
			try:
				models.append(
					Model(
						model["id"],
						model.get("description", ""),
						model.get("contextWindow", 32768),
						model.get("maxOutputToken", -1),
						model.get("maxTemperature", 2.0),
						model.get("defaultTemperature", 1.0),
						model.get("vision", False),
						model.get("preview", False),
						extraInfo={k: v for k, v in model.items() if k not in ("id", "description", "contextWindow", "maxOutputToken", "maxTemperature", "defaultTemperature", "vision", "preview")}
					)
				)
			except Exception as e:
				log.error(f"Error creating model: {e}\n{json.dumps(model, indent=2)}")
	for model in models:
		model.provider = provider_name
	_models[provider_name] = models
	return models
