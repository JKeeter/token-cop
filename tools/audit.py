import json
from datetime import datetime, timedelta, timezone

from strands import tool

from agent.tracing import get_tracer
from models.pricing import PRICING_PER_MILLION


@tool
def token_audit(days: int = 7) -> str:
    """Run a comprehensive token efficiency audit across all providers.

    Analyzes usage patterns over the specified period and scores efficiency
    across 6 dimensions. Returns a report card with grades and recommendations.

    Args:
        days: Number of days to analyze (default 7).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("tool.token_audit", attributes={"token_cop.audit_days": days}):
        return _token_audit_impl(days)


def _token_audit_impl(days: int) -> str:
    from tools.bedrock_usage import bedrock_usage
    from tools.openai_usage import openai_usage
    from tools.openrouter_usage import openrouter_usage

    now = datetime.now(timezone.utc)
    end_date = now.strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    prev_start = (now - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    prev_end = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # Gather current period data from all providers
    current_results = _gather_provider_data(start_date, end_date,
                                            bedrock_usage, openai_usage, openrouter_usage)
    # Gather previous period for trend analysis
    prev_results = _gather_provider_data(prev_start, prev_end,
                                         bedrock_usage, openai_usage, openrouter_usage)

    # Combine all model records
    current_models = _extract_models(current_results)
    prev_models = _extract_models(prev_results)

    if not current_models:
        return json.dumps({
            "period": {"start": start_date, "end": end_date, "days": days},
            "message": "No usage data found for this period.",
            "overall_grade": "N/A",
            "overall_score": 0,
        })

    # Compute summary
    total_input = sum(m.get("input_tokens", 0) for m in current_models)
    total_output = sum(m.get("output_tokens", 0) for m in current_models)
    total_cache_read = sum(m.get("cache_read_tokens", 0) for m in current_models)
    total_cache_write = sum(m.get("cache_write_tokens", 0) for m in current_models)
    total_tokens = total_input + total_output + total_cache_read + total_cache_write
    total_cost = sum(m.get("estimated_cost_usd", 0) for m in current_models)
    total_requests = sum(m.get("requests", 0) for m in current_models)
    avg_cost = total_cost / total_requests if total_requests else 0

    # Score each dimension
    doc_score, doc_detail = _score_document_ingestion(current_models)
    mix_score, mix_detail = _score_model_mix(current_models, total_requests)
    cache_score, cache_detail = _score_cache_utilization(total_input, total_cache_read)
    conc_score, conc_detail = _score_cost_concentration(current_models, total_cost)
    trend_score, trend_detail = _score_efficiency_trend(
        current_models, prev_models, total_requests
    )
    savings = _find_top_savings(current_models, total_requests)

    scores_list = [doc_score, mix_score, cache_score, conc_score, trend_score]
    overall_score = round(sum(scores_list) / len(scores_list), 1)
    overall_grade = _grade(overall_score)

    recommendations = _build_recommendations(
        doc_score, mix_score, cache_score, conc_score, trend_score, savings, current_models
    )

    # Invocation log analysis (if S3 bucket configured)
    invocation_analysis = None
    from agent.config import get_secret
    if get_secret("BEDROCK_LOG_BUCKET"):
        try:
            from tools.invocation_logs import analyze_invocation_logs
            raw = analyze_invocation_logs(days=days)
            if isinstance(raw, dict) and "content" in raw:
                text = "".join(
                    block.get("text", "") for block in raw["content"]
                    if isinstance(block, dict)
                )
            else:
                text = str(raw)
            invocation_data = json.loads(text)
            if "error" not in invocation_data:
                invocation_analysis = invocation_data
                # Merge invocation log recommendations
                for rec in invocation_data.get("recommendations", []):
                    if rec not in recommendations:
                        recommendations.append(rec)
        except Exception:
            pass
    else:
        recommendations.append(
            "Configure BEDROCK_LOG_BUCKET to enable request-level invocation log analysis"
        )

    result = {
        "period": {"start": start_date, "end": end_date, "days": days},
        "summary": {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 2),
            "total_requests": total_requests,
            "avg_cost_per_request": round(avg_cost, 4),
        },
        "scores": {
            "document_ingestion": {"score": doc_score, "detail": doc_detail},
            "model_mix": {"score": mix_score, "detail": mix_detail},
            "cache_utilization": {"score": cache_score, "detail": cache_detail},
            "cost_concentration": {"score": conc_score, "detail": conc_detail},
            "efficiency_trend": {"score": trend_score, "detail": trend_detail},
            "top_savings": savings,
        },
        "overall_grade": overall_grade,
        "overall_score": overall_score,
        "recommendations": recommendations,
    }
    if invocation_analysis:
        result["invocation_log_analysis"] = invocation_analysis
    return json.dumps(result, indent=2)


def _gather_provider_data(start_date, end_date, bedrock_fn, openai_fn, openrouter_fn):
    """Call each provider tool and collect results, ignoring failures."""
    results = []
    for fn in [bedrock_fn, openai_fn, openrouter_fn]:
        try:
            raw = fn(start_date=start_date, end_date=end_date)
            # Strands tools may return tool_result dict
            if isinstance(raw, dict) and "content" in raw:
                text = "".join(
                    block.get("text", "") for block in raw["content"]
                    if isinstance(block, dict)
                )
            else:
                text = str(raw)
            data = json.loads(text)
            if "error" not in data:
                results.append(data)
        except Exception:
            continue
    return results


def _extract_models(provider_results):
    """Pull out per-model records from provider results."""
    models = []
    for data in provider_results:
        for m in data.get("by_model", []):
            m.setdefault("provider", data.get("provider", "unknown"))
            models.append(m)
    return models


def _get_model_input_price(model_name: str) -> float:
    """Get the input price per million tokens for a model."""
    pricing = PRICING_PER_MILLION.get(model_name, {})
    return pricing.get("input", 1.0)


def _score_document_ingestion(models):
    """Score 1-10 based on input:output token ratio."""
    total_input = sum(m.get("input_tokens", 0) for m in models)
    total_output = sum(m.get("output_tokens", 0) for m in models)
    if total_output == 0:
        return 5, "No output tokens — cannot compute ratio"
    ratio = total_input / total_output
    if ratio > 10:
        score = max(1, 3 - int((ratio - 10) / 5))
        return score, f"Avg input:output ratio {ratio:.1f}:1 — likely raw document ingestion"
    if ratio > 5:
        score = max(3, 7 - int(ratio - 5))
        return score, f"Avg input:output ratio {ratio:.1f}:1 — somewhat heavy input"
    if ratio >= 2:
        return 8, f"Avg input:output ratio {ratio:.1f}:1 — healthy"
    return 10, f"Avg input:output ratio {ratio:.1f}:1 — excellent"


def _score_model_mix(models, total_requests):
    """Score 1-10 based on whether expensive models dominate request count."""
    if not models or total_requests == 0:
        return 5, "No request data available"

    # Find most expensive model by input price
    expensive_model = max(models, key=lambda m: _get_model_input_price(m.get("model", "")))
    expensive_name = expensive_model.get("model", "unknown")
    expensive_requests = expensive_model.get("requests", 0)
    pct = (expensive_requests / total_requests) * 100

    if pct > 50:
        score = max(1, 4 - int((pct - 50) / 15))
        return score, f"{expensive_name} handles {pct:.0f}% of requests — consider downgrading routine tasks"
    if pct > 30:
        return 6, f"{expensive_name} handles {pct:.0f}% of requests — acceptable but watch it"
    return 9, f"Good model distribution — most expensive model ({expensive_name}) handles only {pct:.0f}% of requests"


def _score_cache_utilization(total_input, total_cache_read):
    """Score 1-10 based on cache_read / total_input ratio."""
    if total_input == 0:
        return 5, "No input token data"
    ratio = total_cache_read / total_input
    pct = ratio * 100
    if pct >= 20:
        return min(10, 8 + int((pct - 20) / 10)), f"{pct:.0f}% cache hit rate — excellent"
    if pct >= 5:
        score = 4 + int((pct - 5) / 4)
        return score, f"{pct:.0f}% cache hit rate — room for improvement"
    return max(1, int(pct)), f"Only {pct:.1f}% cache hit rate — enable prompt caching"


def _score_cost_concentration(models, total_cost):
    """Score 1-10 based on cost distribution across models."""
    if not models or total_cost == 0:
        return 5, "No cost data available"

    top_model = max(models, key=lambda m: m.get("estimated_cost_usd", 0))
    top_name = top_model.get("model", "unknown")
    top_cost = top_model.get("estimated_cost_usd", 0)
    pct = (top_cost / total_cost) * 100

    if pct > 70:
        return max(1, 4 - int((pct - 70) / 10)), f"{top_name} accounts for {pct:.0f}% of cost — over-concentrated"
    if pct > 50:
        return 5, f"{top_name} accounts for {pct:.0f}% of cost — moderately concentrated"
    return 8, f"Well-diversified — top model ({top_name}) is {pct:.0f}% of cost"


def _score_efficiency_trend(current_models, prev_models, current_requests):
    """Score 1-10 by comparing cost-per-request across periods."""
    current_cost = sum(m.get("estimated_cost_usd", 0) for m in current_models)
    prev_cost = sum(m.get("estimated_cost_usd", 0) for m in prev_models)
    prev_requests = sum(m.get("requests", 0) for m in prev_models)

    if prev_requests == 0 or current_requests == 0:
        return 5, "Insufficient data for trend comparison"

    current_cpr = current_cost / current_requests
    prev_cpr = prev_cost / prev_requests

    if prev_cpr == 0:
        return 5, "No previous cost data for trend"

    change_pct = ((current_cpr - prev_cpr) / prev_cpr) * 100

    if change_pct <= -15:
        return 10, f"Cost per request down {abs(change_pct):.0f}% vs previous period — great improvement"
    if change_pct < 0:
        return 7, f"Cost per request down {abs(change_pct):.0f}% vs previous period"
    if change_pct < 10:
        return 5, f"Cost per request up {change_pct:.0f}% vs previous period — roughly flat"
    return max(1, 4 - int(change_pct / 20)), f"Cost per request up {change_pct:.0f}% vs previous period — worsening"


def _find_top_savings(models, total_requests):
    """Identify the single biggest optimization opportunity."""
    if not models:
        return {"action": "No data to analyze", "estimated_weekly_savings_usd": 0}

    best_action = None
    best_savings = 0

    for m in models:
        model_name = m.get("model", "")
        model_requests = m.get("requests", 0)
        model_cost = m.get("estimated_cost_usd", 0)
        input_price = _get_model_input_price(model_name)

        # Check if this expensive model could be replaced by a cheaper one for some tasks
        if input_price >= 10.0 and model_requests > 0:
            # Estimate savings from moving to a mid-tier model
            cheap_ratio = 0.2  # Haiku is ~5x cheaper than Opus
            potential_savings = model_cost * 0.5 * (1 - cheap_ratio)  # Move 50% of calls
            if potential_savings > best_savings:
                best_savings = potential_savings
                best_action = f"Move ~{model_requests // 2} routine calls from {model_name} to a cheaper model"
        elif input_price >= 2.5 and model_requests > 10:
            cheap_ratio = 0.3
            potential_savings = model_cost * 0.3 * (1 - cheap_ratio)
            if potential_savings > best_savings:
                best_savings = potential_savings
                best_action = f"Route ~{int(model_requests * 0.3)} simple calls from {model_name} to Haiku"

    if best_action:
        return {"action": best_action, "estimated_weekly_savings_usd": round(best_savings, 2)}
    return {"action": "No obvious optimizations — usage looks efficient", "estimated_weekly_savings_usd": 0}


def _grade(score):
    """Convert numeric score to letter grade."""
    if score >= 8:
        return "A"
    if score >= 6:
        return "B"
    if score >= 4:
        return "C"
    if score >= 2:
        return "D"
    return "F"


def _build_recommendations(doc, mix, cache, conc, trend, savings, models):
    """Build prioritized recommendation list based on scores."""
    recs = []
    if cache < 5:
        recs.append("Enable prompt caching for system prompts and tool schemas — up to 90% discount on cache hits")
    if mix < 5:
        recs.append("Route formatting, summarization, and simple extraction tasks to Haiku or Nova to reduce cost")
    if conc < 5:
        recs.append("Diversify model usage — over-reliance on one model creates cost risk and single-point-of-failure")
    if doc < 5:
        recs.append("Convert large documents to markdown before ingestion — reduces input tokens dramatically")
    if trend < 5:
        recs.append("Cost per request is trending up — review recent prompt changes and model selections")
    if savings.get("estimated_weekly_savings_usd", 0) > 5:
        recs.append(f"{savings['action']} — estimated ${savings['estimated_weekly_savings_usd']:.2f}/week savings")
    if not recs:
        recs.append("Usage looks healthy — keep monitoring with weekly audits")
    return recs
