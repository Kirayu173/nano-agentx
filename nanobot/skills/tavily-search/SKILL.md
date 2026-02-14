---
name: tavily-search
description: AI-optimized web search via Tavily API with concise source-grounded results.
homepage: https://tavily.com
metadata: {"nanobot":{"emoji":"🔎","requires":{"bins":["node"],"env":["TAVILY_API_KEY"]},"primaryEnv":"TAVILY_API_KEY"}}
---

# Tavily Search

AI-optimized web search using Tavily API. Designed for AI agents and returns clean, relevant content.

## Search

```bash
node {baseDir}/scripts/search.mjs "query"
node {baseDir}/scripts/search.mjs "query" -n 10
node {baseDir}/scripts/search.mjs "query" --deep
node {baseDir}/scripts/search.mjs "query" --topic news
```

## Options

- `-n <count>`: Number of results (default: 5, max: 20)
- `--deep`: Use advanced search for deeper research (slower, more comprehensive)
- `--topic <topic>`: Search topic - `general` (default) or `news`
- `--days <n>`: For news topic, limit to last `n` days

## Extract Content From URL

```bash
node {baseDir}/scripts/extract.mjs "https://example.com/article"
```

Notes:
- Requires `TAVILY_API_KEY` from https://tavily.com
- Use `--deep` for complex research questions
- Use `--topic news` for current events
