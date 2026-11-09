"""Deterministic first-fit packing into scalar weight/volume cartons."""
from boxes import validate as validate_boxes
from catalog import products
from orders import quantities
from policies import compile as compile_policy


def _carton_fits(carton, sku, weight, volume, rules):
    box = carton["box"]
    if (carton["weight"] + weight > box["max_weight"] or
            carton["volume"] + volume > box["max_volume"]):
        return False
    if rules is None:
        return True

    max_units = rules["max_units"]
    if max_units is not None and carton["units"] + 1 > max_units:
        return False

    fixed = rules["fixed_box"].get(sku)
    if fixed is not None and box["code"] != fixed:
        return False

    incompatible = rules["incompatible"]
    if any(frozenset((sku, other)) in incompatible
           for other in carton["quantities"]):
        return False
    return True


def _new_box_fits(box, sku, weight, volume, rules):
    if weight > box["max_weight"] or volume > box["max_volume"]:
        return False
    if rules is None:
        return True
    fixed = rules["fixed_box"].get(sku)
    return fixed is None or box["code"] == fixed


def pack(catalog, lines, boxes, policy=None):
    entries = products(catalog)
    counts = quantities(lines, entries)
    box_types = validate_boxes(boxes)
    rules = None if policy is None else compile_policy(policy, entries, box_types)

    # Process each indivisible unit by descending weight, descending volume,
    # then ascending SKU.  Units of one SKU are interchangeable, so repeating
    # the SKU in that order is equivalent to expanding them before sorting.
    ordered_skus = sorted(
        counts,
        key=lambda sku: (-entries[sku]["weight"], -entries[sku]["volume"], sku),
    )

    cartons = []
    for sku in ordered_skus:
        weight = entries[sku]["weight"]
        volume = entries[sku]["volume"]
        for _ in range(counts[sku]):
            for carton in cartons:
                if _carton_fits(carton, sku, weight, volume, rules):
                    carton["weight"] += weight
                    carton["volume"] += volume
                    carton["units"] += 1
                    carton["quantities"][sku] = carton["quantities"].get(sku, 0) + 1
                    break
            else:
                for box in box_types:
                    if _new_box_fits(box, sku, weight, volume, rules):
                        cartons.append({
                            "box": box,
                            "weight": weight,
                            "volume": volume,
                            "units": 1,
                            "quantities": {sku: 1},
                        })
                        break
                else:
                    raise ValueError("unit does not fit any box")

    return [
        {
            "box": carton["box"]["code"],
            "items": [
                {"sku": sku, "quantity": quantity}
                for sku, quantity in sorted(carton["quantities"].items())
            ],
            "weight": carton["weight"],
            "volume": carton["volume"],
        }
        for carton in cartons
    ]
