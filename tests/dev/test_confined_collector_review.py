"""One independent candidate-only validation regression; no native execution."""
import unittest
from unittest.mock import patch

from agent_lab import confined_collector as candidate
from agent_lab.tools import ToolError


class ConfinedCollectorReviewTests(unittest.TestCase):
    def test_unrepresentable_integer_limits_use_fixed_error_before_any_admission(self):
        cases=[('positive_timeout',{'timeout':10**500}),
               ('negative_timeout',{'timeout':-(10**500)}),
               ('positive_cleanup',{'timeout':1,'cleanup_timeout':10**500}),
               ('negative_cleanup',{'timeout':1,'cleanup_timeout':-(10**500)})]
        for label,limits in cases:
            with self.subTest(limit=label), \
                 patch.object(candidate,'owner_dead',side_effect=AssertionError('no owner query')) as owner, \
                 patch.object(candidate,'current_tracker',side_effect=AssertionError('no tracker admission')) as tracker, \
                 patch.object(candidate.subprocess,'Popen',side_effect=AssertionError('no process launch')) as launch:
                caught=None
                try:
                    candidate.collect([],'.',{},**limits)
                except Exception as error:
                    caught=error
                owner.assert_not_called(); tracker.assert_not_called(); launch.assert_not_called()
                self.assertIsInstance(caught,ToolError)
                self.assertEqual(caught.code,'invalid_collector_limits')
