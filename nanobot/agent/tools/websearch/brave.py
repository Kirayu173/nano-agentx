"""Brave Search API adapter."""

import httpx

from nanobot.agent.tools.websearch.models import SearchHit


async def search_brave(
    *,
    query: str,
    count: int,
    api_key: str,
    base_url: str,
) -> list[SearchHit]:
    """Search with Brave API and normalize results."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            base_url,
            params={"q": query, "count": count},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            timeout=10.0,
        )
        response.raise_for_status()

    results = response.json().get("web", {}).get("results", [])
    return [
        SearchHit(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", ""),
        )
        for item in results[:count]
    ]
