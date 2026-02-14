"""
AI & N8N Trend Monitor — Multi-Country OSINT News Intelligence System
=====================================================================
Monitors Google Trends RSS feeds across 9 target countries, filters for
AI/N8N-related trending topics, extracts associated news items, translates
headlines to English, and outputs structured machine-readable JSON + Markdown.

Adapted from: rss_checker.py (Peru Google Trends RSS monitor)
"""

import xml.etree.ElementTree as ET
import requests
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

# ─────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────

# Target countries: geo code → metadata
COUNTRIES = {
    "US": {"name": "United States",       "lang": "en", "tz": "America/New_York"},
    "GB": {"name": "United Kingdom",      "lang": "en", "tz": "Europe/London"},
    "PT": {"name": "Portugal",            "lang": "pt", "tz": "Europe/Lisbon"},
    "DE": {"name": "Germany",             "lang": "de", "tz": "Europe/Berlin"},
    "CN": {"name": "China",               "lang": "zh", "tz": "Asia/Shanghai"},
    "JP": {"name": "Japan",               "lang": "ja", "tz": "Asia/Tokyo"},
    "IL": {"name": "Israel",              "lang": "he", "tz": "Asia/Jerusalem"},
    "AE": {"name": "United Arab Emirates", "lang": "ar", "tz": "Asia/Dubai"},
    "CA": {"name": "Canada",              "lang": "en", "tz": "America/Toronto"},
}

RSS_URL_TEMPLATE = "https://trends.google.com/trending/rss?geo={geo}"

# XML namespace for Google Trends RSS
NS = {"ht": "https://trends.google.com/trending/rss"}

# ─────────────────────────────────────────────────────────────────────
#  SEMANTIC KEYWORD ENGINE
# ─────────────────────────────────────────────────────────────────────
# Multi-tier keyword matching for precision filtering.
# Tier 1: Exact-match high-confidence keywords (case-insensitive)
# Tier 2: Pattern-based matching (regex) for compound terms
# Tier 3: Contextual disambiguation — reject false positives

# Tier 1 — Primary keywords (exact token match, case-insensitive)
PRIMARY_KEYWORDS = {
    # Core scope
    "n8n", "artificial intelligence", "machine learning", "deep learning",
    "generative ai", "gen ai", "genai",
    # Model types
    "llm", "large language model", "language model", "foundation model",
    "transformer model", "diffusion model", "multimodal model",
    # Specific technologies & products
    "chatgpt", "openai", "claude", "anthropic", "gemini ai", "google ai",
    "copilot ai", "github copilot", "midjourney", "stable diffusion",
    "dall-e", "dalle", "sora ai", "mistral ai", "mistral", "llama model",
    "grok ai", "perplexity ai", "hugging face", "huggingface",
    "deepseek", "cohere ai",
    # Automation & agents
    "ai agent", "ai agents", "autonomous agent", "agentic ai",
    "automation workflow", "workflow automation", "intelligent automation",
    "robotic process automation", "rpa", "ai orchestration",
    "make.com", "zapier ai", "langchain", "langgraph", "autogen",
    "crewai", "crew ai",
    # ML/DL core concepts
    "neural network", "neural networks", "convolutional neural",
    "recurrent neural", "reinforcement learning", "supervised learning",
    "unsupervised learning", "transfer learning", "federated learning",
    "computer vision", "natural language processing", "nlp",
    "speech recognition", "text to speech", "text-to-speech",
    "image generation", "image recognition", "object detection",
    # AI safety & governance
    "ai regulation", "ai safety", "ai ethics", "ai governance",
    "ai alignment", "ai risk", "ai policy", "ai act", "ai bill",
    "responsible ai", "explainable ai", "xai",
    # Industry applications
    "ai in healthcare", "ai in finance", "ai in education",
    "ai in manufacturing", "ai chip", "ai chips", "ai hardware",
    "ai accelerator", "gpu cluster", "ai datacenter", "ai data center",
    "ai startup", "ai startups", "ai investment", "ai funding",
}

