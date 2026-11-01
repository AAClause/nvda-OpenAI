import pytest

from AIHub.apiclient._think_tags import (
	_apply_think_chain_to_chunk,
	_flush_think_chain,
	_new_think_chain_states,
	_split_ollama_think_inline,
)


def _stream(chunks):
	states = _new_think_chain_states()
	visible, reasoning = [], []
	for chunk in chunks:
		v, r = _apply_think_chain_to_chunk(chunk, states)
		visible.append(v)
		reasoning.append(r)
	v, r = _flush_think_chain(states)
	return "".join(visible) + v, "".join(reasoning) + r


CASES = [
	("Hi <think>plan A</think>answer", "Hi answer", "plan A"),
	("<THINKING>Upper</Thinking>case", "case", "Upper"),
	("<thought>g</thought>x <think>t</think>y", "x y", "tg"),
	# Text that only looks like the start of a tag is not held back forever.
	("if a < b and <thin then", "if a < b and <thin then", ""),
	# A tag left open at the end of the answer is reasoning.
	("Answer<think>cut off", "Answer", "cut off"),
]


@pytest.mark.parametrize("text, visible, reasoning", CASES)
def test_inline_split(text, visible, reasoning):
	v, r, _in_think = _split_ollama_think_inline(text)
	assert (v, sorted(r)) == (visible, sorted(reasoning))


@pytest.mark.parametrize("text, visible, reasoning", CASES)
def test_stream_split_does_not_depend_on_chunk_boundaries(text, visible, reasoning):
	# Every two-chunk split, and one character at a time. Reasoning from
	# different tag pairs may come out in any order.
	chunkings = [[text[:cut], text[cut:]] for cut in range(1, len(text))] + [list(text)]
	for chunks in chunkings:
		v, r = _stream(chunks)
		assert v == visible, chunks
		assert sorted(r) == sorted(reasoning), chunks
