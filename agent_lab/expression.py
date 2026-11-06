"""A deliberately small Python expression language, not eval or a Python sandbox."""
import ast
import json
import math
import operator
import sys


def calculate(source):
    if not isinstance(source, str) or len(source.encode()) > 8000:
        raise ValueError('invalid_expression')
    tree = ast.parse(source, mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 512:
        raise ValueError('expression_limit')
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
    functions = {'sum': sum, 'len': len, 'min': min, 'max': max, 'abs': abs,
                 'round': round, 'sorted': sorted}

    def bounded(value):
        if type(value) in (int, float):
            if not math.isfinite(value) or abs(value) > 1e100:
                raise ValueError('number_limit')
        elif isinstance(value, list):
            if len(value) > 1000 or any(type(v) not in (int, float) for v in value):
                raise ValueError('list_limit')
            for v in value:
                bounded(v)
        else:
            raise ValueError('unsupported_value')
        return value

    def visit(node, depth=0):
        if depth > 32:
            raise ValueError('depth_limit')
        sub = lambda n: visit(n, depth + 1)
        if isinstance(node, ast.Constant):
            return bounded(node.value)
        if isinstance(node, (ast.List, ast.Tuple)):
            return bounded([sub(n) for n in node.elts])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = sub(node.operand)
            if type(value) not in (int, float):
                raise ValueError('numeric_required')
            return bounded(value if isinstance(node.op, ast.UAdd) else -value)
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = sub(node.left), sub(node.right)
            if type(left) not in (int, float) or type(right) not in (int, float):
                raise ValueError('numeric_required')
            return bounded(binary[type(node.op)](left, right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and not node.keywords:
            return bounded(functions[node.func.id](*[sub(n) for n in node.args]))
        raise ValueError('unsupported_expression')

    return visit(tree.body)


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'value': calculate(sys.stdin.read(8001))}, allow_nan=False))
    except Exception:
        print(json.dumps({'ok': False, 'error': 'invalid_expression'}))
