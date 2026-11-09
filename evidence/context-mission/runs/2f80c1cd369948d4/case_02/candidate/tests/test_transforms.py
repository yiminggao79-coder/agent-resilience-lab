import unittest

import api
import observe
import pitch
import rhythm
from score import measure
from transforms import apply


def E(key, start, duration, pitch_value=60, velocity=64):
    return {
        'id': key,
        'start': start,
        'duration': duration,
        'pitch': pitch_value,
        'velocity': velocity,
    }


class RhythmTests(unittest.TestCase):
    def test_ratio_accepts_identity_pairs_and_separate_arguments(self):
        self.assertEqual(rhythm.ratio(None), (1, 1))
        self.assertEqual(rhythm.ratio([3, 2]), (3, 2))
        self.assertEqual(rhythm.ratio((2, 1)), (2, 1))
        self.assertEqual(rhythm.ratio(4, 3), (4, 3))

    def test_invalid_ratios_are_rejected(self):
        for value in ([0, 1], [1, 0], [True, 1], [1, False],
                      [1, 2, 3], [1], 2, 1.5, '2/1'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    rhythm.ratio(value)

    def test_scale_is_exact_and_never_rounds(self):
        self.assertEqual(rhythm.scale(6, [3, 2]), 9)
        self.assertEqual(rhythm.scale(6, 3, 2), 9)
        self.assertEqual(rhythm.scale(8, [2, 4]), 4)
        with self.assertRaises(ValueError):
            rhythm.scale(5, [3, 2])
        with self.assertRaises(ValueError):
            rhythm.scale(1, [1, 2])


class TransformTests(unittest.TestCase):
    def test_reverse_reflects_about_original_maximum_end(self):
        events = [
            E('a', 0, 4, 60),
            E('b', 4, 2, 62),
            E('c', 2, 1, 64),
        ]
        notes = apply(events, reverse=True)
        self.assertEqual([note['id'] for note in notes], ['b', 'a', 'c'])
        self.assertEqual([(note['start'], note['duration']) for note in notes],
                         [(0, 2), (2, 4), (3, 1)])

    def test_exact_ratio_scales_starts_and_durations(self):
        notes = apply([E('a', 2, 4, pitch_value=60, velocity=99)],
                      stretch=[3, 2])
        self.assertEqual(notes, [
            {'id': 'a', 'start': 3, 'duration': 6,
             'pitch': 60, 'velocity': 99},
        ])

    def test_nonintegral_start_or_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            apply([E('a', 1, 2)], stretch=[1, 2])
        with self.assertRaises(ValueError):
            apply([E('a', 2, 1)], stretch=[1, 2])
        with self.assertRaises(ValueError):
            apply([E('a', 1, 2)], stretch=[3, 2])

    def test_combined_reverse_stretch_shift_and_transpose(self):
        events = [E('a', 2, 4, 60, 77), E('b', 6, 2, 62, 88)]
        notes = apply(events, transpose=12, stretch=[3, 2], shift=5,
                      reverse=True)
        # Original end is 8. Reverse starts are 2 and 0; scaled they are 3
        # and 0; shifted they are 8 and 5.
        self.assertEqual(notes, [
            {'id': 'b', 'start': 5, 'duration': 3,
             'pitch': 74, 'velocity': 88},
            {'id': 'a', 'start': 8, 'duration': 6,
             'pitch': 72, 'velocity': 77},
        ])

    def test_negative_final_start_is_rejected(self):
        with self.assertRaises(ValueError):
            apply([E('a', 0, 1)], shift=-1)
        with self.assertRaises(ValueError):
            apply([E('a', 2, 1)], stretch=[1, 2], shift=0)

    def test_transpose_boundaries(self):
        self.assertEqual(pitch.transpose(60, 67), 127)
        self.assertEqual(pitch.transpose(60, -60), 0)
        self.assertEqual(pitch.transpose('C4', 12), 72)
        with self.assertRaises(ValueError):
            pitch.transpose(60, 68)
        with self.assertRaises(ValueError):
            pitch.transpose(60, -61)
        with self.assertRaises(ValueError):
            pitch.transpose('C4', True)
        with self.assertRaises(ValueError):
            pitch.transpose('C4', 1.0)

    def test_empty_score_still_validates_all_options(self):
        invalid_options = [
            {'transpose': True},
            {'transpose': 1.0},
            {'shift': False},
            {'shift': 2.0},
            {'reverse': 1},
            {'reverse': None},
            {'stretch': [0, 1]},
            {'stretch': [1, 0]},
            {'stretch': [True, 1]},
            {'stretch': [1, 2, 3]},
            {'stretch': [1]},
            {'stretch': 2},
        ]
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    apply([], **options)

    def test_empty_score_with_valid_options(self):
        self.assertEqual(apply([], transpose=3, stretch=None, shift=-5,
                               reverse=True), [])
        self.assertEqual(api.transform_report([]), {
            'events': [],
            'metrics': {'notes': 0, 'end': 0, 'duration_sum': 0,
                        'pitches': []},
        })

    def test_observed_transform_report_matches_direct_interfaces(self):
        events = [
            E('a', 2, 4, 60, 77),
            E('b', 6, 2, 62, 88),
        ]
        payload = {
            'events': events,
            'options': {
                'transpose': 12,
                'stretch': [3, 2],
                'shift': 5,
                'reverse': True,
            },
        }
        observed = observe.second(payload)
        self.assertTrue(observed['preserved'])
        self.assertTrue(observed['detached'])
        self.assertTrue(observed['result']['direct_consistent'])
        self.assertEqual(observed['result']['report']['events'], [
            {'id': 'b', 'start': 5, 'duration': 3,
             'pitch': 74, 'velocity': 88},
            {'id': 'a', 'start': 8, 'duration': 6,
             'pitch': 72, 'velocity': 77},
        ])

    def test_report_is_detached_and_preserves_input(self):
        events = [E('a', 0, 2, 60, 80), E('b', 2, 2, 62, 90)]
        before = [dict(event) for event in events]
        report = api.transform_report(events, transpose=1, stretch=[2, 1])
        self.assertEqual(events, before)
        self.assertEqual(report['metrics'], measure(report['events']))
        report['events'][0]['start'] = 999
        report['events'].append(E('c', 10, 1))
        report['metrics']['pitches'].append(0)
        self.assertEqual(events, before)
        self.assertEqual(apply(events), [
            {'id': 'a', 'start': 0, 'duration': 2,
             'pitch': 60, 'velocity': 80},
            {'id': 'b', 'start': 2, 'duration': 2,
             'pitch': 62, 'velocity': 90},
        ])


if __name__ == '__main__':
    unittest.main()
