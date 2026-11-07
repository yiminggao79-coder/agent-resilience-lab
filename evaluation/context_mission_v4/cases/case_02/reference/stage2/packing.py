"""First-fit decreasing under weight and volume limits."""
from catalog import products
from orders import quantities
from boxes import validate
def pack(catalog,lines,boxes):
    entries=products(catalog)
    counts=quantities(lines,entries)
    box_types=validate(boxes)
    units=[(entries[sku]['weight'],entries[sku]['volume'],sku,index)
           for sku,quantity in counts.items() for index in range(quantity)]
    units.sort(key=lambda item:(-item[0],-item[1],item[2],item[3]))
    cartons=[]
    for weight,volume,sku,index in units:
        chosen=None
        for carton in cartons:
            if carton['weight']+weight<=carton['type']['max_weight'] and carton['volume']+volume<=carton['type']['max_volume']:
                chosen=carton
                break
        if chosen is None:
            box=next((box for box in box_types if weight<=box['max_weight'] and volume<=box['max_volume']),None)
            if box is None: raise ValueError('unit cannot fit any box')
            chosen={'type':box,'weight':0,'volume':0,'items':{}}
            cartons.append(chosen)
        chosen['weight']+=weight
        chosen['volume']+=volume
        chosen['items'][sku]=chosen['items'].get(sku,0)+1
    return [{'box':carton['type']['code'],'items':[{'sku':sku,'quantity':q} for sku,q in sorted(carton['items'].items())],
             'weight':carton['weight'],'volume':carton['volume']} for carton in cartons]
