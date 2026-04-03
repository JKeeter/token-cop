"""Configuration loader for Token Cop.

In production (AgentCore Runtime): loads secrets from SSM Parameter Store.
In local dev: falls back to .env file.
"""
import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

SSM_PREFIX = "/token-cop"
SSM_PARAMS = {
    "OPENROUTER_API_KEY": f"{SSM_PREFIX}/openrouter-api-key",
    "OPENAI_ADMIN_API_KEY": f"{SSM_PREFIX}/openai-admin-api-key",
    "OPENAI_ORG_ID": f"{SSM_PREFIX}/openai-org-id",
    "BEDROCK_LOG_BUCKET": f"{SSM_PREFIX}/bedrock-log-bucket",
    "BEDROCK_LOG_PREFIX": f"{SSM_PREFIX}/bedrock-log-prefix",
}

_ssm_cache: dict[str, str] = {}


def get_secret(env_var: str) -> str:
    """Get a secret, trying env var first then SSM Parameter Store.

    Args:
        env_var: The environment variable name (e.g., "OPENROUTER_API_KEY").

    Returns:
        The secret value, or empty string if not found.
    """
    # 1. Check environment / .env
    value = os.environ.get(env_var, "")
    if value:
        return value

    # 2. Check SSM cache
    if env_var in _ssm_cache:
        return _ssm_cache[env_var]

    # 3. Try SSM Parameter Store
    ssm_name = SSM_PARAMS.get(env_var)
    if not ssm_name:
        return ""

    try:
        import boto3
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        resp = ssm.get_parameter(Name=ssm_name, WithDecryption=True)
        value = resp["Parameter"]["Value"]
        _ssm_cache[env_var] = value
        # Also set in env so tools pick it up via os.environ
        os.environ[env_var] = value
        return value
    except Exception as exc:
        logger.warning("Failed to load %s from SSM (%s): %s", env_var, ssm_name, exc)
        return ""


def load_all_secrets():
    """Pre-load all secrets from SSM into environment variables."""
    for env_var in SSM_PARAMS:
        get_secret(env_var)


# Provider configuration
PROVIDERS = {
    "bedrock": {
        "enabled": True,
        "auth": "iam",
    },
    "openrouter": {
        "enabled": bool(get_secret("OPENROUTER_API_KEY")),
        "auth": "api_key",
    },
    "anthropic": {
        "enabled": bool(get_secret("ANTHROPIC_ADMIN_API_KEY")),
        "auth": "api_key",
    },
    "openai": {
        "enabled": bool(get_secret("OPENAI_ADMIN_API_KEY")),
        "auth": "api_key",
    },
}


def get_enabled_providers() -> list[str]:
    """Return list of provider names that have credentials configured."""
    return [name for name, cfg in PROVIDERS.items() if cfg["enabled"]]
