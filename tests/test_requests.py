from AIHub.anthropicthinking import (
	anthropic_reasoning_always_on,
	anthropic_supports_default_fallback,
	anthropic_use_adaptive_thinking,
	anthropic_web_search_tool,
)
from AIHub.apiclient._client import build_multipart_body
from AIHub.model import ollama_catalog_fields
from AIHub.providertools_helpers import parse_modelfile
from AIHub.reasoningrequest import (
	detect_reasoning_mandatory,
	openai_fallback_efforts,
	openai_reasoning_model,
)


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


def test_anthropic_profiles():
	assert anthropic_reasoning_always_on("claude-opus-5-5")
	assert not anthropic_reasoning_always_on("claude-opus-5")
	assert anthropic_use_adaptive_thinking("claude-haiku-5-5", adaptive_thinking=None)
	assert not anthropic_use_adaptive_thinking("claude-haiku-4-5", adaptive_thinking=None)
	assert anthropic_web_search_tool("claude-sonnet-4-6")["type"] == "web_search_20260209"
	assert anthropic_web_search_tool("claude-haiku-5-5")["type"] == "web_search_20250305"
	assert anthropic_supports_default_fallback("claude-opus-5-5")
	assert anthropic_supports_default_fallback("claude-sonnet-5-5")
	assert not anthropic_supports_default_fallback("claude-haiku-5-5")
	assert not anthropic_supports_default_fallback("claude-sonnet-5")


def test_openai_gpt6_fallback():
	assert openai_reasoning_model("gpt-6.1-sol")
	assert openai_fallback_efforts("gpt-6-astra")[-1] == "max"
	assert detect_reasoning_mandatory("CustomOpenAI", "gpt-6.1-sol", {})
	assert not detect_reasoning_mandatory("CustomOpenAI", "gpt-6-luna", {})
	# Catalog metadata wins over the id-based fallback.
	assert not detect_reasoning_mandatory("OpenAI", "gpt-6-astra", {"reasoning": {"mandatory": False}})


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
