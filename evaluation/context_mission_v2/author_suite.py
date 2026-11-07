"""Sealed fresh synthetic authoring source for mission acceptance version 2."""
import json
from pathlib import Path
import textwrap
ROOT = Path(__file__).resolve().parent

def write(name, value):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(value).lstrip('\n'))

def install(case, location, mapping):
    for name, value in mapping.items():
        write(f'cases/{case}/{location}/{name}', value)

A = {
 'validation.py': '''
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
 ''',
 'portions.py': '''
    """Split integer cents among weighted members."""
    from validation import integer, weights as valid_weights

    def apportion(total, weights):
        integer(total, 'total')
        weights = valid_weights(weights)
        denominator = sum(weights.values())
        # The current implementation loses rounding remainders.
        return {name: total * weights[name] // denominator for name in sorted(weights)}
 ''',
 'ledger.py': '''
    """Expense records and per-member net balances."""
    from portions import apportion
    from validation import integer, member, weights

    def records(entries):
        output, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('expense must be a dictionary')
            try:
                identity = member(entry['id'])
                payer = member(entry['payer'])
                amount = integer(entry['amount'], 'amount')
                shares = weights(entry['weights'])
            except KeyError:
                raise ValueError('expense is missing a required field') from None
            if identity in seen:
                raise ValueError('duplicate expense id')
            seen.add(identity)
            output.append(dict(id=identity, payer=payer, amount=amount, weights=shares))
        return output

    def balances(entries):
        result = {}
        for entry in records(entries):
            payer = entry['payer']
            result[payer] = result.get(payer, 0) + entry['amount']
            for name, share in apportion(entry['amount'], entry['weights']).items():
                result[name] = result.get(name, 0) - share
        return dict(sorted(result.items()))
 ''',
 'views.py': '''
    """JSON-ready expense statement."""
    from ledger import records, balances

    def statement(entries):
        canonical = records(entries)
        paid = {}
        for entry in canonical:
            paid[entry['payer']] = paid.get(entry['payer'], 0) + entry['amount']
        return {'balances': balances(canonical), 'paid': dict(sorted(paid.items())),
                'spent': sum(entry['amount'] for entry in canonical)}
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from portions import apportion
    from ledger import balances, records
    from views import statement

    def expense(identity='e', payer='a', amount=10, weights=None):
        return dict(id=identity, payer=payer, amount=amount,
                    weights={'a': 1, 'b': 1, 'c': 1} if weights is None else weights)

    class OriginalContracts(unittest.TestCase):
        def test_conserves_cents(self):
            self.assertEqual(apportion(10, {'a': 1, 'b': 1, 'c': 1}), {'a': 4, 'b': 3, 'c': 3})
        def test_largest_remainder(self):
            self.assertEqual(apportion(7, {'a': 1, 'b': 3}), {'a': 2, 'b': 5})
        def test_negative_symmetry(self):
            self.assertEqual(apportion(-5, {'a': 1, 'b': 2}), {'a': -2, 'b': -3})
        def test_name_breaks_ties(self):
            self.assertEqual(apportion(1, {'z': 1, 'a': 1}), {'a': 1, 'z': 0})
        def test_zero_weight_stays_zero(self):
            self.assertEqual(apportion(2, {'a': 0, 'b': 1}), {'a': 0, 'b': 2})
        def test_validation(self):
            for total, weights in [(True, {'a': 1}), (3, {'a': 0}), (3, {'a': -1}), (3, {' a': 1})]:
                with self.assertRaises(ValueError):
                    apportion(total, weights)
        def test_balances_sum_to_zero(self):
            self.assertEqual(balances([expense()]), {'a': 6, 'b': -3, 'c': -3})
        def test_owned_nested_records(self):
            data = [expense()]
            before = copy.deepcopy(data)
            records(data)[0]['weights']['a'] = 99
            self.assertEqual(data, before)
        def test_duplicate_expenses(self):
            with self.assertRaises(ValueError):
                balances([expense(), expense()])
        def test_statement(self):
            self.assertEqual(statement([expense(payer='x', amount=6, weights={'b': 1})]),
                {'balances': {'b': -6, 'x': 6}, 'paid': {'x': 6}, 'spent': 6})

    if __name__ == '__main__':
        unittest.main()
 ''',
 'observe.py': '''
    """Protected observation boundary; finite answers are external."""
    import copy

    def _observe(payload, action):
        before = copy.deepcopy(payload)
        try:
            value = action()
        except (ValueError, KeyError, ImportError, AttributeError) as exc:
            value = {'error': type(exc).__name__}
        return {'value': value, 'input_intact': payload == before}

    def first(payload):
        def action():
            from portions import apportion
            from ledger import balances
            return {'shares': apportion(payload['total'], payload['weights']),
                    'balances': balances(payload['entries'])}
        return _observe(payload, action)

    def second(payload):
        def action():
            from reimburse import recommend
            return recommend(payload['entries'], payload.get('payments', []))
        return _observe(payload, action)

    def third(payload):
        def action():
            from closeout import installments
            return installments(payload['entries'], payload.get('payments', []), payload.get('cap', 1000))
        return _observe(payload, action)
 ''',
 'README.md': '''
    # Shared-outing expense helpers

    Pure standard-library Python, with all amounts in integer cents. A positive
    member balance means that member should receive money; a negative balance
    means the member should send money. Negative expense amounts represent a
    refund. Functions must preserve caller-owned lists and nested dictionaries.
    Run `python3 -m unittest discover -s tests`. The original test and observe.py
    are protected byte-for-byte; new separate tests are welcome.
 ''',
}
A1 = {
 'portions.py': '''
    """Exact integer-cent allocation with largest remainders."""
    from validation import integer, weights as valid_weights

    def apportion(total, weights):
        integer(total, 'total')
        weights = valid_weights(weights)
        denominator = sum(weights.values())
        magnitude = abs(total)
        result = {name: magnitude * weight // denominator for name, weight in weights.items()}
        remaining = magnitude - sum(result.values())
        order = sorted(weights, key=lambda name: (-(magnitude * weights[name] % denominator), name))
        for name in order[:remaining]:
            result[name] += 1
        sign = -1 if total < 0 else 1
        return {name: result[name] * sign for name in sorted(result)}
 ''',
}
A2 = {
 'reimburse.py': '''
    """Record real payments, then suggest deterministic exact transfers."""
    from ledger import balances
    from validation import integer, member

    def after_payments(entries, payments):
        net = balances(entries)
        seen = set()
        for payment in payments:
            if not isinstance(payment, dict):
                raise ValueError('payment must be a dictionary')
            try:
                identity = member(payment['id'])
                sender = member(payment['from'])
                recipient = member(payment['to'])
                cents = integer(payment['cents'], 'cents', 1)
            except KeyError:
                raise ValueError('payment is missing a required field') from None
            if identity in seen or sender == recipient:
                raise ValueError('duplicate payment id or self payment')
            if sender not in net or recipient not in net:
                raise ValueError('payment names an unknown member')
            seen.add(identity)
            net[sender] += cents
            net[recipient] -= cents
        return net

    def recommend(entries, payments=()):
        net = after_payments(entries, payments)
        debtors = [[name, -value] for name, value in sorted(net.items()) if value < 0]
        creditors = [[name, value] for name, value in sorted(net.items()) if value > 0]
        transfers = []
        left = right = 0
        while left < len(debtors) and right < len(creditors):
            cents = min(debtors[left][1], creditors[right][1])
            transfers.append({'from': debtors[left][0], 'to': creditors[right][0], 'cents': cents})
            debtors[left][1] -= cents
            creditors[right][1] -= cents
            if debtors[left][1] == 0:
                left += 1
            if creditors[right][1] == 0:
                right += 1
        return {'balances': net, 'transfers': transfers}
 ''',
}
A3 = {
 'closeout.py': '''
    """Split a transfer plan into sender-capped rounds."""
    from reimburse import recommend
    from validation import integer

    def installments(entries, payments=(), cap=1000):
        integer(cap, 'cap', 1)
        plan = recommend(entries, payments)
        remaining = [dict(transfer) for transfer in plan['transfers']]
        rounds = []
        while any(item['cents'] for item in remaining):
            budgets = {}
            batch = []
            for transfer in remaining:
                sender = transfer['from']
                available = budgets.setdefault(sender, cap)
                amount = min(transfer['cents'], available)
                if amount:
                    batch.append({'from': sender, 'to': transfer['to'], 'cents': amount})
                    transfer['cents'] -= amount
                    budgets[sender] -= amount
            rounds.append(batch)
        return {'balances': plan['balances'], 'rounds': rounds,
                'principal': sum(value for value in plan['balances'].values() if value > 0),
                'count': sum(len(batch) for batch in rounds)}
 ''',
}
install('case_01','workspace',A)
for stage, source in enumerate([A1,A2,A3],1):
    install('case_01',f'reference/stage{stage}',source)
