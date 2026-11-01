import pytest

from AIHub.apiclient._usage import _normalize_usage_from_payload
from AIHub.pricingutils import effective_pricing, estimate_usage_cost


def test_gemini_thinking_tokens_are_billed_as_output():
	payload = {
		"usageMetadata": {
			"promptTokenCount": 100,
			"candidatesTokenCount": 50,
			"thoughtsTokenCount": 200,
			"totalTokenCount": 350,
		},
		"candidates": [{"groundingMetadata": {"webSearchQueries": ["a", "b"]}}],
	}
	usage = _normalize_usage_from_payload(payload)
	assert usage["output_tokens"] == 250
	assert usage["reasoning_tokens"] == 200
	assert usage["web_search_requests"] == 2
	pricing = {"prompt": "0.000002", "completion": "0.00001", "web_search": "0.014"}
	assert estimate_usage_cost(usage, pricing) == pytest.approx(100 * 2e-6 + 250 * 1e-5 + 2 * 0.014)


def test_anthropic_one_hour_cache_writes_and_web_search():
	payload = {
		"usage": {
			"input_tokens": 10,
			"output_tokens": 100,
			"cache_read_input_tokens": 1000,
			"cache_creation_input_tokens": 500,
			"cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 300},
			"server_tool_use": {"web_search_requests": 3},
		}
	}
	usage = _normalize_usage_from_payload(payload)
	assert usage["cache_creation_1h_input_tokens"] == 300
	assert usage["web_search_requests"] == 3
	pricing = {
		"prompt": "0.000004",
		"completion": "0.00002",
		"input_cache_read": "0.0000002",
		"input_cache_write": "0.000005",
		"input_cache_write_1h": "0.000008",
		"web_search": "0.01",
	}
	expected = 10 * 4e-6 + 100 * 2e-5 + 1000 * 2e-7 + 200 * 5e-6 + 300 * 8e-6 + 3 * 0.01
	assert estimate_usage_cost(usage, pricing) == pytest.approx(expected)


def test_openai_responses_web_search_calls_and_long_context_tier():
	payload = {
		"usage": {"input_tokens": 300000, "output_tokens": 10},
		"output": [{"type": "web_search_call"}, {"type": "message"}],
	}
	usage = _normalize_usage_from_payload(payload)
	assert usage["web_search_requests"] == 1
	pricing = {
		"prompt": "0.000002",
		"completion": "0.00001",
		"web_search": "0.01",
		"overrides": [{"min_prompt_tokens": 272000, "prompt": "0.000004", "completion": "0.000015"}],
	}
	assert estimate_usage_cost(usage, pricing) == pytest.approx(300000 * 4e-6 + 10 * 1.5e-5 + 0.01)


def test_effective_pricing_keeps_base_rates_below_threshold():
	pricing = {"prompt": "1", "overrides": [{"min_prompt_tokens": 10, "prompt": "2"}]}
	assert effective_pricing(pricing, 5)["prompt"] == "1"
	assert effective_pricing(pricing, 10)["prompt"] == "2"
	assert "overrides" not in effective_pricing(pricing, 10)


def test_missing_pricing_costs_nothing():
	assert estimate_usage_cost({"input_tokens": 10}, {}) == 0.0
