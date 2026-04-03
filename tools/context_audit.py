import json
import os
from pathlib import Path

from strands import tool


@tool
def context_audit(project_dir: str = ".") -> str:
    """Audit the Claude Code environment for context bloat and waste.

    Inspects CLAUDE.md files, MCP server configs, skills, and plugins
    to estimate per-session token overhead and recommend pruning.

    Args:
        project_dir: Project root directory to audit (default current dir).
    """
    return _context_audit_impl(project_dir)


def _context_audit_impl(project_dir: str) -> str:
    project = Path(project_dir).resolve()
    home = Path.home()

    breakdown = {}
    total_tokens = 0
    recommendations = []

    # 1. System prompt weight — CLAUDE.md files
    global_claude_md = home / ".claude" / "CLAUDE.md"
    project_claude_md = project / "CLAUDE.md"

    global_info = _analyze_file(global_claude_md, "~/.claude/CLAUDE.md")
    project_info = _analyze_file(project_claude_md, "CLAUDE.md")

    breakdown["claude_md_global"] = global_info
    breakdown["claude_md_project"] = project_info
    total_tokens += global_info["est_tokens"] + project_info["est_tokens"]

    if global_info["est_tokens"] > 2000:
        recommendations.append({
            "action": "review",
            "target": f"Global CLAUDE.md ({global_info['est_tokens']} tokens)",
            "est_savings": global_info["est_tokens"] - 1500,
            "reason": "Exceeds 2000 tokens — trim to essentials, move details to project-level files",
        })
    if project_info["est_tokens"] > 2000:
        recommendations.append({
            "action": "review",
            "target": f"Project CLAUDE.md ({project_info['est_tokens']} tokens)",
            "est_savings": project_info["est_tokens"] - 1500,
            "reason": "Exceeds 2000 tokens — check for information duplicated in README or code comments",
        })

    # 2. MCP server inventory
    mcp_servers = []
    mcp_configs = [
        project / ".mcp.json",
        home / ".claude" / "settings.json",
    ]
    for config_path in mcp_configs:
        servers = _parse_mcp_config(config_path)
        mcp_servers.extend(servers)

    mcp_total = sum(s["est_tokens"] for s in mcp_servers)
    breakdown["mcp_servers"] = mcp_servers
    total_tokens += mcp_total

    # Flag heavyweight MCP servers
    for server in mcp_servers:
        if server["tools_count"] > 20:
            est_savings = (server["tools_count"] - 10) * 800
            recommendations.append({
                "action": "review",
                "target": f"MCP: {server['name']} ({server['tools_count']} tools)",
                "est_savings": est_savings,
                "reason": f"Heavy tool schema — do you use all {server['tools_count']} tools?",
            })

    # 3. Skill/plugin tax — estimate from skills directories
    skills_count = _count_skills(project, home)
    skills_tokens = skills_count * 150
    breakdown["skills_loaded"] = skills_count
    breakdown["skills_est_tokens"] = skills_tokens
    total_tokens += skills_tokens

    if skills_count > 30:
        recommendations.append({
            "action": "review",
            "target": f"Skills ({skills_count} loaded)",
            "est_savings": (skills_count - 20) * 150,
            "reason": "Over 30 skills loaded — each adds ~150 tokens of context overhead",
        })

    # 4. CLAUDE.md bloat detection — check for duplication with README
    bloat_findings = _detect_bloat(project, project_claude_md)
    for finding in bloat_findings:
        recommendations.append(finding)

    # Sort recommendations by savings
    recommendations.sort(key=lambda r: r.get("est_savings", 0), reverse=True)
    total_potential_savings = sum(r.get("est_savings", 0) for r in recommendations)

    # Grade based on overhead
    grade = _grade_context(total_tokens, total_potential_savings)

    result = {
        "environment_token_estimate": total_tokens,
        "breakdown": breakdown,
        "recommendations": recommendations,
        "grade": grade,
        "total_potential_savings": total_potential_savings,
    }
    return json.dumps(result, indent=2)