write('cases/case_01/turn1.md','''
Our shared-outing expense helper drops cents and handles refunds asymmetrically. Fix portions.apportion(total, weights) and keep the rest of the ledger consistent. total is an integer number of cents, including negative refunds; booleans are invalid. weights is a nonempty dictionary from nonempty trimmed member names to nonnegative integer weights (also not booleans); at least one weight must be positive.

Allocate abs(total) by the largest-remainder method: take each exact weighted quota's floor, then distribute the remaining cents by descending fractional remainder, breaking ties by ascending member name. Finally restore the total's sign. Include zero-weight members with zero shares and return member keys sorted. Allocated shares must sum exactly to total. Do not use floating point. A refund must allocate the exact negatives of a positive amount's shares.

A ledger balance is amount paid minus the member's allocated shares; positive means owed money and negative means owing money. Payers need not appear among that expense's weights. Include all payers and weighted members, including zero balances, with sorted keys. Preserve existing expense validation, statements, and all caller objects. The protected original test and observe.py must remain byte-identical; add separate tests if useful. Finish with a short account of your change and validation.
''')
write('cases/case_01/turn2.md','''
The exact ledger is working. Add reimburse.py with recommend(entries, payments=()). Keep Turn 1 behavior and the protected original tests/observe.py unchanged.

payments is a list of {"id": payment_id, "from": sender, "to": recipient, "cents": positive_integer}. Use the existing nonempty trimmed string rules for all identities. Payment IDs must be unique within payments; sender and recipient must differ, and both must be members appearing in the expense ledger, including members whose initial balance is zero. Invalid or missing fields raise ValueError; booleans are not cents. An actual payment increases the sender's net balance and decreases the recipient's by its amount. Overpayment is allowed and can reverse who owes whom. Apply payments to the expense balances without changing any caller input.

Return {"balances": post_payment_balance_map, "transfers": suggested_transfers}. Include zero balances and preserve sorted member keys. Suggest transfers using two lists: debtors by ascending name and creditors by ascending name. At each step send min(debtor's remaining debt, creditor's remaining credit), record {"from", "to", "cents"}, and advance whichever side reaches zero. Keep transfer order from that sweep; omit zero transfers. This is a deterministic settlement proposal, with no claim that its transfer count is globally minimal. Empty or fully settled ledgers produce no transfers. Add tests for refunds, recorded payments, overpayment, validation and input preservation.
''')
write('cases/case_01/turn3.md','''
Some members cannot send their entire suggested settlement at once. Add closeout.py with installments(entries, payments=(), cap=1000). Preserve the earlier allocation, ledger and recommendation behavior, plus the immutable original tests/observe.py.

Use the exact post-payment balances and ordered suggested transfers from recommend. cap is a positive integer cents limit per SENDER per ROUND, shared across all that sender's transfers in that round; booleans are invalid. Start each round with a fresh cap for each sender. Scan the original suggested-transfer order, emitting as much of each remaining transfer as the sender's remaining round budget permits. Never emit zero transfers. A recipient has no receiving limit. Repeat until all principal is sent; do not mutate the recommendation or any caller object. Validate the cap even for an empty ledger.

Return {"balances": post_payment_balances, "rounds": [[{"from", "to", "cents"}, ...], ...], "principal": total_positive_post_payment_balances, "count": number_of_emitted_transfer_rows}. For no outstanding balances, rounds is [], principal is 0, and count is 0. The cap applies to each sender's combined rows in a round, not independently to each row. Add tests for a sender owing multiple recipients, independent sender budgets, refunds, prior payments, and no-work/invalid-cap cases.
''')

