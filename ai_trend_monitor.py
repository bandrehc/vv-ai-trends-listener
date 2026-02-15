"""
AI & N8N Daily Newsletter Generator — MVP
==========================================
Single script that:
  1. Fetches AI/N8N news from 9 countries (Google News RSS + Tech feeds)
  2. Deduplicates and clusters by topic
  3. Generates a ~4000-word English newsletter in Markdown
  4. Outputs JSON for downstream processing

Run daily via GitHub Actions. No API keys needed.

Dependencies: pip install requests feedparser deep-translator
"""

import feedparser
import requests
import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote, unquote

# ═════════════════════════════════════════════════════════════════════
#  CONFIG
# ═════════════════════════════════════════════════════════════════════

MAX_NEWSLETTER_WORDS = 4000

COUNTRIES = {
    "US": {"name": "United States",        "lang": "en", "hl": "en-US", "gl": "US", "ceid": "US:en"},
    "GB": {"name": "United Kingdom",       "lang": "en", "hl": "en-GB", "gl": "GB", "ceid": "GB:en"},
    "CA": {"name": "Canada",               "lang": "en", "hl": "en-CA", "gl": "CA", "ceid": "CA:en"},
    "DE": {"name": "Germany",              "lang": "de", "hl": "de",    "gl": "DE", "ceid": "DE:de"},
    "PT": {"name": "Portugal",             "lang": "pt", "hl": "pt-PT", "gl": "PT", "ceid": "PT:pt-150"},
    "JP": {"name": "Japan",                "lang": "ja", "hl": "ja",    "gl": "JP", "ceid": "JP:ja"},
    "CN": {"name": "China",                "lang": "zh", "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
    "IL": {"name": "Israel",               "lang": "he", "hl": "he",    "gl": "IL", "ceid": "IL:he"},
    "AE": {"name": "United Arab Emirates",  "lang": "ar", "hl": "ar",    "gl": "AE", "ceid": "AE:ar"},
}

SEARCH_QUERIES = {
    "en": [
        "artificial intelligence",
        "ChatGPT OpenAI",
        "generative AI",
        "n8n automation",
        "LLM large language model",
        "AI regulation",
        "AI startup funding",
        "AI agents",
        "Anthropic Claude",
        "AI chip GPU",
        "deep learning",
        "machine learning",
    ],
    "de": ["künstliche Intelligenz", "generative KI", "ChatGPT", "KI Regulierung", "maschinelles Lernen"],
    "pt": ["inteligência artificial", "IA generativa", "ChatGPT", "automação n8n", "aprendizado de máquina"],
    "ja": ["人工知能", "生成AI", "ChatGPT", "大規模言語モデル", "機械学習"],
    "zh": ["人工智能", "生成式AI", "ChatGPT", "大语言模型", "机器学习", "深度学习"],
    "he": ["בינה מלאכותית", "ChatGPT", "artificial intelligence Israel"],
    "ar": ["الذكاء الاصطناعي", "ChatGPT", "artificial intelligence UAE"],
}

TECH_FEEDS = {
    "TechCrunch AI":   "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI":    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Wired AI":        "https://www.wired.com/feed/tag/ai/latest/rss",
    "VentureBeat AI":  "https://venturebeat.com/category/ai/feed/",
    "Ars Technica":    "https://feeds.arstechnica.com/arstechnica/index",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
    "AI News":         "https://www.artificialintelligence-news.com/feed/",
    "HN AI":           "https://hnrss.org/newest?q=AI+OR+LLM+OR+n8n&count=20",
}

# AI relevance filter (for general feeds like Ars Technica)
AI_KEYWORDS = {
    "n8n", "artificial intelligence", "machine learning", "deep learning",
    "generative ai", "genai", "llm", "large language model", "chatgpt",
    "openai", "anthropic", "gemini", "copilot", "midjourney", "stable diffusion",
    "dall-e", "dalle", "mistral", "deepseek", "hugging face", "huggingface",
    "ai agent", "ai agents", "agentic ai", "langchain", "crewai", "autogen",
    "neural network", "computer vision", "nlp", "natural language processing",
    "ai regulation", "ai safety", "ai ethics", "ai chip", "ai startup",
    "ai funding", "grok", "perplexity", "foundation model", "transformer",
    "reinforcement learning", "workflow automation", "ai governance",
    "inteligência artificial", "künstliche intelligenz", "人工智能", "人工知能",
    "בינה מלאכותית", "الذكاء الاصطناعي",
}

AI_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bai\s+(?:model|system|tool|platform|startup|company|research|chip|regulation|safety|agent|powered|driven|based|enabled)\b",
        r"\b(?:generative|autonomous|agentic|responsible|explainable)\s+ai\b",
        r"\bai[-\s](?:powered|generated|driven|based|enabled)\b",
        r"\bgpt[-\s]?\d", r"\bclaude\s+\d", r"\bgemini\s+(?:pro|ultra|flash)\b",
        r"\bllama\s*\d", r"\bmistral\s+\d",
        r"\b(?:artificial|machine|deep)\s+(?:intelligence|learning)\b",
        r"\bneural\s+net(?:work)?", r"\bn8n\b",
        r"\b(?:openai|anthropic|deepseek|google)\s+(?:launches?|releases?|announces?)\b",
    ]
]


