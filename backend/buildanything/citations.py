"""Finding papers, and checking in code that a quote really appears in the paper."""
from __future__ import annotations

import io
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from html.parser import HTMLParser

from defusedxml import ElementTree as ET
from pypdf import PdfReader

NS = {"a": "http://www.w3.org/2005/Atom"}


@dataclass(frozen=True)
class Paper:
    id: str
    title: str
    text: str
    url: str


class PaperIndex(Protocol):
    def search(self, query: str) -> list[Paper]: ...
    def get(self, paper_id: str) -> Paper | None: ...


class ArxivIndex:
    """Abstracts only. Paywalled or full-text sources arrive through user upload."""

    ATTEMPTS = 3

    def __init__(self, min_interval: float = 3.2):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()  # arXiv asks for one request every three seconds, and an agent can search in parallel.

    def _fetch(self, params: dict[str, str | int]) -> bytes:
        """arXiv often refuses a request briefly when it's busy, so wait as it asks and try again."""
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
        with self._lock:
            for attempt in range(1, self.ATTEMPTS + 1):
                wait = self.min_interval - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "buildanything/0.1"}), timeout=40) as response:
                        return response.read()
                except OSError as exc:
                    if attempt == self.ATTEMPTS or (isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429):
                        raise
                    asked = exc.headers.get("Retry-After", "") if isinstance(exc, urllib.error.HTTPError) else ""
                    time.sleep(min(int(asked), 30) if asked.isdigit() else 5 * attempt)
                finally:
                    self._last = time.time()
        raise OSError("arXiv couldn't be reached.")

    def _call(self, params: dict[str, str | int]) -> list[Paper]:
        root = ET.fromstring(self._fetch(params))
        papers = []
        for entry in root.findall("a:entry", NS):
            identifier = entry.find("a:id", NS).text
            title = entry.find("a:title", NS)
            summary = entry.find("a:summary", NS)
            if title is None or summary is None:
                continue
            short = identifier.rsplit("/", 1)[-1]
            papers.append(Paper(short, " ".join(title.text.split()), " ".join(summary.text.split()), identifier))
        return papers

    def search(self, query: str) -> list[Paper]:
        return self._call({"search_query": f"all:{query}", "max_results": 6})

    def get(self, paper_id: str) -> Paper | None:
        found = self._call({"id_list": re.sub(r"v\d+$", "", paper_id), "max_results": 1})
        return found[0] if found else None


class PageError(ValueError):
    pass


def _public_host(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        return all(ipaddress.ip_address(info[4][0]).is_global for info in socket.getaddrinfo(parsed.hostname, None))
    except (OSError, ValueError):
        return False


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.skip += tag in ("script", "style", "noscript")

    def handle_endtag(self, tag: str) -> None:
        self.skip -= tag in ("script", "style", "noscript") and self.skip > 0

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)


def fetch_page(url: str, limit: int = 8_000_000) -> tuple[str, str]:
    """The title and readable text of a public page or PDF. Addresses on this computer or a private network are refused."""
    if not _public_host(url):
        raise PageError("Only public web addresses can be opened.")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BuildAnything research)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if not _public_host(response.geturl()):
            raise PageError("That page redirected somewhere private.")
        raw = response.read(limit)
        kind = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
    if raw[:5] == b"%PDF-" or kind == "application/pdf":
        reader = PdfReader(io.BytesIO(raw))
        return (url.rsplit("/", 1)[-1], " ".join((page.extract_text() or "") for page in reader.pages[:60]))
    parser = _Text()
    parser.feed(raw.decode(charset, errors="replace"))
    found = re.search(r"<title[^>]*>(.*?)</title>", raw.decode(charset, errors="replace"), re.I | re.S)
    return (" ".join(found.group(1).split())[:200] if found else url, re.sub(r"\s+", " ", " ".join(parser.out)))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_text(quote: str, text: str, min_length: int = 20) -> bool:
    q = normalize(quote)
    return len(q) >= min_length and q in normalize(text)
