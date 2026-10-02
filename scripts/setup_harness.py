"""Set up the Token Cop AgentCore *harness* twin (opt-in).

The harness is AWS's managed Strands agent loop: model, system prompt, tools,
and limits are declared as configuration and AgentCore runs the loop. Token
Cop's container runtime is left untouched — this script stands up a second
chassis that calls the SAME 13 tools through the SAME gateway (and therefore
the same Cedar policy engine).

Resources created (idempotent; nothing here touches the gateway itself, the
existing ``token-cop-target``, or Cognito):
  - IAM role        : token-cop-tools-lambda-role
  - Lambda          : token-cop-tools              (13 tools, python3.13/arm64)
  - Gateway target  : token-cop-tools              (Lambda target on token-cop-gateway-7q9nodpeem;
                                                    tools appear as token-cop-tools___<name>)
  - Lambda permission + inline policy on AgentCoreGatewayExecutionRole to invoke it
  - OAuth2 provider : token-cop-cognito            (AgentCore Identity; Cognito client_credentials)
  - IAM role        : token-cop-harness-role
  - Harness         : token_cop_harness
  - SSM parameters  : /token-cop/harness-arn, /token-cop/harness-id

Usage:
    source .venv/bin/activate
    python -m scripts.setup_harness --status
    python -m scripts.setup_harness --dry-run --enable
    python -m scripts.setup_harness --enable
    python -m scripts.setup_harness --enable --skip-lambda        # don't rebuild the zip
    python -m scripts.setup_harness --emit-tool-schema             # print generated gateway schema
    python -m scripts.setup_harness --update-prompt                # re-render prompt -> new version
    python -m scripts.setup_harness --endpoint PROD --version 1    # pin a named endpoint
    python -m scripts.setup_harness --teardown

The gateway tool schema is GENERATED from each Strands tool's ``tool_spec`` so
the two chassis cannot drift. The harness reaches the gateway with an OAuth2
credential provider built from the Cognito client already in SSM
(``/token-cop/gateway-client-id`` / ``-secret``), scope ``token-cop-gateway/invoke``.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from agent.agent import MODEL_ID, TOKEN_COP_TOOLS, build_system_prompt
from agent.harness_client import (
    DEFAULT_ALLOWED_TOOLS,
    GATEWAY_TOOL_NAME,
    SSM_HARNESS_ARN,
    TOOLS_TARGET_NAME,
)

REGION = os.environ.get("AWS_REGION", "us-east-1")
REPO_ROOT = Path(__file__).resolve().parent.parent

# Resource names — prefixed so teardown is unambiguous.
PREFIX = "token-cop"
TOOLS_FN = f"{PREFIX}-tools"
TOOLS_ROLE = f"{PREFIX}-tools-lambda-role"
HARNESS_ROLE = f"{PREFIX}-harness-role"
HARNESS_NAME = "token_cop_harness"  # pattern [a-zA-Z][a-zA-Z0-9_]{0,39}
PROVIDER_NAME = f"{PREFIX}-cognito"
GATEWAY_INVOKE_POLICY = f"{PREFIX}-tools-invoke"  # inline policy on the gateway role
LAMBDA_HANDLER = "tool_dispatch.handler"

# Existing infrastructure we attach to (never created/deleted here).
GATEWAY_ID = "token-cop-gateway-7q9nodpeem"
GATEWAY_ROLE = "AgentCoreGatewayExecutionRole"
COGNITO_POOL_ID = "us-east-1_hYAk8mbYH"
COGNITO_DOMAIN = "agentcore-d4673f36"
COGNITO_DISCOVERY_URL = (
    f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_POOL_ID}/.well-known/openid-configuration"
)
COGNITO_SCOPE = "token-cop-gateway/invoke"
ENFORCEMENT_TABLE = f"{PREFIX}-enforcement-usage"
DENY_POLICY_NAME = "TokenCopBedrockBudgetDeny"
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "TokenCopMemory-oGHHvc2vSN")

# SSM
SSM_PREFIX = "/token-cop"
SSM_HARNESS_ID = f"{SSM_PREFIX}/harness-id"
SSM_CLIENT_ID = f"{SSM_PREFIX}/gateway-client-id"
SSM_CLIENT_SECRET = f"{SSM_PREFIX}/gateway-client-secret"

# Harness loop limits — the knobs Token Cop preaches, applied to itself.
HARNESS_MAX_ITERATIONS = 12
HARNESS_MAX_TOKENS = 8000
HARNESS_TIMEOUT_SECONDS = 180
HARNESS_SLIDING_WINDOW = 30

# Lambda packaging
LAMBDA_RUNTIME = "python3.13"
LAMBDA_ARCH = "arm64"
LAMBDA_MEMORY_MB = 1024
LAMBDA_TIMEOUT_S = 300
LAMBDA_DEPS = ["strands-agents>=1.29,<2", "boto3", "requests", "python-dateutil", "python-dotenv"]
PACKAGE_DIRS = ["tools", "models", "memory", "utils"]
PACKAGE_AGENT_FILES = ["__init__.py", "config.py", "tracing.py", "guardrails.py", "agent.py", "harness_client.py"]
ZIP_DIRECT_UPLOAD_LIMIT = 45 * 1024 * 1024  # Lambda ZipFile= hard limit is 50MB; keep headroom
ZIP_FIXED_DATE = (2000, 1, 1, 0, 0, 0)  # deterministic zip -> stable CodeSha256

# Gateway SchemaDefinition allows ONLY these keys (verified against the boto3 service model).
SCHEMA_ALLOWED_KEYS = {"type", "description", "properties", "required", "items"}
SCHEMA_ALLOWED_TYPES = {"string", "number", "object", "array", "boolean", "integer"}
TOOL_DESCRIPTION_MAX = 1000

TAGS = {"ManagedBy": "token-cop-harness"}

log = logging.getLogger("setup_harness")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Never let --verbose turn on botocore wire logging: it prints signed
    # requests including session tokens.
    for noisy in ("botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def account_id(sts) -> str:
    return sts.get_caller_identity()["Account"]


def _exists(call, *not_found_codes: str):
    """Run an AWS call; return None if NotFound, else the response."""
    try:
        return call()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in not_found_codes:
            return None
        raise


def _wait(describe, ok: set[str], failed: set[str], what: str, timeout: int = 600, interval: int = 5):
    """Poll ``describe()`` -> (status, detail) until status in ok/failed."""
    deadline = time.monotonic() + timeout
    while True:
        status, detail = describe()
        if status in ok:
            log.info("%s is %s", what, status)
            return status
        if status in failed:
            raise RuntimeError(f"{what} entered {status}: {detail}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what} still {status} after {timeout}s")
        log.debug("%s is %s; waiting", what, status)
        time.sleep(interval)


def gateway_arn(acct: str) -> str:
    return f"arn:aws:bedrock-agentcore:{REGION}:{acct}:gateway/{GATEWAY_ID}"


def provider_resource_arn(acct: str) -> str:
    return f"arn:aws:bedrock-agentcore:{REGION}:{acct}:token-vault/default/oauth2credentialprovider/{PROVIDER_NAME}"


# ---------------------------------------------------------------------------
# Pure helpers: gateway tool schema (unit-tested, no AWS)
# ---------------------------------------------------------------------------

def _clean_schema(schema: dict) -> dict:
    """Recursively reduce a JSON schema to the gateway SchemaDefinition subset.

    Strips ``default`` (and anything else unknown), normalizes nullable type
    lists (``["string", "null"]`` -> ``"string"``), and recurses into
    ``properties`` and ``items``.
    """
    out: dict = {}
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t is None and "properties" in schema:
        t = "object"
    if t is None and "items" in schema:
        t = "array"
    if t is None:
        t = "string"  # last resort; every tool arg here is typed, this is defensive
    if t not in SCHEMA_ALLOWED_TYPES:
        raise ValueError(f"unsupported schema type {t!r}")
    out["type"] = t
    if "description" in schema and schema["description"]:
        out["description"] = str(schema["description"])
    if "properties" in schema:
        out["properties"] = {k: _clean_schema(v) for k, v in schema["properties"].items()}
    if schema.get("required"):
        out["required"] = list(schema["required"])
    if "items" in schema:
        items = schema["items"]
        out["items"] = _clean_schema(items if isinstance(items, dict) else {})
    return out


def strands_spec_to_gateway_tool(tool) -> dict:
    """Convert a Strands ``DecoratedFunctionTool`` into a gateway ToolDefinition."""
    spec = tool.tool_spec
    input_schema = spec["inputSchema"]["json"]
    description = (spec.get("description") or tool.tool_name).strip()
    if len(description) > TOOL_DESCRIPTION_MAX:
        description = description[: TOOL_DESCRIPTION_MAX - 1].rstrip() + "…"
    return {
        "name": tool.tool_name,
        "description": description,
        "inputSchema": _clean_schema(input_schema),
    }


def gateway_tool_schema(tools=None) -> list[dict]:
    """The ``toolSchema.inlinePayload`` for the Lambda gateway target."""
    return [strands_spec_to_gateway_tool(t) for t in (tools if tools is not None else TOKEN_COP_TOOLS)]


# ---------------------------------------------------------------------------
# Pure helpers: IAM policy documents (unit-tested, no AWS)
# ---------------------------------------------------------------------------

def lambda_trust_policy() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }


def harness_trust_policy(acct: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": acct},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{acct}:*"},
            },
        }],
    }


def tools_lambda_policy(acct: str) -> dict:
    """Everything the 13 tools call (mirrors the runtime execution role).

    Verified against ``boto3.client(`` calls in tools/, memory/, agent/config.py:
    CloudWatch metrics, SSM+KMS (secrets), S3 (invocation logs), Cost Explorer,
    DynamoDB + IAM (enforcement), AgentCore Memory (snapshots).
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockUsageMetrics",
                "Effect": "Allow",
                "Action": ["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"],
                "Resource": "*",
            },
            {
                "Sid": "TokenCopSecrets",
                "Effect": "Allow",
                "Action": ["ssm:GetParameter", "ssm:GetParameters"],
                "Resource": f"arn:aws:ssm:{REGION}:{acct}:parameter/token-cop/*",
            },
            {
                "Sid": "DecryptSecureStrings",
                "Effect": "Allow",
                "Action": "kms:Decrypt",
                "Resource": "*",
                "Condition": {"StringEquals": {"kms:ViaService": f"ssm.{REGION}.amazonaws.com"}},
            },
            {
                # The invocation-log bucket is resolved from SSM at runtime, so it
                # cannot be pinned here without a second setup pass. Read-only,
                # acceptable for the demo; scope to the bucket for production.
                "Sid": "InvocationLogsReadOnly",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": "arn:aws:s3:::*",
            },
            {
                "Sid": "CostAttribution",
                "Effect": "Allow",
                "Action": ["ce:GetCostAndUsage", "ce:GetDimensionValues", "ce:GetTags"],
                "Resource": "*",
            },
            {
                "Sid": "EnforcementTable",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                    "dynamodb:Query", "dynamodb:Scan", "dynamodb:DeleteItem",
                ],
                "Resource": f"arn:aws:dynamodb:{REGION}:{acct}:table/{ENFORCEMENT_TABLE}",
            },
            {
                "Sid": "EnforcementDenyPolicyOnly",
                "Effect": "Allow",
                "Action": [
                    "iam:AttachUserPolicy", "iam:AttachRolePolicy",
                    "iam:DetachUserPolicy", "iam:DetachRolePolicy",
                    "iam:ListAttachedUserPolicies", "iam:ListAttachedRolePolicies",
                ],
                "Resource": "*",
                "Condition": {"ArnEquals": {"iam:PolicyARN": f"arn:aws:iam::{acct}:policy/{DENY_POLICY_NAME}"}},
            },
            {
                "Sid": "UsageSnapshotsMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents", "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:GetMemory",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{REGION}:{acct}:memory/{MEMORY_ID}",
            },
        ],
    }