B = {
 'palette.py': '''
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
 ''',
 'canvas.py': '''
    """Rectangular grids are copied before returning them."""
    from palette import color

    def owned(grid):
        if not isinstance(grid, list):
            raise ValueError('grid must be a list')
        if not grid:
            return []
        if not isinstance(grid[0], list) or not grid[0]:
            raise ValueError('nonempty grid rows must be nonempty lists')
        width = len(grid[0])
        output = []
        for row in grid:
            if not isinstance(row, list) or len(row) != width:
                raise ValueError('grid must be rectangular')
            output.append([color(pixel) for pixel in row])
        return output
 ''',
 'runs.py': '''
    """Row-major run-length packets."""
    from canvas import owned
    from palette import color, integer

    def pack(grid):
        grid = owned(grid)
        width, height = (len(grid[0]) if grid else 0), len(grid)
        runs = []
        previous, count = None, 0
        for row in grid:
            for pixel in row:
                if pixel == previous:
                    count += 1
                else:
                    if count:
                        runs.append([count, previous])
                    previous, count = pixel, 1
        # The pending final run is currently never flushed.
        return {'width': width, 'height': height, 'runs': runs}

    def unpack(packet):
        if not isinstance(packet, dict):
            raise ValueError('packet must be a dictionary')
        try:
            width, height = integer(packet['width'], 0), integer(packet['height'], 0)
            runs = packet['runs']
        except KeyError:
            raise ValueError('packet fields missing') from None
        if (width == 0) != (height == 0) or not isinstance(runs, list):
            raise ValueError('invalid dimensions or runs')
        flat = []
        for run in runs:
            if not isinstance(run, list) or len(run) != 2:
                raise ValueError('run must be [count, color]')
            count, pixel = integer(run[0], 1), color(run[1])
            flat.extend([pixel] * count)
        # The current decoder silently tolerates a mismatched length.
        return [flat[offset:offset + width] for offset in range(0, len(flat), width)] if width else []
 ''',
 'metrics.py': '''
    """JSON histograms use string keys."""
    from canvas import owned

    def histogram(grid):
        counts = {}
        for row in owned(grid):
            for pixel in row:
                key = str(pixel)
                counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from canvas import owned
    from runs import pack, unpack
    from metrics import histogram

    class OriginalContracts(unittest.TestCase):
        def test_final_run_is_present(self):
            self.assertEqual(pack([[1, 1, 2], [2, 2, 2]]),
                {'width': 3, 'height': 2, 'runs': [[2, 1], [4, 2]]})
        def test_single_pixel(self):
            self.assertEqual(unpack(pack([[9]])), [[9]])
        def test_empty_roundtrip(self):
            self.assertEqual(pack([]), {'width': 0, 'height': 0, 'runs': []})
            self.assertEqual(unpack(pack([])), [])
        def test_exact_packet_area(self):
            for runs in [[[1, 3]], [[3, 3]]]:
                with self.assertRaises(ValueError):
                    unpack({'width': 2, 'height': 1, 'runs': runs})
        def test_noncanonical_runs_accepted(self):
            self.assertEqual(unpack({'width': 2, 'height': 1, 'runs': [[1, 4], [1, 4]]}), [[4, 4]])
        def test_histogram(self):
            self.assertEqual(histogram([[0, 1], [1, 9]]), {'0': 1, '1': 2, '9': 1})
        def test_copy(self):
            grid = [[1, 2], [3, 4]]
            before = copy.deepcopy(grid)
            owned(grid)[0][0] = 9
            self.assertEqual(grid, before)
        def test_invalid_grid(self):
            for grid in [[[1], [1, 2]], [[]], [[True]], [[10]]]:
                with self.assertRaises(ValueError):
                    owned(grid)
        def test_invalid_run(self):
            for run in [[0, 1], [True, 1], [1, False], [1, 10]]:
                with self.assertRaises(ValueError):
                    unpack({'width': 1, 'height': 1, 'runs': [run]})
        def test_dimension_pair(self):
            with self.assertRaises(ValueError):
                unpack({'width': 0, 'height': 1, 'runs': []})

    if __name__ == '__main__':
        unittest.main()
 ''',
 'observe.py': '''
    """Protected JSON observations. No external expected answers are included."""
    import copy

    def _observe(payload, action):
        before = copy.deepcopy(payload)
        try:
            value = action()
        except (ValueError, KeyError, ImportError, AttributeError) as exc:
            value = {'error': type(exc).__name__}
        return {'value': value, 'input_intact': payload == before}

    def first(payload):
        def action():
            from runs import pack, unpack
            from metrics import histogram
            packed = pack(payload['grid'])
            return {'packed': packed, 'decoded': unpack(payload.get('packet', packed)),
                    'histogram': histogram(payload['grid'])}
        return _observe(payload, action)

    def second(payload):
        def action():
            from regions import paint, components
            grid = payload['grid']
            diagonal = payload.get('diagonal', False)
            return {'paint': paint(grid, payload['start'], payload['replacement'], diagonal),
                    'components': components(grid, diagonal)}
        return _observe(payload, action)

    def third(payload):
        def action():
            from layers import render
            return render(payload['base'], payload['layers'], payload.get('diagonal', False))
        return _observe(payload, action)
 ''',
 'README.md': '''
    # Tiny palette canvas toolkit

    A grid is a list of equally sized nonempty rows of integers 0 through 9. []
    is the sole empty-grid representation. Coordinates are [row, column]. This
    pure standard-library package stores no files and calls no service. Public
    functions must preserve caller objects. Run `python3 -m unittest discover
    -s tests`. Keep the original tests and observe.py byte-identical; separate new
    tests may be added.
 ''',
}
B1 = {
 'runs.py': B['runs.py'].replace('        # The pending final run is currently never flushed.\n', '        if count:\n            runs.append([count, previous])\n').replace('        # The current decoder silently tolerates a mismatched length.\n', "        if len(flat) != width * height:\n            raise ValueError('run length does not match dimensions')\n"),
}
B2 = {
 'regions.py': '''
    """Same-color connected components and a pure flood-fill operation."""
    from canvas import owned
    from palette import color, integer

    def _diagonal(value):
        if type(value) is not bool:
            raise ValueError('diagonal must be a boolean')
        return value

    def _region(grid, start, diagonal):
        height, width = len(grid), len(grid[0])
        target = grid[start[0]][start[1]]
        seen, pending = {start}, [start]
        offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]
        if diagonal:
            offsets += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        while pending:
            row, column = pending.pop()
            for dr, dc in offsets:
                neighbor = row + dr, column + dc
                r, c = neighbor
                if 0 <= r < height and 0 <= c < width and neighbor not in seen and grid[r][c] == target:
                    seen.add(neighbor)
                    pending.append(neighbor)
        return sorted(seen)

    def _bounds(points):
        return [min(r for r, c in points), min(c for r, c in points),
                max(r for r, c in points) + 1, max(c for r, c in points) + 1]

    def components(grid, diagonal=False):
        grid = owned(grid)
        _diagonal(diagonal)
        seen, result = set(), []
        for row in range(len(grid)):
            for column in range(len(grid[0])):
                if (row, column) not in seen:
                    points = _region(grid, (row, column), diagonal)
                    seen.update(points)
                    result.append({'color': grid[row][column], 'size': len(points),
                                   'anchor': [row, column], 'bounds': _bounds(points)})
        return result

    def paint(grid, start, replacement, diagonal=False):
        grid = owned(grid)
        _diagonal(diagonal)
        color(replacement)
        if not isinstance(start, list) or len(start) != 2:
            raise ValueError('start must be [row, column]')
        row, column = integer(start[0], 0), integer(start[1], 0)
        if row >= len(grid) or column >= len(grid[0]):
            raise ValueError('start is outside grid')
        changed = []
        if grid[row][column] != replacement:
            changed = _region(grid, (row, column), diagonal)
            for r, c in changed:
                grid[r][c] = replacement
        return {'grid': grid, 'changed': [list(point) for point in changed],
                'bounds': _bounds(changed) if changed else None}
 ''',
}
B3 = {
 'layers.py': '''
    """Clipped integer-color layers, followed by canonical observations."""
    from canvas import owned
    from palette import integer, color
    from regions import components
    from runs import pack

    def render(base, layers, diagonal=False):
        base = owned(base)
        if type(diagonal) is not bool:
            raise ValueError('diagonal must be a boolean')
        if not isinstance(layers, list):
            raise ValueError('layers must be a list')
        prepared = []
        for layer in layers:
            if not isinstance(layer, dict):
                raise ValueError('layer must be a dictionary')
            try:
                pixels = owned(layer['pixels'])
                top, left = integer(layer['top']), integer(layer['left'])
            except KeyError:
                raise ValueError('layer is missing a required field') from None
            transparent = layer.get('transparent', 0)
            if transparent is not None:
                color(transparent)
            prepared.append((pixels, top, left, transparent))
        output = [row[:] for row in base]
        height, width = len(base), len(base[0]) if base else 0
        written = set()
        for pixels, top, left, transparent in prepared:
            for row, values in enumerate(pixels):
                for column, pixel in enumerate(values):
                    r, c = top + row, left + column
                    if 0 <= r < height and 0 <= c < width and pixel != transparent:
                        output[r][c] = pixel
                        written.add((r, c))
        changed = [[r, c] for r in range(height) for c in range(width) if output[r][c] != base[r][c]]
        return {'grid': output, 'written': [list(point) for point in sorted(written)], 'changed': changed,
                'components': components(output, diagonal), 'packed': pack(output)}
 ''',
}
install('case_02','workspace',B)
for stage, source in enumerate([B1,B2,B3],1):
    install('case_02',f'reference/stage{stage}',source)
