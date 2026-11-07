"""Exact integer-cent allocation with largest remainders."""
from validation import integer, weights as valid_weights

def apportion(total, weights):
    integer(total, 'total')
    weights = valid_weights(weights)
    denominator = sum(weights.values())
    magnitude = abs(total)
    result = {name: magnitude * weight // denominator for name, weight in weights.items()}
    remaining = magnitude - sum(result.values())
    order = sorted(weights, key=lambda name: (-(magnitude * weights[name] % denominator), name))
    for name in order[:remaining]:
        result[name] += 1
    sign = -1 if total < 0 else 1
    return {name: result[name] * sign for name in sorted(result)}
