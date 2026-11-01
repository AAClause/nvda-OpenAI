from AIHub.apiclient._client import build_multipart_body
from AIHub.chatparams import _params_for_error_log
from AIHub.model import ollama_catalog_fields
from AIHub.providertools_helpers import parse_modelfile


def test_multipart_repeats_lists_and_sanitizes_file_names():
	body, content_type = build_multipart_body(
		{"model": "m", "diarize": True, "context_bias": ["a", "b"], "temperature": 0},
		[("files", 'x"y.pdf', b"DATA", "application/pdf")],
	)
	boundary = content_type.split("boundary=", 1)[1]
	text = body.decode()
	assert text.count('name="context_bias"') == 2
	assert 'name="diarize"\r\n\r\ntrue' in text
	assert 'name="temperature"\r\n\r\n0' in text
	assert 'filename="x_y.pdf"' in text
	assert text.endswith(f"--{boundary}--\r\n")


def test_parse_modelfile():
	fields = parse_modelfile(
		'FROM llama3.2\nPARAMETER temperature 0.7\nPARAMETER stop "<|end|>"\nPARAMETER stop "<|eot|>"\n'
		'SYSTEM """You are\nhelpful."""\nMESSAGE user Hi\n# comment\nLICENSE MIT'
	)
	assert fields == {
		"from": "llama3.2",
		"system": "You are\nhelpful.",
		"license": "MIT",
		"parameters": {"temperature": 0.7, "stop": ["<|end|>", "<|eot|>"]},
		"messages": [{"role": "user", "content": "Hi"}],
	}


def test_ollama_show_metadata():
	named = ollama_catalog_fields({
		"capabilities": ["completion", "thinking", "tools"],
		"thinking": {"values": ["low", "medium", "high"], "default": "medium"},
	})
	assert named["supported_parameters"] == ["tools"]
	assert named["reasoning"]["supported_efforts"] == ["low", "medium", "high"]
	assert named["reasoning"]["mandatory"] is True
	toggle = ollama_catalog_fields({
		"capabilities": ["completion", "thinking", "vision"],
		"thinking": {"values": [True, False], "default": True},
	})
	assert toggle["input_modalities"] == ["text", "image"]
	assert toggle["reasoning"] == {"mandatory": False, "supports_max_tokens": False, "default_enabled": True}
	assert ollama_catalog_fields({"capabilities": ["completion"], "thinking": {"values": [False]}}) == {}


def test_error_log_parameters_never_contain_the_conversation():
	params = {"model": "m", "temperature": 0.2, "messages": [{"role": "user", "content": "my secret diary"}]}
	logged = _params_for_error_log(params)
	assert "my secret diary" not in repr(logged)
	assert logged == {"model": "m", "temperature": 0.2, "messages": "<1 message(s) omitted>"}
	assert params["messages"][0]["content"] == "my secret diary"
