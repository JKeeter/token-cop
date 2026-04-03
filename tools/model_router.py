import json

from strands import tool

from agent.tracing import get_tracer
from models.model_tiers import TIERS, classify_task, get_cost_comparison, get_model_tier
from models.normalization import normalize_model_name
from models.pricing import PRICING_PER_MILLION


@tool
def recommend_model(task_description: str, current_model: str = "") -> str:
    """Analyze a task and recommend the most cost-effective model tier.

    Given a description of what needs to be done, recommends whether to use
    a reasoning model (Opus), execution model (Sonnet), or polish model (Haiku).

    Args:
        task_description: What the user wants to accomplish.
        current_model: The model currently being used (optional).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "tool.recommend_model",
        attributes={"token_cop.tool": "recommend_model"},
    ):
        return _recommend_model_impl(task_description, current_model)


def _recommend_model_impl(task_description: str, current_model: str) -> str:
    recommended_tier = classify_task(task_description)
    tier_info = TIERS[recommended_tier]

    # Get pricing for recommended models
    recommended_costs = {}
    for model in tier_info["models"]:
        pricing = PRICING_PER_MILLION.get(model)
        if pricing:
            recommended_costs[model] = {
                "input": pricing["input"],
                "output": pricing["output"],
            }

    # Build reasoning explanation
    reasoning = (
        f"This task matches the '{recommended_tier}' tier: {tier_info['description'].lower()}. "
        f"Recommended cost range: {tier_info['cost_range']}."
    )

    result = {
        "recommended_tier": recommended_tier,
        "recommended_models": tier_info["models"],
        "estimated_cost_per_million": recommended_costs,
        "reasoning": reasoning,
        "cost_comparison": get_cost_comparison(recommended_tier),
    }

    # Assess current model if provided
    if current_model:
        normalized = normalize_model_name(current_model)
        current_tier = get_model_tier(normalized)
        current_pricing = PRICING_PER_MILLION.get(normalized)

        if current_tier and current_pricing:
            tier_rank = {"polish": 0, "execution": 1, "reasoning": 2}
            current_rank = tier_rank.get(current_tier, 1)
            recommended_rank = tier_rank.get(recommended_tier, 1)

            if current_rank > recommended_rank:
                assessment = "overkill"
                # Calculate savings: difference between current and cheapest recommended model
                cheapest_rec = min(
                    (PRICING_PER_MILLION[m] for m in tier_info["models"] if m in PRICING_PER_MILLION),
                    key=lambda p: p["input"] + p["output"],
                )
                savings_input = round(current_pricing["input"] - cheapest_rec["input"], 2)
                savings_output = round(current_pricing["output"] - cheapest_rec["output"], 2)
                result["savings_if_downgraded"] = {
                    "input_savings_per_million": savings_input,
                    "output_savings_per_million": savings_output,
                    "total_savings_per_million_tokens": round(savings_input + savings_output, 2),
                }
                explanation = (
                    f"{normalized} is a {current_tier}-tier model but this task only needs "
                    f"{recommended_tier}-tier. Downgrading could save "
                    f"${savings_input + savings_output:.2f}/M tokens."
                )
            elif current_rank < recommended_rank:
                assessment = "underpowered"
                explanation = (
                    f"{normalized} is a {current_tier}-tier model but this task needs "
                    f"{recommended_tier}-tier for best results."
                )
            else:
                assessment = "appropriate"
                explanation = f"{normalized} is well-matched for this {recommended_tier}-tier task."

            result["current_model_assessment"] = {
                "assessment": assessment,
                "current_model": normalized,
                "current_tier": current_tier,
                "explanation": explanation,
            }

    return json.dumps(result, indent=2)
