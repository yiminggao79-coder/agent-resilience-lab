import unittest
from planner import build_batches


class PlannerTests(unittest.TestCase):
    def test_parallel_roots_and_next_wave(self):
        jobs = {"publish": ["bundle"], "lint": [], "bundle": ["compile"], "compile": []}
        self.assertEqual(build_batches(jobs),
                         {"batches": [["compile", "lint"], ["bundle"], ["publish"]], "blocked": []})

    def test_empty(self):
        self.assertEqual(build_batches({}), {"batches": [], "blocked": []})

    def test_input_ownership(self):
        jobs = {"a": [], "b": ["a"]}
        completed = ["a"]
        self.assertEqual(build_batches(jobs, completed),
                         {"batches": [["b"]], "blocked": []})
        self.assertEqual(jobs, {"a": [], "b": ["a"]})
        self.assertEqual(completed, ["a"])

