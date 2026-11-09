# SDD ledger — plan: docs/superpowers/plans/2026-11-07-harness.md
Pre-flight: tasks 1→2→3 share Tools intent dict and Store snapshot/event API; signatures agree. Store.create in plan replaced by Engine.create + Store.save to avoid duplicate creation API.
Task 1: Ruling: permit canonical system symlink ancestors for state path (macOS /var), reject state-file symlink itself; tool paths retain component rejection. Cost if wrong: malicious state parent substitution is outside cooperative single-user threat model.
Task 1: complete (commits 9b2753b..9b2753b, tests: python3 -m unittest discover -s tests/dev -v → OK)
Task 1: commit correction: implementation is 3cd6ca0 (task-done ran immediately before commit; first range was empty).
Task 2: Ruling: use child-process HTTP wall timeout and no implicit proxy; local HTTP tests require sandbox escalation for loopback binding only. Cost if wrong: proxy-only environments must supply an accessible endpoint.
Task 2: complete (commits 3cd6ca0..2d3f456, tests: python3 -m unittest discover -s tests/dev -v → OK)
Final: fixed malformed provider usage and unexpected model_pending exception accounting — malformed usage tests and exception safety test RED→GREEN, suite 46/46.
Final: fixed provider body impersonating trusted retry/error envelope — provider-body test RED→GREEN, suite 46/46.
Final: fixed filesystem tools lacking wall timeout — real subprocess timeout test RED→GREEN, suite 46/46.
Final: Ruling: same-root cooperative locks do not cover overlapping nested roots; documented disjoint workspace requirement. Cost if wrong: concurrent nested runs can conflict or overwrite in a misconfigured deployment.
Final: Ruling: no real-provider or billing claims without credentials; live eval remains opt-in. Cost if wrong: real endpoint compatibility may fail on first live run.
Final: Ruling: no hostile FS race, OS sandbox, power-loss durability or human audit claim; POSIX local prototype only. Cost if wrong: use outside scope can expose data or lose state.
Final review: independent read-only AI reviewer on 9b2753b..2fbb9fc; three Important fixed in 5b9990b, no deferred minors. Acceptance is a distinct frozen-implementation phase, not a second review/fix loop.
Task 3: in progress — cross-process experiments, docs, review fixes complete; waiting only on independent acceptance. Implementation frozen at 5b9990b, 46/46 final development tests.
Task 3: complete (implementation 2d3f456..5b9990b; final tests: python3 -m unittest discover -s tests -v → 56/56, evidence/final-all-tests.txt; independent acceptance 10/10; frozen hashes unchanged).
Final: real model execution remains unmeasured, explicitly supported offline fallback per user instruction. New standalone repository kept on main locally; no base branch to merge, remote publication not requested.
