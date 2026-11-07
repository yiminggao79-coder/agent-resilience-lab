import copy
import unittest

from usage_report import report_usage


class UsageReportTests(unittest.TestCase):
    def test_nested_session_does_not_shorten_coverage(self):
        records = [
            {"device": "pump", "start": "2025-01-01T10:00:00Z", "end": "2025-01-01T10:20:00Z"},
            {"device": "pump", "start": "2025-01-01T10:05:00Z", "end": "2025-01-01T10:10:00Z"},
        ]
        self.assertEqual(report_usage(records, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z"), [
            {"device": "pump", "busy_seconds": 1200, "span_count": 1},
        ])

    def test_touching_sessions_form_one_span(self):
        records = [
            {"device": "fan", "start": "2025-01-01T10:10:00Z", "end": "2025-01-01T10:20:00Z"},
            {"device": "fan", "start": "2025-01-01T10:00:00Z", "end": "2025-01-01T10:10:00Z"},
        ]
        original = copy.deepcopy(records)
        self.assertEqual(report_usage(records, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z"), [
            {"device": "fan", "busy_seconds": 1200, "span_count": 1},
        ])
        self.assertEqual(records, original)

    def test_clips_single_record_to_window(self):
        records = [{"device": "sensor", "start": "2025-01-01T09:00:00Z", "end": "2025-01-01T12:00:00Z"}]
        self.assertEqual(report_usage(records, "2025-01-01T10:00:00Z", "2025-01-01T11:00:00Z"), [
            {"device": "sensor", "busy_seconds": 3600, "span_count": 1},
        ])


if __name__ == "__main__":
    unittest.main()
