"""Fresh synthetic v4 author source, never a coding-model payload."""
import json
from pathlib import Path
import textwrap
ROOT=Path(__file__).resolve().parent

def put(name,text):
    path=ROOT/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip('\n'))
def install(case,where,mapping):
    for name,text in mapping.items(): put(f'cases/{case}/{where}/{name}',text)

A={
 'nodes.py': '''
    """Immutable expression tuples and names."""
    import re
    def identifier(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value):
            raise ValueError('invalid identifier')
        return value
    def number(value): return ('number',value)
    def variable(value): return ('variable',identifier(value))
    def variables(node):
        kind=node[0]
        if kind=='number': return set()
        if kind=='variable': return {node[1]}
        if kind in ('neg','pow'): return variables(node[1])
        return variables(node[1])|variables(node[2])
 ''',
 'lexing.py': r'''
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
 ''',
 'parsing.py': '''
    """Recursive expression parser with a deliberately small power grammar."""
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
        def signed_atom():
            if peek() in ('+','-'):
                sign=take()
                child=signed_atom()
                return ('neg',child) if sign=='-' else child
            return atom()
        def power():
            # Current precedence wrongly includes the sign in the base.
            result=signed_atom()
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
        def unary(): return power()
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
 ''',
 'evaluation.py': '''
    """Exact rational expression evaluation."""
    from fractions import Fraction
    from nodes import identifier
    def value(raw):
        if type(raw) is int: return Fraction(raw)
        if isinstance(raw,list) and len(raw)==2 and all(type(v) is int for v in raw) and raw[1]!=0:
            return Fraction(raw[0],raw[1])
        raise ValueError('value must be integer or rational pair')
    def environment(values):
        if not isinstance(values,dict): raise ValueError('values must be a dictionary')
        return {identifier(name):value(raw) for name,raw in values.items()}
    def evaluate(node,values):
        env=environment(values)
        def run(node):
            kind=node[0]
            if kind=='number': return Fraction(node[1])
            if kind=='variable':
                if node[1] not in env: raise ValueError('unbound variable')
                return env[node[1]]
            if kind=='neg': return -run(node[1])
            if kind=='pow':
                base=run(node[1])
                if not base and node[2]<0: raise ValueError('zero negative power')
                return base**node[2]
            left,right=run(node[1]),run(node[2])
            if kind=='add': return left+right
            if kind=='sub': return left-right
            if kind=='mul': return left*right
            if kind=='div':
                if not right: raise ValueError('division by zero')
                # Current quotient discards fractional results, despite the exact API.
                return Fraction(left//right)
            raise ValueError('unknown expression node')
        return run(node)
    def pair(value): return [value.numerator,value.denominator]
 ''',
 'rendering.py': '''
    """Unambiguous fully parenthesized canonical expressions."""
    def render(node):
        kind=node[0]
        if kind in ('number','variable'): return str(node[1])
        if kind=='neg': return '(-'+render(node[1])+')'
        if kind=='pow': return '('+render(node[1])+'^'+str(node[2])+')'
        symbol={'add':'+','sub':'-','mul':'*','div':'/'}[kind]
        return '('+render(node[1])+symbol+render(node[2])+')'
 ''',
 'api.py': '''
    """Public JSON-ready expression calculations."""
    from parsing import parse
    from evaluation import evaluate,pair
    from rendering import render
    def calculate(text,values):
        tree=parse(text)
        return {'canonical':render(tree),'value':pair(evaluate(tree,values))}
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from api import calculate
    from parsing import parse
    from rendering import render
    class OriginalContracts(unittest.TestCase):
        def test_exact_division(self):
            self.assertEqual(calculate('1/2+1/3',{})['value'],[5,6])
        def test_power_before_unary(self):
            self.assertEqual(calculate('-2^2',{}),{'canonical':'(-(2^2))','value':[-4,1]})
        def test_parenthesized_negative(self):
            self.assertEqual(calculate('(-2)^2',{})['value'],[4,1])
        def test_left_associativity(self):
            self.assertEqual(calculate('8/4/2',{})['value'],[1,1])
        def test_negative_exponent(self):
            self.assertEqual(calculate('2^-2',{})['value'],[1,4])
        def test_variables(self):
            self.assertEqual(calculate('x*y',{'x':[2,3],'y':9})['value'],[6,1])
        def test_owned_inputs(self):
            env={'x':[1,2]}
            before=copy.deepcopy(env)
            calculate('x+1',env)
            self.assertEqual(env,before)
        def test_unbound_and_zero(self):
            for text in ['x','1/0','0^-1']:
                with self.assertRaises(ValueError): calculate(text,{})
        def test_invalid_syntax(self):
            for text in ['', '2^13','x^y','1+','a.b','2^2^2']:
                with self.assertRaises(ValueError): parse(text)
        def test_render(self):
            self.assertEqual(render(parse('a*(b+3)')),'(a*(b+3))')
    if __name__=='__main__': unittest.main()
 ''',
 'observe.py': '''
    """Protected observations; finite answers exist only outside the workspace."""
    import copy
    def _observe(payload,action):
        before=copy.deepcopy(payload)
        try: result=action()
        except (ValueError,KeyError,ImportError,AttributeError) as exc: result={'error':type(exc).__name__}
        return {'result':result,'preserved':payload==before}
    def first(payload):
        def action():
            from api import calculate
            return calculate(payload['expression'],payload.get('values',{}))
        return _observe(payload,action)
    def second(payload):
        def action():
            from api import slope
            report=slope(payload['expression'],payload.get('values',{}),payload['variable'])
            from differentiation import derivative
            from parsing import parse
            from evaluation import evaluate,pair
            direct=pair(evaluate(derivative(parse(payload['expression']),payload['variable']),payload.get('values',{})))
            return {'report':report,'direct_consistent':report['slope']==direct}
        return _observe(payload,action)
    def third(payload):
        def action():
            from api import reduced
            report=reduced(payload['expression'],payload.get('values',{}),payload['variable'])
            from simplification import reduce
            from parsing import parse
            from rendering import render
            return {'report':report,'direct_consistent':report['canonical']==render(reduce(parse(payload['expression'])))}
        return _observe(payload,action)
    def fourth(payload):
        def action():
            from api import workbook_report
            report=workbook_report(payload['formulas'],payload['inputs'],payload['outputs'],payload['wrt'])
            from workbook import run
            direct=run(payload['formulas'],payload['inputs'],payload['outputs'],payload['wrt'])
            return {'report':report,'direct_consistent':all(report[key]==direct[key] for key in ('order','results'))}
        return _observe(payload,action)
 ''',
 'README.md': '''
    # Small exact expression engine

    Stdlib-only expression tuples, rational calculations and canonical rendering.
    Numbers are integer literals; environment values are integers or [numerator,
    nonzero denominator] pairs. The supported operators are + - * / unary signs,
    parentheses and a single signed integer literal power suffix per atom, with
    exponent magnitude at most 12. Fully parenthesized output is intentional.
    No service or file storage is used. Preserve caller inputs and the original
    tests/observe.py bytes; separate added tests are welcome. Run python3 -m
    unittest discover -s tests.
 ''',
}
A1={
 'parsing.py':A['parsing.py'].replace('            # Current precedence wrongly includes the sign in the base.\n            result=signed_atom()', '            result=atom()').replace('        def unary(): return power()', "        def unary():\n            if peek() in ('+','-'):\n                sign=take()\n                child=unary()\n                return ('neg',child) if sign=='-' else child\n            return power()"),
 'evaluation.py':A['evaluation.py'].replace("                # Current quotient discards fractional results, despite the exact API.\n                return Fraction(left//right)","                return left/right"),
}
A2={
 'differentiation.py': '''
    """Structural exact symbolic differentiation over immutable tuples."""
    from nodes import number,identifier
    def derivative(node,variable):
        identifier(variable)
        def diff(node):
            kind=node[0]
            if kind=='number': return number(0)
            if kind=='variable': return number(int(node[1]==variable))
            if kind=='neg': return ('neg',diff(node[1]))
            if kind in ('add','sub'): return (kind,diff(node[1]),diff(node[2]))
            if kind=='mul':
                left,right=node[1:]
                return ('add',('mul',diff(left),right),('mul',left,diff(right)))
            if kind=='div':
                left,right=node[1:]
                return ('div',('sub',('mul',diff(left),right),('mul',left,diff(right))),('pow',right,2))
            if kind=='pow':
                base,exponent=node[1:]
                if exponent==0: return number(0)
                return ('mul',('mul',number(exponent),('pow',base,exponent-1)),diff(base))
            raise ValueError('unknown expression node')
        return diff(node)
 ''',
 'api.py':A['api.py']+'''
    from differentiation import derivative
    def slope(text,values,variable):
        tree=parse(text)
        original=evaluate(tree,values)
        return {'value':pair(original),'slope':pair(evaluate(derivative(tree,variable),values))}
 ''',
}
A3={
 'simplification.py': '''
    """Conservative identities preserve evaluation-domain requirements."""
    from evaluation import evaluate
    from nodes import variables
    def reduce(node):
        kind=node[0]
        if kind in ('number','variable'): return node
        if kind=='neg':
            child=reduce(node[1])
            if not variables(child):
                folded=-evaluate(child,{})
                if folded.denominator==1: return ('number',folded.numerator)
                return ('div',('number',folded.numerator),('number',folded.denominator))
            if child[0]=='neg': return child[1]
            return ('neg',child)
        if kind=='pow':
            base=reduce(node[1])
            result=('pow',base,node[2])
            if not variables(base):
                folded=evaluate(result,{})
                if folded.denominator==1: return ('number',folded.numerator)
                return ('div',('number',folded.numerator),('number',folded.denominator))
            return base if node[2]==1 else result
        left,right=reduce(node[1]),reduce(node[2])
        result=(kind,left,right)
        if not variables(result):
            folded=evaluate(result,{})
            if folded.denominator==1: return ('number',folded.numerator)
            return ('div',('number',folded.numerator),('number',folded.denominator))
        zero=('number',0)
        one=('number',1)
        if kind=='add':
            if left==zero: return right
            if right==zero: return left
        if kind=='sub' and right==zero: return left
        if kind=='mul':
            if left==one: return right
            if right==one: return left
        if kind=='div' and right==one: return left
        return result
 ''',
 'api.py':A2['api.py']+'''
    from simplification import reduce
    from nodes import variables
    def reduced(text,values,variable):
        original=parse(text)
        evaluate(original,values)
        tree=reduce(original)
        return {'canonical':render(tree),'variables':sorted(variables(tree)),
                'value':pair(evaluate(tree,values)),'slope':pair(evaluate(derivative(tree,variable),values))}
 ''',
}
A4={
 'workbook.py': '''
    """Formula dependency evaluation and exact chain-rule sensitivities."""
    from fractions import Fraction
    from nodes import identifier,variables
    from parsing import parse
    from evaluation import environment,evaluate,pair
    from differentiation import derivative
    def run(formulas,inputs,outputs,wrt):
        if not isinstance(formulas,dict): raise ValueError('formulas must be a dictionary')
        base=environment(inputs)
        identifier(wrt)
        if wrt not in base: raise ValueError('wrt must name an input')
        trees={identifier(name):parse(text) for name,text in formulas.items()}
        if set(trees)&set(base): raise ValueError('formula shadows an input')
        names=set(trees)|set(base)
        if not isinstance(outputs,list) or any(not isinstance(v,str) or v not in names for v in outputs) or len(set(outputs))!=len(outputs):
            raise ValueError('outputs must be unique known names')
        dependencies={name:variables(tree) for name,tree in trees.items()}
        if any(not deps<=names for deps in dependencies.values()): raise ValueError('unknown formula variable')
        order=[]
        pending=set(trees)
        while pending:
            ready=sorted(name for name in pending if not (dependencies[name]&pending))
            if not ready: raise ValueError('formula cycle')
            chosen=ready[0]
            order.append(chosen)
            pending.remove(chosen)
        values=dict(base)
        sensitivities={name:Fraction(int(name==wrt)) for name in base}
        for name in order:
            env={key:pair(value) for key,value in values.items()}
            tree=trees[name]
            values[name]=evaluate(tree,env)
            sensitivities[name]=sum((evaluate(derivative(tree,dep),env)*sensitivities[dep]
                                     for dep in sorted(dependencies[name])),Fraction(0))
        return {'order':order,'results':[{'name':name,'value':pair(values[name]),
                     'derivative':pair(sensitivities[name])} for name in outputs]}
 ''',
 'api.py':A3['api.py']+'''
    def workbook_report(formulas,inputs,outputs,wrt):
        from workbook import run
        report=run(formulas,inputs,outputs,wrt)
        report['expressions']={name:render(reduce(parse(formulas[name]))) for name in sorted(formulas)}
        return report
 ''',
}
install('case_01','workspace',A)
for stage,overlay in enumerate([A1,A2,A3,A4],1): install('case_01',f'reference/stage{stage}',overlay)
put('cases/case_01/turn1.md','''
The exact expression engine truncates division and handles unary signs at the wrong precedence. Repair the parser and evaluator together, preserving the original tests and observe.py byte-for-byte.

Keep + and - left associative below * and /, also left associative. A power applies to its atom BEFORE an outer unary sign: -2^2 means -(2^2), while (-2)^2 uses a negative base. Unary signs can repeat and can precede parenthesized expressions. A power suffix is ^ followed by an optional sign and decimal integer literal, magnitude at most 12; chained powers and variable/expression exponents remain invalid. Preserve existing ASCII name/integer tokenization and invalid syntax checks.

Evaluate using exact rational arithmetic, never floor or floating division. Inputs are integers or [integer numerator, nonzero integer denominator], with booleans invalid. Division by zero, a zero base to a negative power, invalid environments and unbound variables raise ValueError. Define 0^0 as 1 after evaluating its base. Keep fully parenthesized canonical rendering and every previous validation. Public APIs must preserve caller input. Add separate regression tests as useful and briefly report what changed and what you checked.
''')
put('cases/case_01/turn2.md','''
Add exact symbolic sensitivities. Create differentiation.py with derivative(node, variable) over the existing immutable AST tuples and extend api.py with slope(text, values, variable). Preserve Turn 1, protected original tests, and observe.py.

The variable must be a valid existing identifier spelling, but need not occur in the expression. Use the usual structural rules: constants differentiate to 0, the selected variable to 1 and other variables to 0; unary/add/sub distribute; use the product and quotient rules; a literal power u^n differentiates as n*u^(n-1)*u', with n=0 directly producing 0. Derived AST exponents may go beyond the parser's input limit and must still evaluate exactly. Do not mutate or share mutable state through ASTs.

slope returns {"value": [numerator,denominator], "slope": [numerator,denominator]}. Evaluate the ORIGINAL expression first, so differentiating a constant-looking expression cannot hide a division by zero or missing binding. Then evaluate its symbolic derivative under the same environment. Return reduced rational pairs with positive denominators. Keep all existing syntax and environment errors. Exercise products, quotients, negative powers, unrelated variables and undefined original expressions in separate added tests.
''')
put('cases/case_01/turn3.md','''
Add conservative simplification and integrate it with the value/slope API. Create simplification.py with reduce(node), then add api.reduced(text, values, variable). Keep both earlier Turns and all protected original bytes.

Recursively simplify children first. Fold fully constant operations using exact arithmetic; represent a noninteger constant as ('div',('number',numerator),('number',positive_denominator)), and integers as number nodes. Fold constant powers and unary negations, and remove double negation. Apply ONLY these symbolic identities: 0+x=x, x+0=x, x-0=x, 1*x=x, x*1=x, x/1=x and x^1=x. Do not distribute, combine like terms, cancel x-x, remove a zero multiplier or replace nonconstant x^0: those rewrites could erase required evaluation of an undefined subexpression. Invalid fully constant operations raise ValueError. Preserve all variable/domain requirements.

api.reduced evaluates the original first, simplifies it, and returns {"canonical": fully_parenthesized_reduced_expression, "variables": sorted_distinct_names_in_reduced_AST, "value": exact_pair, "slope": exact_pair}. Use the earlier evaluator and symbolic derivative for the reduced tree. Previous calculate/slope outputs remain unchanged. Add tests for exact fractional constants, identities, retained zero products, double negation and original-domain errors.
''')
put('cases/case_01/turn4.md','''
Build a small named-formula workbook on the expression, derivative and simplification modules. Add workbook.py with run(formulas, inputs, outputs, wrt), plus api.workbook_report with the same arguments. Preserve all three earlier Turns and protected original bytes.

formulas maps valid identifier names to expression texts; inputs is the existing rational environment. Names cannot overlap. Every formula may refer to inputs or other formulas. Parse and validate ALL formulas, rejecting any unknown variable or dependency cycle even if no requested output uses it. outputs is a list of distinct known input/formula names, kept in caller order. wrt must name an existing input. Invalid structures raise ValueError. Compute a deterministic topological order by repeatedly choosing the lexicographically smallest currently ready formula. Evaluate every formula in that order, including unused formulas; an undefined unused formula still raises ValueError.

Compute exact total derivatives with respect to wrt through all named dependencies: input sensitivities are 1 for wrt and 0 otherwise; each formula sensitivity is the sum over its direct referenced names of its symbolic partial derivative evaluated at current values times that dependency's sensitivity. Reuse the prior AST, evaluator and differentiation APIs. run returns {"order": formula_names_in_evaluation_order, "results": [{"name", "value": exact_pair, "derivative": exact_pair} in outputs order]}.

workbook_report additionally includes "expressions": a sorted-name map to each formula's conservatively simplified canonical text using Turn 3. Keep every input object unchanged on success and failure. Add tests for chains, shared dependencies, requested inputs, deterministic order, cycles, unknown names and unused undefined formulas.
''')

