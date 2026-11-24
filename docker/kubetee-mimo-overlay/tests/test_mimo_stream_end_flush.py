# Copyright 2023-2024 SGLang Team
# Licensed under the Apache License, Version 2.0 (the "License");
#
# KubeTEE overlay test (0004): MiMo streaming must not swallow tool calls, nor
# the prose around them, and must not leak tool-call markup as text.
# CPU-only, no model weights.
#
# Covers upstream sgl-project/sglang#46566 (items 3 and 4), #41909 and #50615:
#   A. text buffered after the last tool call must reach the client (either in
#      the call's own chunk or via finish() — never dropped)
#   B. non-streaming parse must keep prose after a call
#   C. a chunk holding more than one complete <tool_call> block yields them all
#   D. the FIRST tool call must never be passed through as plain text
#   E. a tag split across chunk boundaries is neither lost nor leaked

import unittest

from sglang.srt.entrypoints.openai.protocol import Function, Tool
from sglang.srt.function_call.mimo_detector import MiMoDetector

TOOLS = [
    Tool(
        type="function",
        function=Function(
            name=name,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        ),
    )
    for name in ("search", "fetch")
]


def _block(name: str, values: dict) -> str:
    params = "".join(f"<parameter={k}>{v}</parameter>" for k, v in values.items())
    return f"<tool_call>\n<function={name}>\n{params}\n</function>\n</tool_call>"


def _feed(det, chunks):
    """Drive the detector like the serving loop: chunks, then flush."""
    text, calls = "", []
    for chunk in chunks:
        res = det.parse_streaming_increment(chunk, TOOLS)
        text += res.normal_text or ""
        calls.extend(res.calls)
    end = det.finish(TOOLS)
    return text + (end.normal_text or ""), calls + list(end.calls)


class TestMimoStreamEndFlush(unittest.TestCase):
    def test_trailing_text_after_a_call_is_delivered(self):
        """A: the answer following a tool call must reach the client."""
        det = MiMoDetector()
        text, calls = _feed(
            det, [_block("search", {"x": "q"}) + "Here is the answer: 42."]
        )
        self.assertEqual([c.name for c in calls], ["search"])
        self.assertIn("Here is the answer: 42.", text)
        # ...and it must not leak the tool-call markup itself.
        self.assertNotIn("<tool_call>", text)

    def test_finish_releases_text_held_for_a_possible_tag(self):
        """A: text held back for a straddling tag is flushed at stream end."""
        det = MiMoDetector()
        res = det.parse_streaming_increment(
            _block("search", {"x": "q"}) + "done <tool_", TOOLS
        )
        # Only the partial tag is held back; the prose is already delivered.
        self.assertIn("done", res.normal_text)
        end = det.finish(TOOLS)
        self.assertIn("<tool_", end.normal_text)
        # Idempotent: a second flush yields nothing.
        self.assertFalse(det.finish(TOOLS).normal_text)

    def test_detect_and_parse_keeps_text_after_the_call(self):
        """B: non-streaming path must not drop prose after a call."""
        det = MiMoDetector()
        text = "Before. " + _block("search", {"x": "q"}) + " After."
        res = det.detect_and_parse(text, TOOLS)
        self.assertEqual([c.name for c in res.calls], ["search"])
        self.assertIn("Before.", res.normal_text)
        self.assertIn("After.", res.normal_text)

    def test_two_complete_calls_in_one_chunk_both_emitted(self):
        """C: batched/speculative chunk must yield BOTH calls."""
        det = MiMoDetector()
        res = det.parse_streaming_increment(
            _block("search", {"x": "1"}) + _block("fetch", {"x": "2"}), TOOLS
        )
        self.assertEqual([c.name for c in res.calls], ["search", "fetch"])

    def test_first_call_is_never_passed_through_as_text(self):
        """D: regression guard — the first call must not appear as content."""
        det = MiMoDetector()
        text, calls = _feed(det, [_block("search", {"x": "q"})])
        self.assertEqual([c.name for c in calls], ["search"])
        self.assertNotIn("<tool_call>", text)
        self.assertNotIn("<function=", text)

    def test_call_split_across_chunks_still_emitted_once(self):
        """E: a block spanning chunks must be emitted exactly once."""
        det = MiMoDetector()
        block = _block("search", {"x": "q"})
        _, calls = _feed(det, [block[i : i + 7] for i in range(0, len(block), 7)])
        self.assertEqual([c.name for c in calls], ["search"])

    def test_prose_between_calls_is_preserved(self):
        """Multi-call turn: text around and between calls survives."""
        det = MiMoDetector()
        text, calls = _feed(
            det,
            [
                "I'll check. "
                + _block("search", {"x": "1"})
                + " Now fetching. "
                + _block("fetch", {"x": "2"})
                + " All done."
            ],
        )
        self.assertEqual([c.name for c in calls], ["search", "fetch"])
        for frag in ("I'll check.", "Now fetching.", "All done."):
            self.assertIn(frag, text)


if __name__ == "__main__":
    unittest.main()
