import unittest

from ranges import select_ranges


class RangeTests(unittest.TestCase):
    def test_suffix_and_open_end(self):
        self.assertEqual(select_ranges("bytes=-3", 10, 4),
                         {"status": 206, "ranges": [[7, 9]], "selected_bytes": 3})
        self.assertEqual(select_ranges("bytes=7-", 10, 4),
                         {"status": 206, "ranges": [[7, 9]], "selected_bytes": 3})

    def test_limit_is_after_transitive_merge(self):
        self.assertEqual(select_ranges("bytes=8-9,0-2,3-7", 12, 1),
                         {"status": 206, "ranges": [[0, 9]], "selected_bytes": 10})

    def test_unsatisfiable_members_are_discarded(self):
        self.assertEqual(select_ranges("bytes=-0,50-,8-2,1-2", 10, 10),
                         {"status": 206, "ranges": [[1, 2]], "selected_bytes": 2})

    def test_invalid_header_falls_back(self):
        for header in ("items=0-2", "bytes=0-2,", "bytes=0- 2", "bytes=+1-2"):
            with self.subTest(header=header):
                self.assertEqual(select_ranges(header, 5, 3),
                                 {"status": 200, "ranges": [[0, 4]], "selected_bytes": 5})

    def test_empty_asset(self):
        self.assertEqual(select_ranges("bytes=0-", 0, 3),
                         {"status": 416, "ranges": [], "selected_bytes": 0})
        self.assertEqual(select_ranges(None, 0, 3),
                         {"status": 200, "ranges": [], "selected_bytes": 0})


if __name__ == "__main__":
    unittest.main()
