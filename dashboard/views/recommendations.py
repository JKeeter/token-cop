"""Optimization recommendations view for the Token Cop dashboard."""

import streamlit as st


# Effort level colors
EFFORT_COLORS = {
    "easy": "#22c55e",
    "medium": "#eab308",
    "hard": "#ef4444",
}


def render_recommendations(data: dict, audit: dict | None) -> None:
    """Render optimization recommendations with savings estimates."""
    st.header("Recommendations")

    totals = data.get("totals", {})
    total_cost = totals.get("total_cost_usd", 0)

    # --- Top savings opportunities from audit ---
    if audit:
        st.subheader("Top Savings Opportunities")

        recommendations = audit.get("recommendations", [])
        scores = audit.get("scores", {})
        top_savings = scores.get("top_savings", {})

        if recommendations:
            for i, rec in enumerate(recommendations[:5], 1):
                effort = _estimate_effort(rec)
                color = EFFORT_COLORS.get(effort, "#9ca3af")
                weekly_est = _estimate_weekly_savings(rec, total_cost)

                st.markdown(
                    f"**{i}.** "
                    f"<span style='background-color:{color};color:white;"
                    f"padding:2px 8px;border-radius:4px;font-size:0.8rem'>"
                    f"{effort.upper()}</span> "
                    f"{rec}",
                    unsafe_allow_html=True,
                )
                if weekly_est > 0:
                    st.caption(
                        f"Estimated savings: ${weekly_est:.2f}/week "
                        f"(${weekly_est * 4.3:.2f}/month)"
                    )

        if top_savings.get("estimated_weekly_savings_usd", 0) > 0:
            st.info(
                f"**Biggest single opportunity:** {top_savings.get('action', 'N/A')} "
                f"-- est. ${top_savings['estimated_weekly_savings_usd']:.2f}/week"
            )

        # Score breakdown
        st.divider()
        st.subheader("Score Breakdown")
        score_cols = st.columns(5)
        score_labels = [
            ("Document Ingestion", "document_ingestion"),
            ("Model Mix", "model_mix"),
            ("Cache Utilization", "cache_utilization"),
            ("Cost Concentration", "cost_concentration"),
            ("Efficiency Trend", "efficiency_trend"),
        ]
        for col, (label, key) in zip(score_cols, score_labels):
            sc = scores.get(key, {})
            val = sc.get("score", 0)
            with col:
                _render_score_badge(label, val)
                st.caption(sc.get("detail", ""))
    else:
        st.warning("Audit data unavailable. Run a token audit to see personalized recommendations.")

    st.divider()

    # --- Quick wins ---
    st.subheader("Quick Wins (5 minutes each)")
    quick_wins = [
        (
            "Convert documents to markdown before reading",
            "Use markitdown or pandoc to pre-process PDFs, DOCX, and PPTX. "
            "Cuts input tokens by 60-80%.",
        ),
        (
            "Start fresh conversations every 10-15 turns",
            "Long conversations accumulate context that gets re-sent every turn. "
            "A fresh conversation resets the token counter.",
        ),
        (
            "Use Haiku for formatting tasks",
            "Reformatting code, fixing markdown, and simple transformations "
            "don't need expensive reasoning models.",
        ),
        (
            "Enable prompt caching on system prompts",
            "System prompts are sent every request. Caching gives up to 90% "
            "discount on repeated prefix tokens.",
        ),
    ]

    for title, desc in quick_wins:
        with st.expander(f"{title}"):
            st.markdown(desc)

    st.divider()

    # --- Infrastructure wins ---
    st.subheader("Infrastructure Wins (require setup)")
    infra_wins = [
        (
            "Set up heavy-file-ingestion hook",
            "Automatically convert large files before they hit the LLM. "
            "Intercept at the tool level for transparent savings.",
            "medium",
        ),
        (
            "Configure model router",
            "Route tasks to the right tier automatically: reasoning for complex work, "
            "execution for implementation, polish for formatting.",
            "medium",
        ),
        (
            "Schedule weekly audits",
            "Use `/loop 7d /tokcop run a token audit` to get automatic weekly reports. "
            "Track trends and catch regressions early.",
            "easy",
        ),
        (
            "Deploy team dashboard",
            "This dashboard! Share it with the team via a shared server or "
            "Streamlit Cloud for always-on visibility.",
            "hard",
        ),
    ]

    for title, desc, effort in infra_wins:
        color = EFFORT_COLORS.get(effort, "#9ca3af")
        with st.expander(
            f"{title}"
        ):
            st.markdown(
                f"<span style='background-color:{color};color:white;"
                f"padding:2px 8px;border-radius:4px;font-size:0.8rem'>"
                f"{effort.upper()}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(desc)

    st.divider()

    # --- Week-over-week tracking ---
    st.subheader("Trend Tracking")
    if audit:
        trend = scores.get("efficiency_trend", {})
        trend_detail = trend.get("detail", "")
        trend_score = trend.get("score", 5)

        if trend_score >= 7:
            st.success(f"Improving! {trend_detail}")
        elif trend_score >= 4:
            st.info(f"Holding steady. {trend_detail}")
        else:
            st.warning(f"Needs attention. {trend_detail}")

        # Celebration or alert
        if trend_score >= 8:
            st.balloons()
            st.markdown("**Great work!** Efficiency is trending in the right direction.")
    else:
        st.info(
            "Historical trend data requires running audits over multiple periods. "
            "Schedule weekly audits to build up trend data."
        )


def _render_score_badge(label: str, score: int) -> None:
    """Render a colored score badge."""
    if score >= 8:
        color = "#22c55e"
    elif score >= 6:
        color = "#3b82f6"
    elif score >= 4:
        color = "#eab308"
    else:
        color = "#ef4444"

    st.markdown(
        f"<div style='text-align:center'>"
        f"<span style='font-size:1.8rem;color:{color};font-weight:bold'>"
        f"{score}</span>/10<br>"
        f"<span style='font-size:0.8rem;color:#6b7280'>{label}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _estimate_effort(recommendation: str) -> str:
    """Estimate effort level from recommendation text."""
    easy_signals = ["enable", "cache", "caching", "convert", "switch", "use"]
    hard_signals = ["diversify", "infrastructure", "deploy", "migrate"]

    rec_lower = recommendation.lower()
    for signal in hard_signals:
        if signal in rec_lower:
            return "hard"
    for signal in easy_signals:
        if signal in rec_lower:
            return "easy"
    return "medium"


def _estimate_weekly_savings(recommendation: str, total_weekly_cost: float) -> float:
    """Rough estimate of weekly savings from a recommendation."""
    rec_lower = recommendation.lower()

    # If the recommendation already contains a dollar amount, extract it
    if "$" in recommendation:
        import re
        match = re.search(r"\$(\d+\.?\d*)", recommendation)
        if match:
            return float(match.group(1))

    # Rough heuristics based on recommendation type
    if "cache" in rec_lower or "caching" in rec_lower:
        return round(total_weekly_cost * 0.08, 2)
    if "haiku" in rec_lower or "cheaper" in rec_lower or "routine" in rec_lower:
        return round(total_weekly_cost * 0.10, 2)
    if "document" in rec_lower or "markdown" in rec_lower:
        return round(total_weekly_cost * 0.05, 2)
    if "diversify" in rec_lower or "concentrated" in rec_lower:
        return round(total_weekly_cost * 0.07, 2)
    return 0
