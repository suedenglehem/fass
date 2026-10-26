"""fass RAG: version-pinned FFmpeg documentation retrieval (M11+).

Advisory-evidence-only: the registry / builder / argv gate stays the
source of truth (authority ladder, joint PRD 3.2). This package is
independent of fass/ code — stdlib only.

M11.0 surface:
    rag.parser    makeinfo HTML -> structured Document
    rag.chunker   Document -> evidence chunks (PRD 5.3 metadata)
    rag.ingest    operator CLI: parse + chunk a corpus, new index generation
"""

PARSER_VERSION = "1.1"  # 1.1: stacked deflist pairing (shared/continuation <dd>, nested <dl>)