write('cases/case_02/turn1.md','''
The tiny palette toolkit's run-length codec drops the last run and tolerates invalid packet sizes. Repair runs.pack and runs.unpack while preserving the protected original tests and observe.py.

pack(grid) must flatten pixels in row-major order, merging equal adjacent colors even across a row boundary. Return {"width": columns, "height": rows, "runs": [[positive_count, color], ...]}. Flush the final run. [] becomes {"width": 0, "height": 0, "runs": []}. Keep the existing grid rules: list of equally sized nonempty list rows, integer colors 0..9 (booleans invalid); [] is the only empty representation.

unpack(packet) must require nonnegative integer dimensions (not booleans), either both zero or both positive, and a list of [positive_integer_count, valid_color] runs. The total run length must equal width*height EXACTLY, including for an empty packet; otherwise raise ValueError. Adjacent runs may repeat a color and should still decode normally. Return a fresh rectangular grid with the declared shape. Keep histogram behavior and preserve all caller input. Add separate regression tests and briefly report the checks you ran.
''')
write('cases/case_02/turn2.md','''
The codec now round-trips correctly. Add regions.py with components(grid, diagonal=False) and paint(grid, start, replacement, diagonal=False). Keep Turn 1 behavior and the protected original tests/observe.py unchanged.

A component is a maximal same-color region connected by four side neighbors, or all eight neighbors when diagonal=True. diagonal must be an actual bool. components returns a row-major-anchor-ordered list of {"color": color, "size": number_of_pixels, "anchor": [first_row, first_column], "bounds": [min_row, min_column, max_row_exclusive, max_column_exclusive]}. Include every color, including zero. The anchor is the component's lexicographically smallest coordinate. Empty grids have no components.

paint selects only the original same-color component containing start and replaces it with replacement. start must be a two-element list of nonnegative integer row/column indices (not booleans) inside the grid; an empty grid has no valid start. replacement uses the existing color validation. Return {"grid": new_grid, "changed": [[row, column], ...] in row-major order, "bounds": bounding_box_of_changed_pixels_or_null}. If replacement already equals the starting color, nothing changes and bounds is null. Preserve every caller object, use the same connectivity for both APIs, and retain existing invalid-grid checks. Add tests for diagonal connections, barriers, no-op fills and boundaries.
''')
write('cases/case_02/turn3.md','''
We need clipped layer rendering on top of the grid toolkit. Add layers.py with render(base, layers, diagonal=False), preserving both earlier Turns and the byte-identical original tests/observe.py.

layers is a list of dictionaries containing pixels (a valid grid), top and left (signed integers, booleans invalid), and optional transparent (default color 0). transparent may be a valid color or None; None means no transparency. Process layers in input order, with later visible pixels winning. A layer's [row,column] goes to base [top+row,left+column]. Clip outside the base's fixed dimensions; never resize it. Transparent pixels do not write. Empty bases and empty layers are allowed, but validate every layer even when it is entirely clipped. Missing required fields or malformed layers raise ValueError; diagonal must be an actual bool.

Return {"grid": rendered_grid, "written": sorted_unique_destination_coordinates_ever_receiving_a_visible_pixel, "changed": row_major_coordinates_whose_FINAL_color_differs_from_base, "components": components(rendered_grid, diagonal), "packed": pack(rendered_grid)}. A later layer can restore a pixel's original color: it then remains in written but disappears from changed. Reuse the repaired codec and component rules. Preserve the base, layers and every nested object on success or failure. Add tests for clipping, transparency, overwrite/restoration, empty canvases and invalid invisible layers.
''')

