# Batch planner
This small stdlib-only library is being extended to plan dependency batches.
planner.build_batches(jobs, completed=None) is the intended public API.
graph.py provides a detached dependency mapping, and waves.py groups computed ranks.
The tests folder contains the original public contract tests.

