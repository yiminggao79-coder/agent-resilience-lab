"""Unambiguous fully parenthesized canonical expressions."""
def render(node):
    kind=node[0]
    if kind in ('number','variable'): return str(node[1])
    if kind=='neg': return '(-'+render(node[1])+')'
    if kind=='pow': return '('+render(node[1])+'^'+str(node[2])+')'
    symbol={'add':'+','sub':'-','mul':'*','div':'/'}[kind]
    return '('+render(node[1])+symbol+render(node[2])+')'