# Tier 2 — Regex patterns for compound/contextual matching
COMPOUND_PATTERNS = [
    # "AI" as a standalone word with AI-relevant context
    r"\bai\s+(?:model|system|tool|platform|assistant|chatbot|startup|company|research|lab|chip|regulation|safety|ethics|agent|powered|generated|driven|based|enabled|native)\b",
    r"\b(?:generative|conversational|predictive|autonomous|agentic|responsible|explainable)\s+ai\b",
    r"\bai[-\s](?:powered|generated|driven|based|enabled|native|first|ready)\b",
    # Specific patterns
    r"\bgpt[-\s]?\d",                         # GPT-4, GPT-5, etc.
    r"\bclaude\s+\d",                          # Claude 3, Claude 4, etc.
    r"\bgemini\s+(?:pro|ultra|nano|flash)\b",  # Gemini variants
    r"\bllama\s*\d",                           # LLaMA 2, LLaMA 3
    r"\bmistral\s+\d",                         # Mistral 7B etc.
    r"\b(?:text|image|video|music|code)\s+generation\b",
    r"\bprompt\s+(?:engineering|injection|tuning)\b",
    r"\b(?:fine[- ]?tun(?:e|ing)|rag|retrieval.augmented)\b",
    r"\b(?:artificial|machine|deep)\s+(?:intelligence|learning)\b",
    r"\bneural\s+(?:net(?:work)?|architecture|engine)\b",
    r"\brobot(?:ic)?s?\s+(?:ai|automation|process)\b",
    r"\bn8n\b",
    r"\bno[- ]?code\s+(?:ai|automation|workflow)\b",
    r"\blow[- ]?code\s+(?:ai|automation|workflow)\b",
]

# Compile all patterns for performance
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in COMPOUND_PATTERNS]

# Tier 3 — False positive exclusion rules
# If a trend title matches ONLY the bare token "AI" and contains any of these
# contextual signals, it's likely a false positive (e.g., sports team "AI",
# person name, place, etc.)
FALSE_POSITIVE_SIGNALS = {
    "allen iverson", "al ain", "ai weiwei", "ai miyazato",
    "ai thinker", "ai uehara", "love ai", "ai takahashi",
    "ai shinozaki", "ai otsuka", "ai kago",
}

# ─────────────────────────────────────────────────────────────────────
#  FUNCTIONS
# ─────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Normalize unicode, strip accents for matching, lowercase."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return ascii_text.lower().strip()


def is_ai_related(title: str, news_titles: list[str] = None) -> dict:
    """
    Multi-tier semantic filter. Returns a dict with:
      - match: bool
      - confidence: 'high' | 'medium' | 'low'
      - matched_by: str (which keyword/pattern triggered)
      - tier: int
    """
    title_lower = title.lower().strip()
    title_normalized = normalize_text(title)

    # Check false positives first
    for fp in FALSE_POSITIVE_SIGNALS:
        if fp in title_lower:
            return {"match": False, "confidence": None, "matched_by": f"fp_exclusion:{fp}", "tier": 0}

    # Tier 1: Primary keyword exact match
    for kw in PRIMARY_KEYWORDS:
        kw_lower = kw.lower()
        # Word boundary check
        pattern = r"\b" + re.escape(kw_lower) + r"\b"
        if re.search(pattern, title_lower):
            return {"match": True, "confidence": "high", "matched_by": kw, "tier": 1}

    # Tier 2: Compound regex patterns
    for i, compiled in enumerate(COMPILED_PATTERNS):
        if compiled.search(title_lower):
            return {"match": True, "confidence": "high", "matched_by": f"pattern:{COMPOUND_PATTERNS[i]}", "tier": 2}

    # Tier 2b: Check associated news headlines for contextual reinforcement
    if news_titles:
        combined = " ".join(news_titles).lower()
        ai_signal_count = 0
        for kw in PRIMARY_KEYWORDS:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", combined):
                ai_signal_count += 1
        for compiled in COMPILED_PATTERNS:
            if compiled.search(combined):
                ai_signal_count += 1
        if ai_signal_count >= 2:
            return {"match": True, "confidence": "medium", "matched_by": f"contextual_news_signals:{ai_signal_count}", "tier": 3}

    # Tier 3: Bare "AI" token check with contextual disambiguation
    if re.search(r"\bai\b", title_lower):
        # Only accept if news titles also contain strong AI signals
        if news_titles:
            combined = " ".join(news_titles).lower()
            strong_signals = [
                "artificial intelligence", "machine learning", "deep learning",
                "chatbot", "llm", "neural", "algorithm", "automation",
                "openai", "chatgpt", "generative", "model", "training",
                "dataset", "gpu", "chip", "regulation", "safety",
            ]
            signal_hits = sum(1 for s in strong_signals if s in combined)
            if signal_hits >= 2:
                return {"match": True, "confidence": "medium", "matched_by": "bare_ai_contextual", "tier": 3}
        return {"match": False, "confidence": None, "matched_by": "bare_ai_no_context", "tier": 0}

    return {"match": False, "confidence": None, "matched_by": None, "tier": 0}


