"""Tests for token estimation."""

from claude_context.tokens import estimate_tokens


def test_empty_string():
    assert estimate_tokens("") == 0


def test_single_word():
    assert estimate_tokens("hello") == 1  # 1 * 1.3 = 1.3 → 1


def test_typical_sentence():
    text = "This is a typical English sentence with ten words total"
    result = estimate_tokens(text)
    # 10 words * 1.3 = 13
    assert result == 13


def test_longer_text():
    words = ["word"] * 100
    text = " ".join(words)
    result = estimate_tokens(text)
    assert result == 130  # 100 * 1.3


def test_none_like_empty():
    assert estimate_tokens("") == 0
