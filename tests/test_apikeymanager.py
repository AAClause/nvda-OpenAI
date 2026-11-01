import json

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


def test_legacy_key_files_move_into_accounts_json(key_store, tmp_path):
	(tmp_path / "OpenAI.key").write_text("sk-legacy\n")
	(tmp_path / "OpenAI_org.key").write_text("My org:=org-123")
	# Typoed file name some users have.
	(tmp_path / "MistralAI.kley").write_text("mistral-legacy")

	openai = key_store.get(Provider.OpenAI)
	account = openai.get_active_account()
	assert account["api_key"] == "sk-legacy"
	assert (account["org_name"], account["org_key"]) == ("My org", "org-123")
	assert openai.get_organization_key() == "org-123"
	assert key_store.get(Provider.MistralAI).get_api_key() == "mistral-legacy"
	assert not (tmp_path / "OpenAI.key").exists()
	assert not (tmp_path / "OpenAI_org.key").exists()
	assert not (tmp_path / "MistralAI.kley").exists()
	assert "sk-legacy" in (tmp_path / "accounts.json").read_text(encoding="utf-8")


def test_legacy_key_never_replaces_existing_accounts(key_store, tmp_path):
	acc = key_store.get(Provider.OpenAI).add_account(name="Current", api_key="sk-current")
	(tmp_path / "OpenAI.key").write_text("sk-legacy")
	key_store._migrated_dirs.clear()

	openai = key_store.get(Provider.OpenAI)
	assert [a["id"] for a in openai.list_accounts()] == [acc]
	assert openai.get_api_key() == "sk-current"
	assert (tmp_path / "OpenAI.key").exists()


def test_per_provider_account_files_are_merged(key_store, tmp_path):
	bucket = {"active_account_id": "a1", "accounts": [{"id": "a1", "name": "Work", "api_key": "sk-ant"}]}
	(tmp_path / "Anthropic_accounts.json").write_text(json.dumps(bucket))

	anthropic = key_store.get(Provider.Anthropic)
	assert anthropic.get_active_account_id() == "a1"
	assert anthropic.get_api_key() == "sk-ant"
	assert not (tmp_path / "Anthropic_accounts.json").exists()


def test_accounts_survive_a_restart(key_store, tmp_path):
	openai = key_store.get(Provider.OpenAI)
	first = openai.add_account(name="One", api_key="sk-1")
	second = openai.add_account(name="Two", api_key="sk-2", set_active=False)
	openai.remove_account(first)
	# A new NVDA session reads accounts.json again.
	key_store._managers.clear()
	key_store._store_cache_by_dir.clear()
	key_store._migrated_dirs.clear()
	key_store._loaded_data_dir = None
	key_store.load(str(tmp_path))

	reloaded = key_store.get(Provider.OpenAI)
	assert [a["id"] for a in reloaded.list_accounts()] == [second]
	# Removing the active account activates the remaining one.
	assert reloaded.get_api_key() == "sk-2"