def is_ai_related(text):
    t = text.lower()
    for kw in AI_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", t):
            return True
    for p in AI_PATTERNS:
        if p.search(t):
            return True
    return False


# ═════════════════════════════════════════════════════════════════════
#  TRANSLATION
# ═════════════════════════════════════════════════════════════════════

def is_english(text):
    """Quick heuristic: if >70% of chars are ASCII letters, likely English."""
    if not text:
        return True
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters == 0:
        return True
    return (ascii_letters / total_letters) > 0.7


def translate_to_english(text):
    """Translate text to English. Returns original if already English or on failure."""
    if not text or is_english(text):
        return text
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source="auto", target="en").translate(text)
        return result if result else text
    except Exception:
        return text


def translate_batch(texts):
    """Translate a list of texts, preserving order. Skips already-English ones."""
    results = []
    to_translate = []  # (index, text) pairs that need translation
    
    for i, text in enumerate(texts):
        if not text or is_english(text):
            results.append(text)
        else:
            results.append(None)  # placeholder
            to_translate.append((i, text))
    
    # Batch translate non-English texts
    if to_translate:
        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source="auto", target="en")
            for idx, text in to_translate:
                try:
                    translated = translator.translate(text)
                    results[idx] = translated if translated else text
                except Exception:
                    results[idx] = text
                time.sleep(0.1)  # Rate limit politeness
        except ImportError:
            # Fallback: return originals
            for idx, text in to_translate:
                results[idx] = text
    
    return results


# ═════════════════════════════════════════════════════════════════════
#  DATA COLLECTION
# ═════════════════════════════════════════════════════════════════════

def fetch_feed(url, label=""):
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "AI-Newsletter/1.0"})
        items = []
        for e in feed.entries:
            title = unescape(e.get("title", "")).strip()
            link = e.get("link", "")
            pub = e.get("published", e.get("updated", ""))
            src = ""
            if hasattr(e, "source"):
                src = e.source.get("title", "") if isinstance(e.source, dict) else getattr(e.source, "title", "")
            if title and link:
                items.append({"title": title, "url": link, "published": pub, "source": src or label})
        return items
    except Exception as e:
        print(f"  ⚠️  {label}: {e}")
        return []


