#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET

import trafilatura


USER_AGENT = "BasedNewsBot/0.1"

def utc_now_iso():
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")

def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def discover_feeds(homepage_url):
    try:
        body, _ = fetch_url(homepage_url)
    except Exception:
        return []
    html = body.decode("utf-8", errors="ignore")
    links = set()
    for match in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I):
        if "rss" in match.lower() or "feed" in match.lower() or "atom" in match.lower():
            if match.startswith("//"):
                links.add("https:" + match)
            elif match.startswith("/"):
                links.add(homepage_url.rstrip("/") + match)
            else:
                links.add(match)
    return sorted(links)


def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    channel = root.find("channel")
    if channel is None:
        return items
    for item in channel.findall("item"):
        title = text_or_none(item.find("title"))
        link = text_or_none(item.find("link"))
        guid = text_or_none(item.find("guid"))
        published = text_or_none(item.find("pubDate"))
        summary = text_or_none(item.find("description"))
        items.append(
            {
                "title": title,
                "url": link,
                "guid": guid,
                "published": published,
                "summary": summary,
            }
        )
    return items


def parse_atom(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = text_or_none(entry.find("atom:title", ns))
        link_el = entry.find("atom:link", ns)
        link = link_el.attrib.get("href") if link_el is not None else None
        published = text_or_none(entry.find("atom:updated", ns)) or text_or_none(
            entry.find("atom:published", ns)
        )
        summary = text_or_none(entry.find("atom:summary", ns))
        items.append(
            {
                "title": title,
                "url": link,
                "guid": None,
                "published": published,
                "summary": summary,
            }
        )
    return items


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    tag = root.tag.lower()
    if tag.endswith("rss"):
        return parse_rss(xml_bytes)
    if tag.endswith("feed"):
        return parse_atom(xml_bytes)
    channel = root.find("channel")
    if channel is not None:
        return parse_rss(xml_bytes)
    return []


def text_or_none(el):
    if el is None:
        return None
    text = el.text.strip() if el.text else None
    return text


def clean_text(value):
    if not value:
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def looks_like_structured_payload(text):
    if not text:
        return True
    stripped = text.lstrip()
    if stripped.startswith("{") and "\"@context\"" in text:
        return True
    if "<" in text and ">" in text:
        return True
    if "css-" in text and "{" in text and "}" in text:
        return True
    return False


def extract_jsonld_text(html_text):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not scripts:
        return None

    texts = []

    def collect(node):
        if isinstance(node, list):
            for item in node:
                collect(item)
            return
        if not isinstance(node, dict):
            return

        node_type = node.get("@type")
        if isinstance(node_type, list):
            types = [t for t in node_type if isinstance(t, str)]
        elif isinstance(node_type, str):
            types = [node_type]
        else:
            types = []

        types_lower = {t.lower() for t in types}
        if "liveblogposting" in types_lower:
            updates = node.get("liveBlogUpdate") or []
            if isinstance(updates, dict):
                updates = [updates]
            for update in updates:
                if isinstance(update, dict):
                    body = update.get("articleBody")
                    if body:
                        texts.append(body)

        body = node.get("articleBody")
        if body:
            texts.append(body)

        for key in ("mainEntity", "mainEntityOfPage", "itemListElement"):
            collect(node.get(key))

    for script in scripts:
        script = script.strip()
        if not script:
            continue
        try:
            data = json.loads(script)
        except json.JSONDecodeError:
            continue
        collect(data)

    combined = clean_text("\n\n".join(texts))
    return combined


def fetch_article_text(url):
    try:
        html_bytes, _ = fetch_url(url, timeout=20)
    except Exception:
        return None
    html_text = html_bytes.decode("utf-8", errors="ignore")
    jsonld_text = extract_jsonld_text(html_text)
    if jsonld_text:
        return jsonld_text
    extracted = trafilatura.extract(
        html_text,
        output_format="txt",
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        favor_recall=False,
    )
    if not extracted:
        return None
    extracted = clean_text(extracted)
    if looks_like_structured_payload(extracted):
        return None
    return extracted


def normalize_item(item, outlet, country, feed_url, full_text=None):
    url = item.get("url") or item.get("guid") or ""
    raw_id = (url or "") + "|" + (item.get("title") or "")
    item_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
    return {
        "id": item_id,
        "title": item.get("title"),
        "url": url,
        "published": item.get("published"),
        "summary": item.get("summary"),
        "outlet_id": outlet["id"],
        "outlet_name": outlet["name"],
        "country": country,
        "feed_url": feed_url,
        "ingested_at": utc_now_iso(),
        "full_text": full_text,
    }


def ensure_dir(path):
    if not path:
        return
    os.makedirs(path, exist_ok=True)

def safe_dir_name(value):
    safe = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip()
    safe = re.sub(r"\s+", "-", safe)
    return safe or "unknown"

def append_metrics(path, record):
    ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


def init_cache(path):
    ensure_dir(os.path.dirname(path))
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_urls (url TEXT PRIMARY KEY, first_seen TEXT)"
    )
    return conn


