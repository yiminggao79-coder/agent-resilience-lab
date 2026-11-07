import copy
import unittest

from planner import plan_build


class BuildQueueTests(unittest.TestCase):
    def test_newly_ready_work_competes_lexically(self):
        graph = {"z": [], "a": ["b"], "b": []}
        self.assertEqual(plan_build(graph, ["z", "a"], []),
                         {"order": ["b", "a", "z"], "blocked": []})

    def test_cached_branch_prunes_a_cycle(self):
        graph = {"app": ["sdk"], "sdk": ["loop"], "loop": ["loop"]}
        self.assertEqual(plan_build(graph, ["app"], ["sdk"]),
                         {"order": ["app"], "blocked": []})

    def test_duplicate_dependencies_and_targets(self):
        graph = {"app": ["lib", "lib"], "lib": []}
        self.assertEqual(plan_build(graph, ["app", "app"], []),
                         {"order": ["lib", "app"], "blocked": []})

    def test_independent_work_survives_cycle(self):
        graph = {"a": ["b"], "b": ["a"], "c": ["a"], "d": []}
        self.assertEqual(plan_build(graph, ["c", "d"], []),
                         {"order": ["d"], "blocked": ["a", "b", "c"]})

    def test_inputs_are_preserved(self):
        graph, targets, cached = {"x": ["y", "y"], "y": []}, ["x"], []
        before = copy.deepcopy((graph, targets, cached))
        plan_build(graph, targets, cached)
        self.assertEqual((graph, targets, cached), before)


if __name__ == "__main__":
    unittest.main()