def harness_role_policy(acct: str, gateway_arn_: str, provider_arn: str) -> dict:
    """AWS sample harness execution policy + the OAuth gateway hop."""
    workload_identity = f"arn:aws:bedrock-agentcore:{REGION}:{acct}:workload-identity-directory/default"
    harness_identity = f"{workload_identity}/workload-identity/harness_{HARNESS_NAME}-*"
    token_vault = f"arn:aws:bedrock-agentcore:{REGION}:{acct}:token-vault/default"
    provider_resources = sorted({provider_resource_arn(acct), provider_arn})
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockModelInvoke",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*",
                    f"arn:aws:bedrock:{REGION}:{acct}:*",
                ],
            },
            {
                "Sid": "ECRPublicAndSTS",
                "Effect": "Allow",
                "Action": ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"],
                "Resource": "*",
            },
            {
                "Sid": "XRay",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules", "xray:GetSamplingTargets",
                ],
                "Resource": "*",
            },
            {
                "Sid": "LogsGroups",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:DescribeLogStreams"],
                "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "LogsDescribe",
                "Effect": "Allow",
                "Action": "logs:DescribeLogGroups",
                "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:*",
            },
            {
                "Sid": "LogsStreams",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
            },
            {
                "Sid": "LogsResourcePolicy",
                "Effect": "Allow",
                "Action": "logs:PutResourcePolicy",
                "Resource": "*",
            },
            {
                "Sid": "CloudWatchMetrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "WorkloadIdentity",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [workload_identity, harness_identity],
            },
            {
                "Sid": "GatewayOAuthToken",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:GetResourceOauth2Token",
                "Resource": [token_vault, *provider_resources, workload_identity, harness_identity],
            },
            {
                "Sid": "GatewayOAuthClientSecret",
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": f"arn:aws:secretsmanager:{REGION}:{acct}:secret:bedrock-agentcore-identity!default/oauth2/{PROVIDER_NAME}-*",
            },
            {
                "Sid": "InvokeGateway",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": gateway_arn_,
            },
        ],
    }


