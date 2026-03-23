"""Output guardrails for Token Cop.

Scans agent responses before they reach the user to ensure
sensitive data (API keys, account IDs) is not leaked.

When deployed to AgentCore with Gateway + Policy Engine, Cedar
policies handle this at the infrastructure level. This module
provides defense-in-depth for local dev and direct invocations.
"""
import re

# Patterns that should never appear in agent output
SENSITIVE_PATTERNS = [
    (re.compile(r"sk-or-v1-[a-f0-9]{64}", re.IGNORECASE), "OpenRouter API key"),
    (re.compile(r"sk-ant-(?:admin|api)\S{20,}", re.IGNORECASE), "Anthropic API key"),
    (re.compile(r"sk-admin-\S{20,}", re.IGNORECASE), "OpenAI admin key"),
    (re.compile(r"sk-proj-\S{20,}", re.IGNORECASE), "OpenAI project key"),
    (re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE), "AWS access key"),
    (re.compile(r"(?:aws_secret_access_key|AWS_SECRET)\s*=\s*\S+", re.IGNORECASE), "AWS secret key"),
]


def scrub_response(text: str) -> str:
    """Remove any sensitive data patterns from agent output.

    Args:
        text: The agent's response text.

    Returns:
        Scrubbed text with sensitive values replaced.
    """
    for pattern, label in SENSITIVE_PATTERNS:
        text = pattern.sub(f"[REDACTED {label}]", text)
    return text


def check_response(text: str) -> list[str]:
    """Check if response contains sensitive data without modifying it.

    Args:
        text: The agent's response text.

    Returns:
        List of violation descriptions. Empty list means clean.
    """
    violations = []
    for pattern, label in SENSITIVE_PATTERNS:
        if pattern.search(text):
            violations.append(f"Response contains {label}")
    return violations
