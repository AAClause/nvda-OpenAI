import os

API_KEY_FILENAME = "OpenAI.key"
API_KEY_ORG_FILENAME = "OpenAI_org.key"

class APIKeyManager:

	"""
	Manage API key
	"""

	def __init__(self, data_dir):
		self.data_dir = data_dir
		self.api_key_path = os.path.join(data_dir, API_KEY_FILENAME)
		self.api_key_org_path = os.path.join(data_dir, API_KEY_ORG_FILENAME)
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
			return None

	def get_api_key(self, use_org=False):
		if use_org:
			if self.api_key_org is None:
				self.api_key_org = self._read_api_key_from_file(self.api_key_org_path)
			return self.api_key_org or os.getenv("OPEN_AI_ORG_API_KEY")

		if self.api_key is None:
			self.api_key = self._read_api_key_from_file(self.api_key_path)
		return self.api_key or os.getenv("OPENAI_API_KEY")

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