# Each expectation below is directly specified, not obtained from a reference run.
def O(value): return {'result':value,'preserved':True}
def oracle(function,pairs): return {'module':'observe','function':function,'inputs':[[p] for p,e in pairs],'expected':[O(e) for p,e in pairs]}
def Expr(text,values=None,variable=None):
    result={'expression':text,'values':{} if values is None else values}
    if variable is not None: result['variable']=variable
    return result
def Calc(canonical,n,d=1): return {'canonical':canonical,'value':[n,d]}
def Slope(n,d,sn,sd=1): return {'value':[n,d],'slope':[sn,sd]}
def Reduced(canonical,names,n,d,sn,sd=1): return {'canonical':canonical,'variables':names,'value':[n,d],'slope':[sn,sd]}
def Result(name,n,d,sn,sd=1): return {'name':name,'value':[n,d],'derivative':[sn,sd]}
def Book(formulas,inputs,outputs,wrt): return dict(formulas=formulas,inputs=inputs,outputs=outputs,wrt=wrt)
ERROR={'error':'ValueError'}
A_ORACLES=[
 oracle('first',[
  (Expr('1/2+1/3'),Calc('((1/2)+(1/3))',5,6)),
  (Expr('-2^2+(-2)^2'),Calc('((-(2^2))+((-2)^2))',0)),
  (Expr('x/3-y',{'x':[3,2],'y':[-1,4]}),Calc('((x/3)-y)',3,4)),
  (Expr('8/4/2'),Calc('((8/4)/2)',1)),
  (Expr('--x^-2',{'x':2}),Calc('(-(-(x^-2)))',1,4)),
  (Expr('1/(x-x)',{'x':2}),ERROR),
  (Expr('x^13',{'x':1}),ERROR),
  (Expr('x',{'x':True}),ERROR),
 ]),
 oracle('second',[
  (Expr('x*x+3*x',{'x':2},'x'),Slope(10,1,7)),
  (Expr('x/y',{'x':3,'y':2},'y'),Slope(3,2,-3,4)),
  (Expr('x^-2',{'x':2},'x'),Slope(1,4,-1,4)),
  (Expr('(x+1)^3',{'x':1},'x'),Slope(8,1,12)),
  (Expr('x+2',{'x':[1,2]},'z'),Slope(5,2,0)),
  (Expr('x^-12',{'x':1},'x'),Slope(1,1,-12)),
  (Expr('(1/0)^0',{},'x'),ERROR),
  (Expr('x',{},'x'),ERROR),
  (Expr('x',{'x':1},'bad-name'),ERROR),
 ]),
 oracle('third',[
  (Expr('((x+0)*1)/1',{'x':3},'x'),Reduced('x',['x'],3,1,1)),
  (Expr('1/2+1/3',{},'x'),Reduced('(5/6)',[],5,6,0)),
  (Expr('--x+x^1',{'x':4},'x'),Reduced('(x+x)',['x'],8,1,2)),
  (Expr('x*0',{'x':7},'x'),Reduced('(x*0)',['x'],0,1,0)),
  (Expr('x^0',{'x':2},'x'),Reduced('(x^0)',['x'],1,1,0)),
  (Expr('0*(1/0)',{},'x'),ERROR),
  (Expr('x*0',{},'x'),ERROR),
  (Expr('x/1',{'x':[1,0]},'x'),ERROR),
 ]),
 oracle('fourth',[
  (Book({'a':'x*x','b':'a+3*x'},{'x':2},['b','a','x'],'x'),
   {'order':['a','b'],'results':[Result('b',10,1,7),Result('a',4,1,4),Result('x',2,1,1)],'expressions':{'a':'(x*x)','b':'(a+(3*x))'}}),
  (Book({'z':'x+0','a':'y*1','m':'z/a'},{'x':3,'y':2},['m'],'x'),
   {'order':['a','z','m'],'results':[Result('m',3,2,1,2)],'expressions':{'a':'y','m':'(z/a)','z':'x'}}),
  (Book({'b':'x+1','c':'x-1','a':'b*c'},{'x':3},['a'],'x'),
   {'order':['b','c','a'],'results':[Result('a',8,1,6)],'expressions':{'a':'(b*c)','b':'(x+1)','c':'(x-1)'}}),
  (Book({}, {'x':[1,2],'y':7},['y','x'],'x'),
   {'order':[],'results':[Result('y',7,1,0),Result('x',1,2,1)],'expressions':{}}),
  (Book({'a':'x^-1','b':'a*a'},{'x':2},['b'],'x'),
   {'order':['a','b'],'results':[Result('b',1,4,-1,4)],'expressions':{'a':'(x^-1)','b':'(a*a)'}}),
  (Book({'a':'b+1','b':'a+1'},{'x':1},[],'x'),ERROR),
  (Book({'unused':'missing+1'},{'x':1},['x'],'x'),ERROR),
  (Book({'unused':'1/0'},{'x':1},['x'],'x'),ERROR),
 ])]

