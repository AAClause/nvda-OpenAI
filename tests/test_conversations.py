import json

import pytest

from AIHub import conversations


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
	conv_dir = tmp_path / "conversations"
	conv_dir.mkdir()
	monkeypatch.setattr(conversations, "DATA_DIR", str(tmp_path))
	monkeypatch.setattr(conversations, "CONVERSATIONS_DIR", str(conv_dir))
	monkeypatch.setattr(conversations, "ATTACHMENTS_DIR", str(conv_dir / "attachments"))
	monkeypatch.setattr(conversations, "INDEX_PATH", str(conv_dir / conversations.INDEX_FILENAME))
	return tmp_path


def _write_conversation(data_dir, conv_id, paths):
	data = {"id": conv_id, "blocks": [{"prompt": "hi", "pathList": [{"path": str(p)} for p in paths]}]}
	(data_dir / "conversations" / f"{conv_id}.json").write_text(json.dumps(data), encoding="utf-8")


def test_delete_conversation_only_removes_its_own_files_in_the_data_folder(data_dir, tmp_path_factory):
	media = data_dir / "media"
	media.mkdir()
	owned = media / "owned.png"
	shared = media / "shared.png"
	# A file the user attached from their own folders must survive.
	user_file = tmp_path_factory.mktemp("Documents") / "report.pdf"
	for path in (owned, shared, user_file):
		path.write_bytes(b"x")
	attachments = data_dir / "conversations" / "attachments" / "c1"
	attachments.mkdir(parents=True)
	(attachments / "img_0_0.png").write_bytes(b"x")
	_write_conversation(data_dir, "c1", [owned, shared, user_file])
	_write_conversation(data_dir, "c2", [shared])

	assert conversations.delete_conversation("c1")

	assert not (data_dir / "conversations" / "c1.json").exists()
	assert not owned.exists()
	assert not attachments.exists()
	assert shared.exists()
	assert user_file.exists()


def test_delete_unknown_conversation_returns_false(data_dir):
	assert conversations.delete_conversation("missing") is False


@pytest.mark.parametrize("path, expected", [
	("media/a.png", True),
	("conversations/attachments/c1/a.png", True),
	("../outside.png", False),
	("https://example.com/a.png", False),
	("", False),
])
def test_is_under_data_dir(data_dir, path, expected):
	full = path if not path or "://" in path else str(data_dir / path)
	assert conversations._is_under_data_dir(full) is expected
