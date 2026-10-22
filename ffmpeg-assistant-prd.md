# Product Requirements Document: AI FFmpeg Command Assistant

**Version:** 1.0  
**Status:** Draft  
**Product scope:** Natural-language instructions → validated FFmpeg commands

## 1. Product overview

Build an assistant that translates plain-language video transformation requests into FFmpeg commands. Users describe an edit, provide input and output file references, and receive a copyable command with a short explanation and explicit assumptions.

The product will use an LLM to produce a constrained, structured editing plan. A deterministic command builder will validate that plan and translate it into FFmpeg arguments.

**Key architectural decision:** The LLM interprets intent; application code constructs commands. Model-generated shell commands will never be treated as trusted executable output.

Example request:

> Resize to 720p, cut from 00:10 to 00:30, make it black and white, and save as MP4.

Expected response:

- A validated FFmpeg command.
- A summary of the requested transformations.
- Any defaults or assumptions, such as aspect-ratio behavior and encoding settings.
- A warning if compatibility depends on the user’s FFmpeg installation.

## 2. Problem statement

FFmpeg supports extensive media processing capabilities, but its command syntax is difficult to learn and easy to misuse. Users commonly struggle with option placement, codec/container compatibility, filter ordering, stream mapping, and quoting.

Existing general-purpose AI assistants can suggest plausible commands without consistently checking these constraints.

This product must make common transformations accessible while making unsupported requests, ambiguity, and validation limitations visible.

## 3. Goals and non-goals

### Goals

1. Translate common natural-language video editing requests into correct commands.
2. Support multiple transformations in a single request.
3. Prevent invented flags, unsupported filters, and unsafe command construction.
4. Explain defaults, trade-offs, and compatibility issues.
5. Provide a small, extensible implementation that is easy to test.
6. Establish measurable quality before investing in model fine-tuning.

### Non-goals for the MVP

- Executing commands or processing uploaded media on the server.
- Training a foundation model.
- Supporting every FFmpeg option or filter.
- Building a timeline-based video editor.
- Supporting arbitrary user-supplied shell commands or filter expressions.
- Automatically installing FFmpeg or downloading media.
- Batch orchestration, live streaming, or distributed transcoding.
- Guaranteeing compatibility with unknown FFmpeg builds.

## 4. Target users and use cases

| User | Need | Example |
|---|---|---|
| Developer | Generate commands for scripts and applications | “Convert this MOV to MP4 using H.264.” |
| Content creator | Perform common edits without learning FFmpeg | “Add my logo to the bottom-right corner.” |
| Technical operator | Avoid repeatedly assembling complex commands | “Trim 20 seconds, resize to 720p, and remove audio.” |

The MVP targets technically comfortable users who can run FFmpeg locally.

## 5. Scope

### MVP capabilities

| Capability | Supported behavior |
|---|---|
| Resize | Set height or dimensions; preserve aspect ratio by default |
| Trim | Specify start/end or start/duration |
| Container conversion | MP4, MOV, MKV, and WebM through supported encoding presets |
| Video encoding | H.264, H.265, and VP9 through an explicit compatibility matrix |
| Grayscale | Apply a black-and-white video filter |
| Watermark | Add one static image with a preset position and margin |
| Audio handling | Preserve through compatible encoding, or remove audio |
| Composition | Combine supported operations in one request |
| Explanation | Describe operations, defaults, and compatibility notes |
| Revision | Modify the previous structured plan from follow-up instructions |

The MVP supports one primary video input and one optional watermark image. It selects the first video stream and, when retained, the first audio stream. Additional streams are excluded with a visible notice.

### Deferred capabilities

Subtitles, concatenation, transitions, multiple watermarks, arbitrary cropping, speed changes, audio mixing, HDR workflows, hardware acceleration, and batch processing.

## 6. User experience

### Primary flow

1. User enters a transformation request.
2. User supplies the main input filename and desired output filename.
3. User supplies a watermark filename when applicable.
4. The assistant extracts a structured editing plan.
5. The application validates the plan and resolves defaults.
6. If essential information is missing, the assistant asks a focused clarification question.
7. The application builds the FFmpeg argument array.
8. The UI displays the command, explanation, assumptions, and validation status.
9. User copies the command or revises the request.

Filename references are text only in the MVP; files are not uploaded.

### Clarification policy

Use documented defaults for low-risk choices. Ask when alternatives materially change the edit or output.

