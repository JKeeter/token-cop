"""Analyze Bedrock model invocation logs from S3 for optimization opportunities."""

import gzip
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

import boto3
from strands import tool

from agent.config import AWS_REGION, get_secret
from agent.tracing import get_tracer
from models.model_tiers import classify_task, get_model_tier
from models.normalization import normalize_model_name
from models.pricing import PRICING_PER_MILLION
from models.schemas import InvocationLogEntry

# Truncation limits for payload extraction
_SYSTEM_PROMPT_MAX = 8192  # 8KB — enough for context overhead pattern matching
_USER_MESSAGE_MAX = 1024
_RESPONSE_MAX = 512


@tool
def analyze_invocation_logs(days: int = 7, sample_size: int = 300) -> str:
    """Analyze Bedrock model invocation logs from S3 for optimization opportunities.

    Reads actual request/response payloads from Bedrock's model invocation
    logging bucket and identifies prompt bloat, caching opportunities,
    model-task mismatches, and other inefficiencies.

    Args:
        days: Number of days of logs to analyze (default 7).
        sample_size: Maximum number of log entries to sample (default 300).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "tool.analyze_invocation_logs",
        attributes={"token_cop.audit_days": days, "token_cop.sample_size": sample_size},
    ):
        return _analyze_impl(days, sample_size)


def _analyze_impl(days: int, sample_size: int) -> str:
    bucket, prefix = _get_log_config()
    if not bucket:
        return json.dumps({
            "error": "BEDROCK_LOG_BUCKET not configured. "
            "Set the BEDROCK_LOG_BUCKET environment variable or SSM parameter "
            "to the S3 bucket where Bedrock invocation logs are stored.",
        })

    s3 = boto3.client("s3", region_name=AWS_REGION)

    now = datetime.now(timezone.utc)
    end_date = now.strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # Discover and sample log objects
    objects = _list_log_objects(s3, bucket, prefix, days)
    if not objects:
        return json.dumps({
            "period": {"days": days, "start": start_date, "end": end_date},
            "message": "No invocation log files found in S3 for this period.",
            "bucket": bucket,
            "prefix": prefix,
        })

    sampled = _sample_objects(objects, sample_size)
    entries = _parse_log_entries(s3, bucket, sampled)

    if not entries:
        return json.dumps({
            "period": {"days": days, "start": start_date, "end": end_date},
            "message": "Found log files but could not parse any valid entries.",
            "total_objects": len(objects),
        })

    # Run all 7 analysis dimensions
    prompt_bloat = _analyze_prompt_bloat(entries)
    model_mismatch = _analyze_model_task_mismatch(entries)
    caching = _analyze_caching_opportunities(entries)
    io_ratio = _analyze_io_ratio(entries)
    sys_weight = _analyze_system_prompt_weight(entries)
    response_waste = _analyze_response_waste(entries)
    context_overhead = _analyze_context_overhead(entries)

    # Build findings and recommendations
    findings = _build_findings(
        prompt_bloat, model_mismatch, caching, io_ratio,
        sys_weight, response_waste, context_overhead,
    )
    recommendations = _build_recommendations(findings)
    grade = _grade(findings)

    return json.dumps({
        "period": {"days": days, "start": start_date, "end": end_date},
        "sample_info": {
            "total_objects": len(objects),
            "sampled_objects": len(sampled),
            "entries_analyzed": len(entries),
        },
        "prompt_bloat": prompt_bloat,
        "model_task_mismatch": model_mismatch,
        "caching_opportunities": caching,
        "io_ratio_analysis": io_ratio,
        "system_prompt_weight": sys_weight,
        "response_waste": response_waste,
        "context_overhead": context_overhead,
        "findings": findings,
        "overall_grade": grade,
        "recommendations": recommendations,
    }, indent=2)


# ---------------------------------------------------------------------------
# S3 discovery and parsing
# ---------------------------------------------------------------------------

def _get_log_config() -> tuple[str, str]:
    bucket = get_secret("BEDROCK_LOG_BUCKET")
    prefix = get_secret("BEDROCK_LOG_PREFIX") or "AWSLogs"
    return bucket, prefix


def _list_log_objects(s3, bucket: str, prefix: str, days: int) -> list[dict]:
    """List S3 objects matching the date-based prefix pattern."""
    now = datetime.now(timezone.utc)
    objects = []

    for day_offset in range(days):
        day = now - timedelta(days=day_offset)
        # Bedrock logs land under: {prefix}/{accountId}/BedrockModelInvocationLogs/{YYYY/MM/DD}/
        # We don't know the account ID, so list with a broader prefix and filter by date path
        date_suffix = day.strftime("%Y/%m/%d")

        paginator = s3.get_paginator("list_objects_v2")
        # Try common prefix patterns
        for pfx in [
            f"{prefix}/BedrockModelInvocationLogs/{date_suffix}",
            f"{prefix}/{date_suffix}",
        ]:
            try:
                for page in paginator.paginate(Bucket=bucket, Prefix=pfx, MaxKeys=1000):
                    for obj in page.get("Contents", []):
                        objects.append({
                            "Key": obj["Key"],
                            "Size": obj.get("Size", 0),
                            "date": day.strftime("%Y-%m-%d"),
                        })
                if objects:
                    break  # Found objects with this prefix pattern
            except Exception:
                continue

        # Also try with account ID embedded in path
        if not objects and day_offset == 0:
            try:
                # List top-level to discover account ID prefix
                resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=10)
                for cp in resp.get("CommonPrefixes", []):
                    acct_prefix = cp["Prefix"]
                    pfx = f"{acct_prefix}BedrockModelInvocationLogs/{date_suffix}"
                    for page in paginator.paginate(Bucket=bucket, Prefix=pfx, MaxKeys=1000):
                        for obj in page.get("Contents", []):
                            objects.append({
                                "Key": obj["Key"],
                                "Size": obj.get("Size", 0),
                                "date": day.strftime("%Y-%m-%d"),
                            })
            except Exception:
                pass

    return objects


def _sample_objects(objects: list[dict], sample_size: int) -> list[dict]:
    """Stratified random sample across days."""
    if len(objects) <= sample_size:
        return objects

    by_date = defaultdict(list)
    for obj in objects:
        by_date[obj["date"]].append(obj)

    per_day = max(1, sample_size // len(by_date))
    sampled = []
    for date_objs in by_date.values():
        if len(date_objs) <= per_day:
            sampled.extend(date_objs)
        else:
            sampled.extend(random.sample(date_objs, per_day))

    return sampled[:sample_size]


def _parse_log_entries(s3, bucket: str, objects: list[dict]) -> list[InvocationLogEntry]:
    """Parse invocation log entries from S3 objects."""
    entries = []
    for obj in objects:
        try:
            resp = s3.get_object(Bucket=bucket, Key=obj["Key"])
            body = resp["Body"].read()

            # Decompress if gzipped
            if obj["Key"].endswith(".gz") or obj["Key"].endswith(".gzip"):
                body = gzip.decompress(body)

            text = body.decode("utf-8", errors="replace")

            # Each file may contain multiple JSON records (one per line or as array)
            for line in text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    entry = _parse_record(record)
                    if entry:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except Exception:
            continue

    return entries


def _parse_record(record: dict) -> InvocationLogEntry | None:
    """Parse a single invocation log record into an InvocationLogEntry."""
    model_id = record.get("modelId", "")
    if not model_id:
        return None

    normalized = normalize_model_name(model_id)
    timestamp = record.get("timestamp", "")

    input_tokens = record.get("inputTokenCount", 0) or 0
    output_tokens = record.get("outputTokenCount", 0) or 0

    # Extract system prompt and user message from input
    system_prompt_text = ""
    user_message_text = ""
    message_count = 0

    input_body = record.get("input", {})
    if isinstance(input_body, str):
        try:
            input_body = json.loads(input_body)
        except (json.JSONDecodeError, TypeError):
            input_body = {}

    # Converse API format: {"messages": [...], "system": [...]}
    if "messages" in input_body:
        messages = input_body.get("messages", [])
        message_count = len(messages)

        # System prompt from "system" field
        system_parts = input_body.get("system", [])
        if isinstance(system_parts, list):
            sys_texts = []
            for part in system_parts:
                if isinstance(part, dict) and "text" in part:
                    sys_texts.append(part["text"])
                elif isinstance(part, str):
                    sys_texts.append(part)
            system_prompt_text = "\n".join(sys_texts)[:_SYSTEM_PROMPT_MAX]
        elif isinstance(system_parts, str):
            system_prompt_text = system_parts[:_SYSTEM_PROMPT_MAX]

        # Last user message for task classification
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and "text" in block:
                            user_message_text = block["text"][:_USER_MESSAGE_MAX]
                            break
                elif isinstance(content, str):
                    user_message_text = content[:_USER_MESSAGE_MAX]
                break

    # InvokeModel format: raw body with "system", "messages", or "prompt"
    elif "system" in input_body or "prompt" in input_body:
        if "system" in input_body:
            sys_val = input_body["system"]
            if isinstance(sys_val, str):
                system_prompt_text = sys_val[:_SYSTEM_PROMPT_MAX]
        if "prompt" in input_body:
            user_message_text = str(input_body["prompt"])[:_USER_MESSAGE_MAX]
        if "messages" in input_body:
            messages = input_body["messages"]
            message_count = len(messages) if isinstance(messages, list) else 0

    # Compute hash and classify
    sys_hash = hashlib.md5(system_prompt_text.encode()).hexdigest()[:12] if system_prompt_text else ""
    sys_length = len(system_prompt_text) // 4  # rough token estimate

    model_tier = get_model_tier(normalized) or ""
    classified_tier = ""
    if user_message_text:
        classified_tier = classify_task(user_message_text)

    return InvocationLogEntry(
        model_id=model_id,
        normalized_model=normalized,
        timestamp=timestamp,
        input_token_count=input_tokens,
        output_token_count=output_tokens,
        system_prompt_hash=sys_hash,
        system_prompt_length=sys_length,
        system_prompt_text=system_prompt_text,
        user_message_text=user_message_text,
        message_count=message_count,
        classified_tier=classified_tier,
        model_tier=model_tier,
    )


# ---------------------------------------------------------------------------
# Analysis dimensions
# ---------------------------------------------------------------------------

def _analyze_prompt_bloat(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 1: System prompt size statistics."""
    lengths = [e.system_prompt_length for e in entries if e.system_prompt_length > 0]
    if not lengths:
        return {"status": "no_system_prompts_found", "score": 10}

    lengths_sorted = sorted(lengths)
    avg = sum(lengths) / len(lengths)
    p50 = lengths_sorted[len(lengths_sorted) // 2]
    p95_idx = min(int(len(lengths_sorted) * 0.95), len(lengths_sorted) - 1)
    p95 = lengths_sorted[p95_idx]

    # Score: p95 < 2000 = 10, 2000-4000 = 7, 4000-8000 = 4, >8000 = 2
    if p95 < 2000:
        score = 10
    elif p95 < 4000:
        score = 7
    elif p95 < 8000:
        score = 4
    else:
        score = 2

    return {
        "avg_tokens": round(avg),
        "p50_tokens": p50,
        "p95_tokens": p95,
        "max_tokens": max(lengths),
        "entries_with_system_prompt": len(lengths),
        "score": score,
    }


def _analyze_model_task_mismatch(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 2: Expensive models used for simple tasks."""
    tier_order = {"polish": 0, "execution": 1, "reasoning": 2}
    mismatches = []
    classified_count = 0

    for e in entries:
        if not e.model_tier or not e.classified_tier:
            continue
        classified_count += 1
        model_level = tier_order.get(e.model_tier, 1)
        task_level = tier_order.get(e.classified_tier, 1)
        if model_level > task_level:
            mismatches.append({
                "model": e.normalized_model,
                "model_tier": e.model_tier,
                "task_tier": e.classified_tier,
                "input_tokens": e.input_token_count,
            })

    if classified_count == 0:
        return {"status": "no_classifiable_entries", "score": 8}

    mismatch_rate = len(mismatches) / classified_count

    # Estimate savings from mismatches
    estimated_savings = 0.0
    for m in mismatches:
        current_pricing = PRICING_PER_MILLION.get(m["model"], {})
        current_cost = current_pricing.get("input", 0) * m["input_tokens"] / 1_000_000
        # Assume could use a model at half the cost
        estimated_savings += current_cost * 0.5

    # Score: <5% mismatch = 10, 5-15% = 7, 15-30% = 4, >30% = 2
    if mismatch_rate < 0.05:
        score = 10
    elif mismatch_rate < 0.15:
        score = 7
    elif mismatch_rate < 0.30:
        score = 4
    else:
        score = 2

    return {
        "classified_entries": classified_count,
        "mismatched_entries": len(mismatches),
        "mismatch_rate": round(mismatch_rate, 3),
        "estimated_weekly_savings_usd": round(estimated_savings * (7 / max(1, len(entries))) * len(entries), 2),
        "top_mismatches": _top_n_by_key(mismatches, "model", 5),
        "score": score,
    }


def _analyze_caching_opportunities(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 3: Repeated system prompts that could benefit from caching."""
    hashes = [e.system_prompt_hash for e in entries if e.system_prompt_hash]
    if not hashes:
        return {"status": "no_system_prompts_found", "score": 8}

    hash_counts = Counter(hashes)
    total = len(hashes)
    unique = len(hash_counts)
    most_common = hash_counts.most_common(5)

    # Calculate potential savings if repeated prompts were cached
    # Cache reads cost 90% less than regular input
    potential_savings = 0.0
    for h, count in hash_counts.items():
        if count <= 1:
            continue
        # Find entries with this hash to get avg system prompt size
        matching = [e for e in entries if e.system_prompt_hash == h]
        avg_sys_tokens = sum(e.system_prompt_length for e in matching) / len(matching)
        # Savings = (count - 1) cache reads instead of full input
        # Assume average input pricing of $3/M tokens (Sonnet-range)
        cacheable_tokens = avg_sys_tokens * (count - 1)
        potential_savings += cacheable_tokens * 0.9 * 3.0 / 1_000_000

    reuse_ratio = 1.0 - (unique / total) if total > 0 else 0

    # Score: high reuse with no caching = low score (opportunity missed)
    # >80% reuse = 3 (huge opportunity), 50-80% = 5, 20-50% = 7, <20% = 9
    if reuse_ratio > 0.80:
        score = 3
    elif reuse_ratio > 0.50:
        score = 5
    elif reuse_ratio > 0.20:
        score = 7
    else:
        score = 9

    return {
        "total_system_prompts": total,
        "unique_system_prompts": unique,
        "reuse_ratio": round(reuse_ratio, 3),
        "most_repeated": [{"hash": h, "count": c} for h, c in most_common],
        "potential_weekly_savings_usd": round(potential_savings, 2),
        "score": score,
    }


def _analyze_io_ratio(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 4: Input/output ratio analysis."""
    ratios = []
    extreme_entries = 0

    for e in entries:
        if e.output_token_count > 0:
            ratio = e.input_token_count / e.output_token_count
            ratios.append(ratio)
            if ratio > 20:
                extreme_entries += 1

    if not ratios:
        return {"status": "no_entries_with_output", "score": 8}

    avg_ratio = sum(ratios) / len(ratios)
    extreme_rate = extreme_entries / len(ratios)

    # Score: <5% extreme = 10, 5-15% = 7, 15-30% = 4, >30% = 2
    if extreme_rate < 0.05:
        score = 10
    elif extreme_rate < 0.15:
        score = 7
    elif extreme_rate < 0.30:
        score = 4
    else:
        score = 2

    return {
        "avg_io_ratio": round(avg_ratio, 1),
        "extreme_ratio_entries": extreme_entries,
        "extreme_ratio_rate": round(extreme_rate, 3),
        "total_entries": len(ratios),
        "score": score,
    }


def _analyze_system_prompt_weight(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 5: System prompt as fraction of total input."""
    weights = []
    for e in entries:
        if e.input_token_count > 0 and e.system_prompt_length > 0:
            weight = min(e.system_prompt_length / e.input_token_count, 1.0)
            weights.append(weight)

    if not weights:
        return {"status": "insufficient_data", "score": 8}

    avg_weight = sum(weights) / len(weights)

    # Score: <20% system prompt = 10, 20-40% = 7, 40-60% = 5, >60% = 3
    if avg_weight < 0.20:
        score = 10
    elif avg_weight < 0.40:
        score = 7
    elif avg_weight < 0.60:
        score = 5
    else:
        score = 3

    return {
        "avg_system_prompt_weight": round(avg_weight, 3),
        "entries_analyzed": len(weights),
        "score": score,
    }


def _analyze_response_waste(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 6: Response patterns suggesting waste."""
    short_on_expensive = 0  # <50 output tokens on reasoning/execution tier
    very_long = 0  # >4000 output tokens
    expensive_count = 0

    for e in entries:
        if e.model_tier in ("reasoning", "execution"):
            expensive_count += 1
            if e.output_token_count < 50 and e.output_token_count > 0:
                short_on_expensive += 1
        if e.output_token_count > 4000:
            very_long += 1

    total = len(entries)
    short_rate = short_on_expensive / expensive_count if expensive_count > 0 else 0
    long_rate = very_long / total if total > 0 else 0

    # Score based on waste patterns
    score = 10
    if short_rate > 0.15:
        score -= 3
    elif short_rate > 0.05:
        score -= 1
    if long_rate > 0.30:
        score -= 3
    elif long_rate > 0.10:
        score -= 1
    score = max(score, 1)

    return {
        "short_responses_on_expensive_models": short_on_expensive,
        "short_response_rate": round(short_rate, 3),
        "very_long_responses": very_long,
        "long_response_rate": round(long_rate, 3),
        "total_entries": total,
        "score": score,
    }


def _analyze_context_overhead(entries: list[InvocationLogEntry]) -> dict:
    """Dimension 7: Detect context bloat from MCP tools, skills, plugins, etc."""
    entries_with_prompt = [e for e in entries if e.system_prompt_text]
    if not entries_with_prompt:
        return {"status": "no_system_prompt_text_available", "score": 8}

    # Analyze a representative sample of system prompts (use most common hash)
    hash_counts = Counter(e.system_prompt_hash for e in entries_with_prompt)
    most_common_hash = hash_counts.most_common(1)[0][0]
    representative = next(e for e in entries_with_prompt if e.system_prompt_hash == most_common_hash)
    prompt_text = representative.system_prompt_text

    # Pattern detection
    breakdown = {}

    # MCP tool schemas — look for tool/function definition patterns
    tool_patterns = [
        re.findall(r'<function>\{', prompt_text),
        re.findall(r'"type"\s*:\s*"function"', prompt_text),
        re.findall(r'<tool_description>', prompt_text),
        re.findall(r'"name"\s*:\s*"[^"]+",\s*"description"', prompt_text),
    ]
    detected_tools = max(len(matches) for matches in tool_patterns) if any(tool_patterns) else 0
    mcp_tokens = detected_tools * 800
    breakdown["mcp_tool_schemas"] = {"detected_tools": detected_tools, "est_tokens": mcp_tokens}

    # Skill blocks
    skill_markers = len(re.findall(r'(?:skills?.*?available|<skill|SKILL\.md|skill_name)', prompt_text, re.IGNORECASE))
    skill_tokens = skill_markers * 150
    breakdown["skills"] = {"detected_blocks": skill_markers, "est_tokens": skill_tokens}

    # CLAUDE.md content
    claude_md_present = bool(re.search(r'(?:CLAUDE\.md|Contents of.*?CLAUDE\.md|project instructions)', prompt_text, re.IGNORECASE))
    # Estimate by looking for the CLAUDE.md section boundaries
    claude_md_tokens = 0
    if claude_md_present:
        match = re.search(r'Contents of.*?CLAUDE\.md.*?(?=Contents of|\Z)', prompt_text, re.DOTALL | re.IGNORECASE)
        if match:
            claude_md_tokens = len(match.group()) // 4
        else:
            claude_md_tokens = 1500  # conservative estimate
    breakdown["claude_md"] = {"detected": claude_md_present, "est_tokens": claude_md_tokens}

    # System reminders
    reminder_count = len(re.findall(r'<system-reminder>', prompt_text))
    reminder_tokens = reminder_count * 500  # avg reminder size
    breakdown["system_reminders"] = {"count": reminder_count, "est_tokens": reminder_tokens}

    # Plugin/MCP server instructions
    plugin_present = bool(re.search(r'(?:MCP Server Instructions|plugin.*?instructions)', prompt_text, re.IGNORECASE))
    plugin_tokens = 0
    if plugin_present:
        match = re.search(r'MCP Server Instructions.*?(?=<system-reminder>|\Z)', prompt_text, re.DOTALL | re.IGNORECASE)
        plugin_tokens = len(match.group()) // 4 if match else 2000
    breakdown["plugin_instructions"] = {"detected": plugin_present, "est_tokens": plugin_tokens}

    # Memory content
    memory_present = bool(re.search(r'(?:MEMORY\.md|auto-memory|memory/)', prompt_text, re.IGNORECASE))
    memory_tokens = 0
    if memory_present:
        match = re.search(r'MEMORY\.md.*?(?=<system-reminder>|Contents of|\Z)', prompt_text, re.DOTALL | re.IGNORECASE)
        memory_tokens = len(match.group()) // 4 if match else 500
    breakdown["memory"] = {"detected": memory_present, "est_tokens": memory_tokens}

    # Total overhead vs core prompt
    total_overhead = mcp_tokens + skill_tokens + claude_md_tokens + reminder_tokens + plugin_tokens + memory_tokens
    total_prompt_tokens = representative.system_prompt_length
    core_tokens = max(0, total_prompt_tokens - total_overhead)
    breakdown["core_system_prompt"] = {"est_tokens": core_tokens}

    overhead_ratio = total_overhead / total_prompt_tokens if total_prompt_tokens > 0 else 0

    # Measure variation across entries (are all prompts similarly bloated?)
    prompt_lengths = [e.system_prompt_length for e in entries_with_prompt]
    avg_prompt_tokens = sum(prompt_lengths) / len(prompt_lengths)

    # Score: <30% overhead = 9, 30-50% = 7, 50-70% = 5, >70% = 3
    if overhead_ratio < 0.30:
        score = 9
    elif overhead_ratio < 0.50:
        score = 7
    elif overhead_ratio < 0.70:
        score = 5
    else:
        score = 3

    return {
        "avg_system_prompt_tokens": round(avg_prompt_tokens),
        "representative_prompt_tokens": total_prompt_tokens,
        "breakdown": breakdown,
        "total_overhead_tokens": total_overhead,
        "overhead_ratio": round(overhead_ratio, 3),
        "unique_prompt_variants": len(hash_counts),
        "score": score,
    }


# ---------------------------------------------------------------------------
# Findings and grading
# ---------------------------------------------------------------------------

def _build_findings(
    prompt_bloat: dict,
    model_mismatch: dict,
    caching: dict,
    io_ratio: dict,
    sys_weight: dict,
    response_waste: dict,
    context_overhead: dict,
) -> list[dict]:
    """Collect analysis results into prioritized findings."""
    findings = []

    def _add(category: str, analysis: dict, detail: str, severity: str, savings: float = 0.0):
        findings.append({
            "category": category,
            "severity": severity,
            "detail": detail,
            "score": analysis.get("score", 8),
            "estimated_savings_usd": savings,
        })

    score = prompt_bloat.get("score", 10)
    if score < 7:
        _add("prompt_bloat", prompt_bloat,
             f"System prompts are large — p95 is {prompt_bloat.get('p95_tokens', 'N/A')} tokens, "
             f"avg {prompt_bloat.get('avg_tokens', 'N/A')} tokens",
             "high" if score < 4 else "medium")

    score = model_mismatch.get("score", 10)
    if score < 7:
        _add("model_task_mismatch", model_mismatch,
             f"{model_mismatch.get('mismatch_rate', 0) * 100:.0f}% of requests use an overpowered model "
             f"({model_mismatch.get('mismatched_entries', 0)} of {model_mismatch.get('classified_entries', 0)})",
             "high" if score < 4 else "medium",
             model_mismatch.get("estimated_weekly_savings_usd", 0))

    score = caching.get("score", 10)
    if score < 7:
        _add("caching_opportunities", caching,
             f"{caching.get('reuse_ratio', 0) * 100:.0f}% of system prompts are repeated — "
             f"only {caching.get('unique_system_prompts', 0)} unique across {caching.get('total_system_prompts', 0)} requests",
             "high" if score < 4 else "medium",
             caching.get("potential_weekly_savings_usd", 0))

    score = io_ratio.get("score", 10)
    if score < 7:
        _add("io_ratio", io_ratio,
             f"{io_ratio.get('extreme_ratio_rate', 0) * 100:.0f}% of requests have >20:1 input:output ratio "
             f"(likely raw document ingestion)",
             "high" if score < 4 else "medium")

    score = sys_weight.get("score", 10)
    if score < 7:
        _add("system_prompt_weight", sys_weight,
             f"System prompts average {sys_weight.get('avg_system_prompt_weight', 0) * 100:.0f}% of total input tokens",
             "medium")

    score = response_waste.get("score", 10)
    if score < 7:
        _add("response_waste", response_waste,
             f"{response_waste.get('short_responses_on_expensive_models', 0)} short responses on expensive models, "
             f"{response_waste.get('very_long_responses', 0)} very long responses",
             "medium" if score > 4 else "high")

    score = context_overhead.get("score", 10)
    if score < 7:
        overhead = context_overhead.get("breakdown", {})
        tools = overhead.get("mcp_tool_schemas", {}).get("detected_tools", 0)
        ratio = context_overhead.get("overhead_ratio", 0)
        _add("context_overhead", context_overhead,
             f"{ratio * 100:.0f}% of system prompt is overhead "
             f"({tools} MCP tool schemas, "
             f"{overhead.get('skills', {}).get('detected_blocks', 0)} skill blocks, "
             f"{overhead.get('system_reminders', {}).get('count', 0)} system reminders)",
             "high" if score < 4 else "medium")

    # Sort by severity then score
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_order.get(f["severity"], 2), f["score"]))

    return findings


def _build_recommendations(findings: list[dict]) -> list[str]:
    """Generate actionable recommendations from findings."""
    recs = []
    categories_seen = set()

    for f in findings:
        cat = f["category"]
        if cat in categories_seen:
            continue
        categories_seen.add(cat)

        if cat == "prompt_bloat":
            recs.append("Reduce system prompt size — consider splitting instructions into cached vs. dynamic sections")
        elif cat == "model_task_mismatch":
            savings = f.get("estimated_savings_usd", 0)
            recs.append(f"Route simple tasks to cheaper models — estimated ${savings:.2f}/week savings. "
                       "Use recommend_model to classify tasks into reasoning/execution/polish tiers")
        elif cat == "caching_opportunities":
            savings = f.get("estimated_savings_usd", 0)
            recs.append(f"Enable prompt caching for repeated system prompts — estimated ${savings:.2f}/week savings. "
                       "Cache reads cost 90% less than regular input")
        elif cat == "io_ratio":
            recs.append("Convert large documents to markdown before ingestion — "
                       "use scripts/convert_heavy_file.py for 10-100x token savings")
        elif cat == "system_prompt_weight":
            recs.append("System prompts dominate input tokens — audit what's loaded into context. "
                       "Run context_audit for a breakdown of CLAUDE.md, MCP servers, and skills")
        elif cat == "response_waste":
            recs.append("Review short-response requests on expensive models — "
                       "these may be better served by cheaper models")
        elif cat == "context_overhead":
            recs.append("Reduce context overhead from MCP tools, skills, and plugins — "
                       "disable unused MCP servers and prune skill definitions to cut per-request cost")

    if not recs:
        recs.append("Token usage looks efficient! No major optimization opportunities found.")

    return recs


def _grade(findings: list[dict]) -> str:
    """Overall grade based on dimension scores."""
    scores = [f["score"] for f in findings]
    if not scores:
        return "A"

    avg = sum(scores) / len(scores)
    # Also weight by number of high-severity findings
    high_count = sum(1 for f in findings if f["severity"] == "high")
    penalty = high_count * 0.5
    adjusted = max(avg - penalty, 1)

    if adjusted >= 8:
        return "A"
    if adjusted >= 6:
        return "B"
    if adjusted >= 4:
        return "C"
    if adjusted >= 2:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _top_n_by_key(items: list[dict], key: str, n: int) -> list[dict]:
    """Summarize top N items grouped by a key."""
    counts = Counter(item[key] for item in items)
    return [{"value": k, "count": v} for k, v in counts.most_common(n)]