B={
 'catalog.py': '''
    """Owned product records with integer unit measures."""
    def name(value):
        if not isinstance(value,str) or not value or value.strip()!=value: raise ValueError('invalid name')
        return value
    def integer(value,minimum=1):
        if type(value) is not int or value<minimum: raise ValueError('invalid integer')
        return value
    def products(rows):
        if not isinstance(rows,list): raise ValueError('catalog must be a list')
        output={}
        for row in rows:
            if not isinstance(row,dict): raise ValueError('product must be a dictionary')
            try:
                sku=name(row['sku'])
                weight,volume=integer(row['weight']),integer(row['volume'])
            except KeyError: raise ValueError('missing product field') from None
            if sku in output: raise ValueError('duplicate sku')
            output[sku]=dict(sku=sku,weight=weight,volume=volume)
        return dict(sorted(output.items()))
 ''',
 'orders.py': '''
    """Canonical ordered quantities."""
    from catalog import name,integer
    def quantities(lines,products):
        if not isinstance(lines,list): raise ValueError('lines must be a list')
        output={}
        for line in lines:
            if not isinstance(line,dict): raise ValueError('line must be a dictionary')
            try: sku,quantity=name(line['sku']),integer(line['quantity'])
            except KeyError: raise ValueError('missing order field') from None
            if sku not in products: raise ValueError('unknown sku')
            # Current implementation overwrites repeated SKU lines.
            output[sku]=quantity
        return dict(sorted(output.items()))
 ''',
 'manifest.py': '''
    """JSON-ready shipment measures."""
    from catalog import products
    from orders import quantities
    def make(catalog,lines):
        entries=products(catalog)
        counts=quantities(lines,entries)
        items=[{'sku':sku,'quantity':quantity,'weight':entries[sku]['weight']*quantity,
                'volume':entries[sku]['volume']*quantity} for sku,quantity in counts.items()]
        return {'items':items,'totals':{'units':len(items),
                'weight':sum(item['weight'] for item in items),'volume':sum(item['volume'] for item in items)}}
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from catalog import products
    from orders import quantities
    from manifest import make
    C=[{'sku':'a','weight':4,'volume':2},{'sku':'b','weight':2,'volume':3}]
    class OriginalContracts(unittest.TestCase):
        def test_duplicate_lines_add(self):
            self.assertEqual(quantities([{'sku':'a','quantity':2},{'sku':'a','quantity':3}],products(C)),{'a':5})
        def test_units_count(self):
            self.assertEqual(make(C,[{'sku':'a','quantity':3}])['totals']['units'],3)
        def test_measure_totals(self):
            self.assertEqual(make(C,[{'sku':'a','quantity':2},{'sku':'b','quantity':1}])['totals'],
                             {'units':3,'weight':10,'volume':7})
        def test_sorted_items(self):
            self.assertEqual([v['sku'] for v in make(C,[{'sku':'b','quantity':1},{'sku':'a','quantity':1}])['items']],['a','b'])
        def test_empty_order(self):
            self.assertEqual(make(C,[]),{'items':[],'totals':{'units':0,'weight':0,'volume':0}})
        def test_catalog_copy(self):
            before=copy.deepcopy(C)
            products(C)['a']['weight']=999
            self.assertEqual(C,before)
        def test_duplicate_catalog(self):
            with self.assertRaises(ValueError): products(C+[C[0]])
        def test_unknown_sku(self):
            with self.assertRaises(ValueError): make(C,[{'sku':'z','quantity':1}])
        def test_invalid_quantities(self):
            for quantity in [0,-1,True]:
                with self.assertRaises(ValueError): make(C,[{'sku':'a','quantity':quantity}])
        def test_invalid_measures(self):
            for weight in [0,True,-1]:
                with self.assertRaises(ValueError): products([{'sku':'x','weight':weight,'volume':1}])
    if __name__=='__main__': unittest.main()
 ''',
 'observe.py': '''
    """Protected adapter with no external expected values."""
    import copy
    def _observe(payload,action):
        before=copy.deepcopy(payload)
        try: result=action()
        except (ValueError,KeyError,ImportError,AttributeError) as exc: result={'error':type(exc).__name__}
        return {'result':result,'preserved':payload==before}
    def first(payload):
        def action():
            from manifest import make
            return make(payload['catalog'],payload['lines'])
        return _observe(payload,action)
    def second(payload):
        def action():
            from manifest import packed
            report=packed(payload['catalog'],payload['lines'],payload['boxes'])
            from packing import pack
            return {'report':report,'direct_consistent':report['cartons']==pack(payload['catalog'],payload['lines'],payload['boxes'])}
        return _observe(payload,action)
    def third(payload):
        def action():
            from manifest import constrained
            report=constrained(payload['catalog'],payload['lines'],payload['boxes'],payload.get('policy'))
            from packing import pack
            return {'report':report,'direct_consistent':report['cartons']==pack(payload['catalog'],payload['lines'],payload['boxes'],payload.get('policy'))}
        return _observe(payload,action)
    def fourth(payload):
        def action():
            from manifest import dispatch_report
            report=dispatch_report(payload['catalog'],payload['stock'],payload['requests'],payload['boxes'],payload.get('policy'))
            from dispatch import allocate
            direct=allocate(payload['catalog'],payload['stock'],payload['requests'],payload['boxes'],payload.get('policy'))
            return {'report':report,'direct_consistent':report==direct}
        return _observe(payload,action)
 ''',
 'README.md': '''
    # Parcel preparation helpers

    Synthetic integer unit weight/volume accounting. A catalog lists positive
    unit measures and order lines contain positive quantities. No geometric
    shape fit is claimed: capacities are two independent scalar limits. No
    services or files are involved. All APIs preserve caller objects. Keep
    original tests and observe.py byte-identical; add separate tests as useful.
    Run python3 -m unittest discover -s tests.
 ''',
}
B1={
 'orders.py':B['orders.py'].replace('            # Current implementation overwrites repeated SKU lines.\n            output[sku]=quantity','            output[sku]=output.get(sku,0)+quantity'),
 'manifest.py':B['manifest.py'].replace("'units':len(items)","'units':sum(item['quantity'] for item in items)"),
}
B2={
 'boxes.py': '''
    """Box-type validation; supply of each type is unlimited."""
    from catalog import name,integer
    def validate(rows):
        if not isinstance(rows,list): raise ValueError('boxes must be a list')
        output=[]
        seen=set()
        for row in rows:
            if not isinstance(row,dict): raise ValueError('box must be a dictionary')
            try:
                code=name(row['code'])
                weight,volume=integer(row['max_weight']),integer(row['max_volume'])
            except KeyError: raise ValueError('missing box field') from None
            if code in seen: raise ValueError('duplicate box code')
            seen.add(code)
            output.append(dict(code=code,max_weight=weight,max_volume=volume))
        return sorted(output,key=lambda box:(box['max_volume'],box['max_weight'],box['code']))
 ''',
 'packing.py': '''
    """First-fit decreasing under weight and volume limits."""
    from catalog import products
    from orders import quantities
    from boxes import validate
    def pack(catalog,lines,boxes):
        entries=products(catalog)
        counts=quantities(lines,entries)
        box_types=validate(boxes)
        units=[(entries[sku]['weight'],entries[sku]['volume'],sku,index)
               for sku,quantity in counts.items() for index in range(quantity)]
        units.sort(key=lambda item:(-item[0],-item[1],item[2],item[3]))
        cartons=[]
        for weight,volume,sku,index in units:
            chosen=None
            for carton in cartons:
                if carton['weight']+weight<=carton['type']['max_weight'] and carton['volume']+volume<=carton['type']['max_volume']:
                    chosen=carton
                    break
            if chosen is None:
                box=next((box for box in box_types if weight<=box['max_weight'] and volume<=box['max_volume']),None)
                if box is None: raise ValueError('unit cannot fit any box')
                chosen={'type':box,'weight':0,'volume':0,'items':{}}
                cartons.append(chosen)
            chosen['weight']+=weight
            chosen['volume']+=volume
            chosen['items'][sku]=chosen['items'].get(sku,0)+1
        return [{'box':carton['type']['code'],'items':[{'sku':sku,'quantity':q} for sku,q in sorted(carton['items'].items())],
                 'weight':carton['weight'],'volume':carton['volume']} for carton in cartons]
 ''',
 'manifest.py':B1['manifest.py']+'''
    def packed(catalog,lines,boxes):
        from packing import pack
        report=make(catalog,lines)
        report['cartons']=pack(catalog,lines,boxes)
        return report
 ''',
}
B3={
 'policies.py': '''
    """Validated co-packing restrictions, independent of order arrival."""
    from catalog import name,integer
    def compile_policy(policy,products,boxes):
        if policy is None: policy={}
        if not isinstance(policy,dict) or set(policy)-{'incompatible','max_units','fixed_box'}: raise ValueError('invalid policy')
        pairs=policy.get('incompatible',[])
        if not isinstance(pairs,list): raise ValueError('incompatible must be a list')
        incompatible=set()
        for pair in pairs:
            if not isinstance(pair,list) or len(pair)!=2: raise ValueError('incompatible pair must have two SKUs')
            left,right=name(pair[0]),name(pair[1])
            if left==right or left not in products or right not in products: raise ValueError('invalid incompatible pair')
            incompatible.add(frozenset([left,right]))
        cap=policy.get('max_units')
        if cap is not None: integer(cap)
        fixed=policy.get('fixed_box',{})
        if not isinstance(fixed,dict): raise ValueError('fixed_box must be a dictionary')
        codes={box['code'] for box in boxes}
        copied={}
        for sku,code in fixed.items():
            name(sku);name(code)
            if sku not in products or code not in codes: raise ValueError('unknown fixed SKU or box')
            copied[sku]=code
        return incompatible,cap,copied
 ''',
 'packing.py':B2['packing.py'].replace('    def pack(catalog,lines,boxes):','    def pack(catalog,lines,boxes,policy=None):').replace('        box_types=validate(boxes)','        box_types=validate(boxes)\n        from policies import compile_policy\n        incompatible,cap,fixed=compile_policy(policy,entries,box_types)').replace("                if carton['weight']+weight<=carton['type']['max_weight'] and carton['volume']+volume<=carton['type']['max_volume']:","                compatible=all(frozenset([sku,other]) not in incompatible for other in carton['items'])\n                below_cap=cap is None or sum(carton['items'].values())<cap\n                correct_box=sku not in fixed or carton['type']['code']==fixed[sku]\n                if compatible and below_cap and correct_box and carton['weight']+weight<=carton['type']['max_weight'] and carton['volume']+volume<=carton['type']['max_volume']:").replace("if weight<=box['max_weight'] and volume<=box['max_volume']","if weight<=box['max_weight'] and volume<=box['max_volume'] and (sku not in fixed or box['code']==fixed[sku])"),
 'manifest.py':B2['manifest.py']+'''
    def constrained(catalog,lines,boxes,policy=None):
        from packing import pack
        report=make(catalog,lines)
        report['cartons']=pack(catalog,lines,boxes,policy)
        return report
 ''',
}
B4={
 'dispatch.py': '''
    """Whole-order stock reservation with deterministic packing and priority."""
    from catalog import products,name,integer
    from orders import quantities
    from boxes import validate
    from policies import compile_policy
    from manifest import constrained
    def allocate(catalog,stock,requests,boxes,policy=None):
        entries=products(catalog)
        if not isinstance(stock,dict) or any(sku not in entries for sku in stock): raise ValueError('invalid stock map')
        remaining={sku:integer(stock.get(sku,0),0) for sku in entries}
        box_types=validate(boxes)
        compile_policy(policy,entries,box_types)
        if not isinstance(requests,list): raise ValueError('requests must be a list')
        prepared=[]
        seen=set()
        for request in requests:
            if not isinstance(request,dict): raise ValueError('request must be a dictionary')
            try:
                identity,destination=name(request['id']),name(request['destination'])
                counts=quantities(request['lines'],entries)
            except KeyError: raise ValueError('missing request field') from None
            priority=request.get('priority',0)
            if type(priority) is not int or identity in seen: raise ValueError('invalid priority or duplicate request id')
            seen.add(identity)
            prepared.append((priority,identity,destination,counts))
        accepted=[]
        rejected=[]
        totals=dict(units=0,cartons=0,weight=0,volume=0)
        for priority,identity,destination,counts in sorted(prepared,key=lambda item:-item[0]):
            if any(quantity>remaining[sku] for sku,quantity in counts.items()):
                rejected.append({'id':identity,'reason':'stock'})
                continue
            lines=[{'sku':sku,'quantity':quantity} for sku,quantity in counts.items()]
            try: report=constrained(catalog,lines,boxes,policy)
            except ValueError:
                rejected.append({'id':identity,'reason':'unpackable'})
                continue
            for sku,quantity in counts.items(): remaining[sku]-=quantity
            accepted.append({'id':identity,'destination':destination,'manifest':report})
            for key in ('units','weight','volume'): totals[key]+=report['totals'][key]
            totals['cartons']+=len(report['cartons'])
        return {'accepted':accepted,'rejected':rejected,'remaining':remaining,'totals':totals}
 ''',
 'manifest.py':B3['manifest.py']+'''
    def dispatch_report(catalog,stock,requests,boxes,policy=None):
        from dispatch import allocate
        return allocate(catalog,stock,requests,boxes,policy)
 ''',
}
install('case_02','workspace',B)
for stage,overlay in enumerate([B1,B2,B3,B4],1): install('case_02',f'reference/stage{stage}',overlay)
put('cases/case_02/turn1.md','''
Our parcel manifest overwrites repeated SKU lines and counts distinct SKUs instead of units. Repair order aggregation and the manifest together, preserving original tests/observe.py byte-for-byte.

A catalog is a list of unique nonempty trimmed case-sensitive SKUs with positive integer per-unit weight and volume (booleans invalid). Order lines are a list of known SKUs with positive integer quantity; repeated lines must ADD. Return items sorted by SKU, with exactly sku/quantity/weight/volume, where weight and volume are that SKU's TOTAL measures. totals has units as the sum of quantities and weight/volume as sums of all item totals. Empty lists are valid and yield zero totals.

Retain validation of all catalog products, including unused ones, duplicate catalog IDs, unknown SKUs, missing fields and malformed records. Do not mutate caller-owned records or lists. Add separate regressions as useful and briefly report the implementation and checks run.
''')
put('cases/case_02/turn2.md','''
Add deterministic carton packing and integrate it into manifest.py. Create boxes.py with validate(rows), packing.py with pack(catalog, lines, boxes), and manifest.packed(catalog, lines, boxes). Preserve Turn 1 and protected original bytes.

A box type is {"code": nonempty trimmed string, "max_weight": positive_integer, "max_volume": positive_integer}; codes are unique and booleans invalid. A type has unlimited supply. Empty box lists are legal, but cannot pack a nonempty order. Validate all box definitions, even if unused. Sort box types by ascending max_volume, then max_weight, then code.

Expand aggregated order quantities into indivisible units. Process by descending unit weight, then descending unit volume, then ascending SKU (same-SKU units keep occurrence order). For each unit, first try already created cartons in creation order; it fits only if BOTH resulting totals are within that carton's capacity. If none fits, create a carton using the first sorted box type that fits the unit. If none fits, raise ValueError. This uses scalar volume/weight, not physical shape fitting.

pack returns cartons in creation order, each {"box":code,"items":[{"sku","quantity"} sorted by SKU],"weight":total,"volume":total}. packed returns the existing items/totals report plus "cartons". Do not mutate inputs or share internal mutable records. Add tests for first-fit reuse, volume-vs-weight limits, repeated units, deterministic box ties and impossible units.
''')
put('cases/case_02/turn3.md','''
Add co-packing restrictions, keeping earlier behavior unchanged when no policy is supplied. Create policies.py to validate/compile policies, extend packing.pack(catalog, lines, boxes, policy=None), and add manifest.constrained(catalog, lines, boxes, policy=None). Preserve protected original bytes.

A policy is None or a dictionary with ONLY these optional keys:
- incompatible: a list of two-SKU lists. Both SKUs must be different known products. A pair is symmetric; repeated/reversed pairs are harmless. The two SKUs cannot share a carton.
- max_units: None or a positive integer cap on the TOTAL number of units in one carton, booleans invalid.
- fixed_box: a dictionary from known SKU to known box code. A unit of that SKU may be packed only in that exact type. Other SKUs remain free to share it if their own rules allow.

Validate every restriction before packing, even for empty orders. Keep the same unit order and first-fit scan; an existing carton must satisfy capacity, pair compatibility, unit cap and the incoming SKU's fixed-box requirement. New cartons use the same sorted box order, filtered by fixed-box requirement and unit fit. An impossible unit raises ValueError. constrained returns the same report shape as packed; packed still uses the unrestricted behavior. Preserve every input object. Add tests covering conflicts, combined unit caps, fixed-box reuse, incompatible fixed choices and invalid unused policies.
''')
put('cases/case_02/turn4.md','''
Add stock-aware whole-order dispatch while reusing the manifest and restricted packer. Create dispatch.py with allocate(catalog, stock, requests, boxes, policy=None) and add manifest.dispatch_report with the same arguments. Preserve all earlier APIs and protected original bytes.

stock maps known SKUs to nonnegative integer quantities, booleans invalid; omitted SKUs have zero stock. requests is a list of {"id": nonempty trimmed string, "destination": nonempty trimmed string, "lines": ordinary order lines, "priority": integer default 0}. IDs are unique and booleans are invalid priorities. Validate ALL catalog, stock, boxes, policy, and request records/lines before processing ANY request, including malformed late requests. A malformed request raises ValueError rather than becoming a rejected order. Extra record fields may be ignored.

Process higher priority first, keeping input order for equal priorities. Each request reserves its whole aggregated quantity or none. If stock is insufficient, reject as "stock" without attempting packing. Otherwise use the restricted packer; if a valid order cannot be packed, reject as "unpackable" and consume no stock. An accepted request consumes its quantities and keeps its full constrained manifest. No earlier acceptance is displaced.

Return {"accepted": [{"id","destination","manifest"} in processing order], "rejected": [{"id","reason"} in processing order], "remaining": sorted stock map for EVERY catalog SKU, "totals": {"units","cartons","weight","volume"} summed over accepted manifests}. Empty valid requests give empty lists and zero totals. Preserve every input on success or failure. Add tests for stable priorities, atomic reservation, unpackageable rollback, accumulated totals, invalid late requests and interaction with co-packing policies.
''')