# These finite expected values are hand-specified, not computed using references.
def E(payer, amount, weights, identity='e'):
    return dict(id=identity, payer=payer, amount=amount, weights=weights)
def P(identity, sender, recipient, cents):
    return {'id':identity,'from':sender,'to':recipient,'cents':cents}
def T(sender, recipient, cents):
    return {'from':sender,'to':recipient,'cents':cents}
def O(value):
    return {'value':value,'input_intact':True}
def oracle(function, pairs):
    return {'module':'observe','function':function,'inputs':[[payload] for payload, expected in pairs],
            'expected':[O(expected) for payload, expected in pairs]}
def F(total, weights, entries, shares, balances):
    return ({'total':total,'weights':weights,'entries':entries},{'shares':shares,'balances':balances})
ABC=E('a',10,{'a':1,'b':1,'c':1})
CROSS=[E('c',9,{'a':1,'b':2},'e1'),E('a',3,{'b':1},'e2')]
A_ORACLES=[
 oracle('first',[
  F(10,{'a':1,'b':1,'c':1},[ABC],{'a':4,'b':3,'c':3},{'a':6,'b':-3,'c':-3}),
  F(-5,{'a':1,'b':2},[E('b',-5,{'a':1,'b':2})],{'a':-2,'b':-3},{'a':2,'b':-2}),
  F(2,{'a':0,'b':1,'c':1},[],{'a':0,'b':1,'c':1},{}),
  F(7,{'z':1,'a':1},CROSS,{'a':4,'z':3},{'a':0,'b':-9,'c':9}),
  F(0,{'a':2,'b':1},[E('x',0,{'a':1})],{'a':0,'b':0},{'a':0,'x':0}),
  ({'total':True,'weights':{'a':1},'entries':[]},{'error':'ValueError'}),
  ({'total':1,'weights':{'a':1},'entries':[ABC,ABC]},{'error':'ValueError'}),
 ]),
 oracle('second',[
  ({'entries':[ABC]}, {'balances':{'a':6,'b':-3,'c':-3},'transfers':[T('b','a',3),T('c','a',3)]}),
  ({'entries':[E('z',10,{'a':1,'b':1})],'payments':[P('p','a','z',2)]},
   {'balances':{'a':-3,'b':-5,'z':8},'transfers':[T('a','z',3),T('b','z',5)]}),
  ({'entries':[E('a',6,{'b':1})],'payments':[P('p','b','a',8)]},
   {'balances':{'a':-2,'b':2},'transfers':[T('a','b',2)]}),
  ({'entries':CROSS}, {'balances':{'a':0,'b':-9,'c':9},'transfers':[T('b','c',9)]}),
  ({'entries':[E('x',0,{'a':1})]}, {'balances':{'a':0,'x':0},'transfers':[]}),
  ({'entries':[ABC],'payments':[P('p','b','a',1),P('p','c','a',1)]},{'error':'ValueError'}),
  ({'entries':[ABC],'payments':[P('p','absent','a',1)]},{'error':'ValueError'}),
 ]),
 oracle('third',[
  ({'entries':[ABC],'cap':2}, {'balances':{'a':6,'b':-3,'c':-3},'rounds':[[T('b','a',2),T('c','a',2)],[T('b','a',1),T('c','a',1)]],'principal':6,'count':4}),
  ({'entries':[E('b',5,{'a':1},'e1'),E('c',4,{'a':1},'e2')],'cap':3},
   {'balances':{'a':-9,'b':5,'c':4},'rounds':[[T('a','b',3)],[T('a','b',2),T('a','c',1)],[T('a','c',3)]],'principal':9,'count':4}),
  ({'entries':[E('b',-5,{'a':1,'b':2})],'cap':100},
   {'balances':{'a':2,'b':-2},'rounds':[[T('b','a',2)]],'principal':2,'count':1}),
  ({'entries':[E('a',4,{'b':1})],'payments':[P('p','b','a',4)],'cap':1},
   {'balances':{'a':0,'b':0},'rounds':[],'principal':0,'count':0}),
  ({'entries':[],'cap':1},{'balances':{},'rounds':[],'principal':0,'count':0}),
  ({'entries':[],'cap':True},{'error':'ValueError'}),
  ({'entries':[ABC],'payments':[P('p','absent','a',1)],'cap':2},{'error':'ValueError'}),
 ])]