def gateway_invoke_policy(fn_arn: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["lambda:InvokeFunction"],
            "Resource": [fn_arn],
        }],
    }


# ---------------------------------------------------------------------------
# Pure helper: harness configuration
# ---------------------------------------------------------------------------

def harness_config(role_arn: str, gateway_arn_: str, provider_arn: str) -> dict:
    """The create_harness/update_harness kwargs (minus harnessName/harnessId)."""
    return {
        "executionRoleArn": role_arn,
        "model": {"bedrockModelConfig": {"modelId": MODEL_ID}},
        "systemPrompt": [{"text": build_system_prompt()}],
        "tools": [{
            "type": "agentcore_gateway",
            "name": GATEWAY_TOOL_NAME,
            "config": {"agentCoreGateway": {
                "gatewayArn": gateway_arn_,
                "outboundAuth": {"oauth": {
                    "providerArn": provider_arn,
                    "scopes": [COGNITO_SCOPE],
                    "grantType": "CLIENT_CREDENTIALS",
                }},
            }},
        }],
        "allowedTools": list(DEFAULT_ALLOWED_TOOLS),
        "memory": {"disabled": {}},
        "maxIterations": HARNESS_MAX_ITERATIONS,
        "maxTokens": HARNESS_MAX_TOKENS,
        "timeoutSeconds": HARNESS_TIMEOUT_SECONDS,
        "truncation": {
            "strategy": "sliding_window",
            "config": {"slidingWindow": {"messagesCount": HARNESS_SLIDING_WINDOW}},
        },
    }


def oauth_provider_config(client_id: str, client_secret: str) -> dict:
    """``oauth2ProviderConfigInput`` for the Cognito client_credentials provider.

    Uses the pool's OIDC discovery document (it advertises ``token_endpoint``
    and ``client_secret_post``). If discoveryUrl validation ever fails, swap
    ``oauthDiscovery`` for the explicit ``authorizationServerMetadata`` dict
    below — one-dict change.
    """
    discovery = {"discoveryUrl": COGNITO_DISCOVERY_URL}
    # discovery = {"authorizationServerMetadata": {
    #     "issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_POOL_ID}",
    #     "authorizationEndpoint": f"https://{COGNITO_DOMAIN}.auth.{REGION}.amazoncognito.com/oauth2/authorize",
    #     "tokenEndpoint": f"https://{COGNITO_DOMAIN}.auth.{REGION}.amazoncognito.com/oauth2/token",
    #     "responseTypes": ["token"],
    # }}
    return {"customOauth2ProviderConfig": {
        "oauthDiscovery": discovery,
        "clientId": client_id,
        "clientSecret": client_secret,
        "clientAuthenticationMethod": "CLIENT_SECRET_POST",
    }}


# ---------------------------------------------------------------------------
# Lambda packaging
# ---------------------------------------------------------------------------