def Product(sku,weight,volume): return dict(sku=sku,weight=weight,volume=volume)
def Line(sku,q): return dict(sku=sku,quantity=q)
def Box(code,w,v): return dict(code=code,max_weight=w,max_volume=v)
def Carton(code,items,weight,volume): return dict(box=code,items=[Line(sku,q) for sku,q in items],weight=weight,volume=volume)
def Manifest(items,cartons=None):
    rows=[dict(sku=sku,quantity=q,weight=w,volume=v) for sku,q,w,v in items]
    result={'items':rows,'totals':dict(units=sum(row['quantity'] for row in rows),weight=sum(row['weight'] for row in rows),volume=sum(row['volume'] for row in rows))}
    if cartons is not None: result['cartons']=cartons
    return result
def Shipment(catalog,lines,boxes=None,policy=None):
    result=dict(catalog=catalog,lines=lines)
    if boxes is not None: result['boxes']=boxes
    if policy is not None: result['policy']=policy
    return result
def Request(identity,destination,lines,priority=0): return dict(id=identity,destination=destination,lines=lines,priority=priority)
def Dispatch(stock,requests,boxes,policy=None):
    result=dict(catalog=C,stock=stock,requests=requests,boxes=boxes)
    if policy is not None: result['policy']=policy
    return result
