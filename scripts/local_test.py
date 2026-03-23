"""Run Token Cop locally without AgentCore deployment.

Usage:
    source .venv/bin/activate
    python -m scripts.local_test
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from agent.agent import create_agent
from agent.guardrails import scrub_response


def main():
    agent = create_agent()
    print("Token Cop - Local Test Mode")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if prompt.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        if not prompt:
            continue

        response = agent(prompt)
        clean = scrub_response(str(response))
        print(f"\nToken Cop: {clean}\n")


if __name__ == "__main__":
    main()
