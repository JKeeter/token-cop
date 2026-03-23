# Pricing per 1M tokens (USD) - update as prices change
# Source: provider pricing pages as of March 2026
PRICING_PER_MILLION = {
    # Anthropic models
    "claude-opus-4.6": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4.6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4.5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4.5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_write": 1.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3.5-haiku": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_write": 1.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    # OpenAI models
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25, "cache_write": 2.50},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.15},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00, "cache_read": 5.00, "cache_write": 10.00},
    "o1": {"input": 15.00, "output": 60.00, "cache_read": 7.50, "cache_write": 15.00},
    "o1-mini": {"input": 3.00, "output": 12.00, "cache_read": 1.50, "cache_write": 3.00},
    "o3-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.55, "cache_write": 1.10},
    # Amazon models
    "amazon-nova-pro": {"input": 0.80, "output": 3.20, "cache_read": 0.80, "cache_write": 0.80},
    "amazon-nova-lite": {"input": 0.06, "output": 0.24, "cache_read": 0.06, "cache_write": 0.06},
    "amazon-nova-micro": {"input": 0.035, "output": 0.14, "cache_read": 0.035, "cache_write": 0.035},
    # Meta models
    "llama-3.1-405b": {"input": 5.32, "output": 16.00, "cache_read": 5.32, "cache_write": 5.32},
    "llama-3.1-70b": {"input": 0.72, "output": 0.72, "cache_read": 0.72, "cache_write": 0.72},
    "llama-3.1-8b": {"input": 0.22, "output": 0.22, "cache_read": 0.22, "cache_write": 0.22},
}

# Default fallback for unknown models
DEFAULT_PRICING = {"input": 1.00, "output": 3.00, "cache_read": 1.00, "cache_write": 1.00}


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Estimate cost in USD for given token counts."""
    pricing = PRICING_PER_MILLION.get(model, DEFAULT_PRICING)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    cache_read_cost = (cache_read_tokens / 1_000_000) * pricing["cache_read"]
    cache_write_cost = (cache_write_tokens / 1_000_000) * pricing["cache_write"]
    return round(input_cost + output_cost + cache_read_cost + cache_write_cost, 4)
