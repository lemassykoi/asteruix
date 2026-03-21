"""Tests for Mermaid label sanitizer (Phase 1 security hardening)."""

import pytest

from app.dialplan import sanitize_mermaid_label, _MERMAID_MAX_LABEL


class TestSanitizeMermaidLabel:
    """Verify dangerous characters are stripped and output is safe for Mermaid."""

    def test_none_returns_empty(self):
        assert sanitize_mermaid_label(None) == ""

    def test_plain_text_unchanged(self):
        assert sanitize_mermaid_label("spam-prefix") == "spam-prefix"

    def test_strips_angle_brackets(self):
        assert "<script>" not in sanitize_mermaid_label("<script>alert(1)</script>")

    def test_strips_backticks(self):
        assert "`" not in sanitize_mermaid_label("foo`bar")

    def test_strips_quotes(self):
        result = sanitize_mermaid_label('he said "hello" and \'goodbye\'')
        assert '"' not in result
        assert "'" not in result

    def test_strips_braces_and_brackets(self):
        result = sanitize_mermaid_label("a{b}[c](d)")
        assert result == "abcd"

    def test_semicolons_replaced(self):
        assert ";" not in sanitize_mermaid_label("a;b;c")
        assert sanitize_mermaid_label("a;b;c") == "a b c"

    def test_newlines_collapsed(self):
        assert "\n" not in sanitize_mermaid_label("line1\nline2\nline3")
        assert sanitize_mermaid_label("line1\nline2") == "line1 line2"

    def test_carriage_return_collapsed(self):
        assert "\r" not in sanitize_mermaid_label("a\r\nb")

    def test_length_trimmed(self):
        long_input = "A" * 300
        result = sanitize_mermaid_label(long_input)
        assert len(result) == _MERMAID_MAX_LABEL

    def test_integer_value(self):
        assert sanitize_mermaid_label(4900) == "4900"

    def test_whitespace_collapsed(self):
        assert sanitize_mermaid_label("  a   b  ") == "a b"

    def test_mermaid_injection_subgraph(self):
        result = sanitize_mermaid_label('end\nsubgraph "injected"')
        assert "subgraph" in result  # text is kept
        assert "\n" not in result    # but newline is gone — no syntax break
        assert '"' not in result     # quotes stripped