def _install_deps(target: Path):
    """Install LAMBDA_DEPS for aarch64/py3.13 into ``target`` (uv, else pip)."""
    if shutil.which("uv"):
        cmd = [
            "uv", "pip", "install", "--quiet",
            "--python-platform", "aarch64-manylinux2014",
            "--python-version", "3.13",
            "--only-binary", ":all:",
            "--target", str(target),
            *LAMBDA_DEPS,
        ]
    else:
        cmd = [
            sys.executable, "-m", "pip", "install", "--quiet",
            "--platform", "manylinux2014_aarch64",
            "--implementation", "cp",
            "--python-version", "3.13",
            "--only-binary=:all:",
            "--target", str(target),
            *LAMBDA_DEPS,
        ]
    log.info("Installing Lambda deps with %s", cmd[0])
    subprocess.run(cmd, check=True)


def _copy_tree(src: Path, dst: Path):
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.swp", ".DS_Store"),
        dirs_exist_ok=True,
    )


def _zip_dir_deterministic(root: Path) -> bytes:
    """Zip ``root`` with sorted entries and fixed timestamps (stable sha256)."""
    buf = io.BytesIO()
    skip_dirs = {"__pycache__", "bin"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if any(part in skip_dirs for part in path.relative_to(root).parts):
                continue
            if path.is_dir() or path.suffix in (".pyc", ".pyo") or path.name == ".lock":
                continue  # .lock is uv's install marker
            arcname = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=ZIP_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 << 16)
            zf.writestr(info, path.read_bytes())
    return buf.getvalue()


def build_lambda_package(repo_root: Path | None = None, install_deps: bool = True) -> bytes:
    """Build the ``token-cop-tools`` deployment zip.

    Layout (all at zip root, importable as-is):
        tool_dispatch.py            <- scripts/lambda/tool_dispatch.py
        agent/{__init__,config,tracing,guardrails,agent,harness_client}.py
        tools/ models/ memory/ utils/
        <site-packages of LAMBDA_DEPS>   (skipped when install_deps=False, for tests)
    """
    repo_root = Path(repo_root or REPO_ROOT)
    with tempfile.TemporaryDirectory(prefix="token-cop-tools-") as tmp:
        stage = Path(tmp)
        if install_deps:
            _install_deps(stage)
        for d in PACKAGE_DIRS:
            src = repo_root / d
            if not src.is_dir():
                raise FileNotFoundError(f"package dir missing: {src}")
            _copy_tree(src, stage / d)
        (stage / "agent").mkdir(exist_ok=True)
        for f in PACKAGE_AGENT_FILES:
            shutil.copy2(repo_root / "agent" / f, stage / "agent" / f)
        shutil.copy2(repo_root / "scripts" / "lambda" / "tool_dispatch.py", stage / "tool_dispatch.py")
        package = _zip_dir_deterministic(stage)
    size_mb = len(package) / 1024 / 1024
    log.info("Lambda package: %.1f MB zipped", size_mb)
    if len(package) > ZIP_DIRECT_UPLOAD_LIMIT:
        raise RuntimeError(
            f"Lambda package is {size_mb:.1f} MB zipped; exceeds the {ZIP_DIRECT_UPLOAD_LIMIT // 1024 // 1024} MB "
            "direct-upload guard. Trim LAMBDA_DEPS or switch to an S3 upload / container image."
        )
    return package


def _code_sha256(package: bytes) -> str:
    """Lambda's ``CodeSha256`` is base64(sha256(zip))."""
    return base64.b64encode(hashlib.sha256(package).digest()).decode()


# ---------------------------------------------------------------------------
# IAM roles
# ---------------------------------------------------------------------------

def ensure_role(iam, role_name: str, trust: dict, inline_policy: dict, managed: list[str], dry_run: bool) -> str:
    existing = _exists(lambda: iam.get_role(RoleName=role_name), "NoSuchEntity")
    if existing:
        arn = existing["Role"]["Arn"]
        if not dry_run:
            iam.put_role_policy(
                RoleName=role_name, PolicyName=f"{role_name}-inline",
                PolicyDocument=json.dumps(inline_policy),
            )
        log.info("IAM role exists: %s (inline policy refreshed)", role_name)
        return arn

    if dry_run:
        log.info("[dry-run] Would create IAM role: %s (managed=%s, inline sids=%s)",
                 role_name, [m.rsplit('/', 1)[-1] for m in managed],
                 [s.get("Sid", "?") for s in inline_policy["Statement"]])
        return f"arn:aws:iam::DRY-RUN:role/{role_name}"

    log.info("Creating IAM role: %s", role_name)
    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
    )
    for m in managed:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=m)
    iam.put_role_policy(
        RoleName=role_name, PolicyName=f"{role_name}-inline",
        PolicyDocument=json.dumps(inline_policy),
    )
    time.sleep(10)  # IAM propagation before Lambda/harness try to assume it
    return resp["Role"]["Arn"]


