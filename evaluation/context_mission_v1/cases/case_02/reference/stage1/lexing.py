"""Unicode alphanumeric tokens with internal apostrophes."""
import re

def tokens(text):
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text.casefold().replace('’', "'"))
