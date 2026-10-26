"""M11.2 client-side RAG integration (PRD §6).

The fass side of the fass-rag service:

- query.py — the deterministic query builder (one /v1/search request per
  LLM-NLU call: component mentions, jargon fallback, media facts, corpus
  selection, evidence budget);
- client.py — the fail-open HTTP client (every failure degrades to the
  RAG-less generation, PRD §4.4);
- evidence.py — the prompt evidence block and its budget (PRD §6.3);
- citations.py — citation derivation from the executed plan (PRD §6.4).

The rules adapter never calls any of this at request time (PRD §6.5):
RAG is an opt-in LLM-path feature.
"""