def collect_google_news(geo, meta):
    lang = meta["lang"]
    queries = SEARCH_QUERIES.get(lang, [])
    if lang != "en":
        queries = queries + SEARCH_QUERIES["en"][:4]
    queries = list(dict.fromkeys(queries))

    items = []
    seen = set()
    for q in queries:
        url = f"https://news.google.com/rss/search?q={quote(q)}&hl={meta['hl']}&gl={meta['gl']}&ceid={meta['ceid']}"
        for item in fetch_feed(url, f"GNews:{geo}:{q}"):
            if item["url"] not in seen:
                seen.add(item["url"])
                item["geo"] = geo
                item["country"] = meta["name"]
                item["query"] = q
                item["layer"] = "google_news"
                items.append(item)
        time.sleep(0.4)
    return items


def collect_tech_feeds():
    items = []
    seen = set()
    for name, url in TECH_FEEDS.items():
        print(f"  📡 {name}...")
        for item in fetch_feed(url, name):
            if item["url"] in seen:
                continue
            if name in ("Ars Technica", "MIT Tech Review") and not is_ai_related(item["title"]):
                continue
            seen.add(item["url"])
            item["geo"] = "GLOBAL"
            item["country"] = "Global"
            item["query"] = name
            item["layer"] = "tech_feed"
            items.append(item)
        time.sleep(0.3)
    return items


# ═════════════════════════════════════════════════════════════════════
#  CLUSTERING & RANKING
# ═════════════════════════════════════════════════════════════════════

def normalize_for_clustering(title):
    """Extract key terms for fuzzy topic clustering."""
    t = re.sub(r"[^\w\s]", " ", title.lower())
    t = re.sub(r"\s+", " ", t).strip()
    # Remove very common words
    stops = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
             "for", "of", "and", "or", "its", "it", "by", "as", "with", "from",
             "that", "this", "has", "have", "had", "be", "been", "will", "can",
             "new", "says", "said", "how", "what", "why", "when", "who"}
    words = [w for w in t.split() if w not in stops and len(w) > 2]
    return set(words)


def cluster_items(items):
    """Group items by topic similarity. Returns list of clusters."""
    clusters = []  # Each cluster: {"keywords": set, "items": [...]}

    for item in items:
        item_words = normalize_for_clustering(item["title"])
        if not item_words:
            continue

        # Find best matching cluster
        best_cluster = None
        best_overlap = 0
        for cluster in clusters:
            overlap = len(item_words & cluster["keywords"])
            # Require at least 2 shared significant words
            if overlap >= 2 and overlap > best_overlap:
                best_overlap = overlap
                best_cluster = cluster

        if best_cluster:
            best_cluster["items"].append(item)
            best_cluster["keywords"] |= item_words
        else:
            clusters.append({"keywords": item_words, "items": [item]})

    # Sort clusters by size (most covered = most important)
    clusters.sort(key=lambda c: len(c["items"]), reverse=True)
    return clusters


def pick_representative(cluster):
    """Pick the best headline from a cluster."""
    # Prefer English items, then by source quality
    preferred_sources = ["TechCrunch", "The Verge", "Wired", "Ars Technica",
                         "MIT Tech Review", "VentureBeat", "Reuters", "Bloomberg",
                         "BBC", "NYT", "WSJ", "Financial Times"]

    items = cluster["items"]

    # Score each item
    def score(item):
        s = 0
        # English preference
        geo = item.get("geo", "")
        if geo in ("US", "GB", "CA", "GLOBAL"):
            s += 10
        # Known source bonus
        src = item.get("source", "").lower()
        for i, ps in enumerate(preferred_sources):
            if ps.lower() in src:
                s += (len(preferred_sources) - i)
                break
        # Longer titles tend to be more descriptive
        s += min(len(item["title"]) / 20, 5)
        return s

    items.sort(key=score, reverse=True)
    return items[0]


# ═════════════════════════════════════════════════════════════════════
#  NEWSLETTER GENERATION
# ═════════════════════════════════════════════════════════════════════

def count_words(text):
    return len(text.split())


