from dataclasses import dataclass, field


@dataclass
class TokenUsageRecord:
    """Single normalized usage record from any provider."""
    provider: str  # "bedrock" | "openrouter" | "anthropic" | "openai"
    model: str  # Normalized model name (e.g., "claude-sonnet-4")
    date: str  # YYYY-MM-DD
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    estimated_cost_usd: float = 0.0

    def __post_init__(self):
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class ProviderSummary:
    """Aggregated usage for a single provider."""
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    request_count: int = 0
    models_used: list[str] = field(default_factory=list)


@dataclass
class UsageSummary:
    """Aggregated usage across providers and time ranges."""
    start_date: str
    end_date: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    by_provider: dict[str, ProviderSummary] = field(default_factory=dict)
    records: list[TokenUsageRecord] = field(default_factory=list)
