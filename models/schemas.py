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
    # AWS Bedrock granular cost attribution fields (April 17, 2026 feature).
    # Empty defaults preserve backward compatibility with existing kwargs callers.
    iam_principal: str = ""
    inference_profile_arn: str = ""
    principal_tags: dict[str, str] = field(default_factory=dict)

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
class InvocationLogEntry:
    """Parsed entry from a Bedrock model invocation log."""
    model_id: str
    normalized_model: str
    timestamp: str
    input_token_count: int = 0
    output_token_count: int = 0
    system_prompt_hash: str = ""
    system_prompt_length: int = 0
    system_prompt_text: str = ""
    user_message_text: str = ""
    message_count: int = 0
    classified_tier: str = ""
    model_tier: str = ""
    # AWS Bedrock granular cost attribution fields (April 17, 2026 feature).
    iam_principal: str = ""
    inference_profile_arn: str = ""
    principal_tags: dict[str, str] = field(default_factory=dict)


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
