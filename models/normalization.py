# Maps provider-specific model IDs to normalized names.
# Normalized names match keys in pricing.py PRICING_PER_MILLION.

MODEL_ALIASES = {
    # AWS Bedrock model IDs (from actual CloudWatch metrics)
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": "claude-sonnet-4.5",
    "global.anthropic.claude-opus-4-6-v1": "claude-opus-4.6",
    "us.anthropic.claude-sonnet-4-6": "claude-sonnet-4.6",
    "us.anthropic.claude-opus-4-6-v1": "claude-opus-4.6",
    "global.anthropic.claude-sonnet-4-20250514-v1:0": "claude-sonnet-4",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": "claude-haiku-4.5",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": "claude-haiku-4.5",
    "anthropic.claude-opus-4-20250514-v1:0": "claude-opus-4",
    "anthropic.claude-sonnet-4-20250514-v1:0": "claude-sonnet-4",
    "us.anthropic.claude-opus-4-20250514-v1:0": "claude-opus-4",
    "us.anthropic.claude-sonnet-4-20250514-v1:0": "claude-sonnet-4",
    "anthropic.claude-3-5-sonnet-20241022-v2:0": "claude-3.5-sonnet",
    "anthropic.claude-3-5-haiku-20241022-v1:0": "claude-3.5-haiku",
    "anthropic.claude-3-opus-20240229-v1:0": "claude-3-opus",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0": "claude-3.5-sonnet",
    "us.anthropic.claude-3-5-haiku-20241022-v1:0": "claude-3.5-haiku",
    "amazon.nova-pro-v1:0": "amazon-nova-pro",
    "amazon.nova-lite-v1:0": "amazon-nova-lite",
    "amazon.nova-micro-v1:0": "amazon-nova-micro",
    "us.amazon.nova-pro-v1:0": "amazon-nova-pro",
    "us.amazon.nova-lite-v1:0": "amazon-nova-lite",
    "us.amazon.nova-micro-v1:0": "amazon-nova-micro",
    "meta.llama3-1-405b-instruct-v1:0": "llama-3.1-405b",
    "meta.llama3-1-70b-instruct-v1:0": "llama-3.1-70b",
    "meta.llama3-1-8b-instruct-v1:0": "llama-3.1-8b",
    # OpenRouter model IDs
    "anthropic/claude-sonnet-4": "claude-sonnet-4",
    "anthropic/claude-opus-4": "claude-opus-4",
    "anthropic/claude-3.5-sonnet": "claude-3.5-sonnet",
    "anthropic/claude-3.5-haiku": "claude-3.5-haiku",
    "openai/gpt-4o": "gpt-4o",
    "openai/gpt-4o-mini": "gpt-4o-mini",
    "openai/o1": "o1",
    "openai/o1-mini": "o1-mini",
    "openai/o3-mini": "o3-mini",
    "meta-llama/llama-3.1-405b-instruct": "llama-3.1-405b",
    "meta-llama/llama-3.1-70b-instruct": "llama-3.1-70b",
    # Direct Anthropic API model IDs
    "claude-opus-4-20250514": "claude-opus-4",
    "claude-sonnet-4-20250514": "claude-sonnet-4",
    "claude-3-5-sonnet-20241022": "claude-3.5-sonnet",
    "claude-3-5-haiku-20241022": "claude-3.5-haiku",
    "claude-3-opus-20240229": "claude-3-opus",
    # Direct OpenAI API model IDs
    "gpt-4o-2024-11-20": "gpt-4o",
    "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
    "gpt-4-turbo-2024-04-09": "gpt-4-turbo",
}


def normalize_model_name(raw_model_id: str) -> str:
    """Normalize a provider-specific model ID to a canonical name."""
    if raw_model_id in MODEL_ALIASES:
        return MODEL_ALIASES[raw_model_id]
    # Try partial matching for versioned IDs
    for alias_key, normalized in MODEL_ALIASES.items():
        if alias_key in raw_model_id or raw_model_id in alias_key:
            return normalized
    # Return the raw ID if no match found
    return raw_model_id