def Accepted(identity,destination,report): return dict(id=identity,destination=destination,manifest=report)
def Dispatched(accepted,rejected,remaining,units,cartons,weight,volume):
    return dict(accepted=accepted,rejected=rejected,remaining=remaining,totals=dict(units=units,cartons=cartons,weight=weight,volume=volume))
C=[Product('a',4,2),Product('b',2,3),Product('c',1,1)]
BOXES=[Box('L',10,12),Box('S',6,6)]
B_ORACLES=[
 oracle('first',[
  (Shipment(C,[Line('a',1),Line('b',1),Line('a',1)]),Manifest([('a',2,8,4),('b',1,2,3)])),
  (Shipment(C,[Line('c',3)]),Manifest([('c',3,3,3)])),
  (Shipment(C,[]),Manifest([])),
  (Shipment([Product('z',7,2),Product('a',1,5)],[Line('z',1),Line('a',2)]),Manifest([('a',2,2,10),('z',1,7,2)])),
  (Shipment([],[]),Manifest([])),
  (Shipment(C,[Line('unknown',1)]),ERROR),
  (Shipment(C,[Line('a',True)]),ERROR),
  (Shipment(C+[C[0]],[]),ERROR),
 ]),
 oracle('second',[
  (Shipment(C,[Line('a',2),Line('b',1)],BOXES),Manifest([('a',2,8,4),('b',1,2,3)],[Carton('S',[('a',1),('b',1)],6,5),Carton('S',[('a',1)],4,2)])),
  (Shipment(C,[Line('b',3)],BOXES),Manifest([('b',3,6,9)],[Carton('S',[('b',2)],4,6),Carton('S',[('b',1)],2,3)])),
  (Shipment(C,[Line('c',3),Line('a',1),Line('b',1)],BOXES),Manifest([('a',1,4,2),('b',1,2,3),('c',3,3,3)],[Carton('S',[('a',1),('b',1)],6,5),Carton('S',[('c',3)],3,3)])),
  (Shipment(C,[Line('a',1)],[Box('A',9,6),Box('Z',6,6)]),Manifest([('a',1,4,2)],[Carton('Z',[('a',1)],4,2)])),
  (Shipment(C,[],[]),Manifest([],[])),
  (Shipment(C,[Line('a',1)],[Box('T',3,9)]),ERROR),
  (Shipment(C,[],[Box('S',6,6),Box('S',9,9)]),ERROR),
  (Shipment(C,[],[Box('T',True,9)]),ERROR),
 ]),
 oracle('third',[
  (Shipment(C,[Line('a',1),Line('b',1)],BOXES,{'incompatible':[['a','b'],['b','a']]}),Manifest([('a',1,4,2),('b',1,2,3)],[Carton('S',[('a',1)],4,2),Carton('S',[('b',1)],2,3)])),
  (Shipment(C,[Line('c',3)],BOXES,{'max_units':1}),Manifest([('c',3,3,3)],[Carton('S',[('c',1)],1,1),Carton('S',[('c',1)],1,1),Carton('S',[('c',1)],1,1)])),
  (Shipment(C,[Line('a',1),Line('b',1)],BOXES,{'fixed_box':{'a':'L'}}),Manifest([('a',1,4,2),('b',1,2,3)],[Carton('L',[('a',1),('b',1)],6,5)])),
  (Shipment(C,[Line('a',1),Line('b',1)],BOXES,{'fixed_box':{'a':'S','b':'L'}}),Manifest([('a',1,4,2),('b',1,2,3)],[Carton('S',[('a',1)],4,2),Carton('L',[('b',1)],2,3)])),
  (Shipment(C,[Line('b',2),Line('a',1)],BOXES,{}),Manifest([('a',1,4,2),('b',2,4,6)],[Carton('S',[('a',1),('b',1)],6,5),Carton('S',[('b',1)],2,3)])),
  (Shipment(C,[],BOXES,{'incompatible':[['a','z']]}),ERROR),
  (Shipment(C,[],BOXES,{'fixed_box':{'a':'missing'}}),ERROR),
  (Shipment(C,[],BOXES,{'max_units':0}),ERROR),
 ]),
 oracle('fourth',[
  (Dispatch({'a':2,'b':2,'c':3},[Request('low','West',[Line('a',2)]),Request('high','East',[Line('a',1),Line('b',1)],5)],BOXES),
   Dispatched([Accepted('high','East',Manifest([('a',1,4,2),('b',1,2,3)],[Carton('S',[('a',1),('b',1)],6,5)]))],[{'id':'low','reason':'stock'}],{'a':1,'b':1,'c':3},2,1,6,5)),
  (Dispatch({'c':3},[Request('first','One',[Line('c',2)]),Request('second','Two',[Line('c',2)])],BOXES),
   Dispatched([Accepted('first','One',Manifest([('c',2,2,2)],[Carton('S',[('c',2)],2,2)]))],[{'id':'second','reason':'stock'}],{'a':0,'b':0,'c':1},2,1,2,2)),
  (Dispatch({'a':1,'b':1},[Request('bad','One',[Line('a',1)],5),Request('good','Two',[Line('b',1)])],[Box('T',3,6)]),
   Dispatched([Accepted('good','Two',Manifest([('b',1,2,3)],[Carton('T',[('b',1)],2,3)]))],[{'id':'bad','reason':'unpackable'}],{'a':1,'b':0,'c':0},1,1,2,3)),
  (Dispatch({'a':1,'b':1},[Request('mix','One',[Line('a',1),Line('b',1)])],BOXES,{'incompatible':[['a','b']]}),
   Dispatched([Accepted('mix','One',Manifest([('a',1,4,2),('b',1,2,3)],[Carton('S',[('a',1)],4,2),Carton('S',[('b',1)],2,3)]))],[],{'a':0,'b':0,'c':0},2,2,6,5)),
  (Dispatch({},[],[]),Dispatched([],[],{'a':0,'b':0,'c':0},0,0,0,0)),
  (Dispatch({'a':1,'b':2,'c':3},[Request('low','West',[Line('b',1)],-1),Request('high','East',[Line('c',2)],3)],BOXES),
   Dispatched([Accepted('high','East',Manifest([('c',2,2,2)],[Carton('S',[('c',2)],2,2)])),Accepted('low','West',Manifest([('b',1,2,3)],[Carton('S',[('b',1)],2,3)]))],[],{'a':1,'b':1,'c':1},3,2,4,5)),
  (Dispatch({'a':2},[Request('same','One',[Line('a',1)]),Request('same','Two',[Line('a',1)])],BOXES),ERROR),
  (Dispatch({'a':2},[Request('valid','One',[Line('a',1)]),Request('bad','Two',[Line('a',1)],True)],BOXES),ERROR),
  (Dispatch({'unknown':1},[],BOXES),ERROR),
 ])]

