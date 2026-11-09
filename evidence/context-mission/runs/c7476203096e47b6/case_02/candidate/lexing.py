"""Body text tokenization.

A token is a maximal run of Unicode alphanumeric characters, optionally joined
by internal straight apostrophes between such runs. Underscores, hyphens and
other punctuation separate tokens. Text is normalized first: U+2019 becomes a
straight apostrophe and the text is casefolded, so returned tokens are already
in normalized form.

Combining marks (Unicode category M*, e.g. the dot casefold leaves after
"\u0130") attach to the preceding alphanumeric run so they never split a token;
decorations without a preceding run are punctuation and separate tokens.
"""
import unicodedata

_APOSTROPHE = "'"
_CURLY_APOSTROPHE = '\u2019'

def _is_run_char(char):
    if char.isalnum():
        return True
    return unicodedata.category(char).startswith('M')


def normalize(text):
    """Return the normalized form used for tokens and queries."""
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    return text.replace(_CURLY_APOSTROPHE, _APOSTROPHE).casefold()


def tokens(text):
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    normalized = text.replace(_CURLY_APOSTROPHE, _APOSTROPHE).casefold()
    result = []
    current = []
    # pending_apostrophe tracks an internal apostrophe that is kept only if a
    # single run character follows it; leading/trailing and doubled
    # apostrophes act as separators.
    pending_apostrophe = False
    for char in normalized:
        if char.isalnum():
            if pending_apostrophe:
                current.append(_APOSTROPHE)
                pending_apostrophe = False
            current.append(char)
        elif _is_run_char(char):
            # Combining mark: only continues an existing run.
            if current:
                if pending_apostrophe:
                    result.append(''.join(current))
                    current = []
                    pending_apostrophe = False
                    continue
                current.append(char)
            else:
                pending_apostrophe = False
        elif char == _APOSTROPHE:
            if current and not pending_apostrophe:
                pending_apostrophe = True
            else:
                if current:
                    result.append(''.join(current))
                    current = []
                pending_apostrophe = False
        else:
            if current:
                result.append(''.join(current))
                current = []
            pending_apostrophe = False
    if current:
        result.append(''.join(current))
    return result
