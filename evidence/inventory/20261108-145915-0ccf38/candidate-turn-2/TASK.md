Repair this small stdlib-only inventory allocation project. Inspect its modules
and tests, make the necessary changes, and verify the saved code by running tests.

`allocate(stock, orders)` must return `{"remaining": stock_after, "results": rows}`.
Each order has a unique string `id` and an `items` list of dictionaries with a
string `sku` and positive integer `qty`. Stock maps SKU strings to nonnegative
integers. Inputs meet these types. Preserve case-sensitive SKU names.

Process orders in arrival order. Combine repeated SKU lines within each order.
An order is accepted only when every combined quantity is available; an unknown
SKU counts as unavailable. Rejection consumes no stock. Each result row is
exactly `{"id": order_id, "accepted": bool}`. Empty orders are accepted. Preserve
all original stock keys, including zero counts. Do not mutate any caller input.
Preserve the API and byte contents of `tests/test_original.py`. You may add tests.