# Observe both the integrated public entry point and its required feature module.
# This computed consistency flag is not an external answer or model self-claim.
for checks in (A_ORACLES,B_ORACLES):
    for check in checks[1:]:
        for observed in check['expected']:
            result=observed['result']
            if 'error' not in result:
                observed['result']={'report':result,'direct_consistent':True}

cases=[]
for identity,checks in [('case_01',A_ORACLES),('case_02',B_ORACLES)]:
    cases.append({'id':identity,'workspace_dir':f'cases/{identity}/workspace',
        'immutable_files':['tests/test_original.py','observe.py'],
        'turns':[{'task':f'cases/{identity}/turn{stage}.md','oracle':check} for stage,check in enumerate(checks,1)]})
manifest={'version':1,'runtime_profile':{'experiment_profile':'thinking128k','model':'deepseek-flash'},'cases':cases}
put('manifest.json',json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
put('PROVENANCE.md','''
# Fresh v4 synthetic provenance

This new dispatch authored two wholly new application domains and APIs, each
with four natural feature Turns. Every stage requires integration across modules
and preserves earlier behavior. The projects are not renamed previous tasks.
No previous-suite files, runtime code, project tests, private transcripts,
credentials or external solutions were read. The author read only the delegated
mission update and received public constraints. No live API/model coding run
was made. All source, task text, protected original tests, adapters, finite
inputs/expected values and reference overlays are newly authored. Expectations
are directly specified, never extracted from reference execution.

Independence is role separation within the same AI environment, not external
statistical independence. Only candidate workspace and the current natural task
are coding-model payload. External finite inputs/expected values, reference
code, author sources and author validation records remain outside workspaces.
The runtime implementer may read only public interface/receipt counts and hashes
before source freeze and first exposure. No filler, scripted tool sequence,
case recognition, model selection or manufactured summary request is in tasks.

This explicitly changed practical profile does not repair or rescore the earlier
48k stress results. Prior attempts/scores remain unchanged. Actual model quality,
automatic-note visibility in the final implementation-changing request, distinct
PIDs across four Turns, execution completion and all fixed profile limits are
separate Runner gates. Offline references do not stand in for any of them.

Freeze before exposure and never revise semantic files to improve a score.
Every stage checks original bytes, original tests, all candidate-added tests and
all current/prior finite observations. Later fixes cannot erase earlier failure.
This is the mission's last fresh configuration. Stop authoring after this v4
freeze; no v5, API calls, Git commits or old-artifact edits belong to this role.
''')
