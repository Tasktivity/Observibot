"""Infrastructure query executor — retrieves service/deploy data from stored state."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from observibot.core.models import SystemModel
from observibot.core.store import Store


async def execute_infra_query(
    action: str,
    params: dict,
    store: Store,
    system_model: SystemModel | None,
) -> list[dict]:
    """Execute an infrastructure query against stored data."""
    if action == "service_status":
        if system_model is None:
            return []
        return [
            {
                "name": svc.name,
                "type": svc.type,
                "status": svc.status or "unknown",
                "environment": svc.environment or "",
                "last_deploy_at": (
                    svc.last_deploy_at.isoformat()
                    if svc.last_deploy_at else None
                ),
            }
            for svc in system_model.services
        ]

    if action == "deployment_history":
        since_hours = params.get("since_hours", 48)
        since = datetime.now(UTC) - timedelta(hours=since_hours)
        events = await store.get_recent_change_events(since=since)
        return [
            {
                "event_type": e.event_type,
                "connector": e.connector_name,
                "summary": e.summary,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in events
        ]

    if action == "service_details":
        service_name = params.get("service_name", "")
        if system_model is None:
            return []
        results = []
        for svc in system_model.services:
            if service_name and service_name.lower() not in svc.name.lower():
                continue
            results.append(svc.to_dict())
        return results

    return []
