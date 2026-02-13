"""Cron job execution dispatch helpers."""

from typing import Any


async def dispatch_cron_job(job, agent, bus) -> str | None:
    """Execute one cron job payload."""
    from nanobot.bus.events import OutboundMessage

    async def _deliver(content: str) -> None:
        if not (job.payload.deliver and job.payload.to):
            return
        await bus.publish_outbound(
            OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=content,
            )
        )

    if job.payload.kind == "system_event":
        message = job.payload.message or ""
        await _deliver(message)
        return message

    if job.payload.kind == "tool_call":
        tool_name = (job.payload.tool_name or "").strip()
        tool_args: dict[str, Any] = job.payload.tool_args or {}
        if not tool_name:
            result = "Error: tool_name is required for tool_call payload"
        else:
            result = await agent.tools.execute(tool_name, tool_args)
        await _deliver(result or "")
        return result

    response = await agent.process_direct(
        job.payload.message,
        session_key=f"cron:{job.id}",
        channel=job.payload.channel or "cli",
        chat_id=job.payload.to or "direct",
    )
    await _deliver(response or "")
    return response

