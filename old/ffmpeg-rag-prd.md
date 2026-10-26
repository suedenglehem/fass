# PRD: Dockerized FFmpeg Documentation RAG

**Status:** Proposed MVP  
**Primary user:** An existing LLM assistant that generates FFmpeg commands for video and audio transformations.  
**Deployment:** One Docker container, with persistent storage and an HTTP API.

**Recommended approach:** Build a small retrieval service that returns relevant, version-aware documentation to your existing assistant. Keep command generation in the assistant. Add FFmpeg capability checks because documentation retrieval alone cannot establish whether a command is supported by the user’s installed build.

## 1. Problem

An LLM generating FFmpeg commands can choose nonexistent options, mix input and output option scopes, produce invalid filtergraphs, or recommend encoders unavailable in the target build.

The service must supply authoritative documentation supporting the requested transformation, with enough surrounding context to preserve syntax, restrictions, and examples.

**Version compatibility is a core requirement.** FFmpeg’s website says its documentation is regenerated nightly and corresponds to the newest revision; it recommends locally installed documentation for older versions. A snapshot of the current website must therefore never be presented as documentation for an arbitrary installed release. Source: https://ffmpeg.org/documentation.html

## 2. Goals and boundaries

### Goals

- Retrieve relevant FFmpeg documentation from natural-language requests and exact component names.
- Preserve option syntax, defaults, units, examples, and important caveats.
- Return citations linking to the relevant documentation section.
- Distinguish documented functionality from functionality available in a target FFmpeg build.
- Run locally on CPU without requiring another GPU.
- Support reproducible ingestion, persistent indexes, and offline queries after initialization.
- Integrate with the existing assistant through a small HTTP API.

### Out of scope for MVP

- A chat interface or standalone agent.
- Hosting a command-generation LLM.
- Arbitrary execution of generated FFmpeg commands.
- Uploading, storing, or processing user media.
- Crawling the entire web, FFmpeg source code, or C API documentation.
- Distributed infrastructure or a separate vector-database server.
- Fine-tuning.

## 3. Core user stories

1. **Transformation request:** An assistant receives “normalize dialogue loudness and preserve the video.” It retrieves documentation covering loudness normalization, audio encoding, stream selection, and stream copying.
2. **Exact component lookup:** An assistant requests `filter:loudnorm` and gets its options, constraints, and examples.
3. **Compatibility check:** The assistant requests an NVENC command, but the supplied target capabilities do not list the required encoder. The service flags it as unavailable.
4. **Error diagnosis:** An assistant submits an FFmpeg error and retrieves documentation relevant to the reported component or option.
5. **Insufficient evidence:** If the indexed documentation does not support an answer, the service returns insufficient evidence instead of inventing syntax.

## 4. Documentation scope

### Initial sources

Ingest these individual manuals linked from https://ffmpeg.org/documentation.html:

| Manual | Purpose |
|---|---|
| `ffmpeg.html` | Command structure, option scope, stream selection, mapping |
| `ffprobe.html` | Media inspection and structured metadata |
| `ffmpeg-filters.html` | Audio/video filters and filtergraphs |
| `ffmpeg-codecs.html` | Encoder and decoder options |
| `ffmpeg-formats.html` | Muxer and demuxer options |
| `ffmpeg-utils.html` | Escaping, expressions, time values, shared syntax |
| `ffmpeg-scaler.html` | Scaling and pixel-format conversion |
| `ffmpeg-resampler.html` | Audio resampling |
| `ffmpeg-bitstream-filters.html` | Bitstream transformations |
| `ffmpeg-protocols.html` | File and network protocols |
| `ffmpeg-devices.html` | Capture and playback devices |

Exclude `ffmpeg-all.html` and other aggregate manuals from the initial index because their overlapping content would duplicate individual manuals.

Defer FAQs, wiki recipes, and external examples until the official-manual retrieval baseline has been evaluated. If added later, identify their provenance separately.

### Two corpus modes

**Website snapshot**
- Fetch the allowlisted official manuals.
- Record retrieval time and content hashes.
- Label the corpus `nightly-snapshot`.
- Do not infer an exact release or commit from the retrieval date.

**Pinned release**
- Import manuals generated from an explicitly selected FFmpeg release or commit.
- Record the source revision and generation procedure.
- Keep this corpus separate from nightly snapshots.
- Make it the preferred corpus when matching target-version documentation is available.