def fetch_rss(geo: str) -> Optional[bytes]:
    """Fetch RSS content for a given country geo code."""
    url = RSS_URL_TEMPLATE.format(geo=geo)
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "AI-Trend-Monitor/1.0 (OSINT Research)"
        })
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch RSS for {geo}: {e}")
        return None


def parse_rss(xml_content: bytes, geo: str) -> list[dict]:
    """Parse Google Trends RSS into structured items."""
    root = ET.fromstring(xml_content)
    items = []

    for item_el in root.findall("./channel/item"):
        title = item_el.find("title").text or ""
        traffic_el = item_el.find("ht:approx_traffic", NS)
        approx_traffic = traffic_el.text if traffic_el is not None else "N/A"
        pub_date = item_el.find("pubDate").text or ""

        # Extract associated news items
        news_items = []
        for news_el in item_el.findall("ht:news_item", NS):
            news_title_el = news_el.find("ht:news_item_title", NS)
            news_url_el = news_el.find("ht:news_item_url", NS)
            news_source_el = news_el.find("ht:news_item_source", NS)
            news_picture_el = news_el.find("ht:news_item_picture", NS)

            news_items.append({
                "title": news_title_el.text if news_title_el is not None else "",
                "url": news_url_el.text if news_url_el is not None else "",
                "source": news_source_el.text if news_source_el is not None else "",
                "picture": news_picture_el.text if news_picture_el is not None else "",
            })

        item_hash = hashlib.sha256(
            (geo + title + pub_date).encode("utf-8")
        ).hexdigest()[:16]

        items.append({
            "id": item_hash,
            "trend_title": title,
            "approx_traffic": approx_traffic,
            "pub_date": pub_date,
            "geo": geo,
            "country": COUNTRIES[geo]["name"],
            "language": COUNTRIES[geo]["lang"],
            "news_items": news_items,
        })

    return items


def translate_headline_heuristic(title: str, lang: str) -> str:
    """
    Lightweight headline translation placeholder.
    In production, replace with Google Translate API, DeepL, or similar.
    For English sources, returns as-is.
    For non-English, marks as needing translation.
    """
    if lang == "en":
        return title
    # Placeholder: in production, call translation API here
    return f"[TRANSLATE:{lang.upper()}] {title}"


def filter_and_enrich(items: list[dict]) -> list[dict]:
    """Apply semantic AI filter, enrich with metadata, deduplicate."""
    results = []
    seen_urls = set()

    for item in items:
        news_titles = [n["title"] for n in item["news_items"]]
        match_result = is_ai_related(item["trend_title"], news_titles)

        if not match_result["match"]:
            continue

        # Extract and deduplicate relevant news pieces
        filtered_news = []
        for news in item["news_items"]:
            if news["url"] in seen_urls:
                continue
            seen_urls.add(news["url"])

            # Check individual news item relevance as well
            news_match = is_ai_related(news["title"])
            if news_match["match"] or match_result["confidence"] == "high":
                translated = translate_headline_heuristic(
                    news["title"], item["language"]
                )
                filtered_news.append({
                    "headline_original": news["title"],
                    "headline_en": translated,
                    "source_url": news["url"],
                    "source_name": news["source"],
                    "news_relevance": news_match["confidence"] or "inherited",
                })

        if not filtered_news:
            continue

        results.append({
            "id": item["id"],
            "trend_title": item["trend_title"],
            "approx_traffic": item["approx_traffic"],
            "pub_date": item["pub_date"],
            "country": item["country"],
            "geo": item["geo"],
            "match_confidence": match_result["confidence"],
            "matched_by": match_result["matched_by"],
            "match_tier": match_result["tier"],
            "news_items": filtered_news,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        })

    return results


def load_processed_hashes(filepath: str) -> set:
    """Load previously processed item hashes."""
    if not os.path.exists(filepath):
        return set()
    with open(filepath, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_processed_hashes(filepath: str, hashes: set):
    """Save processed hashes (keep last 5000 to prevent unbounded growth)."""
    sorted_hashes = sorted(hashes)
    if len(sorted_hashes) > 5000:
        sorted_hashes = sorted_hashes[-5000:]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted_hashes) + "\n")


