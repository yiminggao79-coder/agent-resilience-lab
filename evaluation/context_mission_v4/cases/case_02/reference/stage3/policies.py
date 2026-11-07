"""Validated co-packing restrictions, independent of order arrival."""
from catalog import name,integer
def compile_policy(policy,products,boxes):
    if policy is None: policy={}
    if not isinstance(policy,dict) or set(policy)-{'incompatible','max_units','fixed_box'}: raise ValueError('invalid policy')
    pairs=policy.get('incompatible',[])
    if not isinstance(pairs,list): raise ValueError('incompatible must be a list')
    incompatible=set()
    for pair in pairs:
        if not isinstance(pair,list) or len(pair)!=2: raise ValueError('incompatible pair must have two SKUs')
        left,right=name(pair[0]),name(pair[1])
        if left==right or left not in products or right not in products: raise ValueError('invalid incompatible pair')
        incompatible.add(frozenset([left,right]))
    cap=policy.get('max_units')
    if cap is not None: integer(cap)
    fixed=policy.get('fixed_box',{})
    if not isinstance(fixed,dict): raise ValueError('fixed_box must be a dictionary')
    codes={box['code'] for box in boxes}
    copied={}
    for sku,code in fixed.items():
        name(sku);name(code)
        if sku not in products or code not in codes: raise ValueError('unknown fixed SKU or box')
        copied[sku]=code
    return incompatible,cap,copied
