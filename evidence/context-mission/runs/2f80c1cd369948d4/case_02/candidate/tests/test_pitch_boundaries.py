import copy
import unittest

import api
from pitch import parse, transpose
from score import canonical


class PitchBoundaryTests(unittest.TestCase):
    def test_accidentals_carry_and_borrow_before_range_checks(self):
        self.assertEqual(parse('B#3'), 60)
        self.assertEqual(parse('Cb4'), 59)
        self.assertEqual(parse('B#-1'), 12)
        self.assertEqual(parse('Cb0'), 11)
        with self.assertRaises(ValueError):
            parse('B#9')
        with self.assertRaises(ValueError):
            parse('Cb-1')

    def test_malformed_spellings_and_boolean_pitches_are_rejected(self):
        for value in ('C', 'C#', 'C+4', 'C##4', 'Cb#4', ' C4', 'C4 ',
                      'H4', True, -1, 128):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse(value)

    def test_transpose_validates_semitones_and_result_range(self):
        self.assertEqual(transpose('C0', -12), 0)
        self.assertEqual(transpose('B#8', 7), 127)
        for value, semitones in ((0, -1), (127, 1), (60, True)):
            with self.subTest(value=value, semitones=semitones):
                with self.assertRaises(ValueError):
                    transpose(value, semitones)


class CanonicalOrderingTests(unittest.TestCase):
    def test_start_then_pitch_then_identity_and_only_public_fields(self):
        notes = canonical([
            {'id': 'z', 'start': 0, 'duration': 1, 'pitch': 'D4',
             'velocity': 1, 'extra': 'ignored'},
            {'id': 'b', 'start': 0, 'duration': 1, 'pitch': 'C4'},
            {'id': 'a', 'start': 0, 'duration': 1, 'pitch': 'C4'},
            {'id': 'later', 'start': 1, 'duration': 2, 'pitch': 'C4'},
        ])
        self.assertEqual([note['id'] for note in notes], ['a', 'b', 'z', 'later'])
        self.assertEqual([(note['start'], note['pitch']) for note in notes],
                         [(0, 60), (0, 60), (0, 62), (1, 60)])
        self.assertEqual(set(notes[0]), {'id', 'start', 'duration',
                                         'pitch', 'velocity'})
        self.assertEqual(notes[1]['velocity'], 64)

    def test_normalize_preserves_input_and_detaches_mutable_output(self):
        events = [{'id': 'a', 'start': 0, 'duration': 1, 'pitch': 'C4'}]
        before = copy.deepcopy(events)
        report = api.normalize(events)
        self.assertEqual(events, before)
        report['events'][0]['start'] = 99
        report['metrics']['pitches'].append(0)
        self.assertEqual(events, before)


if __name__ == '__main__':
    unittest.main()
