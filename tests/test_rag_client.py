"""M11.2 — the fail-open RAG client (PRD §4.4): every failure degrades
to a RAG-less generation, never an exception."""

from __future__ import annotations

import io
import json
import socket
import urllib.error

from fass.config import RagConfig
from fass.rag.client import RagSearchResult, retrieve, search

CORPORA_BODY = json.dumps({
    "corpora": [
        {"corpus_id": "ffmpeg-6.1.1", "version_status": "pinned",
         "loaded": False, "kinds": {"manual": 10}},
        {"corpus_id": "ffmpeg-9.0.1", "version_status": "pinned",
         "loaded": True, "kinds": {"manual": 10}},
    ],
    "loaded_corpus_id": "ffmpeg-9.0.1",
}).encode()

SEARCH_BODY = json.dumps({
    "query": "use yadif",
    "corpus_id": "ffmpeg-9.0.1",
    "corpus_match": "exact",   # server-side label; the client ignores it
    "evidence_status": "supported",
    "chunks": [{"chunk_id": "c1", "title": "yadif",
                "url": "https://ffmpeg.org/ffmpeg-filters.html",
                "anchor": "yadif", "component_type": "filter",
                "component_name": "yadif", "option_names": [],
                "text": "Deinterlace", "score": 1.0, "parent_id": None}],
    "exact_matches": [{"type": "filter", "name": "yadif"}],
    "mentioned_components": [],
    "omitted": {"chunks": 0, "chars": 0},
    "timing_ms": 12,
}).encode()


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """opener(request, timeout=...) -> context manager with .read().
    Records every request so tests can inspect headers and bodies."""

    def __init__(self):
        self.requests = []       # urllib.request.Request, in order
        self.queue = []          # _Resp | Exception, in request order

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def calls(self):
        return [(r.get_method(), r.full_url) for r in self.requests]


def _opener(*items):
    op = _FakeOpener()
    op.queue.extend(items)
    return op


def _cfg(**kw):
    return RagConfig(enabled=True, url="http://rag:8100", **kw)


def _http_error(code, body=b""):
    return urllib.error.HTTPError(
        "http://rag:8100/v1/search", code, "err", None, io.BytesIO(body))


# ------------------------------------------------- entry gate

def test_retrieve_disabled_returns_none():
    assert retrieve(RagConfig(enabled=False), "crop the video") is None


def test_retrieve_enabled_calls_search():
    op = _opener(_Resp(CORPORA_BODY), _Resp(SEARCH_BODY))
    r = retrieve(_cfg(), "use yadif", opener=op)
    assert isinstance(r, RagSearchResult) and r.ok


# ------------------------------------------------- success path

def test_search_success_shape_and_routing():
    op = _opener(_Resp(CORPORA_BODY), _Resp(SEARCH_BODY))
    r = search(_cfg(), "use yadif",
               probed_version="ffmpeg version 9.0.1", opener=op)
    assert r.ok
    assert r.corpus_id == "ffmpeg-9.0.1"
    assert r.corpus_match == "exact"          # client-side, not the server's
    assert r.evidence_status == "supported"
    assert r.retrieval_ms == 12
    assert r.chunks[0]["chunk_id"] == "c1"
    assert r.warnings == []
    # two requests, in order: corpora then search
    assert op.calls == [
        ("GET", "http://rag:8100/v1/corpora"),
        ("POST", "http://rag:8100/v1/search")]
    # the POST carries the deterministic request (component filter from
    # the instruction, corpus selected against the probed build)
    post = op.requests[1]
    body = json.loads(post.data.decode("utf-8"))
    assert body["query"] == "use yadif"
    assert body["component_names"] == ["yadif"]
    assert body["component_types"] == ["filter"]
    assert body["corpus_id"] == "ffmpeg-9.0.1"
    assert body["top_k"] == 6
    assert body["source_kinds"] == ["manual"]


def test_search_sends_auth_header():
    op = _opener(_Resp(CORPORA_BODY), _Resp(SEARCH_BODY))
    search(_cfg(auth_token="tok"), "use yadif", opener=op)
    assert all(r.get_header("Authorization") == "Bearer tok"
               for r in op.requests)


# ------------------------------------------------- fail-open paths

def test_search_down_never_raises():
    op = _opener(
        urllib.error.URLError("Name or service not known"),
        urllib.error.URLError("Name or service not known"))
    r = search(_cfg(), "use yadif", opener=op)
    assert not r.ok
    assert r.chunks == []
    assert len(r.warnings) == 2
    assert "RAG corpus lookup failed" in r.warnings[0]
    assert "RAG search failed" in r.warnings[1]


def test_search_timeout_never_raises():
    op = _opener(socket.timeout("timed out"), socket.timeout("timed out"))
    r = search(_cfg(), "use yadif", opener=op)
    assert not r.ok
    assert r.warnings[0].startswith("RAG corpus lookup failed")
    assert r.warnings[1].startswith("RAG search failed")


def test_http_500_on_search():
    op = _opener(_Resp(CORPORA_BODY), _http_error(500))
    r = search(_cfg(), "use yadif", opener=op)
    assert not r.ok
    assert any("HTTP 500" in w for w in r.warnings)


def test_corpus_404_mismatch_reports_loaded():
    body = json.dumps({
        "error": "corpus 'ffmpeg-1.0.0' not loaded",
        "corpus_match": "missing",
        "requested_corpus_id": "ffmpeg-1.0.0",
        "loaded_corpus_id": "ffmpeg-9.0.1",
    }).encode()
    op = _opener(_Resp(CORPORA_BODY), _http_error(404, body))
    r = search(_cfg(corpus="ffmpeg-1.0.0"), "use yadif", opener=op)
    assert not r.ok
    assert any("is not loaded by the service" in w and "ffmpeg-9.0.1" in w
               for w in r.warnings)


def test_corpora_lookup_failure_still_searches():
    op = _opener(urllib.error.URLError("refused"), _Resp(SEARCH_BODY))
    r = search(_cfg(), "use yadif", opener=op)
    assert r.ok
    assert len(r.warnings) == 1 and "corpus lookup failed" in r.warnings[0]
    assert r.corpus_match == "none"  # server default applies


def test_malformed_search_body():
    op = _opener(_Resp(CORPORA_BODY), _Resp(b"not json"))
    r = search(_cfg(), "use yadif", opener=op)
    assert not r.ok
    assert any("RAG search failed" in w for w in r.warnings)


def test_malformed_corpora_body_degrades_to_default():
    op = _opener(_Resp(b"oops"), _Resp(SEARCH_BODY))
    r = search(_cfg(), "use yadif", opener=op)
    assert r.ok
    assert r.warnings and "corpus lookup failed" in r.warnings[0]
