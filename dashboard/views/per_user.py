"""Per-user (IAM-principal) breakdown view.

Top section: real IAM-principal attribution from Cost Explorer / CUR 2.0.
Bottom section: per-model efficiency analysis, preserved from v1 since
model-level insight is still useful even with principal-level data available.
"""

import plotly.express as px
import streamlit as st

from dashboard.data import get_model_mix, get_principal_breakdown
from models.model_tiers import get_model_tier


def render_per_user(data: dict) -> None:
    """Render per-principal attribution plus per-model efficiency."""
    st.header("Per-User & Per-Model Breakdown")
    st.caption(
        "IAM-principal attribution via AWS Cost Explorer (CUR 2.0), "
        "with per-model efficiency as a secondary view."
    )

    anonymize = st.toggle("Anonymize names (for presentations)", value=False)

    # --- Top: IAM-principal attribution ---
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    principal_data = _load_principal_data(start_date, end_date)

    tab_principal, tab_model = st.tabs(
        ["By IAM Principal", "By Model (efficiency)"]
    )

    with tab_principal:
        _render_principal_section(principal_data, anonymize)

    with tab_model:
        _render_model_section(data, anonymize)


@st.cache_data(ttl=300, show_spinner="Fetching principal attribution...")
def _load_principal_data(start_date: str, end_date: str) -> dict:
    """Cache principal breakdown for 5 minutes, same TTL as provider data."""
    return get_principal_breakdown(start_date, end_date)


# ---------------------------------------------------------------------------
# Principal attribution section
# ---------------------------------------------------------------------------


