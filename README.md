# 🌐 AI & N8N Global Trend Monitor

Real-time OSINT news intelligence system that monitors Google Trends across 9 countries for AI, N8N, and related technology trends.

## Architecture

```
Google Trends RSS (9 countries)
        │
        ▼
   ┌─────────────┐
   │  RSS Fetch   │  Parallel fetch per geo code
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  XML Parse   │  Extract trend titles + news items
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  Dedup Gate  │  Hash-based deduplication (SHA-256)
   └──────┬──────┘
          ▼
   ┌─────────────────────────────────┐
   │  3-Tier Semantic Filter Engine  │
   │  ├─ T1: Exact keyword match     │
   │  ├─ T2: Regex compound patterns  │
   │  └─ T3: Contextual disambig.    │
   └──────┬──────────────────────────┘
          ▼
   ┌─────────────┐
   │  Translate   │  Headline normalization to English
   └──────┬──────┘
          ▼
   ┌──────┴──────┐
   │ JSON + MD   │  Machine-readable + human-readable output
   └─────────────┘
```

## Countries Monitored

| Geo | Country              | Language |
|-----|----------------------|----------|
| US  | United States        | en       |
| GB  | United Kingdom       | en       |
| PT  | Portugal             | pt       |
| DE  | Germany              | de       |
| CN  | China                | zh       |
| JP  | Japan                | ja       |
| IL  | Israel               | he       |
| AE  | United Arab Emirates | ar       |
| CA  | Canada               | en       |

## Semantic Filter — 3-Tier Engine

### Tier 1 — Exact Keyword Match (High Confidence)
Direct match against 100+ curated keywords covering: N8N, AI, ML, DL, specific models (GPT, Claude, Gemini, LLaMA, Mistral, etc.), automation platforms, AI safety/governance terms, and industry applications.

### Tier 2 — Compound Pattern Match (High Confidence)
Regex-based matching for complex phrases: "AI-powered", "GPT-4", "fine-tuning", "prompt engineering", "no-code AI", etc.

### Tier 3 — Contextual Disambiguation (Medium Confidence)
For ambiguous matches (e.g., bare "AI" token), cross-references associated news headlines for reinforcing signals before accepting.

### False Positive Exclusion
Active rejection of known false positives (people named "Ai", sports references, place names, etc.).

## Output Formats

### `ai_trends.json` — Machine-Readable
```json
{
  "meta": {
    "system": "AI Trend Monitor v1.0",
    "generated_at": "2026-02-13T18:00:00+00:00",
    "countries_monitored": ["US", "GB", "PT", "DE", "CN", "JP", "IL", "AE", "CA"],
    "total_trends_matched": 5,
    "total_news_items": 12
  },
  "results": [
    {
      "id": "a1b2c3d4e5f6g7h8",
      "trend_title": "ChatGPT",
      "approx_traffic": "200K+",
      "country": "United States",
      "geo": "US",
      "match_confidence": "high",
      "matched_by": "chatgpt",
      "match_tier": 1,
      "news_items": [
        {
          "headline_original": "OpenAI launches new ChatGPT feature",
          "headline_en": "OpenAI launches new ChatGPT feature",
          "source_url": "https://...",
          "source_name": "TechCrunch",
          "news_relevance": "high"
        }
      ]
    }
  ]
}
```

### `ai_trends.md` — Human-Readable Dashboard
Auto-generated Markdown grouped by country with confidence badges (🟢 high / 🟡 medium).

## Setup

1. Fork this repository
2. Add a `PAT_TOKEN` secret in Settings → Secrets → Actions
3. The workflow runs automatically every 2 hours
4. Manual trigger available via Actions → "Run workflow"

## Production Enhancements

For production deployment, consider:

- **Translation API**: Replace the heuristic translator with Google Cloud Translation, DeepL, or AWS Translate for non-English headlines
- **N8N Integration**: Pipe `ai_trends.json` into an N8N webhook for downstream processing (Slack alerts, database storage, dashboard updates)
- **Extended Sources**: Add Twitter/X Trends API, Reddit trending, Hacker News, or custom RSS feeds
- **Vector Embeddings**: Use sentence-transformers for semantic similarity matching instead of keyword-based filtering
- **Rate Limiting**: Add exponential backoff and request throttling for high-frequency scanning

## License

MIT
