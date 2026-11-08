# Mission context source study

Read date: 2026-11-09 (workspace date). Research only: pinned upstream bodies were
read through connected GitHub file tools, not executed. No upstream tests or model
requests were run. This worker did not change runtime, tests, mission or private
experiment records.

Baseline observed: `codex-reconstruction@d47ecfa4b7ab6c8f681ca51eb4dd7cedbf3ecde1`;
`AUTONOMOUS_MISSION.md` was untracked. This report supports the mission and does
not claim its acceptance gates passed.

All links are fixed to `openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`.
Manifest records returned Git
blob IDs, inspected and exported ranges, and evidence-file SHA-256s. Excerpts
retain unchanged public source text in local JSON wrappers.

## Local input, overflow and summary

**Fact.** [compact.rs L264–407](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/compact.rs#L264) clones session history,
records synthetic summary input on the clone and projects a new normalized prompt
for each attempt. It attaches executed-tool metadata and determines base
instructions from history or the session fallback, with an incremental-tools
special case. One model client session survives retries.

On `ContextWindowExceeded`, projected input longer than one item causes removal of
the oldest history item, retry-counter reset and another attempt. At the one-item
boundary it marks total tokens full and returns the error. Interrupted/aborted,
session-budget, incomplete-response and fatal errors return directly; other
failures use configured bounded retry/backoff. This is reactive overflow handling,
not preselection of a largest budget-fitting prefix.

**Fact.** [L856–954](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/compact.rs#L856) buffers output per attempt and
requires response completion; early EOF fails. Non-post-turn completion records
buffered output with compaction metadata; post-turn returns it in the response.
Completion usage is recorded and incomplete-response errors have a separate usage
path. These checks do not prove semantic summary fidelity.

**Fact.** [L409–476](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/compact.rs#L409) builds replacement from a fresh
session history snapshot, retained user text, summary and canonical current
context. Post-turn requires nonempty assistant summary; other phases take last
assistant text from session history or default empty. Pending mail is selected
from the final trimmed compactor clone so an overflow retry cannot restore
discarded pending input. General retained-user collection instead uses the fresh
session snapshot.

[L625–656 and L734–848](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/compact.rs#L625) exclude recognized previous
summary messages from retained-user collection. Retention walks newest-first under
20,000 approximate tokens, can truncate a boundary message and mark its sources
incomplete, then appends a contextual user compaction summary. Byte-for-byte
retention of every user message is not the pinned local behavior.

## Pairs and normalized prompt views

**Fact.** [history.rs L857–869](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs#L857) removes
the front item, its corresponding counterpart, the usage boundary and world-state
baseline. [normalize.rs L243–342](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L243)
removes the first matching function/custom/tool-search call or output counterpart;
shell calls pair with function outputs. This operates on individual response
items, not this project's multi-call assistant batches. It does not establish
duplicate-ID validation.

**Fact.** [history.rs L704–737, L1170–1186](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs#L704)
normalizes an owned prompt projection and projects media before returning
envelopes/items. [normalize.rs L21–241](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L21)
constructs missing function/custom/shell outputs as `aborted`; missing tool-search
outputs become empty completed client results. Custom/shell missing outputs invoke
`error_or_panic` unless marked inherited before fallback construction. Orphan
paired outputs also invoke that diagnostic. Therefore arbitrary malformed history
is not unconditionally repaired in every build configuration. Outputs without a
paired call ID can stand alone; server tool-search output has an orphan exception.
Synthetic output IDs derive stably from source item IDs where present.

**Inference.** Shape repair and stable IDs protect request validity/cache behavior.
An `aborted` synthetic output does not establish whether a side effect occurred.
Whole-batch prefix selection and explicit malformed-pair rejection remain
independent local contracts.

## Separate remote fitting mechanism

**Fact.** [compact_remote_v2_attempt.rs L95–146](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/compact_remote_v2_attempt.rs#L95)
clones foreground history, invokes the imported trimming helper and produces
normalized annotated input. Following that actual import yielded
[compact_remote_history.rs L77–209](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/compact_remote_history.rs#L77),
blob `3a6f1500f3933ac893a46c0be2cf37bbbc1e07e5`. It estimates base-plus-history
tokens, walks backward through trailing groups while over budget and rewrites
eligible outputs to a truncation marker or empty tool-search tools. Output IDs,
metadata and function/custom success fields survive. Attached resize notices are
grouped with their source. It stops on a nonrewritable group or feature-protected
retained output, so fitting is not guaranteed.

**Inference.** Remote preflight output reduction differs from local reactive
oldest-item removal. Neither establishes our proposed byte-prefix summarizer or
exact provider-token accounting.

## Installation and replay boundaries

**Fact.** [session/mod.rs L4778–4923](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L4778) checks captured
prefix generation, length and auth owner immediately before replacing a prefix
and retaining its unchanged suffix. Complete replacements preserve goal edits
accepted after their input snapshot. Missing item IDs are assigned before the
persisted replacement clone. Live history replacement precedes checkpoint
persistence. Compaction records carry replacement history, retained/review
context, window/response identities and resume metadata, accompanied by
world-state and turn-context records.

[persist_rollout_items L5310–5318](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L5310) returns false on
append failure, while the inspected replacement caller still returns true.
Installation's boolean therefore does not acknowledge an atomic durable commit.
Append implementation, fsync and arbitrary crash behavior were not traced or
executed here.

**Fact.** [rollout_reconstruction.rs L62–93](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/rollout_reconstruction.rs#L62)
lets only the newest compaction bound initial replay input. Replacement history,
window number and newer resume metadata are required, with a paginated-mode
exception for metadata. An incomplete newest compaction falls back to all supplied
items rather than an older initial bound. The later reverse scan selects surviving
state and handles rollback; initial input bounding must not be confused with every
subsequent checkpoint-selection step.

[L519–598](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/rollout_reconstruction.rs#L519) restores the selected
replacement and retained/review context, then replays its suffix. Legacy
compactions without replacement history rebuild user text plus summary while
preserving separately retained facts; stale reference context is cleared for
canonical reinjection. This is model-history reconstruction, not external effect
reconciliation.

## Independent choices and verification implications

**Local choices from AUTONOMOUS_MISSION.md:** reuse Python byte accounting; select
the largest complete fitting projected prefix; include previous summary and whole
request-envelope cost; retain newest complete batch and original user requirements;
advance an archive cursor only after successful installation; reject indivisible
or no-progress input; preserve the archive and public-projection exclusions.
These are not copied or mechanically translated upstream algorithms.

**Inference.** Identity hashes prove the chosen source and installed text survived,
not that summary meaning is correct. This project's SQLite transaction and owned
SIGKILL/new-PID continuation must supply local durability evidence. Source research
cannot satisfy real Coding gates or guarantee external exactly-once effects.
Unknown effects still pause.

## Attribution

Upstream is OpenAI Codex, Copyright 2025 OpenAI, Apache License 2.0.
Exact pinned [LICENSE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/LICENSE) blob:
`4606e72e042564097e8780d66c1d4dcb611869bd`.
Exact pinned [NOTICE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/NOTICE) blob:
`2805899d56d0332d175cfc613c67d45d6f006db7`.
Both are reproduced beside excerpts, including the upstream Ratatui notice.
JSON selection/wrappers, manifest and this prose are local research artifacts.
No upstream implementation was copied into runtime. No source-execution, parity,
live-summary-quality or external exactly-once claim is made.
