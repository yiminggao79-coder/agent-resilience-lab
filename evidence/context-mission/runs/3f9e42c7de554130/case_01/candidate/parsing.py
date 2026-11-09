"""Recursive expression parser with a deliberately small power grammar.

Precedence, from loosest to tightest: + - (left associative), * / (left
associative), unary + -, and finally a power suffix that binds a single atom.
Because unary signs sit above power, -2^2 is -(2^2) while (-2)^2 has a
parenthesized negative base.  Only one signed decimal integer literal of
magnitude at most 12 is allowed as the exponent.
"""
from lexing import tokenize
from nodes import number,variable
def parse(text):
    tokens=tokenize(text)
    index=0
    def peek(): return tokens[index] if index<len(tokens) else None
    def take():
        nonlocal index
        token=peek()
        if token is None: raise ValueError('unexpected end')
        index+=1
        return token
    def atom():
        token=take()
        if token=='(':
            result=expression()
            if take()!=')': raise ValueError('missing closing parenthesis')
            return result
        if token.isdecimal(): return number(int(token))
        if token[0].isalpha() or token[0]=='_': return variable(token)
        raise ValueError('expected atom')
    def power():
        # A power suffix applies to the atom alone, before any outer sign.
        result=atom()
        if peek()=='^':
            take()
            sign=-1 if peek()=='-' else 1
            if peek() in ('+','-'): take()
            exponent=take()
            if not exponent.isdecimal(): raise ValueError('power needs integer literal')
            exponent=sign*int(exponent)
            if abs(exponent)>12: raise ValueError('power exponent out of range')
            result=('pow',result,exponent)
        return result
    def unary():
        # Signs repeat and bind more loosely than the power suffix.
        if peek() in ('+','-'):
            sign=take()
            child=unary()
            return ('neg',child) if sign=='-' else child
        return power()
    def term():
        result=unary()
        while peek() in ('*','/'):
            operation=take()
            result=('mul' if operation=='*' else 'div',result,unary())
        return result
    def expression():
        result=term()
        while peek() in ('+','-'):
            operation=take()
            result=('add' if operation=='+' else 'sub',result,term())
        return result
    result=expression()
    if peek() is not None: raise ValueError('trailing tokens')
    return result