def delete_role(iam, role_name: str):
    try:
        for ap in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=ap["PolicyArn"])
        for ip in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=ip)
        iam.delete_role(RoleName=role_name)
        log.info("Deleted IAM role: %s", role_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            log.warning("Role cleanup %s: %s", role_name, exc)


# ---------------------------------------------------------------------------
# Tools Lambda
# ---------------------------------------------------------------------------

def ensure_tools_lambda(lam, role_arn: str, dry_run: bool, skip_build: bool) -> str:
    existing = _exists(lambda: lam.get_function(FunctionName=TOOLS_FN), "ResourceNotFoundException")

    if dry_run:
        action = "update" if existing else "create"
        log.info("[dry-run] Would %s Lambda %s (%s/%s, %dMB, %ds, handler %s, deps %s)%s",
                 action, TOOLS_FN, LAMBDA_RUNTIME, LAMBDA_ARCH, LAMBDA_MEMORY_MB, LAMBDA_TIMEOUT_S,
                 LAMBDA_HANDLER, LAMBDA_DEPS, " [zip build skipped]" if skip_build else "")
        return existing["Configuration"]["FunctionArn"] if existing else \
            f"arn:aws:lambda:{REGION}:DRY-RUN:function:{TOOLS_FN}"

    if existing and skip_build:
        log.info("Lambda exists: %s (--skip-lambda, code untouched)", TOOLS_FN)
        return existing["Configuration"]["FunctionArn"]

    package = build_lambda_package()
    sha = _code_sha256(package)

    if existing:
        fn_arn = existing["Configuration"]["FunctionArn"]
        if existing["Configuration"].get("CodeSha256") == sha:
            log.info("Lambda code unchanged (sha %s…); skipping update", sha[:12])
        else:
            log.info("Updating Lambda code: %s", TOOLS_FN)
            lam.update_function_code(FunctionName=TOOLS_FN, ZipFile=package, Architectures=[LAMBDA_ARCH])
            lam.get_waiter("function_updated_v2").wait(FunctionName=TOOLS_FN)
        lam.update_function_configuration(
            FunctionName=TOOLS_FN, Role=role_arn, Handler=LAMBDA_HANDLER, Runtime=LAMBDA_RUNTIME,
            MemorySize=LAMBDA_MEMORY_MB, Timeout=LAMBDA_TIMEOUT_S,
        )
        lam.get_waiter("function_updated_v2").wait(FunctionName=TOOLS_FN)
        return fn_arn

    log.info("Creating Lambda: %s", TOOLS_FN)
    resp = lam.create_function(
        FunctionName=TOOLS_FN,
        Runtime=LAMBDA_RUNTIME,
        Architectures=[LAMBDA_ARCH],
        Role=role_arn,
        Handler=LAMBDA_HANDLER,
        Code={"ZipFile": package},
        MemorySize=LAMBDA_MEMORY_MB,
        Timeout=LAMBDA_TIMEOUT_S,
        Description="Token Cop tools (Gateway Lambda target for the AgentCore harness twin)",
        Tags=TAGS,
    )
    lam.get_waiter("function_active_v2").wait(FunctionName=TOOLS_FN)
    return resp["FunctionArn"]


def ensure_gateway_invoke_permissions(lam, iam, fn_arn: str, gateway_arn_: str, dry_run: bool):
    """Belt and braces: resource policy on the Lambda + inline policy on the gateway role."""
    if dry_run:
        log.info("[dry-run] Would add Lambda permission for bedrock-agentcore.amazonaws.com (SourceArn %s)", gateway_arn_)
        log.info("[dry-run] Would put inline policy %s on role %s", GATEWAY_INVOKE_POLICY, GATEWAY_ROLE)
        return
    try:
        lam.add_permission(
            FunctionName=TOOLS_FN,
            StatementId="AllowGatewayInvoke",
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
            SourceArn=gateway_arn_,
        )
        log.info("Added Lambda invoke permission for the gateway")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceConflictException":
            raise
        log.info("Lambda invoke permission already present")
    iam.put_role_policy(
        RoleName=GATEWAY_ROLE, PolicyName=GATEWAY_INVOKE_POLICY,
        PolicyDocument=json.dumps(gateway_invoke_policy(fn_arn)),
    )
    log.info("Gateway role %s may invoke %s", GATEWAY_ROLE, TOOLS_FN)


# ---------------------------------------------------------------------------
# Gateway target
# ---------------------------------------------------------------------------

def find_gateway_target(ctl) -> dict | None:
    token = None
    while True:
        kwargs = {"gatewayIdentifier": GATEWAY_ID}
        if token:
            kwargs["nextToken"] = token
        resp = ctl.list_gateway_targets(**kwargs)
        for item in resp.get("items", []):
            if item.get("name") == TOOLS_TARGET_NAME:
                return item
        token = resp.get("nextToken")
        if not token:
            return None


def _target_status(ctl, target_id: str):
    resp = ctl.get_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=target_id)
    return resp["status"], resp.get("statusReasons")


def ensure_gateway_target(ctl, fn_arn: str, dry_run: bool) -> str:
    schema = gateway_tool_schema()
    target_config = {"mcp": {"lambda": {"lambdaArn": fn_arn, "toolSchema": {"inlinePayload": schema}}}}
    creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    existing = find_gateway_target(ctl)

    if dry_run:
        log.info("[dry-run] Would %s gateway target %s on %s with %d tools: %s",
                 "update" if existing else "create", TOOLS_TARGET_NAME, GATEWAY_ID,
                 len(schema), ", ".join(t["name"] for t in schema))
        return existing["targetId"] if existing else "DRY-RUN"

    if existing:
        target_id = existing["targetId"]
        log.info("Updating gateway target %s (%s) with %d tools", TOOLS_TARGET_NAME, target_id, len(schema))
        ctl.update_gateway_target(
            gatewayIdentifier=GATEWAY_ID, targetId=target_id, name=TOOLS_TARGET_NAME,
            description="Token Cop tools for the AgentCore harness twin (generated from tool_spec)",
            targetConfiguration=target_config, credentialProviderConfigurations=creds,
        )
    else:
        log.info("Creating gateway target %s on %s with %d tools", TOOLS_TARGET_NAME, GATEWAY_ID, len(schema))
        resp = ctl.create_gateway_target(
            gatewayIdentifier=GATEWAY_ID, name=TOOLS_TARGET_NAME,
            description="Token Cop tools for the AgentCore harness twin (generated from tool_spec)",
            targetConfiguration=target_config, credentialProviderConfigurations=creds,
        )
        target_id = resp["targetId"]

    _wait(lambda: _target_status(ctl, target_id), {"READY"},
          {"FAILED", "UPDATE_UNSUCCESSFUL", "SYNCHRONIZE_UNSUCCESSFUL"}, f"gateway target {TOOLS_TARGET_NAME}")
    return target_id


