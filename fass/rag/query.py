"""Deterministic RAG query builder (M11.2, PRD §6.2).

Builds at most ONE /v1/search request per LLM-NLU call:

1. **Exact component mentions.** Scan the instruction for registry
   filter names (video + audio), the filters' reviewed aliases, encoder
   names (every form the registry knows: CPU, GPU, QSV, audio) and
   bitstream-filter names. These become the request's ``component_names``
   / ``component_types`` (the server filters exactly on them).
   Format-option keys are deliberately NOT hard filters: the corpus has
   no format-option component type (PRD §5.3 component set), and a hard
   filter on them would return nothing — they stay in the query text,
   where the lexical search finds them inside muxer/muxer-option prose.
2. **Jargon fallback.** If the instruction names nothing exact, a small
   phrase -> component map applies (stabilise -> vidstabdetect /
   vidstabtransform, remove silence(s) -> silenceremove, green screen
   -> chromakey, deinterlace -> yadif, crossfade -> xfade/acrossfade).
   The candidates become the component filter the same way; the raw
   instruction stays the semantic query (PRD §5.5 example: NL query +
   component_names).
3. **Media facts.** One structured line (duration, WxH, has_audio, fps)
   when the input was probed — it improves "end fade" / "crossfade"
   style queries.
4. **Corpus selection.** Exact probed version, else the nearest lower
   release with a warning, else the nightly snapshot with a stronger
   warning (PRD §5.1). Explicit override via the rag config's ``corpus``
   field (the FASS_RAG_CORPUS equivalent).
5. **Budget.** One query; ``top_k`` / ``max_evidence_chars`` from the
   resolved rag config (defaults 6 / 4000). No query expansion (MVP).
   The raw instruction text is the query only when ``send_instruction``
   is enabled; otherwise the query is the component names alone (the
   media facts line is always appended when the input was probed).

Pure and unit-testable without the service: given an instruction, media
facts, the probed version string and the corpus list, the request JSON
is exactly deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fass.config import RagConfig, resolved_rag
from fass.registry import REGISTRY

# The corpus's prose documentation (the build-help kind is for the
# authoring pipeline and option-level questions, PRD §5.1).
SOURCE_KINDS = ["manual"]

# PRD §6.2 jargon map — phrase -> supported components (filter type).
# "vidstab" in the PRD text is the fass stabilize pair (two-pass plan).
JARGON: dict[str, tuple[str, ...]] = {
    "stabilise": ("vidstabdetect", "vidstabtransform"),
    "stabilize": ("vidstabdetect", "vidstabtransform"),
    "remove silence": ("silenceremove",),
    "remove silences": ("silenceremove",),
    "silence removal": ("silenceremove",),
    "green screen": ("chromakey",),
    "deinterlace": ("yadif",),
    "deinterlacing": ("yadif",),
    "crossfade": ("xfade", "acrossfade"),
    "cross fade": ("xfade", "acrossfade"),
}

# corpus_match values for the evidence block:
#   exact    probed build == corpus release
#   nearest  nearest lower release (PRD §5.1 warning)
#   nightly  nightly snapshot fallback (stronger warning)
#   override explicit rag.corpus setting
#   loaded   no probed version to compare against; the service's loaded
#            corpus is used and labelled as such
#   none     no usable corpus id could be selected
_PROBED_VERSION_RE = re.compile(r"version\s+(\d+)\.(\d+)(?:\.(\d+))?")
_RELEASE_RE = re.compile(r"^ffmpeg-(\d+)\.(\d+)(?:\.(\d+))?$")
_NIGHTLY_RE = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class RagQuery:
    """One deterministic /v1/search request + its corpus-selection facts
    (corpus_match / corpus_warnings feed the response evidence block)."""

    query: str
    corpus_id: str | None
    component_types: list[str] = field(default_factory=list)
    component_names: list[str] = field(default_factory=list)
    top_k: int = 6
    max_evidence_chars: int = 4000
    corpus_match: str = "none"
    corpus_warnings: list[str] = field(default_factory=list)

    def to_request(self) -> dict:
        """The POST /v1/search body (PRD §5.5)."""
        req: dict = {
            "query": self.query,
            "corpus_id": self.corpus_id,
            "source_kinds": list(SOURCE_KINDS),
            "top_k": self.top_k,
            "max_evidence_chars": self.max_evidence_chars,
        }
        if self.component_types:
            req["component_types"] = list(self.component_types)
        if self.component_names:
            req["component_names"] = list(self.component_names)
        return req


def _component_name_map(reg: dict) -> dict[str, tuple[str, str]]:
    """Lowercased name or alias -> (component type, canonical registry
    name) for every component the registry knows: filter names + their
    reviewed aliases (registry 2.5.0), every encoder form, every
    bitstream-filter spec name. Aliases resolve to the filter's
    canonical name — the request may only carry registry keys."""
    m: dict[str, tuple[str, str]] = {}
    # canonical names first, so an alias can never shadow the name of
    # another component
    for kind in ("video", "audio"):
        for name in reg["filters"][kind]:
            m[name.lower()] = ("filter", name.lower())
    for kind in ("video", "audio"):
        for name, entry in reg["filters"][kind].items():
            for alias in entry.get("aliases", []) or []:
                a = alias.lower()
                if a and a not in m:
                    m[a] = ("filter", name.lower())
    for entry in reg["video_codecs"].values():
        enc = entry["encoder"].lower()
        m[enc] = ("encoder", enc)
    for entry in reg["audio_codecs"].values():
        enc = entry["encoder"].lower()
        m[enc] = ("encoder", enc)
    for form in ("gpu", "qsv"):
        for coder in reg[form]["coders"].values():
            enc = coder["encoder"].lower()
            m[enc] = ("encoder", enc)
    for name in reg["bitstream_filters"]["specs"]:
        s = name.lower()
        m[s] = ("bitstream_filter", s)
    return m


def mentioned_components(
    instruction: str, registry: dict | None = None,
) -> list[tuple[str, str]]:
    """Step 1 — (component_type, name) pairs the instruction mentions
    exactly, in first-occurrence order, with the canonical registry
    name (FFmpeg identifiers are lowercase). Word-boundary matching, so
    'mp4' never fires inside 'mp4_mov_to_mpegts' (underscore is a word
    character) and 'scale' inside 'scaleup' does not."""
    reg = registry if registry is not None else REGISTRY
    names = _component_name_map(reg)
    # longest first so 'mpegts_to_mp4_mov' wins over any shorter prefix
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in
                          sorted(names, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in pattern.finditer(instruction):
        ctype, canon = names[m.group(1).lower()]
        if (ctype, canon) not in seen:
            seen.add((ctype, canon))
            found.append((ctype, canon))
    return found


def jargon_candidates(instruction: str) -> list[tuple[str, str]]:
    """Step 2 — jargon-map (type, name) pairs, in first-occurrence order
    of the matched phrase."""
    low = instruction.lower()
    # (phrase position, position inside the phrase's target tuple, type,
    # name) — the tuple order is the documented preference order
    hits: list[tuple[int, int, str, str]] = []
    for phrase, targets in JARGON.items():
        i = low.find(phrase)
        if i < 0:
            continue
        for pos, name in enumerate(targets):
            hits.append((i, pos, "filter", name))
    hits.sort(key=lambda t: (t[0], t[1]))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, _, ctype, name in hits:
        if name not in seen:
            seen.add(name)
            out.append((ctype, name))
    return out


def media_facts_line(media) -> str | None:
    """Step 3 — one structured facts line for a probed input, else None."""
    if media is None:
        return None
    parts: list[str] = []
    if media.duration is not None:
        parts.append(f"duration={media.duration:.1f}s")
    if media.width and media.height:
        parts.append(f"{media.width}x{media.height}")
    parts.append("has_audio=true" if media.has_audio else "has_audio=false")
    if media.fps is not None:
        parts.append(f"fps={media.fps:g}")
    return "media: " + " ".join(parts) if parts else None


def _release_tuple(corpus_id: str) -> tuple[int, int, int] | None:
    m = _RELEASE_RE.match(corpus_id.strip())
    if not m:
        return None
    return (int(m[1]), int(m[2]), int(m[3] or 0))


def select_corpus(
    probed_version: str | None,
    available: list[str],
    override: str = "auto",
    loaded: str | None = None,
    warnings: list[str] | None = None,
) -> tuple[str | None, str]:
    """Step 4 — (corpus_id, corpus_match) per PRD §5.1.

    ``probed_version`` is the probed build's first ``ffmpeg -version``
    line (or None when the build could not be probed). ``available`` is
    the service's corpus list; ``loaded`` is the corpus it currently
    serves (used when there is nothing to compare against)."""
    w = warnings if warnings is not None else []
    if override and override != "auto":
        if available and override not in available:
            w.append(f"configured RAG corpus '{override}' is not available "
                     f"from the service")
        return override, "override"
    if not probed_version:
        # Nothing to compare against: use what the service loads and say
        # so (labelled in the evidence block, not presented as exact).
        return (loaded, "loaded") if loaded else (None, "none")
    m = _PROBED_VERSION_RE.search(probed_version)
    if not m:
        w.append("could not parse the probed FFmpeg version for corpus "
                 "selection")
        return (loaded, "loaded") if loaded else (None, "none")
    probed = (int(m[1]), int(m[2]), int(m[3] or 0))
    exact = f"ffmpeg-{probed[0]}.{probed[1]}.{probed[2]}"
    if exact in available:
        return exact, "exact"
    pinned = [(cid, t) for cid in available
              if (t := _release_tuple(cid)) is not None and t <= probed]
    if pinned:
        cid, t = max(pinned, key=lambda p: p[1])
        w.append(f"RAG corpus {cid} is a lower release than the probed "
                 f"build — documentation may lag the build")
        return cid, "nearest"
    nightlies = sorted((cid for cid in available
                        if _NIGHTLY_RE.match(cid)), reverse=True)
    if nightlies:
        w.append(f"no pinned release corpus at or below the probed build; "
                 f"using the nightly snapshot {nightlies[0]} (not a "
                 f"release — verify against the build)")
        return nightlies[0], "nightly"
    w.append("no corpus matches the probed build; using the service's "
             "loaded corpus")
    return (loaded, "loaded") if loaded else (None, "none")


def build_query(
    instruction: str,
    media=None,
    probed_version: str | None = None,
    available_corpora: list[str] | None = None,
    loaded_corpus: str | None = None,
    rag: RagConfig | None = None,
) -> RagQuery:
    """The one deterministic search request for an LLM-NLU call."""
    cfg = rag if rag is not None else resolved_rag()
    mentions = mentioned_components(instruction) or \
        jargon_candidates(instruction)
    names: list[str] = []
    types: list[str] = []
    for ctype, name in mentions:
        if name not in names:
            names.append(name)
            types.append(ctype)
    # send_instruction=False sends only the component names (plus the
    # media facts) — the raw user text stays local.
    query = instruction.strip() if cfg.send_instruction else " ".join(names)
    line = media_facts_line(media)
    if line:
        query = f"{query} {line}".strip()
    if not query:
        query = instruction.strip()
    if available_corpora is not None:
        warnings: list[str] = []
        # every warning select_corpus emits is meaningful (override not
        # available, unparseable version, lagging/nightly corpus)
        corpus_id, match = select_corpus(
            probed_version, available_corpora, cfg.corpus,
            loaded=loaded_corpus, warnings=warnings)
    else:
        # No service metadata (unit tests / offline): honor the explicit
        # override, otherwise let the server default apply.
        corpus_id = cfg.corpus if cfg.corpus != "auto" else None
        match = "override" if corpus_id else "none"
        warnings = []
    return RagQuery(
        query=query,
        corpus_id=corpus_id,
        component_types=types,
        component_names=names,
        top_k=cfg.top_k,
        max_evidence_chars=cfg.max_evidence_chars,
        corpus_match=match,
        corpus_warnings=warnings,
    )
