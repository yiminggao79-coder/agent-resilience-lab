import inspect
import unittest

import acceptance_probe
from planner import build_batches


class EdgeCaseTests(unittest.TestCase):
    def test_signature_preserved(self):
        self.assertEqual(list(inspect.signature(build_batches).parameters), ["jobs", "completed"])

    def test_cycle_blocked(self):
        jobs = {"a": ["b"], "b": ["a"], "c": []}
        self.assertEqual(build_batches(jobs), {"batches": [["c"]], "blocked": ["a", "b"]})

    def test_self_dependency_blocked(self):
        self.assertEqual(build_batches({"a": ["a"]}), {"batches": [], "blocked": ["a"]})

    def test_unresolved_dependency_and_downstream(self):
        jobs = {"a": ["missing"], "b": ["a"], "c": []}
        self.assertEqual(build_batches(jobs), {"batches": [["c"]], "blocked": ["a", "b"]})

    def test_unresolved_dependency_satisfied_by_completed(self):
        jobs = {"a": ["missing"], "b": ["a"]}
        self.assertEqual(build_batches(jobs, ["missing"]),
                         {"batches": [["a"], ["b"]], "blocked": []})

    def test_repeated_dependencies(self):
        jobs = {"a": [], "b": ["a", "a", "a"]}
        self.assertEqual(build_batches(jobs), {"batches": [["a"], ["b"]], "blocked": []})

    def test_completed_duplicates_and_unknown(self):
        jobs = {"a": [], "b": ["a"]}
        completed = ["a", "a", "zzz"]
        self.assertEqual(build_batches(jobs, completed),
                         {"batches": [["b"]], "blocked": []})
        self.assertEqual(completed, ["a", "a", "zzz"])

    def test_same_batch_does_not_satisfy(self):
        jobs = {"a": ["b"], "b": ["c"], "c": []}
        self.assertEqual(build_batches(jobs),
                         {"batches": [["c"], ["b"], ["a"]], "blocked": []})

    def test_all_completed(self):
        self.assertEqual(build_batches({"a": []}, ["a"]), {"batches": [], "blocked": []})

    def test_completed_none_and_empty_list(self):
        jobs = {"a": []}
        self.assertEqual(build_batches(jobs, None), {"batches": [["a"]], "blocked": []})
        self.assertEqual(build_batches(jobs, []), {"batches": [["a"]], "blocked": []})

    def test_inputs_not_mutated(self):
        jobs = {"publish": ["bundle"], "lint": [], "bundle": ["compile"], "compile": []}
        snapshot = {k: list(v) for k, v in jobs.items()}
        build_batches(jobs, [])
        self.assertEqual(jobs, snapshot)

    def test_probe_inputs_unchanged(self):
        jobs = {"a": ["b"], "b": ["c"], "c": [], "d": ["d"]}
        observed = acceptance_probe.observe(jobs, ["c"])
        self.assertTrue(observed["inputs_unchanged"])
        self.assertEqual(observed["result"], {"batches": [["b"], ["a"]], "blocked": ["d"]})


if __name__ == "__main__":
    unittest.main()
