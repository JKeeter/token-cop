"""Cache-aware token ratios shared by the audit tool, dashboard, and report.

Provider ``input_tokens`` is only the UNCACHED delta: CloudWatch's
``InputTokenCount`` and the invocation-log ``inputTokenCount`` both exclude
cache reads and writes, which arrive as separate counters. On cached traffic
the cached portion is most of the prompt, so any ratio that divides by the
uncached delta alone is wrong by orders of magnitude (the live audit once
reported a "7942% cache hit rate"). Always measure against EFFECTIVE input.
"""


def effective_input_tokens(input_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> int:
    """Total prompt tokens the model actually saw: uncached + cache reads + cache writes."""
    return int(input_tokens or 0) + int(cache_read_tokens or 0) + int(cache_write_tokens or 0)


def cache_hit_ratio(input_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Share of effective input served from cache, in [0.0, 1.0]. Zero-safe."""
    effective = effective_input_tokens(input_tokens, cache_read_tokens, cache_write_tokens)
    if effective <= 0:
        return 0.0
    return min(1.0, int(cache_read_tokens or 0) / effective)