def _analyze_file(path: Path, display_path: str) -> dict:
    """Read a file and estimate token count."""
    if not path.exists():
        return {"path": display_path, "est_tokens": 0, "lines": 0, "exists": False}
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        est_tokens = len(text) // 4  # ~4 chars per token
        return {"path": display_path, "est_tokens": est_tokens, "lines": lines, "exists": True}
    except Exception:
        return {"path": display_path, "est_tokens": 0, "lines": 0, "exists": False}


def _parse_mcp_config(config_path: Path) -> list[dict]:
    """Parse MCP server definitions from a config file."""
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    servers = []
    # .mcp.json format: {"mcpServers": {"name": {...}}}
    # settings.json format: {"mcpServers": {"name": {...}}} (nested under projects sometimes)
    mcp_section = data.get("mcpServers", {})
    if not mcp_section and "projects" in data:
        # Scan project-level settings
        for _proj_path, proj_data in data.get("projects", {}).items():
            mcp_section.update(proj_data.get("mcpServers", {}))

    for name, config in mcp_section.items():
        # Estimate tools — we can't know exact count without invoking,
        # so use heuristics based on known servers
        tools_count = _estimate_tool_count(name, config)
        est_tokens = tools_count * 800  # ~800 tokens per tool schema
        servers.append({
            "name": name,
            "tools_count": tools_count,
            "est_tokens": est_tokens,
            "source": str(config_path.name),
        })
    return servers


def _estimate_tool_count(name: str, config: dict) -> int:
    """Estimate tool count for known MCP servers."""
    known_counts = {
        "github": 45,
        "playwright": 25,
        "slack": 15,
        "filesystem": 8,
        "memory": 5,
        "sequential-thinking": 1,
    }
    for known_name, count in known_counts.items():
        if known_name in name.lower():
            return count
    # Default: assume a modest server
    return 3


def _count_skills(project: Path, home: Path) -> int:
    """Count skill directories across project and global locations."""
    count = 0
    skill_dirs = [
        project / "skills",
        project / ".claude" / "skills",
        home / ".claude" / "skills",
    ]
    for skill_dir in skill_dirs:
        if skill_dir.is_dir():
            for entry in skill_dir.iterdir():
                if entry.is_dir() and (entry / "SKILL.md").exists():
                    count += 1
    return count


def _detect_bloat(project: Path, claude_md_path: Path) -> list[dict]:
    """Check if CLAUDE.md duplicates information from other sources."""
    findings = []
    if not claude_md_path.exists():
        return findings

    try:
        claude_text = claude_md_path.read_text(encoding="utf-8").lower()
    except Exception:
        return findings

    # Check README overlap
    readme_path = project / "README.md"
    if readme_path.exists():
        try:
            readme_text = readme_path.read_text(encoding="utf-8").lower()
            # Check for substantial shared sections (headings that appear in both)
            claude_headings = {
                line.strip().lstrip("#").strip()
                for line in claude_text.splitlines()
                if line.strip().startswith("#")
            }
            readme_headings = {
                line.strip().lstrip("#").strip()
                for line in readme_text.splitlines()
                if line.strip().startswith("#")
            }
            overlap = claude_headings & readme_headings
            if overlap:
                findings.append({
                    "action": "prune",
                    "target": f"CLAUDE.md sections duplicated in README: {', '.join(sorted(overlap)[:3])}",
                    "est_savings": len(overlap) * 200,
                    "reason": "Claude can read README.md on demand — no need to duplicate in CLAUDE.md",
                })
        except Exception:
            pass

    # Check .gitattributes overlap
    gitattr_path = project / ".gitattributes"
    if gitattr_path.exists() and "git filter" in claude_text:
        findings.append({
            "action": "prune",
            "target": "CLAUDE.md: Git filter documentation",
            "est_savings": 400,
            "reason": "Filter config is in .gitattributes — CLAUDE.md doesn't need to repeat it",
        })

    return findings


def _grade_context(total_tokens: int, potential_savings: int) -> str:
    """Grade the environment context efficiency."""
    waste_ratio = potential_savings / total_tokens if total_tokens > 0 else 0
    if total_tokens < 5000 and waste_ratio < 0.1:
        return "A"
    if total_tokens < 10000 and waste_ratio < 0.2:
        return "B"
    if total_tokens < 20000 and waste_ratio < 0.3:
        return "C"
    if total_tokens < 40000:
        return "D"
    return "F"
