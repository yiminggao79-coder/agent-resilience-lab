"""Validation for the deliberately small integer palette."""
def integer(value, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError('expected an integer in range')
    return value

def color(value):
    integer(value, 0)
    if value > 9:
        raise ValueError('color must be between 0 and 9')
    return value
