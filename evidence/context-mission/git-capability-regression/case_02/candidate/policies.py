"""Validation and compilation of co-packing policies."""
from boxes import validate as validate_boxes
from catalog import products

_KEYS = frozenset(("incompatible", "max_units", "fixed_box"))


def _catalog_entries(catalog):
    if catalog is None:
        return None
    if isinstance(catalog, dict):
        return catalog
    if isinstance(catalog, list):
        return products(catalog)
    raise ValueError("catalog must be product rows or a product mapping")


def _box_codes(boxes):
    if boxes is None:
        return None
    if isinstance(boxes, list):
        return {box["code"] for box in validate_boxes(boxes)}
    raise ValueError("boxes must be box rows")


def compile(policy, catalog=None, boxes=None):
    """Return an owned, normalized policy, or None for no policy.

    ``catalog`` may be the raw catalog rows or the validated product mapping.
    ``boxes`` may be raw box rows or already-validated box definitions.
    """
    if policy is None:
        return None
    if not isinstance(policy, dict):
        raise ValueError("policy must be a dictionary or None")

    unknown = set(policy) - _KEYS
    if unknown:
        raise ValueError("unknown policy key")

    entries = _catalog_entries(catalog)
    box_codes = _box_codes(boxes)

    incompatible = set()
    if "incompatible" in policy:
        pairs = policy["incompatible"]
        if not isinstance(pairs, list):
            raise ValueError("incompatible must be a list")
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("each incompatible entry must be a two-SKU list")
            first, second = pair
            if not isinstance(first, str) or not isinstance(second, str):
                raise ValueError("incompatible SKUs must be strings")
            if first == second:
                raise ValueError("incompatible SKUs must be different")
            if entries is not None and (first not in entries or second not in entries):
                raise ValueError("incompatible SKU is not a known product")
            incompatible.add(frozenset((first, second)))

    max_units = policy.get("max_units")
    if max_units is not None and (type(max_units) is not int or max_units <= 0):
        raise ValueError("max_units must be a positive integer or None")

    fixed_box = {}
    if "fixed_box" in policy:
        choices = policy["fixed_box"]
        if not isinstance(choices, dict):
            raise ValueError("fixed_box must be a dictionary")
        for sku, code in choices.items():
            if not isinstance(sku, str) or not isinstance(code, str):
                raise ValueError("fixed_box SKU and box code must be strings")
            if entries is not None and sku not in entries:
                raise ValueError("fixed_box SKU is not a known product")
            if box_codes is not None and code not in box_codes:
                raise ValueError("fixed_box code is not a known box")
            fixed_box[sku] = code

    return {
        "incompatible": incompatible,
        "max_units": max_units,
        "fixed_box": fixed_box,
    }


def validate(policy, catalog=None, boxes=None):
    """Validate and compile a policy.  ``None`` remains ``None``."""
    return compile(policy, catalog, boxes)
