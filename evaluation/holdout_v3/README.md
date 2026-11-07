# Holdout V3 — sealed future coding acceptance

Two freshly authored small stdlib Python coding tasks (one bugfix, one feature).
The coordinating implementation agent must not inspect manifest task bodies,
workspace contents, reference overlays, external oracle code/data, or mutations
before an explicitly authorized one-shot evaluation. Shared filesystem access is
a process boundary by agreement, not cryptographic secrecy.

Independently authored in the same AI environment; not external statistical
independence. No consumed V2 candidate, answer, or result was read to author this
package. All fixtures are synthetic. None has been sent to any model or consumed
by an agent run. Offline reference/baseline/mutation checks are fixture validation,
not model acceptance scores.

The candidate receives only its workspace and TASK.md text. The manifest,
external expectations, references and mutation code remain outside its workspace.
Original public tests and acceptance_probe.py are byte-immutable and must be
host protected for execution as well as edits. Additional tests are allowed.
acceptance_probe observes return value and caller input preservation but contains
no expected answers. The external finite oracle checks exact JSON semantics.

manifest.json retains the existing generic evaluator's case interface:
id/task/workspace_dir/immutable_files/oracle(module,function,inputs,expected).
Extra provenance/reference/constraints fields are descriptive. Current old runner
success also requires coding_cycle; DO NOT use that policy for V3 scoring or
change old V1/V2 semantics. A separately versioned V3 runner must use frozen
observable behavior, immutable hashes and declared constraints as correctness
gates; tool order, plans, Skills and model success claims are not gates.

FREEZE.json covers every other suite file. Do not modify it or covered files once
frozen. self_validate.py defaults to nonmutating validation and no harness/model
imports; run with Python -I -B. Its --write-report mode is allowed only before
FREEZE exists. Any oracle correction after exposure requires a new suite identity
and must preserve this original package and failed attempt.

Before future one-shot use, verify freeze and original-test hashes, record the
actual evaluated committed runtime/evaluator source hashes and provider settings,
enforce candidate confinement/private oracle separation, and confirm existing
native sandbox availability. Declared baseline is commit
324e457897914d5b6007a87a86829650da68c439 as supplied by the coordinator, not a claim
that changing workspace sources were that tree. Any later runtime is a separately
recorded evaluation implementation. No model run is authorized by this package.

