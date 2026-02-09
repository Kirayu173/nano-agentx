"""Shared web search models."""

from dataclasses import dataclass


@dataclass(slots=True)
class SearchHit:
    """Normalized search result item."""

    title: str
    url: str
    snippet: str = ""