| Request | Expected behavior |
|---|---|
| “Resize to 720p” | Set output height to 720; preserve aspect ratio and choose an encoder-compatible width |
| “Make it smaller” | Ask whether the user means dimensions or file size |
| “Cut from 10 to 30 seconds” | Start at 10 seconds; output duration is 20 seconds |
| “Add a watermark” | Ask for the watermark filename if missing |
| “Convert to MP4 with H.265” | Use the supported H.265/MP4 preset and disclose encoder requirements |
| “Compress without losing quality” | Explain the distinction between lossless encoding and visually similar lossy encoding; ask which is intended |

A 720p height request does not imply padding or cropping to exactly 1280 × 720.

### Output requirements

Every successful result includes:

- A shell-specific copyable command.
- A machine-readable argument array.
- A plain-language edit summary.
- Applied defaults and warnings.
- Validation status.

The interface must distinguish **statically validated** from **tested against a specific FFmpeg build**. Generated commands must not be labeled execution-verified unless they were actually tested.

## 7. Functional requirements

### FR-1: Natural-language interpretation

The parser must:

- Identify all supported operations in a request.
- Extract timestamps, dimensions, codecs, containers, watermark position, and audio preferences.
- Interpret negation and revisions, such as “keep the audio” or “use H.264 instead.”
- Detect unsupported operations without silently dropping them.
- Return structured output conforming to a versioned schema.
- Preserve meaningful transformation ordering.
- Return clarification requirements when intent cannot be safely resolved.

### FR-2: Structured editing plan

The plan must describe user intent independently of FFmpeg syntax.

Example:

```json
{
  "schema_version": "1.0",
  "input_id": "main",
  "output": {
    "container": "mp4",
    "video_codec": "h264",
    "audio_mode": "encode"
  },
  "trim": {
    "start_seconds": 10,
    "duration_seconds": 20
  },
  "operations": [
    {
      "type": "resize",
      "height": 720,
      "aspect_ratio": "preserve"
    },
    {
      "type": "grayscale"
    }
  ]
}
```

File paths must be bound by the application through a separate asset map. The model references asset identifiers rather than constructing shell-ready paths.

Schema constraints must include:

- Explicit operation and codec allowlists.
- Positive, bounded dimensions.
- Nonnegative start times and positive durations.
- Mutually exclusive end-time and duration representations after normalization.
- Rejection of unknown fields.
- No raw FFmpeg flags or arbitrary filter expressions.
- Limits on the number of operations.

### FR-3: Capability registry

Maintain a version-controlled structured registry containing:

- Supported operations and parameter schemas.
- Deterministic mappings to FFmpeg options and filters.
- Encoder names and encoding presets.
- Supported codec/container combinations.
- Stream selection and audio behavior.
- Defaults, restrictions, and explanatory text.
- Documentation provenance and tested FFmpeg versions/builds.

The registry is the source of truth for **product-supported capabilities**. Actual encoder and filter availability depends on the installed FFmpeg build.

A knowledge graph is unnecessary for the MVP. A small structured registry and explicit builder functions are sufficient.

### FR-4: Deterministic command generation

The command builder must:

- Produce an argument array before rendering a command string.
- Place input and output options correctly.
- Build simple filter chains for single-input transformations.
- Build labeled filter graphs for watermark composition.
- Explicitly map the intended video and optional audio streams.
- Re-encode video whenever filters require decoded frames.
- Prevent incompatible stream-copy and filtering combinations.
- Apply encoding presets compatible with the selected container.
- Avoid overwriting existing outputs by default.
- Reject an output path that resolves to a known input path.
- Never invent paths or silently change requested codecs.

For the MVP, trimming prioritizes accurate re-encoded output over fast stream-copy cuts.

Watermark defaults: bottom-right, 16-pixel margin, original image size, applied after resizing. The command must retain the watermark throughout the selected video interval without making output duration depend on a single-frame image input.

### FR-5: Validation

Validation must occur at distinct levels:

| Level | Checks |
|---|---|
| Schema | Types, required fields, enums, bounds, unknown fields |
| Semantic | Valid trim range, operation interactions, meaningful dimensions |
| Capability | Supported codecs, filters, containers, and combinations |
| Construction | Correct mappings, argument placement, filter graph generation |
| Optional local preflight | File existence, stream metadata, installed encoder/filter availability |
| CI execution | Actual output from generated commands using controlled fixtures |

