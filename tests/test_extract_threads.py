"""Ingest extracts text in six threads; lxml/libxml2 (inside trafilatura) crashed production when they parsed at once
(2026-10-14, "double free or corruption"). The parse must be serialised while downloads stay parallel."""
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pipeline.ingest import extract

PAGE = "<html><body><article><p>" + "Words about an event. " * 40 + "</p></article></body></html>"


def max_parallel_parses(monkeypatch, lock) -> int:
    inside, peak, guard = 0, 0, threading.Lock()

    def fake_extract(html, **kw):
        nonlocal inside, peak
        with guard:
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.01)
        with guard:
            inside -= 1
        return "Words about an event. " * 40

    monkeypatch.setattr(extract.trafilatura, "extract", fake_extract)
    monkeypatch.setattr(extract, "_PARSE", lock)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: extract.text_from_html(PAGE), range(40)))
    return peak


def test_parsing_is_serialised(monkeypatch):
    assert max_parallel_parses(monkeypatch, threading.Lock()) == 1


def test_the_check_would_catch_parallel_parsing(monkeypatch):
    assert max_parallel_parses(monkeypatch, contextlib.nullcontext()) > 1     # without the lock, threads overlap
