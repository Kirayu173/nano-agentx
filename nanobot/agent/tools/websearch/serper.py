"""Serper Search API adapter."""

import httpx

from nanobot.agent.tools.websearch.models import SearchHit


async def search_serper(
    *,
    query: str,
    count: int,
    api_key: str,
    base_url: str,
) -> list[SearchHit]:
    """Search with Serper API and normalize results."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            base_url,
            json={"q": query, "num": count},
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": api_key,
            },
            timeout=10.0,
        )
        response.raise_for_status()

    results = response.json().get("organic", [])
    return [
        SearchHit(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in results[:count]
    ]
