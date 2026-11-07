"""Body text tokenization."""
def tokens(text):
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    return text.lower().split()
