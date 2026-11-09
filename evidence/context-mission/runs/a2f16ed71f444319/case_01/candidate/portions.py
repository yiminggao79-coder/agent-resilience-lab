"""Split integer cents among weighted members."""
from validation import integer, weights as valid_weights

def apportion(total, weights):
    integer(total, 'total')
    weights = valid_weights(weights)
    denominator = sum(weights.values())
    magnitude = abs(total)
    names = sorted(weights)
    # Exact weighted quotas are (magnitude * weight) / denominator.  Work in
    # integers only: keep floors and the leftover numerators as remainders.
    floors = {}
    remainders = {}
    for name in names:
        numerator = magnitude * weights[name]
        floors[name] = numerator // denominator
        remainders[name] = numerator % denominator
    leftover = magnitude - sum(floors.values())
    # Hand out the remaining cents by descending remainder, then ascending name.
    for name in sorted(names, key=lambda item: (-remainders[item], item))[:leftover]:
        floors[name] += 1
    if total < 0:
        floors = {name: -share for name, share in floors.items()}
    return {name: floors[name] for name in names}
