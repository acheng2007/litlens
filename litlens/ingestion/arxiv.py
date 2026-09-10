"""Bounded arXiv Atom search and PDF download from fixed, trusted hosts."""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from defusedxml import ElementTree
from redis import Redis

from litlens.config import settings

ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Paper:
    arxiv_id: str
    title: str


def throttle():
    """One request per three seconds across all workers sharing Redis."""
    redis = Redis.from_url(settings.redis_url, socket_timeout=5)
    while not redis.set("litlens:arxiv:rate", "1", nx=True, px=3100):
        time.sleep(0.2)


def parse_feed(content: bytes) -> list[Paper]:
    root = ElementTree.fromstring(content)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        url = urlparse(entry.findtext(f"{ATOM}id", ""))
        identifier = url.path.removeprefix("/abs/")
        if url.hostname not in {"arxiv.org", "export.arxiv.org"} or not ARXIV_ID.fullmatch(
            identifier
        ):
            raise ValueError("Invalid arXiv identifier in feed")
        title = " ".join(entry.findtext(f"{ATOM}title", "").split())
        if not title:
            raise ValueError("Missing arXiv title")
        papers.append(Paper(identifier, title[:500]))
    return papers


def search_arxiv(query: str, max_results: int = 5) -> list[Paper]:
    throttle()
    params = {"max_results": min(max_results, 20), "start": 0}
    params["id_list" if ARXIV_ID.fullmatch(query) else "search_query"] = query
    with httpx.Client(
        timeout=60, headers={"User-Agent": "LitLens/0.1 (local research tool)"}
    ) as client:
        with client.stream("GET", "https://export.arxiv.org/api/query", params=params) as response:
            response.raise_for_status()
            content = bytearray()
            for part in response.iter_bytes():
                content.extend(part)
                if len(content) > 2_000_000:
                    raise ValueError("arXiv feed exceeds size limit")
    return parse_feed(bytes(content))


def download_pdf(identifier: str, target: Path):
    if not ARXIV_ID.fullmatch(identifier):
        raise ValueError("Invalid arXiv identifier")
    throttle()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never follow feed-provided URLs or redirects to arbitrary hosts.
    with httpx.stream("GET", f"https://arxiv.org/pdf/{identifier}", timeout=90) as response:
        response.raise_for_status()
        size = 0
        with target.open("wb") as output:
            for part in response.iter_bytes():
                size += len(part)
                if size > settings.max_upload_bytes:
                    raise ValueError("arXiv PDF exceeds upload limit")
                output.write(part)
