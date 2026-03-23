import json

from strands import tool


@tool
def aggregate_usage(provider_results: list[str]) -> str:
    """Aggregate token usage data from multiple providers into a unified summary.

    Takes the JSON string outputs from individual provider tools (bedrock_usage,
    openrouter_usage, etc.) and combines them into a single cross-provider report.

    Args:
        provider_results: List of JSON strings from individual provider usage tools.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0
    total_requests = 0
    total_cache_read = 0
    total_cache_write = 0
    providers = []
    all_models = []

    for result_str in provider_results:
        try:
            data = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            continue

        if "error" in data:
            providers.append({
                "provider": data.get("provider", "unknown"),
                "error": data["error"],
            })
            continue

        provider = data.get("provider", "unknown")
        p_input = data.get("total_input_tokens", 0)
        p_output = data.get("total_output_tokens", 0)
        p_cost = data.get("total_estimated_cost_usd", 0)
        p_requests = data.get("total_requests", 0)
        p_cache_read = data.get("total_cache_read_tokens", 0)
        p_cache_write = data.get("total_cache_write_tokens", 0)

        total_input += p_input
        total_output += p_output
        total_cost += p_cost
        total_requests += p_requests
        total_cache_read += p_cache_read
        total_cache_write += p_cache_write

        providers.append({
            "provider": provider,
            "input_tokens": p_input,
            "output_tokens": p_output,
            "total_tokens": p_input + p_output,
            "requests": p_requests,
            "estimated_cost_usd": round(p_cost, 2),
            "total_cache_read_tokens": p_cache_read,
            "total_cache_write_tokens": p_cache_write,
        })

        for model in data.get("by_model", []):
            model["provider"] = provider
            all_models.append(model)

    # Sort models by total tokens descending
    all_models.sort(key=lambda m: m.get("total_tokens", 0), reverse=True)

    # Sort providers by cost descending
    providers.sort(
        key=lambda p: p.get("estimated_cost_usd", 0),
        reverse=True,
    )

    grand_total = total_input + total_output + total_cache_read + total_cache_write
    summary = {
        "cross_provider_summary": {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": grand_total,
            "total_requests": total_requests,
            "total_estimated_cost_usd": round(total_cost, 2),
            "total_cache_read_tokens": total_cache_read,
            "total_cache_write_tokens": total_cache_write,
        },
        "by_provider": providers,
        "top_models": all_models[:10],
    }
    return json.dumps(summary, indent=2)