The MVP cannot confirm local file existence, duration, or installed encoders from filenames alone. It must disclose checks that require local metadata.

### FR-6: Explanations and revisions

Explanations must be derived from the validated plan and builder decisions.

Follow-up requests must modify the previous plan while preserving unaffected operations. The complete updated plan must pass validation again before a replacement command is returned.

### FR-7: Feedback

Users must be able to:

- Mark a result as useful or incorrect.
- Select an issue category.
- Optionally submit an FFmpeg error message.
- Consent separately to using their request for evaluation or training.

The service must not require media uploads for feedback.

## 8. AI and knowledge strategy

### Initial approach

Use a capable pretrained LLM with schema-constrained output and examples covering supported transformations. A separate NLU model is unnecessary initially; the LLM performs intent extraction directly.

**Fine-tuning is a later optimization, contingent on evaluation results.** First establish whether prompting, constrained output, and deterministic validation meet product targets.

### Documentation use

Curate official FFmpeg documentation for supported operations, including:

- Input/output option semantics.
- Seeking and duration behavior.
- Stream mapping.
- Scaling, grayscale, and overlay filters.
- Encoders, pixel formats, and container constraints.

Documentation informs the registry and test cases. RAG may support explanations or future capability expansion, but retrieved text must never automatically expand the executable allowlist.

### Fine-tuning criteria

Consider fine-tuning when:

- Intent extraction misses launch targets after prompt iteration.
- Repeated domain-specific failures are identifiable.
- A curated dataset has sufficient coverage.
- Cost or latency provides a measurable business reason.

Training examples should map requests to structured plans, clarification decisions, and unsupported-operation responses. Raw command strings should not be the primary training target.

Split evaluation data by request pattern and operation combination to reduce leakage from near-duplicate synthetic examples.

## 9. Technical architecture

### Processing pipeline

**User request → LLM parser → schema validation → semantic validation → capability resolution → deterministic builder → shell renderer → response**

### Recommended MVP stack

| Component | Recommendation |
|---|---|
| Backend | Python with FastAPI |
| Schemas | Pydantic |
| LLM integration | One provider adapter supporting structured output |
| Capability registry | Versioned JSON plus small Python builder functions |
| Command representation | `list[str]` |
| Testing | pytest and pinned FFmpeg CI images |
| UI | Simple request form, clarification flow, and command output |
| Storage | None required for basic generation; minimal persistence for consented feedback |

Avoid a vector database, knowledge graph, agent framework, and multiple specialized models until measured needs justify them.

### API contract

`POST /v1/commands`

Input:

- Natural-language instruction.
- Asset map containing local file references.
- Output filename.
- Target shell.
- Optional previous plan for revisions.
- Optional locally collected media and FFmpeg capability metadata.

Response:

- `status`: `ready`, `needs_clarification`, or `unsupported`.
- Validated plan, when available.
- Argument array and rendered command when ready.
- Explanation.
- Applied defaults.
- Warnings and validation results.
- Clarification questions, when needed.

Malformed API requests return validation errors. Ambiguous editing requests return a normal structured clarification response.

The initial copyable command renderer supports POSIX shells. Other platforms can consume the argument array; Windows shell rendering is deferred and must not be represented as supported.

## 10. Security and privacy

- Do not execute generated commands in the MVP service.
- Never concatenate model output into a shell command.
- Treat instructions, retrieved documents, and filenames as untrusted input.
- Bind local filenames outside the model and normalize relative paths so filenames cannot be interpreted as options.
- Reject URL/protocol inputs in the MVP.
- Use a tested shell renderer for display; a subprocess integration must use argument arrays with shell execution disabled.
- Do not allow arbitrary flags, filter expressions, or executable selection.
- Avoid logging full paths or user prompts by default.
- Redact sensitive paths from submitted diagnostics where possible.
- If hosted execution is introduced later, require isolated workers, restricted network access, protocol allowlists, and strict resource limits.

## 11. Nonfunctional requirements

| Area | MVP requirement |
|---|---|
| Correctness | Deterministic builder output for the same normalized plan |
| Maintainability | New operations require registry/schema updates, builder logic, and tests |
| Latency | Provisional p95 under 8 seconds for generation, excluding clarification |
| Observability | Track parsing, validation, provider latency, and error categories |
| Reliability | Timeouts and provider errors produce clear responses without fabricated commands |
| Versioning | Version schemas, prompts, registry entries, and evaluation datasets |
| Cost | Track model cost per successful generation; establish a budget after benchmarking |

