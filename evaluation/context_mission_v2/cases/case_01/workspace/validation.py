"""Small explicit validators for integer-cent records."""
def integer(value, label, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(label + ' must be an integer in range')
    return value

def member(value):
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError('member must be nonempty trimmed text')
    return value

def weights(values):
    if not isinstance(values, dict) or not values:
        raise ValueError('weights must be a nonempty dictionary')
    owned = {member(name): integer(weight, 'weight', 0) for name, weight in values.items()}
    if not sum(owned.values()):
        raise ValueError('at least one weight must be positive')
    return owned
