import json

from AIHub.apiclient._content import _convert_messages_to_anthropic, _messages_to_responses_input
from AIHub.apiclient._google import build_gemini_generate_content_body, messages_to_gemini_contents
from AIHub.apiclient._parsers import parse_anthropic, parse_chat_completion, parse_gemini_generate_content, parse_responses
from AIHub.apiclient._types import ChatCompletion, Choice, ChoiceMessage
from AIHub import toolcalling

TOOL_TURN = [
	{"role": "user", "content": "What time is it?"},
	{
		"role": "assistant",
		"content": "",
		"tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_current_datetime", "arguments": "{}"}}],
	},
	{"role": "tool", "tool_call_id": "call_1", "name": "get_current_datetime", "content": "noon"},
]


def test_parsers_extract_tool_calls():
	chat = parse_chat_completion({"choices": [{"message": {"content": None, "tool_calls": [
		{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}
	]}}]})
	assert chat.choices[0].message.tool_calls == [{"id": "c1", "name": "f", "arguments": "{\"a\": 1}"}]

	content = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}}]
	claude = parse_anthropic({"content": content, "stop_reason": "tool_use"}).choices[0].message
	assert claude.tool_calls == [{"id": "t1", "name": "f", "arguments": json.dumps({"a": 1})}]
	assert claude.raw_turn == content

	parts = [{"functionCall": {"name": "f", "args": {"a": 1}}, "thoughtSignature": "sig"}]
	gemini = parse_gemini_generate_content({"candidates": [{"content": {"parts": parts}}]}).choices[0].message
	assert gemini.tool_calls[0]["name"] == "f"
	assert gemini.raw_turn == parts

	output = [{"type": "reasoning", "id": "r1"}, {"type": "function_call", "call_id": "fc1", "name": "f", "arguments": "{}"}]
	responses = parse_responses({"output": output}).choices[0].message
	assert responses.tool_calls == [{"id": "fc1", "name": "f", "arguments": "{}"}]
	assert responses.raw_turn == output


def test_anthropic_tool_turns_and_raw_round_trip():
	_system, msgs = _convert_messages_to_anthropic(TOOL_TURN)
	assert msgs[1] == {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "get_current_datetime", "input": {}}]}
	assert msgs[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "noon"}]}
	raw = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "tool_use", "id": "call_1", "name": "x", "input": {}}]
	with_raw = [TOOL_TURN[0], dict(TOOL_TURN[1], _raw_turn=raw, _raw_provider="Anthropic"), TOOL_TURN[2]]
	assert _convert_messages_to_anthropic(with_raw)[1][1]["content"] == raw


def test_gemini_tool_turns_and_function_declarations():
	_system, contents = messages_to_gemini_contents(TOOL_TURN)
	assert contents[1] == {"role": "model", "parts": [{"functionCall": {"name": "get_current_datetime", "args": {}}}]}
	assert contents[2] == {"role": "user", "parts": [{"functionResponse": {"name": "get_current_datetime", "response": {"result": "noon"}}}]}
	body = build_gemini_generate_content_body("gemini-4-argon", TOOL_TURN[:1], {"tools": toolcalling.tool_definitions()})
	names = [d["name"] for d in body["tools"][0]["functionDeclarations"]]
	assert names == list(toolcalling.LOCAL_TOOLS)


def test_responses_tool_turns():
	items = _messages_to_responses_input(TOOL_TURN)
	assert items[1] == {"type": "function_call", "call_id": "call_1", "name": "get_current_datetime", "arguments": "{}"}
	assert items[2] == {"type": "function_call_output", "call_id": "call_1", "output": "noon"}


def test_run_tool_loop_executes_calls_and_sums_usage(monkeypatch):
	monkeypatch.setitem(
		toolcalling.LOCAL_TOOLS, "get_current_datetime", ("now", {"type": "object", "properties": {}}, lambda _a: "noon")
	)
	seen = []

	def create(params):
		seen.append(params)
		assert params["stream"] is False
		if len(seen) == 1:
			message = ChoiceMessage("", tool_calls=[{"id": "c1", "name": "get_current_datetime", "arguments": "{}"}], raw_turn=["raw"])
			return ChatCompletion([Choice(message)], usage={"input_tokens": 10, "output_tokens": 2})
		return ChatCompletion([Choice(ChoiceMessage("It is noon."))], usage={"input_tokens": 20, "output_tokens": 5})

	response = toolcalling.run_tool_loop(create, {"messages": [TOOL_TURN[0]], "stream": True}, "Anthropic")
	assert response.choices[0].message.content == "It is noon."
	assert response.usage == {"input_tokens": 30, "output_tokens": 7}
	second = seen[1]["messages"]
	assert second[1]["_raw_turn"] == ["raw"] and second[1]["_raw_provider"] == "Anthropic"
	assert second[2] == {"role": "tool", "tool_call_id": "c1", "name": "get_current_datetime", "content": "noon"}


def test_run_tool_reports_unknown_tools_and_bad_arguments():
	assert "unknown tool" in toolcalling.run_tool("nope", "{}")
	assert json.loads(toolcalling.run_tool("get_current_datetime", "not json"))["iso"]