def Packet(width,height,runs):
    return dict(width=width,height=height,runs=runs)
def Comp(color,size,anchor,bounds):
    return dict(color=color,size=size,anchor=anchor,bounds=bounds)
def Paint(grid,changed,bounds):
    return dict(grid=grid,changed=changed,bounds=bounds)
def Render(grid,written,changed,components,packed):
    return dict(grid=grid,written=written,changed=changed,components=components,packed=packed)
G=[[1,1,0],[1,0,0],[0,0,1]]
B_ORACLES=[
 oracle('first',[
  ({'grid':[[1,1,2],[2,2,2]]},{'packed':Packet(3,2,[[2,1],[4,2]]),'decoded':[[1,1,2],[2,2,2]],'histogram':{'1':2,'2':4}}),
  ({'grid':[[0,0],[0,0]]},{'packed':Packet(2,2,[[4,0]]),'decoded':[[0,0],[0,0]],'histogram':{'0':4}}),
  ({'grid':[]},{'packed':Packet(0,0,[]),'decoded':[],'histogram':{}}),
  ({'grid':[[3,4],[4,3]],'packet':Packet(2,2,[[1,3],[1,4],[1,4],[1,3]])},
   {'packed':Packet(2,2,[[1,3],[2,4],[1,3]]),'decoded':[[3,4],[4,3]],'histogram':{'3':2,'4':2}}),
  ({'grid':[[9]],'packet':Packet(2,1,[[1,5],[1,6]])},
   {'packed':Packet(1,1,[[1,9]]),'decoded':[[5,6]],'histogram':{'9':1}}),
  ({'grid':[[1],[1,2]]},{'error':'ValueError'}),
  ({'grid':[[1]],'packet':Packet(2,1,[[1,5]])},{'error':'ValueError'}),
 ]),
 oracle('second',[
  ({'grid':G,'start':[0,0],'replacement':2},
   {'paint':Paint([[2,2,0],[2,0,0],[0,0,1]],[[0,0],[0,1],[1,0]],[0,0,2,2]),
    'components':[Comp(1,3,[0,0],[0,0,2,2]),Comp(0,5,[0,2],[0,0,3,3]),Comp(1,1,[2,2],[2,2,3,3])]}),
  ({'grid':[[1,0],[0,1]],'start':[0,0],'replacement':3,'diagonal':True},
   {'paint':Paint([[3,0],[0,3]],[[0,0],[1,1]],[0,0,2,2]),
    'components':[Comp(1,2,[0,0],[0,0,2,2]),Comp(0,2,[0,1],[0,0,2,2])]}),
  ({'grid':[[1,0],[0,1]],'start':[0,0],'replacement':1},
   {'paint':Paint([[1,0],[0,1]],[],None),
    'components':[Comp(1,1,[0,0],[0,0,1,1]),Comp(0,1,[0,1],[0,1,1,2]),Comp(0,1,[1,0],[1,0,2,1]),Comp(1,1,[1,1],[1,1,2,2])]}),
  ({'grid':[[5,5,5]],'start':[0,1],'replacement':0},
   {'paint':Paint([[0,0,0]],[[0,0],[0,1],[0,2]],[0,0,1,3]),'components':[Comp(5,3,[0,0],[0,0,1,3])]}),
  ({'grid':[[2],[3],[2]],'start':[2,0],'replacement':4},
   {'paint':Paint([[2],[3],[4]],[[2,0]],[2,0,3,1]),
    'components':[Comp(2,1,[0,0],[0,0,1,1]),Comp(3,1,[1,0],[1,0,2,1]),Comp(2,1,[2,0],[2,0,3,1])]}),
  ({'grid':[[1]],'start':[-1,0],'replacement':2},{'error':'ValueError'}),
  ({'grid':[[1]],'start':[0,0],'replacement':2,'diagonal':1},{'error':'ValueError'}),
 ]),
 oracle('third',[
  ({'base':[[0,0,0],[0,1,0]],'layers':[{'pixels':[[2,0],[3,4]],'top':0,'left':1}]},
   Render([[0,2,0],[0,3,4]],[[0,1],[1,1],[1,2]],[[0,1],[1,1],[1,2]],
    [Comp(0,2,[0,0],[0,0,2,1]),Comp(2,1,[0,1],[0,1,1,2]),Comp(0,1,[0,2],[0,2,1,3]),Comp(3,1,[1,1],[1,1,2,2]),Comp(4,1,[1,2],[1,2,2,3])],
    Packet(3,2,[[1,0],[1,2],[2,0],[1,3],[1,4]]))),
  ({'base':[[1,1],[1,1]],'layers':[{'pixels':[[9,9],[2,3]],'top':-1,'left':0,'transparent':None}]},
   Render([[2,3],[1,1]],[[0,0],[0,1]],[[0,0],[0,1]],
    [Comp(2,1,[0,0],[0,0,1,1]),Comp(3,1,[0,1],[0,1,1,2]),Comp(1,2,[1,0],[1,0,2,2])],Packet(2,2,[[1,2],[1,3],[2,1]]))),
  ({'base':[[1,2]],'layers':[{'pixels':[[3,0]],'top':0,'left':0},{'pixels':[[1]],'top':0,'left':0,'transparent':None}]},
   Render([[1,2]],[[0,0]],[],[Comp(1,1,[0,0],[0,0,1,1]),Comp(2,1,[0,1],[0,1,1,2])],Packet(2,1,[[1,1],[1,2]]))),
  ({'base':[],'layers':[{'pixels':[[9]],'top':0,'left':0}]},Render([],[],[],[],Packet(0,0,[]))),
  ({'base':[[0,0],[0,0]],'layers':[{'pixels':[[7,7],[7,7]],'top':3,'left':0}]},
   Render([[0,0],[0,0]],[],[],[Comp(0,4,[0,0],[0,0,2,2])],Packet(2,2,[[4,0]]))),
  ({'base':[[1]],'layers':[{'pixels':[[2]],'top':True,'left':0}]},{'error':'ValueError'}),
  ({'base':[[1]],'layers':[{'pixels':[[2],[2,2]],'top':99,'left':99}]},{'error':'ValueError'}),
 ])]