MVP supports multiple stored corpora but searches one explicitly selected corpus per request.

## 5. Architecture

```text
Existing assistant
       |
       | HTTP: search, lookup, capabilities
       v
Docker container
  FastAPI
    ├── Documentation importer
    ├── Section-aware parser
    ├── SQLite FTS5 lexical search
    ├── Local embedding model + vector search
    └── Evidence assembly and compatibility checks
       |
       v
Persistent /data volume
  snapshots, metadata, indexes, model cache
```

### Recommended stack

| Component | Choice |
|---|---|
| Language | Python |
| HTTP framework | FastAPI |
| HTML parsing | Beautiful Soup |
| Metadata and lexical search | SQLite with FTS5 |
| Embeddings | `BAAI/bge-small-en-v1.5`, revision pinned |
| Vector storage | Local NumPy array associated with immutable chunk IDs |
| Vector retrieval | Exact cosine similarity over normalized embeddings |
| Deployment | Docker |

**Why this design:** This is a bounded documentation corpus. SQLite plus a local embedding matrix avoids running a separate database service. Approximate vector search, orchestration frameworks, and rerankers should be introduced only if measurements justify them.

The embedding model is a replaceable default. It must pass the retrieval evaluation before being accepted.

## 6. Ingestion requirements

### Fetching

The importer shall:
- Fetch only configured official documentation URLs.
- Use HTTPS with certificate verification.
- Validate redirect destinations against the allowlist.
- Apply timeouts, response-size limits, and bounded retries.
- Use conditional requests where supported.
- Save raw HTML and content hashes.
- Record source URL, retrieval timestamp, corpus identity, and parser version.
- Never follow arbitrary links found in documentation.

Local release imports shall be read-only and restricted to an operator-provided import directory.

### Parsing and chunking

The parser shall preserve:
- Heading hierarchy and section anchors.
- Component names and aliases.
- Option names, values, defaults, units, and scope.
- Code blocks and examples verbatim.
- Tables with their labels and relationships.
- Links to related sections.

Use documentation structure first, token limits second.

**Initial chunking policy:**
- Target approximately 350–700 embedding tokens.
- Split large sections at option or paragraph boundaries.
- Repeat the component name and heading path in child chunks.
- Keep examples with explanatory text where possible.
- Store parent-section relationships for contextual expansion.
- Never silently truncate text to fit the embedding model’s input limit.

The final maximum embedding length must follow the selected model’s supported input length. Oversized examples may be stored as linked evidence blocks rather than embedded in full.

### Deduplication

- Deduplicate identical normalized sections within a corpus.
- Preserve separate records when text differs across corpus versions.
- Avoid repeated boilerplate in search results.
- Assign stable IDs derived from corpus identity, source section, and chunk content.

### Required metadata

| Field | Description |
|---|---|
| `chunk_id` | Stable evidence identifier |
| `corpus_id` | Snapshot or release identity |
| `source_url` | Original documentation URL |
| `anchor` | Original section anchor, when present |
| `heading_path` | Hierarchical section titles |
| `component_type` | Filter, encoder, muxer, protocol, global option, etc. |
| `component_name` | Exact name when extractable |
| `option_names` | Options associated with the section |
| `text` | Canonical cleaned content |
| `parent_id` | Parent section |
| `content_hash` | Change detection |
| `version_status` | Pinned release, nightly snapshot, or unknown |

## 7. Retrieval requirements

### Hybrid search

Each search shall:
1. Apply corpus and component filters.
2. Run lexical retrieval through SQLite FTS5.
3. Run semantic retrieval using local embeddings.
4. Combine rankings using reciprocal rank fusion.
5. Boost exact component and option matches.
6. Remove duplicate or substantially overlapping results.
7. Expand selected results with essential parent or adjacent evidence.
8. Return evidence within a caller-defined size budget.

**Initial defaults:** 30 lexical candidates, 30 semantic candidates, and up to eight final evidence chunks.

FFmpeg identifiers such as `-map`, `scale_cuda`, and `h264_nvenc` need explicit exact-match handling; general-purpose tokenization must not erase their distinguishing syntax.

### Related evidence

Retrieval should make it possible to obtain related material without requiring a second LLM inside the service.

