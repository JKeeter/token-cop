"""Team-wide overview view for the Token Cop dashboard."""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import compute_smart_token_score, get_model_mix


# Grade color mapping
GRADE_COLORS = {
    "A": "#22c55e",  # green
    "B": "#3b82f6",  # blue
    "C": "#eab308",  # yellow
    "D": "#f97316",  # orange
    "F": "#ef4444",  # red
    "N/A": "#9ca3af",  # gray
}


def render_overview(data: dict, audit: dict | None) -> None:
    """Render the team-wide overview dashboard."""
    st.header("Team Overview")

    totals = data.get("totals", {})
    grade = audit.get("overall_grade", "N/A") if audit else "N/A"
    score = audit.get("overall_score", 0) if audit else 0
    smart_score = compute_smart_token_score(data)

    # --- Top row metrics ---
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        cost = totals.get("total_cost_usd", 0)
        st.metric("Total Spend", f"${cost:,.2f}")

    with col2:
        grade_color = GRADE_COLORS.get(grade, "#9ca3af")
        st.markdown(
            f"**Efficiency Grade**<br>"
            f"<span style='font-size:2.5rem;color:{grade_color};font-weight:bold'>"
            f"{grade}</span> "
            f"<span style='font-size:1rem;color:#6b7280'>({score}/10)</span>",
            unsafe_allow_html=True,
        )

    with col3:
        requests = totals.get("total_requests", 0)
        st.metric("Total Requests", f"{requests:,}")

    with col4:
        st.metric("Smart Token Score", f"{smart_score}")

    # Show provider errors if any
    errors = data.get("errors", [])
    if errors:
        st.warning(f"Could not reach: {', '.join(errors)}. Showing partial data.")

    st.divider()

    # --- Charts row ---
    model_mix = get_model_mix(data)

    if model_mix:
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.subheader("Model Mix by Cost")
            fig = px.pie(
                model_mix,
                values="cost",
                names="model",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_traces(textposition="inside", textinfo="label+percent")
            fig.update_layout(
                margin=dict(t=20, b=20, l=20, r=20),
                height=350,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

        with chart_col2:
            st.subheader("Cost by Model")
            fig = px.bar(
                model_mix,
                x="model",
                y="cost",
                color="tier",
                color_discrete_map={
                    "reasoning": "#ef4444",
                    "execution": "#3b82f6",
                    "polish": "#22c55e",
                    "unknown": "#9ca3af",
                },
                labels={"cost": "Cost (USD)", "model": "Model", "tier": "Tier"},
            )
            fig.update_layout(
                margin=dict(t=20, b=20, l=20, r=20),
                height=350,
                xaxis_tickangle=-30,
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- Cache hit ratio ---
    total_input = totals.get("input_tokens", 0)
    cache_read = totals.get("cache_read_tokens", 0)
    cache_ratio = cache_read / total_input if total_input > 0 else 0

    st.subheader("Cache Utilization")
    cache_col1, cache_col2 = st.columns([2, 1])
    with cache_col1:
        st.progress(min(cache_ratio, 1.0))
    with cache_col2:
        st.markdown(
            f"**{cache_ratio * 100:.1f}%** cache hit rate "
            f"({cache_read:,} / {total_input:,} input tokens)"
        )

    st.divider()

    # --- Model breakdown table ---
    st.subheader("Model Breakdown")
    if model_mix:
        table_data = []
        max_cost_model = model_mix[0]["model"] if model_mix else None
        for m in model_mix:
            highlight = " *" if m["model"] == max_cost_model else ""
            table_data.append({
                "Model": m["model"] + highlight,
                "Tier": m["tier"],
                "Requests": m["requests"],
                "Input Tokens": f"{m['input_tokens']:,}",
                "Output Tokens": f"{m['output_tokens']:,}",
                "Cache Read": f"{m['cache_read_tokens']:,}",
                "Cost (USD)": f"${m['cost']:.2f}",
                "% of Total": f"{m['percentage']:.1f}%",
            })
        st.dataframe(table_data, use_container_width=True, hide_index=True)
        if max_cost_model:
            st.caption(f"* {max_cost_model} is the highest-cost model this period.")
    else:
        st.info("No model-level data available.")
