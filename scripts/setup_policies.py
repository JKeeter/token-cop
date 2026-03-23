"""Set up AgentCore Policy demos for Token Cop.

Creates a Cedar policy engine, sample policies, and associates them
with the MCP Gateway. Demonstrates both hand-written and AI-generated
Cedar policies.

Usage:
    source .venv/bin/activate
    python -m scripts.setup_policies              # Full setup (LOG_ONLY)
    python -m scripts.setup_policies --status      # Show current state
    python -m scripts.setup_policies --generate    # AI generation demo
    python -m scripts.setup_policies --demo        # Interactive walkthrough
    python -m scripts.setup_policies --teardown    # Clean up everything
    python -m scripts.setup_policies --mode ENFORCE # Use ENFORCE mode
"""
import argparse
import json
import logging
import sys
import urllib.parse
import urllib.request

import boto3
from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient
from bedrock_agentcore_starter_toolkit.operations.policy import PolicyClient

REGION = "us-east-1"
GATEWAY_ID = "token-cop-gateway-7q9nodpeem"
GATEWAY_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:<REPLACE-WITH-YOUR-AWS-ACCOUNT>:"
    "gateway/token-cop-gateway-7q9nodpeem"
)
POLICY_ENGINE_NAME = "token_cop_policy_engine"
COGNITO_CLIENT_ID = "29ksjo8k257ev205bvh4fjn4gg"

# Cedar policy definitions for the demos
POLICIES = [
    {
        "name": "token_cop_permit_all",
        "description": (
            "Baseline: allow all authenticated callers to invoke any tool. "
            "Cedar is default-deny, so at least one permit is required."
        ),
        "statement": 'permit(principal, action, resource is AgentCore::Gateway);',
    },
    {
        "name": "token_cop_cognito_client_only",
        "description": (
            "Restrict access to the known Cognito client ID. Demonstrates "
            "conditional authorization based on JWT claims."
        ),
        "statement": (
            'permit(principal, action, resource is AgentCore::Gateway)\n'
            f'when {{ context has clientId && context.clientId == "{COGNITO_CLIENT_ID}" }};'
        ),
    },
    {
        "name": "token_cop_forbid_demo",
        "description": (
            "Block a hypothetical dangerous tool. Demonstrates the forbid "
            "keyword, which always overrides permit in Cedar."
        ),
        "statement": (
            'forbid(principal, action, resource is AgentCore::Gateway)\n'
            'when { context has toolName && context.toolName == "dangerous_tool" };'
        ),
    },
]

log = logging.getLogger("setup_policies")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Quiet the SDK's own loggers so our output is readable
    logging.getLogger("bedrock_agentcore.policy").setLevel(logging.WARNING)
    logging.getLogger("bedrock_agentcore.gateway").setLevel(logging.WARNING)


def find_engine(policy_client: PolicyClient) -> dict | None:
    """Find our policy engine by name, or return None."""
    resp = policy_client.list_policy_engines(max_results=100)
    for engine in resp.get("policyEngines", []):
        if engine["name"] == POLICY_ENGINE_NAME:
            return engine
    return None


def create_engine(policy_client: PolicyClient) -> dict:
    log.info("Creating policy engine: %s", POLICY_ENGINE_NAME)
    engine = policy_client.create_or_get_policy_engine(
        name=POLICY_ENGINE_NAME,
        description="Token Cop authorization policy demos",
    )
    log.info("Policy engine ready: %s (status: %s)", engine["policyEngineId"], engine.get("status"))
    return engine


def create_policies(policy_client: PolicyClient, engine_id: str) -> list[dict]:
    results = []
    for p in POLICIES:
        log.info("Creating policy: %s", p["name"])
        policy = policy_client.create_or_get_policy(
            policy_engine_id=engine_id,
            name=p["name"],
            definition={"cedar": {"statement": p["statement"]}},
            description=p["description"],
            validation_mode="IGNORE_ALL_FINDINGS",
        )
        log.info("  -> %s (status: %s)", policy["policyId"], policy.get("status"))
        results.append(policy)
    return results


