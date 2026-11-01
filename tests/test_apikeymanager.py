from AIHub.consts import Provider


def test_custom_endpoint_never_falls_back_to_the_openai_key(key_store, monkeypatch):
	monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-key")
	monkeypatch.setenv("OPENAI_KEY", "sk-real-openai-key")
	custom = key_store.get(Provider.CustomOpenAI)
	acc = custom.add_account(name="Local", api_key="", base_url="http://localhost:8080/v1")
	assert custom.get_api_key(account_id=acc) is None
	assert key_store.get(Provider.OpenAI).get_api_key() == "sk-real-openai-key"


def test_custom_endpoint_uses_its_own_environment_variable(key_store, monkeypatch):
	monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")
	custom = key_store.get(Provider.CustomOpenAI)
	acc = custom.add_account(name="Proxy", api_key="", base_url="https://proxy.example.com/v1")
	assert custom.get_api_key(account_id=acc) == "compat-key"
