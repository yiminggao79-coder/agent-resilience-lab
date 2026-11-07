import copy
import unittest

from dispatch import plan_dispatch


class DispatchTests(unittest.TestCase):
    def test_combines_normalized_lines(self):
        result = plan_dispatch({"BOLT": 8}, [{
            "order_id": "sample", "lines": [
                {"sku": " bolt ", "quantity": 2},
                {"sku": "BOLT", "quantity": 3},
            ],
        }])
        self.assertEqual(result["remaining"], {"BOLT": 3})
        self.assertEqual(result["orders"][0]["items"], [{"sku": "BOLT", "quantity": 5}])
        self.assertEqual(result["orders"][0]["status"], "reserved")

    def test_rejected_order_does_not_reduce_later_availability(self):
        result = plan_dispatch({"A": 3, "B": 1}, [
            {"order_id": "first", "lines": [{"sku": "A", "quantity": 2}, {"sku": "B", "quantity": 2}]},
            {"order_id": "second", "lines": [{"sku": "A", "quantity": 3}]},
        ])
        self.assertEqual([item["status"] for item in result["orders"]], ["rejected", "reserved"])
        self.assertEqual(result["remaining"], {"A": 0, "B": 1})

    def test_reports_all_shortages_and_does_not_mutate_inputs(self):
        stock = {"A": 0}
        orders = [{"order_id": "sample", "lines": [{"sku": "Z", "quantity": 2}, {"sku": "A", "quantity": 1}]}]
        original = copy.deepcopy((stock, orders))
        result = plan_dispatch(stock, orders)
        self.assertEqual(result["orders"][0]["shortages"], [
            {"sku": "A", "requested": 1, "available": 0},
            {"sku": "Z", "requested": 2, "available": 0},
        ])
        self.assertEqual((stock, orders), original)


if __name__ == "__main__":
    unittest.main()