def associate_gateway(gateway_client: GatewayClient, engine_arn: str, mode: str):
    log.info("Associating policy engine with gateway in %s mode", mode)
    gateway_client.update_gateway_policy_engine(
        gateway_identifier=GATEWAY_ID,
        policy_engine_arn=engine_arn,
        mode=mode,
    )
    log.info("Gateway updated")
    if mode == "LOG_ONLY":
        log.info("  Policies are evaluated but NOT enforced (audit mode)")
    else:
        log.info("  Policies are ENFORCED - unauthorized requests will be denied")


def show_status(policy_client: PolicyClient, gateway_client: GatewayClient):
    engine = find_engine(policy_client)
    if not engine:
        print("No policy engine found. Run without flags to set up.")
        return

    engine_id = engine["policyEngineId"]
    print(f"\nPolicy Engine: {engine['name']}")
    print(f"  ID:     {engine_id}")
    print(f"  ARN:    {engine.get('policyEngineArn', 'N/A')}")
    print(f"  Status: {engine.get('status', 'N/A')}")

    resp = policy_client.list_policies(policy_engine_id=engine_id, max_results=100)
    policies = resp.get("policies", [])
    print(f"\nPolicies ({len(policies)}):")
    for p in policies:
        print(f"  - {p['name']} [{p.get('status', '?')}] ({p['policyId']})")

    # Show gateway config
    try:
        gw = gateway_client.client.get_gateway(gatewayIdentifier=GATEWAY_ID)
        pe_config = gw.get("policyEngineConfiguration")
        if pe_config:
            print(f"\nGateway Policy Engine:")
            print(f"  ARN:  {pe_config.get('arn', 'N/A')}")
            print(f"  Mode: {pe_config.get('mode', 'N/A')}")
        else:
            print("\nGateway: no policy engine associated")
    except Exception as e:
        print(f"\nCould not fetch gateway config: {e}")

    print()


def demo_ai_generation(policy_client: PolicyClient, engine_id: str):
    prompt_text = (
        "Allow the Token Cop Cognito client to invoke the token_cop tool "
        "on the gateway, but deny all other clients."
    )
    log.info("Starting AI policy generation...")
    log.info("  Prompt: %s", prompt_text)

    result = policy_client.generate_policy(
        policy_engine_id=engine_id,
        name="token_cop_ai_generated",
        resource={"arn": GATEWAY_ARN},
        content={"rawText": prompt_text},
        fetch_assets=True,
    )

    print(f"\nGeneration ID: {result['policyGenerationId']}")
    print(f"Status: {result.get('status')}")

    assets = result.get("generatedPolicies", [])
    if assets:
        print(f"\nGenerated {len(assets)} Cedar policy(ies):\n")
        for i, asset in enumerate(assets, 1):
            print(f"--- Asset {i} (ID: {asset.get('policyGenerationAssetId', 'N/A')}) ---")
            definition = asset.get("definition", {})
            cedar = definition.get("cedar", {})
            statement = cedar.get("statement", json.dumps(definition, indent=2))
            print(statement)
            print()
    else:
        print("\nNo generated assets returned.")

    return result


GATEWAY_URL = (
    "https://token-cop-gateway-7q9nodpeem.gateway.bedrock-agentcore."
    "us-east-1.amazonaws.com/mcp"
)
TOKEN_ENDPOINT = (
    "https://agentcore-d4673f36.auth.us-east-1.amazoncognito.com/oauth2/token"
)

_cached_token: str | None = None


def _get_gateway_token() -> str:
    """Get a Cognito JWT for gateway calls (cached)."""
    global _cached_token
    if _cached_token:
        return _cached_token

    ssm = boto3.client("ssm", region_name=REGION)
    client_id = ssm.get_parameter(
        Name="/token-cop/gateway-client-id", WithDecryption=True
    )["Parameter"]["Value"]
    client_secret = ssm.get_parameter(
        Name="/token-cop/gateway-client-secret", WithDecryption=True
    )["Parameter"]["Value"]

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "token-cop-gateway/invoke",
    }).encode()

    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        _cached_token = json.loads(resp.read())["access_token"]
    return _cached_token


