from AIHub.apiclient._google import interaction_audio_result
from AIHub.apiclient._parsers import (
	gemini_finish_reason,
	parse_anthropic,
	parse_chat_completion,
	parse_responses,
	responses_finish_reason,
)
from AIHub.apiclient._sse import iter_sse_data_blocks


def test_chat_completion_keeps_finish_reason():
	result = parse_chat_completion({"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]})
	assert result.choices[0].finish_reason == "content_filter"


def test_anthropic_refusal_is_reported():
	result = parse_anthropic({"content": [], "stop_reason": "refusal"})
	assert result.choices[0].finish_reason == "refusal"
	assert result.choices[0].message.content == ""


def test_gemini_blocked_prompt_reason():
	assert gemini_finish_reason({"promptFeedback": {"blockReason": "SAFETY"}}) == "safety"
	assert gemini_finish_reason({"candidates": [{"finishReason": "MAX_TOKENS"}]}) == "max_tokens"
	assert gemini_finish_reason({"candidates": [{"finishReason": "FINISH_REASON_UNSPECIFIED"}]}) is None


def test_responses_incomplete_and_url_citations():
	payload = {
		"status": "incomplete",
		"incomplete_details": {"reason": "max_output_tokens"},
		"output": [
			{
				"type": "message",
				"content": [
					{
						"type": "output_text",
						"text": "Answer",
						"annotations": [
							{"type": "url_citation", "url": "https://example.com/a"},
							{"type": "url_citation", "url": "https://example.com/a"},
						],
					}
				],
			}
		],
	}
	assert responses_finish_reason(payload) == "max_output_tokens"
	result = parse_responses(payload)
	assert result.choices[0].message.content == "Answer"
	assert result.citations == ["https://example.com/a"]


def test_interaction_audio_formats():
	def payload(mime):
		block = {"type": "audio", "data": "QUJD"}
		if mime:
			block["mime_type"] = mime
		return {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "x"}, block]}]}

	assert interaction_audio_result(payload("audio/ogg_opus"))["audio"]["format"] == "ogg"
	assert interaction_audio_result(payload("audio/wav"))["audio"]["format"] == "wav"
	assert interaction_audio_result(payload(None))["audio"]["format"] == "mp3"
	assert interaction_audio_result({"steps": []}) is None


def test_sse_blocks_join_multiline_data_and_accept_missing_space():
	raw = [b"data: {\"a\":1}\n\n", b"data:x\ndata:y\r\n\r\n", b": comment\nevent: e\ndata: last"]
	assert list(iter_sse_data_blocks(raw)) == [b'{"a":1}', b"x\ny", b"last"]
