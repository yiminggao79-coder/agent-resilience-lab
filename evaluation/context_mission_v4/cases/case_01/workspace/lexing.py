"""ASCII names, decimal integers, and expression punctuation."""
import re
def tokenize(text):
    if not isinstance(text,str): raise ValueError('expression must be text')
    output=[]
    offset=0
    while offset<len(text):
        if text[offset].isspace():
            offset+=1
            continue
        found=re.match(r'[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[()+*/^\-]',text[offset:])
        if found is None: raise ValueError('invalid expression character')
        output.append(found.group())
        offset+=len(found.group())
    return output