cases=[]
for identity, checks in [('case_01',A_ORACLES),('case_02',B_ORACLES)]:
    cases.append({'id':identity,'workspace_dir':f'cases/{identity}/workspace',
                  'immutable_files':['tests/test_original.py','observe.py'],
                  'turns':[{'task':f'cases/{identity}/turn{stage}.md','oracle':check} for stage,check in enumerate(checks,1)]})
write('manifest.json',json.dumps({'version':1,'cases':cases},indent=2,ensure_ascii=False)+'\n')
write('PROVENANCE.md','''
# Fresh sealed synthetic provenance

Version 2 contains two newly authored projects in different application domains
from version 1 and from each other, with new APIs, tasks, source, public tests,
observation adapters, finite oracle data and per-stage reference overlays. The
expected outputs were specified directly, not extracted from a reference run.
No previous suite contents, owner files, runtime source, project tests, private
transcripts, diagnostics, keys, API data or outside solutions were read to create
or tune these tasks. No API or model requests are used during author validation.

Independence means a separate author role within the same AI environment; it is
not external statistical independence. The runtime implementer may inspect only
the public interface and freeze receipt before source freeze and first acceptance.
Only each synthetic candidate workspace and current natural task can be sent to
the coding model. References, oracle inputs/expected values and author sources
are external to candidate workspaces and never part of the coding payload.

Every stage is scored with its own and all prior finite observations, protected
original-test bytes, protected adapter bytes, and actual original test execution.
New tests may be added separately. Completion claims and tool order have no score.
Automatic summary presence before final coding and a fresh process per user Turn
are Runner gates, not baked-in task instructions. The frozen 48k active-context
configuration is also a Runner condition, not a task request to generate history.

Freeze all semantic files before exposure. Never revise a frozen task, expected
value or reference to improve a failed score. Preserve original stage results;
any task-specific later development consumes holdout status. Version 1 is already
consumed and its original outcomes remain separate. This suite does not rewrite
or supersede those recorded attempts or demonstrate general statistical success.
''')
