Implement planner.build_batches(jobs, completed=None), a dependency batch planner using only the Python standard library.

jobs maps each unique string job name to a list of dependency names. completed is an optional list of job names already finished; None means no completed jobs. Return {"batches": list-of-lists, "blocked": list}. Omit completed jobs from both outputs. Each batch contains all remaining jobs whose dependencies were either completed before the call or scheduled in an earlier batch; jobs in the same batch do not satisfy one another. Sort names ascending within every batch. Emit batches until no further job is ready, then return all remaining job names in blocked, sorted ascending. A dependency name absent from jobs is unresolved unless it is in completed. Cycles, self-dependencies, and jobs downstream of unresolved dependencies therefore remain blocked. Repeated dependency names have no extra effect. Duplicate completed names and completed names absent from jobs are allowed.

Do not mutate jobs, its dependency lists, or completed. Inputs otherwise follow the types above. Preserve the public function signature and original tests byte-for-byte. You may add tests and modify implementation files.
Keep acceptance_probe.py byte-for-byte unchanged; it is a supplied test observation adapter, not implementation code.

