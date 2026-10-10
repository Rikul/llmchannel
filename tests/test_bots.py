import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot_manager import _ReplyFilter, _is_mentioned  # noqa: E402
from llm_clients import _format_channel_history, build_system_prompt  # noqa: E402


def _run_filter(tokens, nickname="Ada"):
    reply = _ReplyFilter(nickname)
    out = "".join(reply.feed(t) for t in tokens) + reply.finish()
    return out, reply.passed


class ReplyFilterTest(unittest.TestCase):
    def test_plain_reply_passes_through(self):
        self.assertEqual(_run_filter(["Hel", "lo there"]), ("Hello there", False))

    def test_pass_token_split_across_chunks(self):
        self.assertEqual(_run_filter([" [", "PA", "SS", "]"]), ("", True))

    def test_pass_token_with_trailing_explanation(self):
        self.assertEqual(_run_filter(["[PASS] nothing to add"]), ("", True))

    def test_empty_reply_counts_as_pass(self):
        self.assertEqual(_run_filter(["  ", "\n"]), ("", True))

    def test_strips_own_name_prefix(self):
        self.assertEqual(_run_filter(["[Ada", "]: hi"]), ("hi", False))

    def test_strips_think_block(self):
        self.assertEqual(_run_filter(["<thi", "nk>hmm</th", "ink>\n\nAnswer"]), ("Answer", False))

    def test_think_then_pass(self):
        self.assertEqual(_run_filter(["<think>x</think>", "[PASS]"]), ("", True))

    def test_bracketed_reply_is_not_swallowed(self):
        self.assertEqual(_run_filter(["[", "Error: boom]"]), ("[Error: boom]", False))

    def test_unfinished_prefix_at_end_is_emitted(self):
        self.assertEqual(_run_filter(["[P"]), ("[P", False))


class PromptTest(unittest.TestCase):
    def test_placeholders_filled(self):
        prompt = build_system_prompt(
            "{{BOT_NAME}}|{{OPERATOR_NAME}}|{{PARTICIPANTS}}|{{PERSONA}}",
            "Ada", "A pirate.", ["Operator", "Ada", "Bob"],
        )
        self.assertEqual(prompt, "Ada|Operator|Operator, Ada, Bob|A pirate.")

    def test_persona_appended_without_placeholder(self):
        prompt = build_system_prompt("Hi {{BOT_NAME}}", "Ada", "A pirate.", [])
        self.assertTrue(prompt.endswith("## Your persona\nA pirate."))

    def test_history_starts_with_user_turn(self):
        history = [{"sender": "Ada", "content": "hello", "timestamp": 1}]
        formatted = _format_channel_history(history, "Ada")
        self.assertEqual(formatted[0]["role"], "user")
        self.assertEqual(formatted[1], {"role": "assistant", "content": "hello"})

    def test_mentions(self):
        self.assertTrue(_is_mentioned("Ada", "hey @ada, thoughts?"))
        self.assertFalse(_is_mentioned("Ada", "hey @adam"))
        self.assertFalse(_is_mentioned("Ada", "email ada@x.com"))


if __name__ == "__main__":
    unittest.main()
