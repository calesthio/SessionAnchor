"""Token estimation using a simple word-count heuristic.

No external dependencies. ~90% accurate for English text.
Claude's tokenizer averages ~1.3 tokens per whitespace-delimited word.
"""


def estimate_tokens(text: str) -> int:
    """Estimate token count from text. Returns 0 for empty input."""
    if not text:
        return 0
    return int(len(text.split()) * 1.3)
