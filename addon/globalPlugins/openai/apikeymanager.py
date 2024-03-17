import os
from typing import List
from logHandler import log
from . import addoncfg
from .model import Model, get_models

AVAILABLE_PROVIDERS = [
	"OpenAI",
	"MistralAI",
	"Ollama",
	"OpenRouter",
]

BASE_URLs = {
	"MistralAI": "https://api.mistral.ai/v1",
	"OpenAI": "https://api.openai.com/v1",
	"OpenRouter": "https://openrouter.ai/api/v1"
}

_managers = {}

def get_base_url(provider_name: str) -> str:
	if provider_name in BASE_URLs:
		return BASE_URLs.get(provider_name, "")
	data = addoncfg.get()
	if data:
		base_url = data.get("providers", {}).get(provider_name, {}).get("base_url", "")
		if not base_url:
			log.warning(f"Base URL for {provider_name} not found in the config file")
		return base_url
	raise ValueError(f"Unknown provider: {provider_name}. Available: {AVAILABLE_PROVIDERS}")


class Provider:

	"""
	Manage API key
	"""

	def __init__(
		self,
		data_dir,
		name,
		organization_mode_available=False,
		custom=False,
		require_api_key=True
	):
		if name not in AVAILABLE_PROVIDERS:
			raise ValueError(f"Unknown provider: {name}")
		self.data_dir = data_dir
		self.name = name
		self.base_url = get_base_url(name)
		self.organization_mode_available = organization_mode_available
		self.custom = custom
		self.require_api_key = require_api_key
		if name == "Ollama":
			self.require_api_key = False
			self.custom = True
		if self.require_api_key:
			self.api_key = None
			self.api_key_org = None
			self.api_key_path = os.path.join(
				data_dir,
				f"{name}.key"
			)
			self.api_key_org_path = os.path.join(
				data_dir,
				f"{name}_org.key"
			)
			self._ensure_data_dir()

	def _ensure_data_dir(self):
		if not os.path.isdir(self.data_dir):
			os.makedirs(self.data_dir)

	def _read_api_key_from_file(self, file_path):
		try:
			with open(file_path, "r") as f:
				return f.read().strip()
		except FileNotFoundError:
			return ""

	def get_api_key(self, use_org=False):
		if not self.require_api_key:
			return None
		if use_org:
			if self.api_key_org is None:
				self.api_key_org = self._read_api_key_from_file(self.api_key_org_path)
			return self.api_key_org or os.getenv("OPEN_AI_ORG_API_KEY")

		if self.api_key is None:
			self.api_key = self._read_api_key_from_file(self.api_key_path)
		return self.api_key or (
			os.getenv("OPENAI_API_KEY" if self.name == "OpenAI" else "OPENROUTER_API_KEY")
		)

	def get_organization_key(self):
		organization = self.get_api_key(use_org=True)
		if not organization or organization.count(":=") != 1:
			return None
		return organization.split(":=")[1]

	def get_organization_name(self):
		organization = self.get_api_key(use_org=True)
		if not organization or organization.count(":") != 1:
			return None
		return organization.split(":")[0]

	def save(self, key, org=False, org_name=None):
		file_path = self.api_key_org_path if org else self.api_key_path
		with open(file_path, "w") as f:
			content = key.strip()
			if org:
				content = f"{org_name}:={content}"
			f.write(content)
		if org:
			self.api_key_org = f"{org_name}:={key}"
		else:
			self.api_key = key

	def isReady(self):
		return (
			self.base_url
			and self.base_url.startswith("http")
			and (not self.require_api_key or self.get_api_key() is not None)
		)

	def get_models(self) -> List[Model]:
		return get_models(self.name)

	def to_dict(self):
		return {
			"base_url": self.base_url,
			"models": [model.to_dict() for model in self.get_models()],
		}


def load(
	data_dir: str
):
	"""
	Initialize API key manager for all providers
	"""
	global _managers
	for provider in AVAILABLE_PROVIDERS:
		_managers[provider] = Provider(data_dir, provider)


def get(
	provider_name: str
) -> Provider:
	"""
	Get API key manager for provider_name
	"""
	if provider_name not in AVAILABLE_PROVIDERS:
		raise ValueError(f"Unknown provider: {provider_name}. Available: {AVAILABLE_PROVIDERS}")
	return _managers[provider_name]


def getReadyProviders():
	return [_managers[provider] for provider in _managers if _managers[provider].isReady()]