For example:
- A filter request can expose related pixel-format and hardware-frame sections.
- A stream-copy request can expose relevant mapping and container sections.
- A timestamp request can expose time syntax and input/output option scope.

Use extracted documentation links and a small, reviewed alias map. Do not attempt a comprehensive hand-built dependency graph in MVP.

### Evidence status

Return:
- `supported`: directly relevant evidence was found.
- `partial`: evidence covers only part of the request.
- `insufficient`: no adequately relevant evidence was found.

Search scores are ranking signals, not calibrated probabilities of command correctness. Thresholds must be tuned against the evaluation set.

## 8. FFmpeg version and build awareness

**Documentation support and installed availability are separate facts.**

The service shall accept a target capability profile containing:
- FFmpeg version output.
- Build configuration.
- Available encoders and decoders.
- Available filters.
- Available muxers, demuxers, and protocols.
- Advertised hardware acceleration methods.
- Optional component-specific help output.

Profiles can be supplied by the assistant or collected using an explicit operator command.

The container may include FFmpeg for collecting **its own** profile, but that profile must never be substituted for the user’s actual target machine.

### Compatibility behavior

- Missing component in a complete capability list → `unavailable`.
- Component listed → `listed_available`.
- No relevant capability data → `unknown`.
- Documentation does not match the target release → explicit version warning.
- Hardware accelerator listed → hardware runtime usability remains unverified.

Presence in a build does not prove GPU drivers, devices, permissions, or runtime resources are usable.

For option-level questions, allow importing output from fixed commands such as `ffmpeg -h filter=loudnorm`. Execute these with argument arrays and `shell=False`; never accept arbitrary shell strings.

## 9. API contract

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Process liveness |
| `GET /readyz` | Active corpus and indexes loaded |
| `GET /v1/corpora` | Corpus versions and ingestion status |
| `POST /v1/search` | Hybrid documentation retrieval |
| `GET /v1/components/{type}/{name}` | Exact component documentation |
| `POST /v1/capabilities` | Register a target build profile |
| `POST /v1/compatibility/check` | Check named components against a profile |

Keep ingestion as an **operator CLI command** in MVP rather than exposing a remote fetch endpoint.

### Search input

Required: `query`.

Optional: `corpus_id`, `component_types`, `component_names`, `target_profile_id`, `top_k`, `max_evidence_chars`.

Use a character budget at the API boundary to avoid pretending the service knows the downstream LLM’s tokenizer. The assistant applies its own final token budget.

### Search output

- Query and selected corpus.
- Evidence status.
- Ranked evidence chunks.
- Section titles and citation URLs.
- Exact component matches.
- Corpus/version warnings.
- Build-availability observations.
- Truncation or omitted-evidence indicators.
- Retrieval timing.

Every citation must resolve to stored evidence and its original source. The API must not invent section anchors.

## 10. Assistant integration contract

The calling assistant remains responsible for interpreting intent and constructing commands.

Recommended flow:
1. Identify the requested transformation.
2. Obtain relevant media metadata through the assistant’s existing inspection tools, if available.
3. Retrieve documentation for the operation and likely components.
4. Check component availability against the target profile.
5. Generate the command with stated assumptions.
6. Explain material choices and cite retrieved sections.
7. Ask for missing information when it changes correctness or risks data loss.

### Command representation

The assistant should produce:
- An argument array as the canonical executable representation.
- A separately rendered command for the user’s selected shell.
- Input/output assumptions.
- Compatibility warnings.
- Documentation citations.

This prevents shell quoting from being confused with FFmpeg’s own filtergraph escaping.

The assistant must distinguish:
- A command supported by retrieved documentation.
- A command checked against build capabilities.
- A command actually executed and tested.

**MVP never claims execution validation.**

## 11. Docker and operational requirements

### Packaging

- One application image and one persistent `/data` volume.
- CPU-only operation by default.
- Non-root runtime user.
- Pinned dependencies and embedding-model revision.
- Read-only root filesystem where practical.
- Temporary storage restricted to `/tmp`.
- Configurable bind address and port.
- No Docker socket mount or privileged mode.

### Lifecycle

- Explicit initialization downloads the embedding model and ingests a selected corpus.
- Normal startup loads existing artifacts and does not require internet access.
- Empty deployments return live but not ready until initialization completes.
- Reingestion writes a new immutable index generation.
- Validate the new generation before atomically activating it.
- Running requests finish against the generation they started with.
- Keep the previous generation for rollback.