# ---------------------------------------------------------------------------
# OAuth2 credential provider (harness -> gateway)
# ---------------------------------------------------------------------------

def _provider_status(ctl):
    resp = ctl.get_oauth2_credential_provider(name=PROVIDER_NAME)
    return resp.get("status", "READY"), resp.get("failureReason")


def ensure_oauth_provider(ctl, ssm, acct: str, dry_run: bool) -> str:
    existing = _exists(lambda: ctl.get_oauth2_credential_provider(name=PROVIDER_NAME), "ResourceNotFoundException")
    if existing:
        log.info("OAuth2 credential provider exists: %s", PROVIDER_NAME)
        return existing["credentialProviderArn"]

    if dry_run:
        log.info("[dry-run] Would create OAuth2 credential provider %s (CustomOauth2, discovery %s, "
                 "client id from SSM %s)", PROVIDER_NAME, COGNITO_DISCOVERY_URL, SSM_CLIENT_ID)
        return provider_resource_arn(acct)

    client_id = ssm.get_parameter(Name=SSM_CLIENT_ID, WithDecryption=True)["Parameter"]["Value"]
    client_secret = ssm.get_parameter(Name=SSM_CLIENT_SECRET, WithDecryption=True)["Parameter"]["Value"]
    log.info("Creating OAuth2 credential provider: %s", PROVIDER_NAME)
    resp = ctl.create_oauth2_credential_provider(
        name=PROVIDER_NAME,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput=oauth_provider_config(client_id, client_secret),
        tags=TAGS,
    )
    arn = resp["credentialProviderArn"]
    if resp.get("status"):
        _wait(lambda: _provider_status(ctl), {"READY"}, {"CREATE_FAILED"}, f"OAuth2 provider {PROVIDER_NAME}", timeout=120)
    return arn


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def find_harness(ctl) -> dict | None:
    token = None
    while True:
        kwargs = {}
        if token:
            kwargs["nextToken"] = token
        resp = ctl.list_harnesses(**kwargs)
        for h in resp.get("harnesses", []):
            if h.get("harnessName") == HARNESS_NAME:
                return h
        token = resp.get("nextToken")
        if not token:
            return None


def _harness_status(ctl, harness_id: str):
    h = ctl.get_harness(harnessId=harness_id)["harness"]
    return h["status"], h.get("failureReason")