def _render_principal_section(principal_data: dict, anonymize: bool) -> None:
    """Render the IAM-principal bar chart and per-principal details."""
    st.subheader("Bedrock Spend by IAM Principal")

    if not principal_data.get("enabled"):
        _render_setup_cta(principal_data)
        return

    groups = principal_data.get("groups", [])
    total_cost = principal_data.get("total_cost", 0.0)
    caveats = principal_data.get("caveats", [])

    if not groups:
        _render_setup_cta(principal_data)
        return

    # Anonymized display-name mapping is deterministic per rank (User A = top spender)
    anon_map: dict[str, str] = {}
    if anonymize:
        for i, g in enumerate(groups):
            anon_map[g["principal"]] = f"User {chr(65 + (i % 26))}{i // 26 if i >= 26 else ''}".rstrip()

    def display_name(principal: str) -> str:
        if anonymize:
            return anon_map.get(principal, principal)
        # Shorten ARN for display: keep just the resource portion after the last colon
        if principal.startswith("arn:") and ":" in principal:
            tail = principal.rsplit(":", 1)[-1]
            return tail or principal
        return principal

    # Top-line metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Bedrock Spend", f"${total_cost:,.2f}")
    c2.metric("Principals Tracked", f"{len(groups)}")
    top_pct = groups[0]["percentage"] if groups else 0.0
    c3.metric("Top Principal Share", f"{top_pct:.1f}%")

    # Bar chart: cost per principal, sorted descending
    chart_rows = [
        {
            "principal": display_name(g["principal"]),
            "raw_principal": g["principal"],
            "cost": g["cost"],
            "percentage": g["percentage"],
            "tier": _infer_principal_tier(g["principal"]),
        }
        for g in groups
    ]

    fig = px.bar(
        chart_rows,
        x="principal",
        y="cost",
        color="tier",
        color_discrete_map={
            "role": "#3b82f6",
            "user": "#22c55e",
            "service": "#a855f7",
            "untagged": "#9ca3af",
            "unknown": "#6b7280",
        },
        labels={"cost": "Cost (USD)", "principal": "IAM Principal", "tier": "Type"},
        hover_data={"percentage": ":.1f"},
    )
    fig.update_layout(
        margin=dict(t=20, b=20, l=20, r=20),
        height=360,
        xaxis_tickangle=-30,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Table view
    st.subheader("Principal Details")
    for row in chart_rows[:15]:
        name = row["principal"]
        cost = row["cost"]
        pct = row["percentage"]
        tier = row["tier"]
        raw = row["raw_principal"]

        with st.expander(
            f"{name} — ${cost:,.2f} ({pct:.1f}%)",
            expanded=False,
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("Cost", f"${cost:,.2f}")
            c2.metric("Share of Bedrock Spend", f"{pct:.1f}%")
            c3.metric("Type", tier.capitalize())
            if not anonymize:
                st.code(raw, language=None)

    # Surface any caveats the tool returned (e.g. tag activation notes)
    if caveats:
        with st.expander("Data caveats", expanded=False):
            for caveat in caveats:
                st.caption(f"• {caveat}")


def _render_setup_cta(principal_data: dict) -> None:
    """Render a setup call-to-action when attribution isn't available."""
    error = principal_data.get("error") or "Attribution data not available."
    caveats = principal_data.get("caveats", [])

    st.info(
        "**IAM-principal attribution is not yet enabled for this account.**\n\n"
        "Token Cop consumes AWS Cost Explorer data enriched by CUR 2.0 "
        "IAM-principal attribution (GA April 17, 2026). Once enabled, this "
        "page shows per-user / per-role Bedrock spend."
    )
    st.markdown(
        "**Next steps:**\n"
        "1. Run `python -m scripts.enable_cur_attribution` in the payer account.\n"
        "2. Activate the `aws:PrincipalArn` cost-allocation tag in Billing.\n"
        "3. Wait up to 24h for attribution data to populate.\n"
        "4. See `docs/cost-attribution.md` for the full runbook."
    )
    with st.expander("Diagnostic details", expanded=False):
        st.caption(f"**Reason:** {error}")
        for caveat in caveats:
            st.caption(f"• {caveat}")


def _infer_principal_tier(principal: str) -> str:
    """Classify a principal ARN into role/user/service for chart coloring.

    Optional visual cue — falls back to "unknown" for anything unrecognizable.
    """
    if not principal or principal == "(untagged)":
        return "untagged"
    lower = principal.lower()
    if ":assumed-role/" in lower or ":role/" in lower:
        return "role"
    if ":user/" in lower:
        return "user"
    if ":service-role/" in lower or ":federated-user/" in lower:
        return "service"
    return "unknown"


# ---------------------------------------------------------------------------
# Per-model efficiency section (preserved from v1)
# ---------------------------------------------------------------------------


def _render_model_section(data: dict, anonymize: bool) -> None:
    """Render the per-model efficiency breakdown (retained from v1)."""
    st.subheader("Model Efficiency Scores")

    model_mix = get_model_mix(data)
    if not model_mix:
        st.info("No model data available for this period.")
        return

    totals = data.get("totals", {})
    total_requests = totals.get("total_requests", 0)
    total_cost = totals.get("total_cost_usd", 0)

    anon_map: dict[str, str] = {}
    if anonymize:
        for i, m in enumerate(model_mix):
            anon_map[m["model"]] = f"Model {chr(65 + i)}"

    def display_name(model: str) -> str:
        return anon_map.get(model, model) if anonymize else model

    efficiency_data = []
    for m in model_mix:
        input_tok = m.get("input_tokens", 0)
        output_tok = m.get("output_tokens", 0)
        cache_read = m.get("cache_read_tokens", 0)
        requests = m.get("requests", 0)
        cost = m.get("cost", 0)

        output_ratio = output_tok / input_tok if input_tok > 0 else 0
        cache_ratio = cache_read / input_tok if input_tok > 0 else 0
        cpr = cost / requests if requests > 0 else 0
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

    avg_efficiency = (
        sum(e["efficiency_score"] for e in efficiency_data) / len(efficiency_data)
        if efficiency_data else 0
    )
    avg_cpr = total_cost / total_requests if total_requests > 0 else 0

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

            st.markdown(
                f"- **Output ratio:** {eff['output_ratio']:.3f} output tokens per input token"
            )
            st.markdown(f"- **Cache hit rate:** {eff['cache_ratio']:.1f}%")

            diff = eff["efficiency_score"] - avg_efficiency
            if diff > 5:
                st.success(f"Above average by {diff:.1f} points")
            elif diff < -5:
                st.warning(f"Below average by {abs(diff):.1f} points")
            else:
                st.info("Near team average")

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

        reasoning_pct = (
            tier_data["reasoning"] / total_requests * 100 if total_requests else 0
        )
        polish_pct = (
            tier_data["polish"] / total_requests * 100 if total_requests else 0
        )
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
