"""Weekly report generator for Token Cop.

Generates a markdown or JSON summary of LLM token usage for Slack/email.

Usage:
    python -m scripts.generate_report --period weekly --format markdown
    python -m scripts.generate_report --period monthly --format json
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dashboard.data import (
    fetch_all_provider_data,
    fetch_audit_data,
    compute_smart_token_score,
    get_model_mix,
    get_cost_trend,
)

logger = logging.getLogger(__name__)

# Sentinel shown when Cost Explorer / CUR attribution isn't wired up yet.
_ATTRIBUTION_DISABLED_NOTE = (
    "Granular cost attribution not yet enabled — run "
    "`scripts/enable_cur_attribution.py` to enable team-level breakdowns."
)


def generate_report(period: str = "weekly", fmt: str = "markdown") -> str:
    """Generate a usage report for the specified period."""
    now = datetime.now(timezone.utc)
    days = 7 if period == "weekly" else 30

    end_date = now.strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    prev_start = (now - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    prev_end = start_date

    # Fetch data
    data = fetch_all_provider_data(start_date, end_date)
    prev_data = fetch_all_provider_data(prev_start, prev_end)
    audit = fetch_audit_data(days)

    totals = data.get("totals", {})
    model_mix = get_model_mix(data)
    trend = get_cost_trend(data, prev_data)
    smart_score = compute_smart_token_score(data)

    # Attribution sections (principal + team-tag breakdowns)
    attribution = _build_attribution_sections(
        start_date=start_date,
        end_date=end_date,
        prev_start=prev_start,
        prev_end=prev_end,
    )

    grade = "N/A"
    overall_score = 0
    recommendations = []
    scores = {}
    if audit:
        grade = audit.get("overall_grade", "N/A")
        overall_score = audit.get("overall_score", 0)
        recommendations = audit.get("recommendations", [])
        scores = audit.get("scores", {})

    if fmt == "json":
        return _generate_json(
            period, start_date, end_date, totals, model_mix, trend,
            grade, overall_score, smart_score, recommendations, scores,
            attribution,
        )
    else:
        return _generate_markdown(
            period, start_date, end_date, days, totals, model_mix, trend,
            grade, overall_score, smart_score, recommendations, scores,
            attribution,
        )


def _build_attribution_sections(
    start_date: str, end_date: str, prev_start: str, prev_end: str
) -> dict:
    """Build the attribution payload for report rendering.

    Returns a dict with:
        - enabled: bool, False if Cost Explorer / CUR attribution isn't wired.
        - note: sentinel string shown when disabled.
        - top_principals: [{principal, cost, percentage, delta_vs_prior_usd,
                            delta_vs_prior_pct}] sorted desc, top 10.
        - over_budget: [{principal, budget_usd, spend_usd, overage_usd}] —
                       empty when no per-principal budgets defined.
        - team_movement: [{team, current_cost, previous_cost, delta_usd,
                           delta_pct}] sorted by abs(delta_usd) desc.

    All sub-sections collapse to empty lists when the feature isn't enabled —
    callers surface a single note in that case.
    """
    from dashboard.data import get_principal_breakdown
    # attribution_breakdown is called directly for the team-tag dimension; the
    # principal helper already wraps the other dimension.
    try:
        from tools.attribution import attribution_breakdown
    except ImportError:
        attribution_breakdown = None  # pragma: no cover

    # --- Principals, current period ---
    principal_data = get_principal_breakdown(start_date, end_date)
    enabled = bool(principal_data.get("enabled"))

    if not enabled:
        return {
            "enabled": False,
            "note": _ATTRIBUTION_DISABLED_NOTE,
            "top_principals": [],
            "over_budget": [],
            "team_movement": [],
            "error": principal_data.get("error"),
        }

    # --- Principals, prior period (for deltas) ---
    prior_principal = get_principal_breakdown(prev_start, prev_end)
    prior_lookup = {
        g["principal"]: g["cost"]
        for g in (prior_principal.get("groups", []) if prior_principal.get("enabled") else [])
    }

    top_principals = []
    for g in principal_data.get("groups", [])[:10]:
        prior_cost = prior_lookup.get(g["principal"], 0.0)
        delta_usd = round(g["cost"] - prior_cost, 2)
        delta_pct = (
            (delta_usd / prior_cost * 100) if prior_cost > 0
            else (100.0 if g["cost"] > 0 else 0.0)
        )
        top_principals.append({
            "principal": g["principal"],
            "cost": g["cost"],
            "percentage": g["percentage"],
            "delta_vs_prior_usd": delta_usd,
            "delta_vs_prior_pct": round(delta_pct, 1),
        })

    # --- Principals over budget (optional config via env var) ---
    over_budget = _compute_over_budget(principal_data.get("groups", []))

    # --- Team-tag movement ---
    team_movement = _compute_team_movement(
        attribution_breakdown, start_date, end_date, prev_start, prev_end,
    )

    return {
        "enabled": True,
        "note": "",
        "top_principals": top_principals,
        "over_budget": over_budget,
        "team_movement": team_movement,
        "error": None,
    }


def _load_principal_budgets() -> dict:
    """Load optional per-principal budgets from TOKEN_COP_PRINCIPAL_BUDGETS.

    The env var is a JSON object mapping principal ARN -> monthly USD budget.
    Returns {} if unset or malformed.
    """
    raw = os.environ.get("TOKEN_COP_PRINCIPAL_BUDGETS", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): float(v) for k, v in parsed.items()}
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Could not parse TOKEN_COP_PRINCIPAL_BUDGETS: %s", exc)
    return {}


def _compute_over_budget(groups: list) -> list:
    """Compare current-period principal spend against configured budgets."""
    budgets = _load_principal_budgets()
    if not budgets:
        return []
    over = []
    for g in groups:
        budget = budgets.get(g["principal"])
        if budget is None or budget <= 0:
            continue
        if g["cost"] > budget:
            over.append({
                "principal": g["principal"],
                "budget_usd": round(budget, 2),
                "spend_usd": g["cost"],
                "overage_usd": round(g["cost"] - budget, 2),
            })
    over.sort(key=lambda r: r["overage_usd"], reverse=True)
    return over


def _compute_team_movement(
    attribution_breakdown_fn,
    start_date: str, end_date: str,
    prev_start: str, prev_end: str,
) -> list:
    """Compute week-over-week movement by `team` cost-allocation tag."""
    if attribution_breakdown_fn is None:
        return []

    def _fetch_team(start: str, end: str) -> dict:
        try:
            raw = attribution_breakdown_fn(
                dimension="tag:team", start_date=start, end_date=end,
            )
        except Exception as exc:
            logger.warning("attribution_breakdown(tag:team) failed: %s", exc)
            return {}
        try:
            if isinstance(raw, dict) and "content" in raw:
                text = "".join(
                    block.get("text", "")
                    for block in raw["content"]
                    if isinstance(block, dict)
                )
            else:
                text = str(raw)
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {}
        if "error" in parsed:
            return {}
        return {
            g.get("key", ""): float(g.get("cost_usd", 0.0) or 0.0)
            for g in parsed.get("groups", [])
        }

    current = _fetch_team(start_date, end_date)
    previous = _fetch_team(prev_start, prev_end)
    if not current and not previous:
        return []

    all_teams = set(current) | set(previous)
    movement = []
    for team in all_teams:
        curr = round(current.get(team, 0.0), 2)
        prev = round(previous.get(team, 0.0), 2)
        delta_usd = round(curr - prev, 2)
        delta_pct = (
            (delta_usd / prev * 100) if prev > 0
            else (100.0 if curr > 0 else 0.0)
        )
        movement.append({
            "team": team or "(untagged)",
            "current_cost": curr,
            "previous_cost": prev,
            "delta_usd": delta_usd,
            "delta_pct": round(delta_pct, 1),
        })
    movement.sort(key=lambda r: abs(r["delta_usd"]), reverse=True)
    return movement


def _generate_markdown(
    period, start_date, end_date, days, totals, model_mix, trend,
    grade, overall_score, smart_score, recommendations, scores,
    attribution,
) -> str:
    """Generate markdown report."""
    # Determine week label
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    period_label = "Weekly" if period == "weekly" else "Monthly"
    date_label = start_dt.strftime("%B %d, %Y")

    total_cost = totals.get("total_cost_usd", 0)
    total_requests = totals.get("total_requests", 0)
    daily_avg = total_cost / days if days > 0 else 0
    cpr = total_cost / total_requests if total_requests > 0 else 0
    cpr_change = trend.get("cpr_change_pct", 0)
    cpr_arrow = "\u2193" if cpr_change < 0 else ("\u2191" if cpr_change > 0 else "\u2194")

    lines = [
        f"# Token Cop {period_label} Report -- Week of {date_label}",
        "",
        f"## Efficiency Grade: {grade} ({overall_score}/10)",
        "",
        "### Spend Summary",
        f"- **Total**: ${total_cost:,.2f} across {total_requests:,} requests",
        f"- **Daily average**: ${daily_avg:,.2f}",
        f"- **Cost per request**: ${cpr:.4f} ({cpr_arrow} {abs(cpr_change):.0f}% vs last {period})",
        f"- **Smart Token Score**: {smart_score}",
        "",
    ]

    # Model mix table
    if model_mix:
        lines.append("### Model Mix")
        lines.append("| Model | Requests | Cost | % of Total |")
        lines.append("|-------|----------|------|------------|")
        for m in model_mix[:10]:
            lines.append(
                f"| {m['model']} | {m['requests']:,} | ${m['cost']:.2f} | {m['percentage']:.0f}% |"
            )
        lines.append("")

    # Granular cost attribution sections
    _append_attribution_markdown(lines, attribution)

    # Wins from trend data
    wins = _find_wins(scores, trend, totals)
    if wins:
        lines.append("### Top Wins This Period")
        for i, win in enumerate(wins[:3], 1):
            lines.append(f"{i}. {win}")
        lines.append("")

    # Recommendations
    if recommendations:
        lines.append("### Top Recommendations")
        for i, rec in enumerate(recommendations[:3], 1):
            lines.append(f"{i}. **{rec}**")
        lines.append("")

    lines.extend([
        "---",
        "*Generated by Token Cop | More tokens is FINE -- they need to be SMART tokens.*",
    ])

    return "\n".join(lines)


def _append_attribution_markdown(lines: list, attribution: dict) -> None:
    """Append the three attribution sections (or a fallback note) to markdown."""
    if not attribution.get("enabled"):
        lines.append(f"*{attribution.get('note', _ATTRIBUTION_DISABLED_NOTE)}*")
        lines.append("")
        return

    # 1. Top 10 principals
    top_principals = attribution.get("top_principals", [])
    if top_principals:
        lines.append("### Top 10 Principals by Bedrock Cost")
        lines.append("| Principal | Cost | % of Total | Δ vs prior week |")
        lines.append("|-----------|------|------------|-----------------|")
        for p in top_principals:
            delta_usd = p.get("delta_vs_prior_usd", 0.0)
            delta_pct = p.get("delta_vs_prior_pct", 0.0)
            arrow = "↓" if delta_usd < 0 else ("↑" if delta_usd > 0 else "↔")
            delta_cell = f"{arrow} ${abs(delta_usd):,.2f} ({delta_pct:+.1f}%)"
            lines.append(
                f"| `{p['principal']}` | ${p['cost']:,.2f} "
                f"| {p['percentage']:.1f}% | {delta_cell} |"
            )
        lines.append("")

    # 2. Principals over budget (optional; silently skipped when no budgets set)
    over_budget = attribution.get("over_budget", [])
    if over_budget:
        lines.append("### Principals Over Budget")
        lines.append("| Principal | Budget | Spend | Overage |")
        lines.append("|-----------|--------|-------|---------|")
        for r in over_budget:
            lines.append(
                f"| `{r['principal']}` | ${r['budget_usd']:,.2f} "
                f"| ${r['spend_usd']:,.2f} | ${r['overage_usd']:,.2f} |"
            )
        lines.append("")

    # 3. Team-tag movement (top movers by absolute delta)
    team_movement = attribution.get("team_movement", [])
    if team_movement:
        lines.append("### Week-over-Week Movement by Team Tag")
        lines.append("| Team | This period | Last period | Δ |")
        lines.append("|------|-------------|-------------|---|")
        for r in team_movement[:10]:
            delta_usd = r.get("delta_usd", 0.0)
            delta_pct = r.get("delta_pct", 0.0)
            arrow = "↓" if delta_usd < 0 else ("↑" if delta_usd > 0 else "↔")
            lines.append(
                f"| {r['team']} | ${r['current_cost']:,.2f} "
                f"| ${r['previous_cost']:,.2f} "
                f"| {arrow} ${abs(delta_usd):,.2f} ({delta_pct:+.1f}%) |"
            )
        lines.append("")


def _generate_json(
    period, start_date, end_date, totals, model_mix, trend,
    grade, overall_score, smart_score, recommendations, scores,
    attribution,
) -> str:
    """Generate JSON report."""
    payload = {
        "report_type": period,
        "start_date": start_date,
        "end_date": end_date,
        "efficiency_grade": grade,
        "overall_score": overall_score,
        "smart_token_score": smart_score,
        "totals": totals,
        "model_mix": model_mix,
        "trend": trend,
        "recommendations": recommendations,
        "scores": {
            k: v for k, v in scores.items() if k != "top_savings"
        },
        "attribution": {
            "enabled": attribution.get("enabled", False),
            "note": attribution.get("note", ""),
            "top_principals": attribution.get("top_principals", []),
            "over_budget": attribution.get("over_budget", []),
            "team_movement": attribution.get("team_movement", []),
        },
    }
    return json.dumps(payload, indent=2)


def _find_wins(scores: dict, trend: dict, totals: dict) -> list[str]:
    """Extract positive highlights from audit scores and trend data."""
    wins = []

    cache = scores.get("cache_utilization", {})
    if cache.get("score", 0) >= 7:
        wins.append(f"Cache utilization is strong: {cache.get('detail', '')}")

    if trend.get("cpr_change_pct", 0) < -5:
        wins.append(
            f"Cost per request down {abs(trend['cpr_change_pct']):.0f}% vs previous period"
        )

    mix = scores.get("model_mix", {})
    if mix.get("score", 0) >= 7:
        wins.append(f"Good model distribution: {mix.get('detail', '')}")

    doc = scores.get("document_ingestion", {})
    if doc.get("score", 0) >= 7:
        wins.append(f"Healthy input/output ratio: {doc.get('detail', '')}")

    total_cache = totals.get("cache_read_tokens", 0)
    total_input = totals.get("input_tokens", 0)
    if total_input > 0:
        cache_pct = total_cache / total_input * 100
        if cache_pct > 15:
            wins.append(f"Cache hit rate at {cache_pct:.0f}%")

    return wins


def main():
    parser = argparse.ArgumentParser(description="Generate Token Cop usage report")
    parser.add_argument(
        "--period",
        choices=["weekly", "monthly"],
        default="weekly",
        help="Report period (default: weekly)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        dest="fmt",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file path (default: stdout)",
    )

    args = parser.parse_args()
    report = generate_report(period=args.period, fmt=args.fmt)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