## 12. Testing and quality targets

### Unit tests

Cover timestamp normalization, dimensions, codec/container compatibility, operation ordering, stream mapping, audio behavior, shell quoting, and revision merging.

### Integration tests

Use small controlled fixtures covering:

- Video with and without audio.
- Portrait and landscape dimensions.
- Odd dimensions.
- Variable frame rate.
- Filenames with spaces, quotes, and shell metacharacters.
- Transparent watermark images.
- Combined resize, trim, grayscale, and watermark operations.

Run generated commands against pinned FFmpeg builds and inspect outputs with ffprobe. Use visual checks for grayscale and watermark placement; successful execution alone does not establish a correct edit.

### Evaluation targets

These are proposed launch gates, not measured results.

| Metric | Target |
|---|---|
| Schema-valid parser output | At least 99% on the supported benchmark |
| Intent and parameter accuracy | At least 95% using normalized semantic comparison |
| Required clarification detection | At least 95% on a labeled ambiguity set |
| Execution success | At least 98% on valid supported CI fixtures |
| Critical deterministic regression tests | 100% passing |
| Unsafe argument injection | Zero successful cases in the defined adversarial suite |
| Silent omission | Zero silent omissions in the unsupported/mixed-operation acceptance suite |

The benchmark should include at least 300 human-reviewed requests with single operations, compositions, paraphrases, ambiguity, and adversarial inputs.

## 13. Acceptance scenarios

1. **Resize:** “Resize to 720p” generates a height-based scale operation that preserves aspect ratio.
2. **Trim:** “Cut from 00:10 to 00:30” produces a 20-second interval starting at 10 seconds, subject to frame/sample granularity.
3. **Encoding:** “Convert to MP4 using H.265” selects the supported encoder preset and reports build requirements.
4. **Grayscale:** “Make it black and white” produces a grayscale video transformation while retaining audio according to policy.
5. **Watermark:** A supplied image appears at the chosen position throughout the output.
6. **Composition:** A combined request produces one coherent command with all supported operations.
7. **Ambiguity:** “Make it smaller” produces a clarification rather than an arbitrary edit.
8. **Invalid range:** An end time before the start time is rejected.
9. **Missing audio:** A fixture without audio processes successfully when optional audio retention is requested.
10. **Unsupported operation:** The assistant identifies the unsupported portion and does not present a partial command as complete.
11. **Unsafe filename:** Shell metacharacters remain filename content and cannot introduce additional commands.
12. **Revision:** “Use H.264 instead” changes the codec while preserving the existing trim and filters.

## 14. Delivery plan

| Milestone | Deliverables | Exit criterion |
|---|---|---|
| 1. Foundation | Schemas, capability registry, presets, builder | Supported structured plans generate correct commands in unit tests |
| 2. Intent parsing | LLM adapter, constrained output, clarification behavior | Initial language benchmark passes agreed thresholds |
| 3. Product interface | API, request UI, command display, revisions | Complete end-to-end user flow |
| 4. Hardening | Execution tests, adversarial suite, privacy controls, telemetry | Launch acceptance gates pass |
| 5. Optimization | Expanded evaluation, optional RAG or fine-tuning experiment | Demonstrated quality, cost, or latency improvement |

**Planning estimate:** 4–6 weeks for one experienced developer, assuming a hosted LLM, no server-side media execution, and the defined MVP scope.

## 15. Principal risks

| Risk | Mitigation |
|---|---|
| Plausible but incorrect model interpretation | Structured plans, semantic validation, clarification, benchmark coverage |
| FFmpeg build differences | Pinned CI builds and explicit encoder requirements |
| Scope expands toward all of FFmpeg | Supported-operation allowlist and deliberate capability releases |
| Commands execute but edits are wrong | Media-property and visual assertions |
| Quoting or protocol injection | Argument arrays, controlled path binding, no raw expressions or remote inputs |
| Premature fine-tuning increases complexity | Require measured failures and comparative evaluation first |

The first release should prove one reliable path: **understand a bounded set of edits, validate the intent, and generate commands deterministically.** Expansion should follow tested capabilities rather than the breadth of commands an LLM can suggest.