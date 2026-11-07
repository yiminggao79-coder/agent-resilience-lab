Add co-packing restrictions, keeping earlier behavior unchanged when no policy is supplied. Create policies.py to validate/compile policies, extend packing.pack(catalog, lines, boxes, policy=None), and add manifest.constrained(catalog, lines, boxes, policy=None). Preserve protected original bytes.

A policy is None or a dictionary with ONLY these optional keys:
- incompatible: a list of two-SKU lists. Both SKUs must be different known products. A pair is symmetric; repeated/reversed pairs are harmless. The two SKUs cannot share a carton.
- max_units: None or a positive integer cap on the TOTAL number of units in one carton, booleans invalid.
- fixed_box: a dictionary from known SKU to known box code. A unit of that SKU may be packed only in that exact type. Other SKUs remain free to share it if their own rules allow.

Validate every restriction before packing, even for empty orders. Keep the same unit order and first-fit scan; an existing carton must satisfy capacity, pair compatibility, unit cap and the incoming SKU's fixed-box requirement. New cartons use the same sorted box order, filtered by fixed-box requirement and unit fit. An impossible unit raises ValueError. constrained returns the same report shape as packed; packed still uses the unrestricted behavior. Preserve every input object. Add tests covering conflicts, combined unit caps, fixed-box reuse, incompatible fixed choices and invalid unused policies.
