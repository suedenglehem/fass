"""fass RAG: version-pinned FFmpeg documentation retrieval (M11+).

Advisory-evidence-only: the registry / builder / argv gate stays the
source of truth (authority ladder, joint PRD 3.2). This package is
independent of fass/ code — stdlib only.

M11.0 surface:
    rag.parser    makeinfo HTML -> structured Document
    rag.chunker   Document -> evidence chunks (PRD 5.3 metadata)
    rag.ingest    operator CLI: parse + chunk a corpus, new index generation
"""

PARSER_VERSION = "1.3"  # 1.3: build-help chunks carry resolvable provenance
# (source_url build-help://<corpus>/<file>, anchor <stem>-<i>): every chunk
# must resolve its citation to stored evidence (PRD 9.1), ingest rejects
# chunks without url+anchor
# 1.2: texinfo 6.x markup (Ubuntu deb) — copiable-link
# permalinks inside headings: target becomes the section anchor, the
# pilcrow link text is dropped (was corrupting 6.1.1 corpus metadata)
# 1.1: stacked deflist pairing (shared/continuation <dd>, nested <dl>)