def generate_newsletter(clusters, all_items):
    """Generate a ~4000 word English Markdown newsletter."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # ── Pre-translate all headlines that will appear in the newsletter ──
    # Collect all titles we'll display
    display_items = []
    for cluster in clusters:
        for item in cluster["items"]:
            display_items.append(item)

    titles = [item["title"] for item in display_items]
    translated = translate_batch(titles)
    # Write back translated titles into a separate field
    for item, t in zip(display_items, translated):
        item["title_en"] = t or item["title"]

    # Count items per country for stats
    country_counts = Counter(i["country"] for i in all_items)
    total_sources = len(set(i.get("source", "") for i in all_items if i.get("source")))

    # ── Header ──
    lines = [
        f"# 🌐 AI & Automation Daily Brief",
        f"### {today}",
        "",
        f"*Monitoring {len(COUNTRIES)} countries · {len(all_items)} articles scanned · {total_sources} sources · {len(clusters)} topics identified*",
        "",
        "---",
        "",
    ]

    word_count = count_words("\n".join(lines))
    budget = MAX_NEWSLETTER_WORDS - 200  # Reserve for footer

    # ── Top Stories (big clusters) ──
    top_clusters = [c for c in clusters if len(c["items"]) >= 3]
    mid_clusters = [c for c in clusters if len(c["items"]) == 2]
    small_clusters = [c for c in clusters if len(c["items"]) == 1]

    if top_clusters:
        lines.append("## 🔥 Top Stories\n")
        word_count += 3

        for cluster in top_clusters[:10]:
            if word_count >= budget:
                break

            rep = pick_representative(cluster)
            rep_title = rep.get("title_en", rep["title"])
            countries = sorted(set(i.get("country", "?") for i in cluster["items"]))
            sources_list = sorted(set(i.get("source", "?") for i in cluster["items"] if i.get("source")))[:5]
            coverage = len(cluster["items"])

            section = []
            section.append(f"### {rep_title}")
            section.append("")
            section.append(f"📊 **{coverage} sources** across {', '.join(countries)}")
            section.append("")

            # List top 5 unique articles in this cluster
            seen_titles = set()
            article_count = 0
            for item in cluster["items"]:
                t_en = item.get("title_en", item["title"])
                short = t_en[:80]
                if short in seen_titles or article_count >= 5:
                    continue
                seen_titles.add(short)
                src_tag = f" *({item['source']})*" if item.get("source") else ""
                geo_tag = f" `{item.get('geo', '')}`" if item.get("geo") not in ("GLOBAL", "") else ""
                section.append(f"- [{t_en}]({item['url']}){src_tag}{geo_tag}")
                article_count += 1

            section.append("")
            block = "\n".join(section)
            block_words = count_words(block)

            if word_count + block_words < budget:
                lines.extend(section)
                word_count += block_words

    # ── Notable Coverage (2 sources) ──
    if mid_clusters and word_count < budget - 300:
        lines.append("## 📰 Notable Coverage\n")
        word_count += 3

        for cluster in mid_clusters[:15]:
            if word_count >= budget - 100:
                break

            rep = pick_representative(cluster)
            rep_title = rep.get("title_en", rep["title"])
            other = [i for i in cluster["items"] if i["url"] != rep["url"]]
            other_src = other[0].get("source", "") if other else ""
            geo_tag = f" `{rep.get('geo', '')}`" if rep.get("geo") not in ("GLOBAL", "") else ""

            line = f"- **[{rep_title}]({rep['url']})**{geo_tag}"
            if other_src:
                line += f"  \n  Also covered by: *{other_src}*"
            lines.append(line)
            word_count += count_words(line)

        lines.append("")

    # ── Quick Hits (single source, fill remaining budget) ──
    if small_clusters and word_count < budget - 200:
        lines.append("## ⚡ Quick Hits\n")
        word_count += 3

        for cluster in small_clusters[:25]:
            if word_count >= budget - 50:
                break

            item = cluster["items"][0]
            geo = item.get("geo", "")
            geo_tag = f" `{geo}`" if geo not in ("GLOBAL", "") else ""
            src_tag = f" — *{item['source']}*" if item.get("source") else ""

            line = f"- [{item.get('title_en', item['title'])}]({item['url']}){src_tag}{geo_tag}"
            lines.append(line)
            word_count += count_words(line)

        lines.append("")

    # ── Regional Breakdown ──
    if word_count < budget - 100:
        lines.append("## 🗺️ Regional Breakdown\n")
        lines.append("| Region | Articles |")
        lines.append("|--------|----------|")
        for country, count in country_counts.most_common():
            lines.append(f"| {country} | {count} |")
        lines.append("")
        word_count += 5 + len(country_counts)

    # ── Footer ──
    lines.extend([
        "---",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"AI & N8N Global News Monitor v2.0 · "
        f"[View raw data](ai_trends.json)*",
    ])

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════
#  DEDUP & PERSISTENCE
# ═════════════════════════════════════════════════════════════════════

def make_hash(item):
    return hashlib.sha256((item.get("url", "") + item.get("title", "")).encode()).hexdigest()[:16]


def load_hashes(path):
    if not os.path.exists(path):
        return set()
    with open(path, "r") as f:
        return set(l.strip() for l in f if l.strip())


def save_hashes(path, hashes):
    h = sorted(hashes)[-10000:]
    with open(path, "w") as f:
        f.write("\n".join(h) + "\n")


# ═════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  AI & N8N Daily Newsletter Generator")
    print(f"  {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    hash_file = "processed_hashes.txt"
    old_hashes = load_hashes(hash_file)
    all_items = []

    # ── Layer 1: Google News ──
    print("\n📰 Layer 1: Google News")
    for geo, meta in COUNTRIES.items():
        print(f"  🔍 {meta['name']}...", end=" ")
        items = collect_google_news(geo, meta)
        print(f"{len(items)} items")
        all_items.extend(items)

    # ── Layer 2: Tech Feeds ──
    print("\n🔬 Layer 2: Tech Feeds")
    tech = collect_tech_feeds()
    all_items.extend(tech)
    print(f"  Total: {len(tech)}")

    # ── Dedup ──
    seen_urls = set()
    unique = []
    for item in all_items:
        h = make_hash(item)
        if h in old_hashes or item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        item["hash"] = h
        unique.append(item)

    print(f"\n🧹 {len(all_items)} raw → {len(unique)} new unique")

    # ── Cluster & Rank ──
    clusters = cluster_items(unique)
    top = sum(1 for c in clusters if len(c["items"]) >= 3)
    mid = sum(1 for c in clusters if len(c["items"]) == 2)
    solo = sum(1 for c in clusters if len(c["items"]) == 1)
    print(f"📊 {len(clusters)} topics: {top} hot, {mid} notable, {solo} single")

    # ── Generate Newsletter ──
    newsletter = generate_newsletter(clusters, unique)
    wc = count_words(newsletter)
    print(f"📝 Newsletter: {wc} words")

    # ── Save outputs ──
    with open("ai_trends.md", "w", encoding="utf-8") as f:
        f.write(newsletter)

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(newsletter)

    # JSON output (full data for downstream)
    json_out = {
        "meta": {
            "system": "AI & N8N Newsletter v2.0",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_items": len(unique),
            "total_clusters": len(clusters),
            "newsletter_words": wc,
            "countries": {c["name"]: sum(1 for i in unique if i.get("country") == c["name"]) for c in COUNTRIES.values()},
        },
        "items": [
            {
                "id": i["hash"],
                "headline": i["title"],
                "url": i["url"],
                "source": i.get("source", ""),
                "published": i.get("published", ""),
                "country": i.get("country", ""),
                "geo": i.get("geo", ""),
                "query": i.get("query", ""),
                "layer": i.get("layer", ""),
            }
            for i in unique
        ],
    }
    with open("ai_trends.json", "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    # Update hashes
    for i in unique:
        old_hashes.add(i["hash"])
    save_hashes(hash_file, old_hashes)

    print(f"\n✅ Done! Files: ai_trends.md, ai_trends.json, README.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