def wait_harness_ready(ctl, harness_id: str) -> dict:
    _wait(lambda: _harness_status(ctl, harness_id), {"READY"},
          {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"}, f"harness {HARNESS_NAME}")
    return ctl.get_harness(harnessId=harness_id)["harness"]


def ensure_harness(ctl, role_arn: str, gateway_arn_: str, provider_arn: str, dry_run: bool) -> dict:
    existing = find_harness(ctl)
    if existing:
        log.info("Harness exists: %s (%s, v%s) — use --update-prompt to publish a new version",
                 existing["harnessId"], existing["status"], existing.get("harnessVersion", "?"))
        return wait_harness_ready(ctl, existing["harnessId"])

    cfg = harness_config(role_arn, gateway_arn_, provider_arn)
    if dry_run:
        log.info("[dry-run] Would create harness %s: model=%s tools=[%s via oauth %s] allowedTools=%s "
                 "memory=disabled maxIterations=%d maxTokens=%d timeout=%ds prompt=%d chars",
                 HARNESS_NAME, MODEL_ID, GATEWAY_TOOL_NAME, PROVIDER_NAME, cfg["allowedTools"],
                 cfg["maxIterations"], cfg["maxTokens"], cfg["timeoutSeconds"], len(cfg["systemPrompt"][0]["text"]))
        return {"harnessId": "DRY-RUN", "arn": f"arn:aws:bedrock-agentcore:{REGION}:DRY-RUN:harness/{HARNESS_NAME}-DRYRUN000",
                "status": "READY", "harnessVersion": "1"}

    log.info("Creating harness: %s", HARNESS_NAME)
    resp = ctl.create_harness(harnessName=HARNESS_NAME, tags=TAGS, **cfg)
    harness_id = resp["harness"]["harnessId"]
    return wait_harness_ready(ctl, harness_id)


# Fields update_harness accepts that we round-trip from get_harness.
_HARNESS_RW_FIELDS = (
    "executionRoleArn", "model", "tools", "allowedTools", "memory",
    "maxIterations", "maxTokens", "timeoutSeconds", "truncation",
)


def update_prompt(ctl, ssm, dry_run: bool) -> str:
    """Re-render the system prompt for today and publish a new harness version."""
    existing = find_harness(ctl)
    if not existing:
        raise SystemExit(f"Harness {HARNESS_NAME} not found — run --enable first.")
    current = ctl.get_harness(harnessId=existing["harnessId"])["harness"]
    kwargs = {k: current[k] for k in _HARNESS_RW_FIELDS if k in current}
    kwargs["systemPrompt"] = [{"text": build_system_prompt()}]
    # update_harness wraps nullable structures: memory must be {"optionalValue": {...}}.
    if "memory" in kwargs:
        kwargs["memory"] = {"optionalValue": kwargs["memory"]}
    if dry_run:
        log.info("[dry-run] Would update_harness %s (currently v%s) with a re-rendered prompt",
                 existing["harnessId"], current.get("harnessVersion"))
        return current.get("harnessVersion", "?")
    resp = ctl.update_harness(harnessId=existing["harnessId"], **kwargs)
    h = wait_harness_ready(ctl, existing["harnessId"])
    version = h.get("harnessVersion") or resp["harness"].get("harnessVersion", "?")
    write_ssm(ssm, SSM_HARNESS_ARN, h["arn"], dry_run)
    log.info("Harness %s updated -> version %s", HARNESS_NAME, version)
    return version


def _endpoint_status(ctl, harness_id: str, name: str):
    e = ctl.get_harness_endpoint(harnessId=harness_id, endpointName=name)["endpoint"]
    return e["status"], e.get("failureReason")


def ensure_endpoint(ctl, name: str, version: str, dry_run: bool) -> dict:
    """Create or repoint a named harness endpoint at a specific immutable version."""
    existing = find_harness(ctl)
    if not existing:
        raise SystemExit(f"Harness {HARNESS_NAME} not found — run --enable first.")
    harness_id = existing["harnessId"]
    ep = _exists(lambda: ctl.get_harness_endpoint(harnessId=harness_id, endpointName=name), "ResourceNotFoundException")
    if dry_run:
        log.info("[dry-run] Would %s endpoint %s -> version %s on %s",
                 "update" if ep else "create", name, version, harness_id)
        return {"endpointName": name, "targetVersion": version, "status": "DRY-RUN"}
    if ep:
        log.info("Repointing endpoint %s -> version %s", name, version)
        ctl.update_harness_endpoint(harnessId=harness_id, endpointName=name, targetVersion=str(version))
    else:
        log.info("Creating endpoint %s -> version %s", name, version)
        ctl.create_harness_endpoint(harnessId=harness_id, endpointName=name, targetVersion=str(version), tags=TAGS)
    _wait(lambda: _endpoint_status(ctl, harness_id, name), {"READY"}, {"CREATE_FAILED", "UPDATE_FAILED"},
          f"endpoint {name}", timeout=300)
    return ctl.get_harness_endpoint(harnessId=harness_id, endpointName=name)["endpoint"]


# ---------------------------------------------------------------------------
# SSM
# ---------------------------------------------------------------------------

def write_ssm(ssm, name: str, value: str, dry_run: bool):
    if dry_run:
        log.info("[dry-run] Would write SSM %s=%s", name, value)
        return
    ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)


# ---------------------------------------------------------------------------
# Status / teardown
# ---------------------------------------------------------------------------

def show_status(ctl, iam, lam, ssm):
    print("\nToken Cop Harness Twin — Status")
    print("=" * 60)

    fn = _exists(lambda: lam.get_function(FunctionName=TOOLS_FN), "ResourceNotFoundException")
    if fn:
        c = fn["Configuration"]
        print(f"  Tools Lambda     : OK {c['FunctionArn']} [{c.get('State', '?')}, "
              f"{c.get('Runtime')}/{','.join(c.get('Architectures', []))}]")
    else:
        print(f"  Tools Lambda     : missing ({TOOLS_FN})")

    role = _exists(lambda: iam.get_role(RoleName=TOOLS_ROLE), "NoSuchEntity")
    print(f"  Tools role       : {'OK ' + role['Role']['Arn'] if role else 'missing (' + TOOLS_ROLE + ')'}")

    try:
        target = find_gateway_target(ctl)
        print(f"  Gateway target   : {'OK ' + target['targetId'] + ' [' + target['status'] + ']' if target else 'missing'}"
              f" ({TOOLS_TARGET_NAME} on {GATEWAY_ID})")
    except ClientError as exc:
        print(f"  Gateway target   : error listing targets: {exc.response.get('Error', {}).get('Code')}")

    prov = _exists(lambda: ctl.get_oauth2_credential_provider(name=PROVIDER_NAME), "ResourceNotFoundException")
    print(f"  OAuth2 provider  : {'OK ' + prov.get('status', 'READY') + ' ' + prov['credentialProviderArn'] if prov else 'missing (' + PROVIDER_NAME + ')'}")

    role = _exists(lambda: iam.get_role(RoleName=HARNESS_ROLE), "NoSuchEntity")
    print(f"  Harness role     : {'OK ' + role['Role']['Arn'] if role else 'missing (' + HARNESS_ROLE + ')'}")

    h = find_harness(ctl)
    if h:
        full = ctl.get_harness(harnessId=h["harnessId"])["harness"]
        print(f"  Harness          : OK {full['harnessId']} [{full['status']}] v{full.get('harnessVersion', '?')}")
        print(f"                     {full['arn']}")
        if full.get("failureReason"):
            print(f"                     failure: {full['failureReason']}")
        eps = ctl.list_harness_endpoints(harnessId=h["harnessId"]).get("endpoints", [])
        for e in eps:
            # list_harness_endpoints omits targetVersion once an endpoint is READY;
            # liveVersion is the version actually serving traffic.
            note = "  (DEFAULT advances with every update)" if e["endpointName"] == "DEFAULT" else "  (pinned)"
            print(f"  Endpoint {e['endpointName']:8} : [{e['status']}] live=v{e.get('liveVersion', '?')}{note}")
        if not eps:
            print("  Endpoints        : none listed")
    else:
        print(f"  Harness          : not enabled ({HARNESS_NAME})")

    for name in (SSM_HARNESS_ARN, SSM_HARNESS_ID):
        p = _exists(lambda name=name: ssm.get_parameter(Name=name), "ParameterNotFound")
        print(f"  SSM {name:22}: {p['Parameter']['Value'] if p else 'not set'}")
    print()


def teardown(ctl, iam, lam, ssm):
    log.info("Tearing down harness twin resources (gateway, Cognito, %s untouched)", "token-cop-target")

    # Harness (+ its non-default endpoints)
    h = find_harness(ctl)
    if h:
        hid = h["harnessId"]
        try:
            for e in ctl.list_harness_endpoints(harnessId=hid).get("endpoints", []):
                if e["endpointName"] != "DEFAULT":
                    ctl.delete_harness_endpoint(harnessId=hid, endpointName=e["endpointName"])
                    log.info("Deleted endpoint %s", e["endpointName"])
        except ClientError as exc:
            log.warning("Endpoint cleanup: %s", exc)
        ctl.delete_harness(harnessId=hid, deleteManagedMemory=False)
        log.info("Deleting harness %s (%s)…", HARNESS_NAME, hid)

        def _gone():
            r = _exists(lambda: ctl.get_harness(harnessId=hid), "ResourceNotFoundException")
            return ("GONE", None) if r is None else (r["harness"]["status"], r["harness"].get("failureReason"))
        _wait(_gone, {"GONE"}, {"DELETE_FAILED"}, f"harness {HARNESS_NAME} deletion")

    delete_role(iam, HARNESS_ROLE)

    # OAuth2 provider
    try:
        ctl.delete_oauth2_credential_provider(name=PROVIDER_NAME)
        log.info("Deleted OAuth2 credential provider: %s", PROVIDER_NAME)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            log.warning("Provider cleanup: %s", exc)

    # Gateway target (ours only)
    target = find_gateway_target(ctl)
    if target:
        ctl.delete_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=target["targetId"])
        log.info("Deleting gateway target %s (%s)…", TOOLS_TARGET_NAME, target["targetId"])

        def _tgone():
            r = _exists(lambda: ctl.get_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=target["targetId"]),
                        "ResourceNotFoundException")
            return ("GONE", None) if r is None else (r["status"], r.get("statusReasons"))
        _wait(_tgone, {"GONE"}, {"FAILED"}, f"gateway target {TOOLS_TARGET_NAME} deletion", timeout=300)

    # Our inline policy on the gateway role
    try:
        iam.delete_role_policy(RoleName=GATEWAY_ROLE, PolicyName=GATEWAY_INVOKE_POLICY)
        log.info("Removed %s from %s", GATEWAY_INVOKE_POLICY, GATEWAY_ROLE)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            log.warning("Gateway role policy cleanup: %s", exc)

    # Lambda + role
    try:
        lam.delete_function(FunctionName=TOOLS_FN)
        log.info("Deleted Lambda: %s", TOOLS_FN)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            log.warning("Lambda cleanup: %s", exc)
    delete_role(iam, TOOLS_ROLE)

    # SSM
    for name in (SSM_HARNESS_ARN, SSM_HARNESS_ID):
        try:
            ssm.delete_parameter(Name=name)
        except ClientError:
            pass

    log.info("Teardown complete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Token Cop AgentCore harness twin setup")
    parser.add_argument("--enable", action="store_true", help="Explicit opt-in to provision resources")
    parser.add_argument("--status", action="store_true", help="Show current state (default)")
    parser.add_argument("--teardown", action="store_true", help="Remove all harness-twin resources")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without making changes")
    parser.add_argument("--skip-lambda", action="store_true", help="Do not rebuild/upload the tools Lambda zip")
    parser.add_argument("--emit-tool-schema", action="store_true",
                        help="Print the generated gateway tool schema JSON and exit (no AWS calls)")
    parser.add_argument("--update-prompt", action="store_true",
                        help="Re-render the system prompt for today and publish a new harness version")
    parser.add_argument("--endpoint", metavar="NAME", help="Create/update a named harness endpoint (with --version)")
    parser.add_argument("--version", metavar="N", help="Harness version the --endpoint should target")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.emit_tool_schema:
        print(json.dumps(gateway_tool_schema(), indent=2))
        return

    if bool(args.endpoint) != bool(args.version):
        parser.error("--endpoint and --version must be given together")

    sts = boto3.client("sts", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)
    ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    if args.teardown:
        teardown(ctl, iam, lam, ssm)
        return

    if args.update_prompt:
        version = update_prompt(ctl, ssm, args.dry_run)
        print(f"harness version: {version}")
        return

    if args.endpoint:
        ep = ensure_endpoint(ctl, args.endpoint, args.version, args.dry_run)
        print(f"endpoint {ep.get('endpointName')}: status={ep.get('status')} "
              f"target=v{ep.get('targetVersion') or ep.get('liveVersion', '?')} live=v{ep.get('liveVersion', '?')}")
        return

    if args.status or not args.enable:
        if not args.enable and not args.status:
            print("The harness twin is OPT-IN. Pass --enable to provision it.")
            print("Showing current status:\n")
        show_status(ctl, iam, lam, ssm)
        return

    acct = account_id(sts)
    gw_arn = gateway_arn(acct)
    log.info("Provisioning Token Cop harness twin (account=%s region=%s)%s",
             acct, REGION, " [DRY RUN]" if args.dry_run else "")

    tools_role_arn = ensure_role(
        iam, TOOLS_ROLE, lambda_trust_policy(), tools_lambda_policy(acct),
        ["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"], args.dry_run,
    )
    fn_arn = ensure_tools_lambda(lam, tools_role_arn, args.dry_run, args.skip_lambda)
    ensure_gateway_target(ctl, fn_arn, args.dry_run)
    ensure_gateway_invoke_permissions(lam, iam, fn_arn, gw_arn, args.dry_run)
    provider_arn = ensure_oauth_provider(ctl, ssm, acct, args.dry_run)
    harness_role_arn = ensure_role(
        iam, HARNESS_ROLE, harness_trust_policy(acct),
        harness_role_policy(acct, gw_arn, provider_arn), [], args.dry_run,
    )
    harness = ensure_harness(ctl, harness_role_arn, gw_arn, provider_arn, args.dry_run)

    write_ssm(ssm, SSM_HARNESS_ARN, harness["arn"], args.dry_run)
    write_ssm(ssm, SSM_HARNESS_ID, harness["harnessId"], args.dry_run)

    log.info("Harness twin ready: %s (v%s)", harness["arn"], harness.get("harnessVersion", "?"))
    log.info("Try: TOKEN_COP_BACKEND=harness python mcp_server.py   or   python -m agent.harness_client 'what did we spend?'")


if __name__ == "__main__":
    main()
