# Incremental build queue

The build service accepts a dependency graph and requested targets, then computes
a deterministic execution queue. Repair `plan_build(graph, targets, cached)` in
`planner.py`. Use Python 3.9 and only its standard library. All arguments and
results are JSON values.

`graph` maps every known node name to a list of its direct prerequisites. Names
are nonempty ASCII strings. Every prerequisite, target, and cached name is a key
in graph. `targets` and `cached` are lists of names. Duplicate entries in any list
are allowed and have no additional meaning. Cycles and self-dependencies are
allowed. Do not mutate any input, including prerequisite lists.

Return exactly `{"order": [...], "blocked": [...]}` under these rules:

* Start from the requested targets and include their transitive prerequisites.
  A cached node is already satisfied: omit it from the build queue and stop
  traversing its prerequisites through that path. Its prerequisites can still
  be needed through another uncached path. Unrelated graph nodes are ignored.
* Among all included, unbuilt nodes whose prerequisites are satisfied, select
  the lexicographically smallest name next. Repeat until no ready node remains.
  Compare names by ordinary Python string ordering. The ready choice must be
  reconsidered after each emitted node; a newly ready node can precede a node
  that was already ready. Cached prerequisites count as satisfied.
* `order` is that emission sequence. `blocked` is every included, unbuilt node
  that could not be emitted, sorted lexicographically. This includes members of
  prerequisite cycles and nodes depending on those cycles. Continue emitting
  independent ready work even when another requested branch is blocked.
* Empty targets produce two empty lists. Each name occurs at most once in each
  result list. The result must be independent of input dictionary/list ordering.

The supplied README.md and test_planner.py are immutable byte-for-byte. You may
edit implementation files and add files. Add new tests in new files, without
editing or replacing the supplied tests or README. Run public tests with
`python3 -m unittest discover -v` from this directory. No network, fork, external
dependencies, symlinks, or files outside this project are needed.
