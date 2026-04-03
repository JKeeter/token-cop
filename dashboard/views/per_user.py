"""Per-model breakdown view (proxy for per-user in v1)."""

import plotly.express as px
import streamlit as st

from dashboard.data import get_model_mix
from models.model_tiers import get_model_tier, TIERS
from models.pricing import PRICING_PER_MILLION


def render_per_user(data: dict) -> None:
    """Render per-model breakdown as a proxy for per-user analysis.

    In v1, user-level attribution is limited:
    - Bedrock: all usage comes from the same AWS account
    - OpenAI: org admin API can show per-member if available
    - OpenRouter: key labels can distinguish users

    This view provides per-model efficiency analysis instead.
    """
    st.header("Per-Model Breakdown")
    st.caption(
        "Model-level analysis as a proxy for user behavior. "
        "Per-user attribution will be added in v2 with key labeling."
    )

    # Anonymize toggle
    anonymize = st.toggle("Anonymize model names (for presentations)", value=False)

    model_mix = get_model_mix(data)
    if not model_mix:
        st.info("No model data available for this period.")
        return

    totals = data.get("totals", {})
    total_requests = totals.get("total_requests", 0)
    total_cost = totals.get("total_cost_usd", 0)

    # Create anonymized name mapping
    anon_map = {}
    if anonymize:
        for i, m in enumerate(model_mix):
            anon_map[m["model"]] = f"Model {chr(65 + i)}"

    def display_name(model: str) -> str:
        return anon_map.get(model, model) if anonymize else model

    # --- Model efficiency comparison ---
    st.subheader("Model Efficiency Scores")

    # Compute per-model efficiency
    efficiency_data = []
    for m in model_mix:
        input_tok = m.get("input_tokens", 0)
        output_tok = m.get("output_tokens", 0)
        cache_read = m.get("cache_read_tokens", 0)
        requests = m.get("requests", 0)
        cost = m.get("cost", 0)

        # Output ratio: useful output per input
        output_ratio = output_tok / input_tok if input_tok > 0 else 0
        # Cache efficiency
        cache_ratio = cache_read / input_tok if input_tok > 0 else 0
        # Cost per request
        cpr = cost / requests if requests > 0 else 0
        # Composite efficiency score (0-100)
        eff_score = min(100, (min(output_ratio, 1.0) * 50) + (cache_ratio * 30) + 20)

        efficiency_data.append({
            "model": display_name(m["model"]),
            "raw_model": m["model"],
            "tier": m["tier"],
            "requests": requests,
            "cost": cost,
            "cost_per_request": round(cpr, 4),
            "output_ratio": round(output_ratio, 3),
            "cache_ratio": round(cache_ratio * 100, 1),
            "efficiency_score": round(eff_score, 1),
        })

    # Overall averages for comparison
    avg_efficiency = (
        sum(e["efficiency_score"] for e in efficiency_data) / len(efficiency_data)
        if efficiency_data else 0
    )
    avg_cpr = total_cost / total_requests if total_requests > 0 else 0

    # Efficiency bar chart
    fig = px.bar(
        efficiency_data,
        x="model",
        y="efficiency_score",
        color="tier",
        color_discrete_map={
            "reasoning": "#ef4444",
            "execution": "#3b82f6",
            "polish": "#22c55e",
            "unknown": "#9ca3af",
        },
        labels={
            "efficiency_score": "Efficiency Score",
            "model": "Model",
            "tier": "Tier",
        },
    )
    fig.add_hline(
        y=avg_efficiency,
        line_dash="dash",
        line_color="#6b7280",
        annotation_text=f"Avg: {avg_efficiency:.1f}",
    )
    fig.update_layout(
        margin=dict(t=20, b=20, l=20, r=20),
        height=350,
        xaxis_tickangle=-30,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Individual model cards ---
    st.subheader("Model Details")

    for eff in efficiency_data:
        name = eff["model"]
        raw = eff["raw_model"]
        tier = eff["tier"]
        tier_label = tier.capitalize() if tier != "unknown" else "Unclassified"

        with st.expander(f"{name} ({tier_label})", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Requests", f"{eff['requests']:,}")
            c2.metric("Cost", f"${eff['cost']:.2f}")
            c3.metric(
                "Cost/Request",
                f"${eff['cost_per_request']:.4f}",
                delta=f"{'above' if eff['cost_per_request'] > avg_cpr else 'below'} avg",
                delta_color="inverse",
            )
            c4.metric("Efficiency", f"{eff['efficiency_score']:.1f}/100")

            st.markdown(f"- **Output ratio:** {eff['output_ratio']:.3f} output tokens per input token")
            st.markdown(f"- **Cache hit rate:** {eff['cache_ratio']:.1f}%")

            # Comparison to average
            diff = eff["efficiency_score"] - avg_efficiency
            if diff > 5:
                st.success(f"Above average by {diff:.1f} points")
            elif diff < -5:
                st.warning(f"Below average by {abs(diff):.1f} points")
            else:
                st.info("Near team average")

            # Per-model recommendations
            _render_model_recommendation(raw, eff, total_requests, anonymize)

    st.divider()

    # --- Tier distribution ---
    st.subheader("Tier Distribution")
    tier_data = {"reasoning": 0, "execution": 0, "polish": 0, "unknown": 0}
    for m in model_mix:
        tier_data[m.get("tier", "unknown")] += m.get("requests", 0)

    tier_list = [
        {"Tier": k.capitalize(), "Requests": v}
        for k, v in tier_data.items() if v > 0
    ]
    if tier_list:
        fig = px.pie(
            tier_list,
            values="Requests",
            names="Tier",
            color="Tier",
            color_discrete_map={
                "Reasoning": "#ef4444",
                "Execution": "#3b82f6",
                "Polish": "#22c55e",
                "Unknown": "#9ca3af",
            },
            hole=0.4,
        )
        fig.update_layout(
            margin=dict(t=20, b=20, l=20, r=20),
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Ideal mix guidance
        reasoning_pct = tier_data["reasoning"] / total_requests * 100 if total_requests else 0
        polish_pct = tier_data["polish"] / total_requests * 100 if total_requests else 0
        if reasoning_pct > 50:
            st.warning(
                f"Reasoning-tier models handle {reasoning_pct:.0f}% of requests. "
                "Consider routing simpler tasks to execution or polish tier."
            )
        if polish_pct < 10 and total_requests > 50:
            st.info(
                "Low polish-tier usage. Simple formatting and summarization tasks "
                "can be routed to Haiku/Nova for significant savings."
            )


def _render_model_recommendation(
    model: str, eff: dict, total_requests: int, anonymize: bool
) -> None:
    """Render model-specific optimization recommendations."""
    tier = get_model_tier(model)
    requests = eff["requests"]
    display = eff["model"]

    if tier == "reasoning" and requests > 100:
        st.markdown(
            f"**Recommendation:** {display} has {requests} requests. "
            "Are all of these reasoning tasks? Consider routing routine "
            "work to an execution-tier model."
        )
    elif tier == "reasoning" and eff["cache_ratio"] < 5:
        st.markdown(
            f"**Recommendation:** Low cache hit rate on {display}. "
            "Enable prompt caching for system prompts to cut costs by up to 90% on cache hits."
        )
    elif tier == "execution" and eff["output_ratio"] < 0.1:
        st.markdown(
            f"**Recommendation:** {display} has a very low output ratio. "
            "Large inputs with little output may indicate raw document ingestion - "
            "convert documents to markdown first."
        )
    elif tier == "polish" and eff["cost_per_request"] > 0.01:
        st.markdown(
            f"**Recommendation:** {display} cost per request is higher than expected "
            "for a polish-tier model. Check for unexpectedly large inputs."
        )
