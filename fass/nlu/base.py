"""NLU adapter interface (PRD section 8: one provider adapter,
schema-constrained output; the model's only job is to translate intent
into a plan)."""

from __future__ import annotations

from typing import Protocol

from fass.schema import AssetMap, EditPlan, ParseResult
from fass.probe.ffprobe import MediaInfo


class AdapterError(Exception):
    """Provider/transport failure — surfaced as a clear error, never as a
    fabricated command (PRD NFR reliability)."""


class NLUAdapter(Protocol):
    name: str

    def parse(
        self,
        instruction: str,
        assets: AssetMap,
        previous_plan: EditPlan | None = None,
        media: MediaInfo | None = None,
    ) -> ParseResult:
        ...
