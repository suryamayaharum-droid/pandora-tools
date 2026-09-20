from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FRONTIER = ROOT / "frontier" / "queries.json"
OUT = ROOT / "beacon" / "latest.json"
HEARTBEAT = ROOT / "beacon" / "heartbeat.json"
USER_AGENT = "MEAW-Public-Wake-Beacon/1.0 (+https://github.com/suryamayaharum-droid/pandora-tools)"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def request_text(url: str, *, accept: str, timeout: int = 12, github: bool = False) -> tuple[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if github and token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("response exceeds 2 MB bound")
        return response.geturl(), raw.decode("utf-8", errors="replace")


def github_search(query: str, limit: int, timeout: int) -> list[dict]:
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
        "q": query,
        "sort": "updated",
        "order": "desc",
        "per_page": limit,
    })
    resolved, text = request_text(url, accept="application/vnd.github+json", timeout=timeout, github=True)
    data = json.loads(text)
    return [{
        "title": item.get("full_name"),
        "url": item.get("html_url"),
        "summary": item.get("description") or "",
        "stars": item.get("stargazers_count"),
        "language": item.get("language"),
        "updated_at": item.get("updated_at"),
        "source": resolved,
    } for item in (data.get("items") or [])[:limit]]


def npm_search(query: str, limit: int, timeout: int) -> list[dict]:
    url = "https://registry.npmjs.org/-/v1/search?" + urllib.parse.urlencode({
        "text": query,
        "size": limit,
    })
    resolved, text = request_text(url, accept="application/json", timeout=timeout)
    data = json.loads(text)
    rows = []
    for item in (data.get("objects") or [])[:limit]:
        pkg = item.get("package") or {}
        rows.append({
            "title": pkg.get("name"),
            "url": (pkg.get("links") or {}).get("npm"),
            "summary": pkg.get("description") or "",
            "version": pkg.get("version"),
            "date": pkg.get("date"),
            "source": resolved,
        })
    return rows


def crossref_search(query: str, limit: int, timeout: int) -> list[dict]:
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query": query,
        "rows": limit,
        "select": "DOI,title,URL,publisher,published,created",
    })
    resolved, text = request_text(url, accept="application/json", timeout=timeout)
    data = json.loads(text)
    rows = []
    for item in ((data.get("message") or {}).get("items") or [])[:limit]:
        title = item.get("title")
        rows.append({
            "title": title[0] if isinstance(title, list) and title else title,
            "url": item.get("URL"),
            "doi": item.get("DOI"),
            "publisher": item.get("publisher"),
            "published": item.get("published"),
            "source": resolved,
        })
    return rows


def arxiv_search(query: str, limit: int, timeout: int) -> list[dict]:
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": "all:" + query,
        "start": 0,
        "max_results": limit,
        "sortBy": "lastUpdatedDate",
        "sortOrder": "descending",
    })
    resolved, text = request_text(url, accept="application/atom+xml", timeout=timeout)
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    rows = []
    for entry in root.findall("a:entry", ns)[:limit]:
        authors = [a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)]
        rows.append({
            "title": " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split()),
            "url": entry.findtext("a:id", default="", namespaces=ns),
            "summary": " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())[:900],
            "updated": entry.findtext("a:updated", default="", namespaces=ns),
            "authors": [x for x in authors if x][:8],
            "source": resolved,
        })
    return rows


ADAPTERS = {
    "github": github_search,
    "npm": npm_search,
    "crossref": crossref_search,
    "arxiv": arxiv_search,
}


def main() -> int:
    frontier = json.loads(FRONTIER.read_text(encoding="utf-8"))
    bounds = frontier.get("bounds") or {}
    limit = max(1, min(10, int(bounds.get("max_results_per_adapter", 5))))
    timeout = max(3, min(30, int(bounds.get("network_timeout_seconds", 12))))
    query_limit = max(1, min(20, int(bounds.get("max_queries_per_run", 6))))
    queries = sorted(frontier.get("queries") or [], key=lambda x: (-int(x.get("priority", 0)), str(x.get("id", ""))))[:query_limit]

    generated_at = datetime.now(timezone.utc).isoformat()
    evidence = []
    for item in queries:
        query = str(item.get("query", "")).strip()
        if not query:
            continue
        for adapter in item.get("adapters") or []:
            if adapter not in ADAPTERS:
                continue
            started = time.monotonic()
            try:
                results = ADAPTERS[adapter](query, limit, timeout)
                status = "ok"
                error = ""
            except Exception as exc:
                results = []
                status = "error"
                error = f"{type(exc).__name__}: {exc}"[:500]
            record = {
                "frontier_id": item.get("id"),
                "query": query,
                "adapter": adapter,
                "status": status,
                "error": error,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "results": results,
            }
            record["evidence_sha256"] = sha256(record)
            evidence.append(record)

    body = {
        "schema": "meaw.public-wake-beacon/v1",
        "generated_at": generated_at,
        "mode": "public-readonly-research",
        "authority": "evidence-only; never policy or private-memory authority",
        "frontier_sha256": sha256(frontier),
        "evidence": evidence,
        "stats": {
            "queries": len(queries),
            "adapter_runs": len(evidence),
            "successful": sum(1 for x in evidence if x["status"] == "ok"),
            "failed": sum(1 for x in evidence if x["status"] != "ok"),
        },
    }
    body["root_sha256"] = sha256(body)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HEARTBEAT.write_text(json.dumps({
        "schema": "meaw.public-wake-heartbeat/v1",
        "generated_at": generated_at,
        "beacon_sha256": body["root_sha256"],
        "status": "ok",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(body["stats"], ensure_ascii=False))
    print(body["root_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