def is_seen(conn, url):
    cur = conn.execute("SELECT 1 FROM seen_urls WHERE url = ? LIMIT 1", (url,))
    return cur.fetchone() is not None


def mark_seen(conn, url):
    conn.execute(
        "INSERT OR IGNORE INTO seen_urls (url, first_seen) VALUES (?, ?)",
        (url, utc_now_iso()),
    )


def ingest_outlet(outlet, country, out_dir, discover_missing, cache_conn):
    feeds = list(outlet.get("feeds") or [])
    if not feeds and discover_missing:
        feeds = discover_feeds(outlet.get("homepage", ""))
    if not feeds:
        return 0, [f"No feeds found for {outlet['name']}"]

    errors = []
    count = 0
    seen = set()
    for feed_url in feeds:
        try:
            xml_bytes, _ = fetch_url(feed_url)
            items = parse_feed(xml_bytes)
        except Exception as e:
            errors.append(f"{outlet['name']} feed error: {feed_url} -> {e}")
            continue
        for item in items:
            full_text = fetch_article_text(item.get("url") or "")
            if not full_text:
                continue
            normalized = normalize_item(item, outlet, country, feed_url, full_text)
            if not normalized["url"]:
                continue
            if normalized["url"] in seen:
                continue
            seen.add(normalized["url"])
            if is_seen(cache_conn, normalized["url"]):
                continue
            write_jsonl(out_dir, outlet["id"], normalized)
            mark_seen(cache_conn, normalized["url"])
            count += 1
    cache_conn.commit()
    return count, errors


def write_jsonl(out_dir, outlet_id, record):
    ensure_dir(out_dir)
    path = os.path.join(out_dir, f"{outlet_id}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch RSS/Atom feeds and store as JSONL."
    )
    parser.add_argument("--config", default="data/outlets.json")
    parser.add_argument("--out", default="data/raw")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--cache", default="data/ingest_cache.sqlite")
    parser.add_argument("--metrics", default="data/metrics/ingest_counts.jsonl")
    args = parser.parse_args()

    config = load_config(args.config)
    run_at = utc_now_iso()

    total = 0
    all_errors = []
    cache_conn = init_cache(args.cache)
    try:
        for country, outlets in config["countries"].items():
            for outlet in outlets:
                country_dir = os.path.join(args.out, safe_dir_name(country))
                count, errors = ingest_outlet(
                    outlet, country, country_dir, args.discover, cache_conn
                )
                total += count
                all_errors.extend(errors)
                append_metrics(
                    args.metrics,
                    {
                        "retrieved_at": run_at,
                        "country": country,
                        "outlet_id": outlet.get("id"),
                        "outlet_name": outlet.get("name"),
                        "count": count,
                        "error_count": len(errors),
                        "error_sample": errors[0] if errors else None
                    },
                )
    finally:
        cache_conn.close()

    print(f"Ingested {total} articles into {args.out}")
    if all_errors:
        print("Errors:")
        for err in all_errors:
            print(" -", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
