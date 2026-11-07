"""Split integer cents among weighted members."""
from validation import integer, weights as valid_weights

def apportion(total, weights):
    integer(total, 'total')
    weights = valid_weights(weights)
    denominator = sum(weights.values())
    # The current implementation loses rounding remainders.
    return {name: total * weights[name] // denominator for name in sorted(weights)}
