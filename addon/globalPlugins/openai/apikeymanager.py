import os

AVAILABLE_PROVIDERS = [
	"OpenAI",
	"MistralAI",
	"OpenRouter"
]

_managers = {}

class APIKeyManager:

	"""
	Manage API key
	"""

	def __init__(
		self,
		data_dir,
		provider="OpenAI"
	):
		if provider not in AVAILABLE_PROVIDERS:
			raise ValueError(f"Unknown provider: {provider}")
		self.data_dir = data_dir
		self.provider = provider
		self.api_key_path = os.path.join(
			data_dir,
			f"{provider}.key"
		)
		self.api_key_org_path = os.path.join(
			data_dir,
			f"{provider}_org.key"
		)
		self.api_key = None
		self.api_key_org = None
		self.ensure_data_dir()

	def ensure_data_dir(self):
		if not os.path.isdir(self.data_dir):
			os.makedirs(self.data_dir)

	def _read_api_key_from_file(self, file_path):
		try:
			with open(file_path, "r") as f:
				return f.read().strip()
		except FileNotFoundError:
			return ""

	def get_api_key(self, use_org=False):
		if use_org:
			if self.api_key_org is None:
				self.api_key_org = self._read_api_key_from_file(self.api_key_org_path)
			return self.api_key_org or os.getenv("OPEN_AI_ORG_API_KEY")

		if self.api_key is None:
			self.api_key = self._read_api_key_from_file(self.api_key_path)
		return self.api_key or (
			os.getenv("OPENAI_API_KEY" if self.provider == "OpenAI" else "OPENROUTER_API_KEY")
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

	def save_api_key(self, key, org=False, org_name=None):
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
		return self.get_api_key() is not None


def load(
	data_dir: str
):
	"""
	Initialize API key manager for all providers
	"""
	global _managers
	for provider in AVAILABLE_PROVIDERS:
		_managers[provider] = APIKeyManager(data_dir, provider)


def get(
	provider_name: str
) -> APIKeyManager:
	"""
	Get API key manager for provider_name
	"""
	if provider_name not in AVAILABLE_PROVIDERS:
		raise ValueError(f"Unknown provider: {provider_name}. Available: {AVAILABLE_PROVIDERS}")
	return _managers[provider_name]
