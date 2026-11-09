import copy
import unittest
from api import normalize
from pitch import parse
from score import canonical, measure


def E(key='n', start=0, duration=4, pitch='C4', velocity=64):
    return dict(id=key, start=start, duration=duration, pitch=pitch, velocity=velocity)


class PitchBoundaryTests(unittest.TestCase):
    def test_sharp_carries_into_next_octave(self):
        cases = {'B#3': 60, 'B#7': 108, 'B#-1': 12, 'C#-1': 1}
        for spelling, expected in cases.items():
            with self.subTest(spelling=spelling):
                self.assertEqual(parse(spelling), expected)

    def test_flat_borrows_into_previous_octave(self):
        cases = {'Cb4': 59, 'Cb0': 11, 'Cb8': 107, 'db4': 61}
        for spelling, expected in cases.items():
            with self.subTest(spelling=spelling):
                self.assertEqual(parse(spelling), expected)

    def test_accidental_is_checked_after_arithmetic(self):
        for spelling in ['B#8', 'B#9', 'Cb-1', 'Cb-2']:
            with self.subTest(spelling=spelling):
                if spelling == 'B#8':
                    self.assertEqual(parse(spelling), 120)
                else:
                    with self.assertRaises(ValueError):
                        parse(spelling)

    def test_malformed_spellings_and_nonintegers(self):
        for value in [True, False, 1.0, None, '', 'C', '4', 'H4', 'C+4',
                      'C##4', 'Cbb4', 'cB4', 'C4 ', ' C4', 'C4.0', 'C#']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse(value)

    def test_integer_range_is_checked(self):
        for value in [-1, 128]:
            with self.assertRaises(ValueError):
                parse(value)


class CanonicalEventTests(unittest.TestCase):
    def test_order_by_start_then_pitch_then_id(self):
        events = [
            E('late', start=4, pitch='C4'),
            E('z', start=0, pitch='C4'),
            E('a', start=0, pitch='D4'),
            E('b', start=0, pitch='C4'),
            E('early-low', start=0, pitch='B3'),
        ]
        notes = canonical(events)
        self.assertEqual([note['id'] for note in notes],
                         ['early-low', 'b', 'z', 'a', 'late'])

    def test_only_canonical_fields_are_returned(self):
        notes = canonical([{'id': 'a', 'start': 1, 'duration': 2,
                            'pitch': 'B#3', 'extra': 'ignored'}])
        self.assertEqual(notes, [{'id': 'a', 'start': 1, 'duration': 2,
                                  'pitch': 60, 'velocity': 64}])

    def test_ids_must_be_nonempty_and_already_trimmed(self):
        for key in ['', '   ', ' a', 'a ', ' a ']:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    canonical([E(key)])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            canonical([E('a'), E('a')])

    def test_blank_ids_and_boolean_numbers_are_rejected(self):
        for event in [E('   '), E(start=True), E(duration=True),
                      E(velocity=True)]:
            with self.subTest(event=event):
                with self.assertRaises(ValueError):
                    canonical([event])

    def test_velocity_bounds_and_default(self):
        self.assertEqual(canonical([E(velocity=1)])[0]['velocity'], 1)
        self.assertEqual(canonical([E(velocity=127)])[0]['velocity'], 127)
        for velocity in [0, 128, -1]:
            with self.assertRaises(ValueError):
                canonical([E(velocity=velocity)])


class MeasureAndNormalizeTests(unittest.TestCase):
    def test_measure_uses_canonical_fields(self):
        notes = canonical([
            E('a', start=0, duration=4, pitch='C4'),
            E('b', start=2, duration=3, pitch='D4'),
            E('c', start=2, duration=1, pitch='C4'),
        ])
        self.assertEqual(measure(notes), {
            'notes': 3,
            'end': 5,
            'duration_sum': 8,
            'pitches': [60, 62],
        })

    def test_empty_measure(self):
        self.assertEqual(measure([]), {
            'notes': 0,
            'end': 0,
            'duration_sum': 0,
            'pitches': [],
        })

    def test_normalize_preserves_input_and_returns_detached_containers(self):
        events = [E('a', start=4, pitch='B#3'),
                  E('b', start=0, duration=2, pitch='Cb4')]
        before = copy.deepcopy(events)
        result = normalize(events)
        self.assertEqual(events, before)
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result['events'], list)
        self.assertIsInstance(result['metrics'], dict)
        self.assertIsInstance(result['metrics']['pitches'], list)
        result['events'][0]['start'] = 99
        result['metrics']['pitches'].append(0)
        self.assertEqual(events, before)
        self.assertEqual(normalize(events), normalize(before))


if __name__ == '__main__':
    unittest.main()