### Configuration

Expose a small configuration surface:
- Data directory.
- Active corpus.
- Embedding model and pinned revision.
- Maximum query and evidence sizes.
- Fetch timeouts and size limits.
- Authentication token.
- Log level.

No automatic nightly updates by default. Operators explicitly update and evaluate documentation snapshots.

### Security

- Bind to localhost by default; require authentication when exposed remotely.
- Treat documentation and imported capability text as untrusted data.
- Never execute instructions contained in retrieved documents.
- Never interpolate user strings into a shell.
- Do not fetch media URLs or inspect arbitrary filesystem paths.
- Do not log full queries or capability payloads by default.

## 12. Evaluation and acceptance criteria

Create a version-controlled benchmark of **at least 60 queries**, including expected documentation sections and compatibility outcomes.

Cover:
- Scaling, cropping, padding, and aspect ratio.
- Frame rate and timestamps.
- Trimming and concatenation.
- Audio normalization, resampling, and channel layouts.
- Stream mapping and copying.
- Subtitles.
- Codecs and containers.
- Hardware acceleration and frame transfers.
- Escaping and expressions.
- Missing or nonexistent options.
- Version mismatch and unavailable components.

### MVP release gates

| Area | Acceptance target |
|---|---|
| Ingestion | All allowlisted manuals imported or explicitly reported failed |
| Exact lookup | Correct component ranked first for at least 95% of exact-name cases |
| Retrieval | Recall@8 of at least 90% against labeled relevant sections |
| Citations | Every returned citation traceable to stored source evidence |
| Parsing | All curated syntax/example fixtures preserved without corruption |
| Compatibility | All curated missing-component cases flagged correctly |
| Version labeling | Every result identifies its corpus; nightly never mislabeled as a release |
| Offline operation | Search works without network access after initialization |
| Persistence | Restart preserves corpus and index identity |
| Updates | Failed ingestion leaves the prior active index usable |

**Initial performance target:** warm search p95 below one second on a declared four-core CPU reference machine, measured with a fixed evidence budget and one concurrent client. Record actual memory consumption; use 4 GB RAM as an initial deployment budget, not a measured requirement.

Compare lexical-only retrieval against hybrid retrieval. If embeddings do not materially improve the benchmark, ship lexical retrieval first and avoid the extra operational cost.

### End-to-end assistant evaluation

Separately test generated commands on synthetic media in a disposable, resource-limited environment.

Measure:
- Whether the command completes.
- Whether the output meets the requested transformation.
- Whether stream selection is correct.
- Whether unsupported components are avoided.
- Whether cited documentation supports the choices.

This test environment is a development acceptance harness, not a production command-execution endpoint.

## 13. Delivery milestones

**Milestone 1 — Reliable corpus**

Importer, source snapshots, parser, structured chunks, citations, corpus versioning, and parsing fixtures.

**Milestone 2 — Retrieval API**

Exact component lookup, lexical baseline, semantic retrieval, rank fusion, evidence budgets, and the labeled retrieval benchmark.

**Milestone 3 — Compatibility and integration**

Target capability profiles, compatibility checks, assistant integration contract, and end-to-end synthetic-media tests.

**Milestone 4 — Docker release**

Offline startup, persistence, authentication, health checks, atomic updates, rollback, deployment instructions, and measured performance.

## 14. Main risks

| Risk | Mitigation |
|---|---|
| Current website describes features absent from the target build | Explicit corpus identity plus target capability profiles |
| Chunking separates an option from its caveats | Structural chunks and parent-section expansion |
| Semantic search misses precise option names | Exact lookup and lexical retrieval |
| Correct documentation leads to an incorrect filtergraph | End-to-end execution tests during development |
| Examples are copied without adapting streams or formats | Assistant must state assumptions and use media metadata |
| Long evidence overwhelms the coding assistant | Bounded evidence responses and targeted follow-up lookup |
| Container FFmpeg differs from execution host | Separate, explicitly identified target profiles |

The most valuable design choice here is **keeping retrieval small while making provenance and build compatibility explicit**. That gives your assistant useful evidence without turning the RAG container into another agent or a risky media-execution service.