def output_json(results: list[dict], filepath: str):
    """Write structured JSON output."""
    output = {
        "meta": {
            "system": "AI Trend Monitor v1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "countries_monitored": list(COUNTRIES.keys()),
            "total_trends_matched": len(results),
            "total_news_items": sum(len(r["news_items"]) for r in results),
        },
        "results": results,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def output_markdown(results: list[dict], filepath: str):
    """Write human-readable Markdown output."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# 🌐 AI & N8N Global Trend Monitor\n",
        f"**Last updated:** {now_utc}\n",
        f"**Countries:** {', '.join(c['name'] for c in COUNTRIES.values())}\n",
        f"**Trends matched:** {len(results)} | "
        f"**News items:** {sum(len(r['news_items']) for r in results)}\n",
        "---\n",
    ]

    # Group by country
    by_country = {}
    for r in results:
        by_country.setdefault(r["country"], []).append(r)

    for country_name in sorted(by_country.keys()):
        country_results = by_country[country_name]
        geo = country_results[0]["geo"]
        lines.append(f"\n## 🏳️ {country_name} ({geo})\n")

        for item in country_results:
            conf_badge = {"high": "🟢", "medium": "🟡", "low": "🟠"}.get(
                item["match_confidence"], "⚪"
            )
            lines.append(
                f"### {conf_badge} {item['trend_title']} "
                f"({item['approx_traffic']}, {item['pub_date'][:16]})\n"
            )
            lines.append(
                f"> Confidence: **{item['match_confidence']}** | "
                f"Tier: {item['match_tier']} | "
                f"Matched by: `{item['matched_by']}`\n"
            )
            for news in item["news_items"]:
                lines.append(
                    f"- **{news['headline_en']}**  \n"
                    f"  [Source]({news['source_url']})"
                    f"{' | ' + news['source_name'] if news['source_name'] else ''}\n"
                )
            lines.append("")

    if not results:
        lines.append(
            "\n> ℹ️ No AI/N8N-related trends detected in this cycle.\n"
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def clean_old_entries(filepath: str, max_entries: int = 200):
    """Trim old Markdown entries to prevent unbounded file growth."""
    if not os.path.exists(filepath):
        return
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    sections = content.split("### ")
    if len(sections) > max_entries:
        sections = sections[:max_entries]
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("### ".join(sections))


# ─────────────────────────────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("AI & N8N Global Trend Monitor — Starting scan")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Countries: {len(COUNTRIES)}")
    print("=" * 60)

    processed_file = "processed_hashes.txt"
    json_output = "ai_trends.json"
    md_output = "ai_trends.md"
    readme_output = "README.md"

    processed_hashes = load_processed_hashes(processed_file)
    all_items = []

    for geo, meta in COUNTRIES.items():
        print(f"\n🔍 Scanning {meta['name']} ({geo})...")
        xml_content = fetch_rss(geo)
        if xml_content is None:
            continue

        items = parse_rss(xml_content, geo)
        print(f"   Found {len(items)} trending topics")

        # Filter out already-processed items
        new_items = [i for i in items if i["id"] not in processed_hashes]
        print(f"   New (unprocessed): {len(new_items)}")
        all_items.extend(new_items)

    print(f"\n📊 Total new items across all countries: {len(all_items)}")

    # Apply AI semantic filter
    filtered = filter_and_enrich(all_items)
    print(f"✅ AI/N8N-related trends matched: {len(filtered)}")
    print(f"   Total relevant news items: {sum(len(r['news_items']) for r in filtered)}")

    # Mark all scanned items as processed (not just matched ones)
    for item in all_items:
        processed_hashes.add(item["id"])

    # Output
    output_json(filtered, json_output)
    output_markdown(filtered, md_output)

    # Copy to README
    if os.path.exists(md_output):
        with open(md_output, "r", encoding="utf-8") as src:
            content = src.read()
        with open(readme_output, "w", encoding="utf-8") as dst:
            dst.write(content)

    save_processed_hashes(processed_file, processed_hashes)
    clean_old_entries(md_output)

    print(f"\n📁 Outputs written:")
    print(f"   JSON: {json_output}")
    print(f"   Markdown: {md_output}")
    print(f"   README: {readme_output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
