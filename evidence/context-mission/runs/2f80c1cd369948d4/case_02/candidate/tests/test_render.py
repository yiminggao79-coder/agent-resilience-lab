import copy
import unittest

import api
import observe
import timing
from sequencer import compile as compile_arrangement


def E(key, start, duration, pitch=60, velocity=64):
    return {
        'id': key,
        'start': start,
        'duration': duration,
        'pitch': pitch,
        'velocity': velocity,
    }


class TimingTests(unittest.TestCase):
    def test_tempo_boundaries(self):
        tempo = [
            {'tick': 0, 'microseconds_per_beat': 1_000_000},
            {'tick': 480, 'microseconds_per_beat': 500_000},
        ]
        self.assertEqual(timing.to_microseconds(0, tempo, 480), 0)
        self.assertEqual(timing.to_microseconds(480, tempo, 480), 1_000_000)
        self.assertEqual(timing.to_microseconds(960, tempo, 480), 1_500_000)
        self.assertEqual(timing.to_microseconds(1200, tempo, 480), 1_750_000)

    def test_fractional_totals_are_floored_only_after_accumulation(self):
        tempo = [
            {'tick': 0, 'microseconds_per_beat': 1},
            {'tick': 2, 'microseconds_per_beat': 1},
        ]
        self.assertEqual(timing.to_microseconds(1, tempo, 3), 0)
        self.assertEqual(timing.to_microseconds(2, tempo, 3), 0)
        self.assertEqual(timing.to_microseconds(3, tempo, 3), 1)
        self.assertEqual(timing.to_microseconds(4, tempo, 3), 1)

    def test_invalid_tempo_is_checked_in_full(self):
        valid = [{'tick': 0, 'microseconds_per_beat': 1}]
        bad_tempos = [
            [],
            [{'tick': 1, 'microseconds_per_beat': 1}],
            [
                {'tick': 0, 'microseconds_per_beat': 1},
                {'tick': 0, 'microseconds_per_beat': 2},
            ],
            [
                {'tick': 0, 'microseconds_per_beat': 1},
                {'tick': 3, 'microseconds_per_beat': 1},
                {'tick': 2, 'microseconds_per_beat': 1},
            ],
            [{'tick': True, 'microseconds_per_beat': 1}],
            [{'tick': -1, 'microseconds_per_beat': 1}],
            [{'tick': 0, 'microseconds_per_beat': True}],
            [{'tick': 0, 'microseconds_per_beat': 0}],
            [{'tick': 0}],
            [('tick', 0, 'microseconds_per_beat', 1)],
            ({'tick': 0, 'microseconds_per_beat': 1},),
        ]
        for tempo in bad_tempos:
            with self.assertRaises(ValueError):
                timing.validate(tempo)

        for ticks_per_beat in [True, False, 0, -1, 1.0]:
            with self.assertRaises(ValueError):
                timing.validate(valid, ticks_per_beat)
        for tick in [True, -1, 1.0]:
            with self.assertRaises(ValueError):
                timing.to_microseconds(tick, valid, 480)

    def test_tempo_beyond_last_note_is_validated_for_empty_arrangement(self):
        tempo = [
            {'tick': 0, 'microseconds_per_beat': 1},
            {'tick': 5, 'microseconds_per_beat': 0},
        ]
        with self.assertRaises(ValueError):
            compile_arrangement([], tempo)


class CompileTests(unittest.TestCase):
    def test_empty_track_keeps_identity_and_zero_metrics(self):
        tracks = [{'id': 'empty', 'events': []}]
        before = copy.deepcopy(tracks)
        result = compile_arrangement(
            tracks, [{'tick': 0, 'microseconds_per_beat': 1000}])

        self.assertEqual(result, {
            'commands': [],
            'end_tick': 0,
            'end_microseconds': 0,
            'notes': 0,
            'tracks': ['empty'],
        })
        self.assertEqual(tracks, before)

    def test_commands_are_ordered_offs_before_ons_at_same_tick(self):
        tracks = [{
            'id': 't',
            'events': [
                E('a', 0, 2, 60, 10),
                E('b', 2, 1, 62, 20),
                E('c', 0, 2, 64, 30),
                E('d', 2, 1, 50, 40),
            ],
        }]
        result = compile_arrangement(
            tracks, [{'tick': 0, 'microseconds_per_beat': 1000}], 2)

        self.assertEqual(
            [(c['tick'], c['microseconds'], c['kind'], c['pitch'], c['id'])
             for c in result['commands']],
            [
                (0, 0, 'on', 60, 'a'),
                (0, 0, 'on', 64, 'c'),
                (2, 1000, 'off', 60, 'a'),
                (2, 1000, 'off', 64, 'c'),
                (2, 1000, 'on', 50, 'd'),
                (2, 1000, 'on', 62, 'b'),
                (3, 1500, 'off', 50, 'd'),
                (3, 1500, 'off', 62, 'b'),
            ])
        self.assertEqual(result['end_tick'], 3)
        self.assertEqual(result['end_microseconds'], 1500)
        self.assertEqual(result['notes'], 4)
        self.assertEqual(result['tracks'], ['t'])
        fields = {'tick', 'microseconds', 'kind', 'pitch', 'velocity',
                  'track', 'cycle', 'id'}
        self.assertTrue(all(set(command) == fields
                            for command in result['commands']))
        self.assertEqual([c['velocity'] for c in result['commands']],
                         [10, 30, 0, 0, 40, 20, 0, 0])

    def test_repeated_cropped_notes_keep_cycles_and_tick_coordinates(self):
        tracks = [{
            'id': 'r',
            'events': [E('x', 0, 4, 60), E('y', 2, 4, 62)],
            'repeat': 3,
            'period': 3,
            'crop': [1, 8],
        }]
        result = compile_arrangement(
            tracks, [{'tick': 0, 'microseconds_per_beat': 480}])

        self.assertEqual(result['notes'], 5)
        self.assertEqual(len(result['commands']), 10)
        self.assertEqual(result['end_tick'], 8)
        self.assertEqual(result['end_microseconds'], 8)
        starts = [(c['id'], c['cycle'], c['tick'])
                  for c in result['commands'] if c['kind'] == 'on']
        self.assertEqual(starts, [
            ('x', 0, 1), ('y', 0, 2), ('x', 1, 3),
            ('y', 1, 5), ('x', 2, 6),
        ])

    def test_fractional_end_conversion_is_floored_once(self):
        tracks = [{'id': 't', 'events': [E('a', 0, 3)]}]
        tempo = [
            {'tick': 0, 'microseconds_per_beat': 1},
            {'tick': 2, 'microseconds_per_beat': 1},
        ]
        result = compile_arrangement(tracks, tempo, 3)
        self.assertEqual(result['end_tick'], 3)
        self.assertEqual(result['end_microseconds'], 1)
        self.assertEqual([c['microseconds'] for c in result['commands']],
                         [0, 1])

    def test_api_and_observe_match_direct_compiler(self):
        tracks = [{'id': 't', 'events': [E('a', 0, 2), E('b', 2, 2)]}]
        tempo = [{'tick': 0, 'microseconds_per_beat': 500}]
        direct = compile_arrangement(tracks, tempo)
        self.assertEqual(api.render(tracks, tempo), direct)

        observed = observe.fourth({
            'tracks': tracks,
            'tempo': tempo,
            'ticks_per_beat': 480,
        })
        self.assertTrue(observed['preserved'])
        self.assertTrue(observed['detached'])
        self.assertTrue(observed['result']['direct_consistent'])
        self.assertEqual(observed['result']['report'], direct)


if __name__ == '__main__':
    unittest.main()
