# Public synthetic multi-turn coding exercise

All files here are newly AI-authored synthetic data/code. No customer repository,
personal record, secret or held-out V3 artifact is used. This entire example may
be transmitted to the authorized official DeepSeek API for development testing.
It is public development material, not an independent acceptance benchmark.

Turn 1: ask the agent to inspect `TASK.md` and repair the project. Preserve the
original test file bytes. Turn 2 is described outside the initial workspace in
`FOLLOWUP.md`; only copy `workspace` and `TASK.md` into the model workspace first.
Observe actual saved code with fresh sandboxed tests after each turn. Model
completion and a chosen tool sequence do not decide correctness.

Intentional baseline defects: repeated SKU quantities are not combined before
checking, rejected orders can consume stock, and caller-owned stock is mutated.
No implementation answer is supplied. Preserve the first attempt and failures.
