"""AgentCore Memory helpers for storing and retrieving usage snapshots."""
import json
import os
from datetime import datetime, timezone

import boto3

MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "TokenCopMemory-oGHHvc2vSN")
ACTOR_ID = "token-cop-user"
REGION = os.environ.get("AWS_REGION", "us-east-1")


def _get_clients():
    data_client = boto3.client("bedrock-agentcore", region_name=REGION)
    return data_client


def store_usage_snapshot(usage_json: str, session_id: str = "") -> dict:
    """Store a usage snapshot as an event in AgentCore Memory.

    Args:
        usage_json: JSON string of usage data (from aggregate or provider tools).
        session_id: Optional session ID. Defaults to date-based ID.

    Returns:
        dict with event ID or error.
    """
    client = _get_clients()
    now = datetime.now(timezone.utc)

    if not session_id:
        session_id = f"snapshot-{now.strftime('%Y%m%d-%H%M%S')}"

    try:
        # Store as a conversational event so memory strategies can extract insights
        resp = client.create_event(
            memoryId=MEMORY_ID,
            actorId=ACTOR_ID,
            sessionId=session_id,
            eventTimestamp=now,
            payload=[
                {
                    "conversational": {
                        "content": {"text": f"Usage snapshot taken at {now.isoformat()}:\n{usage_json}"},
                        "role": "TOOL",
                    }
                }
            ],
            metadata={
                "snapshot_type": {"stringValue": "usage"},
                "snapshot_date": {"stringValue": now.strftime("%Y-%m-%d")},
            },
        )
        return {"status": "stored", "event_id": resp.get("eventId", "unknown")}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def retrieve_usage_history(query: str, max_results: int = 10) -> list[dict]:
    """Search historical usage snapshots using semantic search.

    Args:
        query: Natural language search query (e.g., "bedrock costs last month").
        max_results: Maximum number of records to return.

    Returns:
        List of memory records matching the query.
    """
    client = _get_clients()

    try:
        resp = client.retrieve_memory_records(
            memoryId=MEMORY_ID,
            namespace=f"/usage/{ACTOR_ID}/",
            searchCriteria={
                "searchQuery": query,
                "topK": max_results,
            },
            maxResults=max_results,
        )
        records = []
        for record in resp.get("memoryRecords", []):
            records.append({
                "id": record.get("memoryRecordId", ""),
                "content": record.get("content", {}).get("text", ""),
                "score": record.get("score", 0),
                "namespace": record.get("namespace", ""),
                "created": record.get("createdAt", ""),
            })
        return records
    except Exception as e:
        return [{"error": str(e)}]


def list_recent_events(max_results: int = 10) -> list[dict]:
    """List recent events (short-term memory) for the current actor.

    Returns:
        List of recent events.
    """
    client = _get_clients()

    try:
        resp = client.list_events(
            memoryId=MEMORY_ID,
            actorId=ACTOR_ID,
            maxResults=max_results,
        )
        events = []
        for event in resp.get("events", []):
            events.append({
                "id": event.get("eventId", ""),
                "session": event.get("sessionId", ""),
                "timestamp": str(event.get("eventTimestamp", "")),
            })
        return events
    except Exception as e:
        return [{"error": str(e)}]


def check_memory_status() -> dict:
    """Check if the memory resource is active and ready."""
    try:
        ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
        resp = ctrl.get_memory(memoryId=MEMORY_ID)
        memory = resp["memory"]
        strategies = []
        for s in memory.get("memoryStrategies", []):
            for key, val in s.items():
                strategies.append({"type": key, "name": val.get("name", ""), "id": val.get("id", "")})
        return {
            "status": memory["status"],
            "name": memory["name"],
            "strategies": strategies,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
