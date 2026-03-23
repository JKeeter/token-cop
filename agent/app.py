"""AgentCore entrypoint for Token Cop.

This file is the deployment target for Bedrock AgentCore Runtime.
It wraps the Strands agent with the BedrockAgentCoreApp decorator.
"""
from bedrock_agentcore import BedrockAgentCoreApp
from agent.config import load_all_secrets
from agent.guardrails import scrub_response
from agent.agent import create_agent

# Load secrets from SSM Parameter Store into env on cold start
load_all_secrets()

app = BedrockAgentCoreApp()


def _extract_response(agent, response):
    """Extract the best response text from the agent conversation.

    str(response) only returns text from the last message. If that's short
    or empty, walk backwards through all messages to find the longest text
    block — checking assistant messages first, then tool results as fallback.
    """
    text = str(response).strip()
    if len(text) > 100:
        return text

    # Check assistant messages for longer text
    for msg in reversed(agent.messages):
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []):
            if isinstance(block, dict) and "text" in block:
                candidate = block["text"].strip()
                if len(candidate) > len(text):
                    text = candidate

    if len(text) > 100:
        return text

    # Fallback: check tool results for raw data
    for msg in reversed(agent.messages):
        for block in msg.get("content", []):
            if isinstance(block, dict) and "content" in block:
                # Tool result blocks have a "content" field
                tool_text = ""
                for sub in block.get("content", []):
                    if isinstance(sub, dict) and "text" in sub:
                        tool_text += sub["text"]
                if len(tool_text) > len(text):
                    text = tool_text

    return text


@app.entrypoint
def token_cop(request):
    agent = create_agent()
    prompt = request.get("prompt", "")
    response = agent(prompt)
    return scrub_response(_extract_response(agent, response))


if __name__ == "__main__":
    app.run()
