"""M11.2 §6.1 — registry 2.5.0 documentation fields.

Every allowlisted filter entry carries, appended last:

- doc_ref:      {url, anchor, title} pinned to the ffmpeg documentation
- aliases:      reviewed natural-language phrases naming the filter
- provenance:   {corpus_id, option_source, verified_builds, reviewed}

plus registry_version == "2.5.0".  Shape checks run over the raw JSON;
the loader accessors (doc_ref / filter_aliases / doc_ref_map) are checked
for consistency against the same data, not against hardcoded strings.
"""
from fass.registry import (
    REGISTRY,
    all_filter_names,
    doc_ref,
    doc_ref_map,
    filter_aliases,
    filter_spec,
)

FILTER_NAMES = all_filter_names()
OPTION_SOURCES = {"manual", "build_help", "both"}


def _entries():
    for domain in ("video", "audio"):
        for name, spec in REGISTRY["filters"][domain].items():
            yield domain, name, spec


def test_registry_version():
    assert REGISTRY["registry_version"] == "2.5.0"


def test_all_entries_have_doc_fields():
    assert len(FILTER_NAMES) == 87
    seen = set()
    for domain, name, spec in _entries():
        seen.add(name)
        keys = list(spec)
        assert keys[-3:] == ["doc_ref", "aliases", "provenance"], (domain, name, keys)
    assert seen == FILTER_NAMES


def test_doc_ref_shape():
    for _domain, name, spec in _entries():
        dr = spec["doc_ref"]
        assert list(dr) == ["url", "anchor", "title"], name
        assert all(isinstance(v, str) and v for v in dr.values()), name


def test_doc_ref_url_matches_anchor():
    for _domain, name, spec in _entries():
        url = spec["doc_ref"]["url"]
        assert url.startswith("https://ffmpeg.org/"), name
        assert url.split("#")[-1] == spec["doc_ref"]["anchor"], name


def test_aliases_shape():
    for _domain, name, spec in _entries():
        aliases = spec["aliases"]
        assert isinstance(aliases, list), name
        assert all(isinstance(a, str) and a for a in aliases), name


def test_provenance_shape():
    for _domain, name, spec in _entries():
        pr = spec["provenance"]
        assert list(pr) == ["corpus_id", "option_source", "verified_builds", "reviewed"], name
        assert pr["corpus_id"] == "ffmpeg-9.0.1", name
        assert pr["option_source"] in OPTION_SOURCES, name
        assert pr["verified_builds"] == ["6.1.1", "9.0.1"], name
        assert pr["reviewed"] is True, name


# ------------------------------------------------------------- accessors

def test_doc_ref_scale():
    ref = doc_ref("scale")
    assert ref is not None
    assert ref == filter_spec("scale")["doc_ref"]
    assert ref["url"].startswith("https://ffmpeg.org/")
    assert ref["url"].split("#")[-1] == ref["anchor"]
    assert all(isinstance(v, str) and v for v in ref.values())


def test_doc_ref_silenceremove():
    ref = doc_ref("silenceremove")
    assert ref is not None
    assert ref == filter_spec("silenceremove")["doc_ref"]
    assert ref["url"].startswith("https://ffmpeg.org/")
    assert ref["url"].split("#")[-1] == ref["anchor"]


def test_filter_aliases_silenceremove():
    aliases = filter_aliases("silenceremove")
    assert isinstance(aliases, list)
    assert all(isinstance(a, str) and a for a in aliases)


def test_filter_aliases_unknown_is_empty():
    assert filter_aliases("not-a-filter") == []


def test_doc_ref_map_covers_all_filters():
    m = doc_ref_map()
    assert set(m) == FILTER_NAMES
    assert len(m) == 87
    for name, ref in m.items():
        assert ref == doc_ref(name)


def test_doc_ref_unknown_is_none():
    assert doc_ref("not-a-filter") is None
