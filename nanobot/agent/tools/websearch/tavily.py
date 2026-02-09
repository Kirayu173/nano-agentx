"""Tavily Search API adapter."""

import httpx

from nanobot.agent.tools.websearch.models import SearchHit


async def search_tavily(
    *,
    query: str,
    count: int,
    api_key: str,
    base_url: str,
) -> list[SearchHit]:
    """Search with Tavily API and normalize results."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            base_url,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": count,
            },
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        response.raise_for_status()

    results = response.json().get("results", [])
    hits: list[SearchHit] = []
    for item in results[:count]:
        hits.append(
            SearchHit(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", "") or item.get("snippet", ""),
            )
        )
    return hits