def call_gateway(method: str = "tools/list", params: dict | None = None) -> str:
    """Make an MCP call to the gateway. Returns result or error string."""
    token = _get_gateway_token()
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }).encode()

    req = urllib.request.Request(
        GATEWAY_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        if "result" in result:
            content = result["result"]
            if isinstance(content, dict) and "tools" in content:
                tools = content["tools"]
                if not tools:
                    return "DENIED - gateway returned 0 tools (forbid policy in effect)"
                names = [t["name"] for t in tools]
                return f"ALLOWED - {len(tools)} tools: {', '.join(names)}"
            if isinstance(content, dict) and "content" in content:
                parts = content["content"]
                text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                return f"ALLOWED - {text[:200]}"
            return f"ALLOWED - {json.dumps(content)[:200]}"
        if "error" in result:
            return f"DENIED - {json.dumps(result['error'])}"
        return json.dumps(result)[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"DENIED (HTTP {e.code}) - {body[:300]}"
    except Exception as e:
        return f"ERROR - {e}"


def _step(n: int, title: str):
    """Print a demo step header and wait for Enter."""
    print(f"\n{'='*60}")
    print(f"  Step {n}: {title}")
    print(f"{'='*60}")
    input("  Press Enter to continue...")
    print()


FORBID_ALL_STATEMENT = "forbid(principal, action, resource is AgentCore::Gateway);"


def demo(policy_client: PolicyClient, gateway_client: GatewayClient):
    """Interactive demo: show policies permitting and denying requests live."""
    # Suppress all SDK logging for clean demo output
    for name in ("bedrock_agentcore.policy", "bedrock_agentcore.gateway",
                 "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)
    log.setLevel(logging.ERROR)

    engine = find_engine(policy_client)
    if not engine:
        print("No policy engine found. Run setup first:")
        print("  python -m scripts.setup_policies")
        return

    engine_id = engine["policyEngineId"]
    engine_arn = engine["policyEngineArn"]

    print("\n" + "="*60)
    print("  AgentCore Policies - Live Demo")
    print("="*60)
    print()
    print("This walkthrough demonstrates Cedar policies controlling")
    print("access to a live MCP Gateway in real time.")
    print()
    print(f"Gateway: {GATEWAY_ID}")
    print(f"Engine:  {engine_id}")

    # Step 1: Show current state
    _step(1, "Current policy state")
    show_status(policy_client, gateway_client)

    # Step 2: Switch to ENFORCE
    _step(2, "Switch gateway to ENFORCE mode")
    print("Policies will now block unauthorized requests.")
    gateway_client.update_gateway_policy_engine(
        gateway_identifier=GATEWAY_ID,
        policy_engine_arn=engine_arn,
        mode="ENFORCE",
    )
    print("  Gateway mode: ENFORCE")

    # Step 3: Test - should work (permit-all allows it)
    _step(3, "Call the gateway (expect: ALLOWED)")
    print("The permit-all policy allows any authenticated caller.\n")
    print(f"  Cedar: {POLICIES[0]['statement']}\n")
    result = call_gateway()
    print(f"  Result: {result}")

    # Step 4: Add blanket forbid
    _step(4, "Add a blanket FORBID policy")
    print("In Cedar, forbid always overrides permit.")
    print(f"\n  Cedar: {FORBID_ALL_STATEMENT}\n")
    forbid_policy = policy_client.create_or_get_policy(
        policy_engine_id=engine_id,
        name="token_cop_forbid_all",
        definition={"cedar": {"statement": FORBID_ALL_STATEMENT}},
        description="Demo: blanket deny to show forbid > permit",
        validation_mode="IGNORE_ALL_FINDINGS",
    )
    print(f"  Policy created: {forbid_policy['policyId']} (status: {forbid_policy.get('status')})")

    # Step 5: Test - should be denied
    _step(5, "Call the gateway again (expect: DENIED)")
    print("The forbid policy now overrides permit-all.\n")
    result = call_gateway()
    print(f"  Result: {result}")

    # Step 6: Remove the forbid
    _step(6, "Remove the forbid policy")
    print("Deleting the forbid restores access.\n")
    forbid_id = forbid_policy["policyId"]
    policy_client.delete_policy(engine_id, forbid_id)
    policy_client._wait_for_policy_deleted(engine_id, forbid_id)
    print(f"  Policy {forbid_id} deleted.")

    # Step 7: Test - should work again
    _step(7, "Call the gateway once more (expect: ALLOWED)")
    print("With the forbid removed, permit-all is back in effect.\n")
    result = call_gateway()
    print(f"  Result: {result}")

    # Step 8: Reset to LOG_ONLY
    _step(8, "Reset gateway to LOG_ONLY mode")
    print("Returning to audit mode for safety.")
    gateway_client.update_gateway_policy_engine(
        gateway_identifier=GATEWAY_ID,
        policy_engine_arn=engine_arn,
        mode="LOG_ONLY",
    )
    print("  Gateway mode: LOG_ONLY")

    print()
    print("="*60)
    print("  Demo complete!")
    print("="*60)
    print()
    print("Key takeaways:")
    print("  - Cedar is default-deny: without a permit, nothing works")
    print("  - forbid always overrides permit (one line locks everything)")
    print("  - LOG_ONLY mode lets you audit before enforcing")
    print("  - Policies are fast: create/delete takes ~5 seconds")
    print()
    print("Try AI generation next:")
    print("  python -m scripts.setup_policies --generate")
    print()


def teardown(policy_client: PolicyClient):
    engine = find_engine(policy_client)
    if not engine:
        log.info("No policy engine found - nothing to tear down")
        return

    engine_id = engine["policyEngineId"]
    log.info("Tearing down policy engine: %s (%s)", engine["name"], engine_id)
    policy_client.cleanup_policy_engine(engine_id)
    log.info("Teardown complete")
    log.info("Note: gateway may still reference the deleted engine until next update")


def main():
    parser = argparse.ArgumentParser(description="AgentCore Policy demos for Token Cop")
    parser.add_argument("--status", action="store_true", help="Show current policy state")
    parser.add_argument("--demo", action="store_true", help="Interactive walkthrough: enforce, deny, allow")
    parser.add_argument("--generate", action="store_true", help="Run AI policy generation demo")
    parser.add_argument("--teardown", action="store_true", help="Delete all policy resources")
    parser.add_argument("--mode", choices=["LOG_ONLY", "ENFORCE"], default="LOG_ONLY",
                        help="Gateway enforcement mode (default: LOG_ONLY)")
    parser.add_argument("--skip-gateway", action="store_true",
                        help="Skip gateway association step")
    args = parser.parse_args()

    setup_logging()
    policy_client = PolicyClient(region_name=REGION)
    gateway_client = GatewayClient(region_name=REGION)

    if args.status:
        show_status(policy_client, gateway_client)
        return

    if args.demo:
        demo(policy_client, gateway_client)
        return

    if args.teardown:
        teardown(policy_client)
        return

    if args.generate:
        engine = find_engine(policy_client)
        if not engine:
            log.info("No engine found - creating one first")
            engine = create_engine(policy_client)
        demo_ai_generation(policy_client, engine["policyEngineId"])
        return

    # Default: full setup
    engine = create_engine(policy_client)
    engine_id = engine["policyEngineId"]
    engine_arn = engine["policyEngineArn"]

    create_policies(policy_client, engine_id)

    if not args.skip_gateway:
        associate_gateway(gateway_client, engine_arn, args.mode)

    log.info("Setup complete!")
    log.info("Next steps:")
    log.info("  python -m scripts.setup_policies --status     # inspect")
    log.info("  python -m scripts.setup_policies --generate   # AI demo")
    log.info("  python -m scripts.setup_policies --teardown   # clean up")


if __name__ == "__main__":
    main()
