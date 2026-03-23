import json

from strands import tool

from memory.store import store_usage_snapshot, retrieve_usage_history, check_memory_status


@tool
def save_snapshot(usage_data: str) -> str:
    """Save a usage snapshot to long-term memory for trend analysis.

    Call this after gathering usage data to persist it. Stored snapshots
    can be retrieved later to answer trend questions like "Is my usage
    going up?" or "What did I spend last month?".

    Args:
        usage_data: JSON string of usage data (output from aggregate_usage,
                    bedrock_usage, openrouter_usage, or openai_usage tools).
    """
    result = store_usage_snapshot(usage_data)
    return json.dumps(result)


@tool
def search_history(query: str, max_results: int = 5) -> str:
    """Search historical usage snapshots stored in long-term memory.

    Use this to answer questions about past usage trends, comparisons
    over time, or budget tracking. The search uses semantic matching
    so natural language queries work well.

    Args:
        query: Natural language search query (e.g., "bedrock costs in February",
               "highest spending day", "opus usage trend").
        max_results: Maximum number of historical records to return (default 5).
    """
    status = check_memory_status()
    if status.get("status") != "ACTIVE":
        return json.dumps({
            "error": f"Memory is not active (status: {status.get('status')}). "
                     "Snapshots cannot be searched yet.",
            "detail": status,
        })

    records = retrieve_usage_history(query, max_results)
    return json.dumps({"query": query, "results": records}, indent=2)
