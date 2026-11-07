Our parcel manifest overwrites repeated SKU lines and counts distinct SKUs instead of units. Repair order aggregation and the manifest together, preserving original tests/observe.py byte-for-byte.

A catalog is a list of unique nonempty trimmed case-sensitive SKUs with positive integer per-unit weight and volume (booleans invalid). Order lines are a list of known SKUs with positive integer quantity; repeated lines must ADD. Return items sorted by SKU, with exactly sku/quantity/weight/volume, where weight and volume are that SKU's TOTAL measures. totals has units as the sum of quantities and weight/volume as sums of all item totals. Empty lists are valid and yield zero totals.

Retain validation of all catalog products, including unused ones, duplicate catalog IDs, unknown SKUs, missing fields and malformed records. Do not mutate caller-owned records or lists. Add separate regressions as useful and briefly report the implementation and checks run.
