import copy
import unittest

import api
import observe
from arrangement import build
from score import by_track


def E(key, start, duration, pitch_value=60, velocity=64):
    return {
        'id': key,
        'start': start,
        'duration': duration,
        'pitch': pitch_value,
        'velocity': velocity,
    }


class ArrangementTests(unittest.TestCase):
    def test_transform_repeat_and_crop_order(self):
        tracks = [{
            'id': 't',
            'events': [E('a', 0, 2, 60), E('b', 2, 2, 62)],
            'transform': {
                'transpose': 12,
                'stretch': [2, 1],
                'shift': 3,
                'reverse': True,
            },
            'repeat': 2,
            'crop': [5, 15],
        }]
        before = copy.deepcopy(tracks)

        result = build(tracks)

        self.assertEqual([(n['id'], n['start'], n['duration'],
                           n['pitch'], n['cycle']) for n in result['notes']],
                         [('b', 5, 2, 74, 0),
                          ('a', 7, 4, 72, 0),
                          ('b', 14, 1, 74, 1)])
        self.assertEqual(result['metrics'],
                         {'notes': 3, 'end': 15, 'duration_sum': 7,
                          'pitches': [72, 74]})
        self.assertEqual(result['tracks']['t'], result['metrics'])
        self.assertEqual(tracks, before)

    def test_overlapping_repetitions_use_explicit_period(self):
        result = build([{
            'id': 'overlap',
            'events': [E('x', 0, 4)],
            'repeat': 3,
            'period': 2,
        }])
        self.assertEqual([(n['start'], n['duration'], n['cycle'])
                          for n in result['notes']],
                         [(0, 4, 0), (2, 4, 1), (4, 4, 2)])
        self.assertEqual(result['metrics'],
                         {'notes': 3, 'end': 8, 'duration_sum': 12,
                          'pitches': [60]})

    def test_crop_is_half_open_and_keeps_global_ticks(self):
        result = build([{
            'id': 'cropped',
            'events': [E('left', 0, 2, 60),
                       E('inside', 2, 4, 62),
                       E('right', 6, 2, 64),
                       E('tail', 4, 4, 65)],
            'crop': [2, 6],
        }])
        self.assertEqual([(n['id'], n['start'], n['duration'])
                          for n in result['notes']],
                         [('inside', 2, 4), ('tail', 4, 2)])
        self.assertEqual(result['metrics'],
                         {'notes': 2, 'end': 6, 'duration_sum': 6,
                          'pitches': [62, 65]})

    def test_empty_tracks_and_reused_source_ids(self):
        empty = build([{'id': 'empty', 'events': []}])
        zeros = {'notes': 0, 'end': 0, 'duration_sum': 0, 'pitches': []}
        self.assertEqual(empty, {'notes': [],
                                 'metrics': zeros,
                                 'tracks': {'empty': zeros}})

        repeated_empty = build([{'id': 'empty', 'events': [],
                                 'repeat': 2, 'period': 3}])
        self.assertEqual(repeated_empty['notes'], [])
        self.assertEqual(repeated_empty['tracks']['empty'], zeros)

        with self.assertRaises(ValueError):
            build([{'id': 'empty', 'events': [], 'repeat': 2}])

        result = build([
            {'id': 'b', 'events': [E('x', 0, 1)]},
            {'id': 'a', 'events': [E('x', 1, 1)],
             'repeat': 2, 'period': 2},
        ])
        self.assertEqual([(n['id'], n['track'], n['cycle'], n['start'])
                          for n in result['notes']],
                         [('x', 'b', 0, 0),
                          ('x', 'a', 0, 1),
                          ('x', 'a', 1, 3)])
        self.assertEqual(sorted(result['tracks']), ['a', 'b'])

    def test_track_validation_covers_empty_and_clipped_tracks(self):
        with self.assertRaises(ValueError):
            build([{'id': 'one', 'events': []},
                   {'id': 'one', 'events': []}])
        for change in ({'id': ''},
                       {'id': ' bad'},
                       {'repeat': True},
                       {'repeat': 0},
                       {'period': False},
                       {'period': 0},
                       {'period': None},
                       {'transform': []},
                       {'transform': {'unknown': 1}},
                       {'crop': [0]},
                       {'crop': None},
                       {'crop': [2, 2]},
                       {'crop': [-1, 2]},
                       {'crop': [0, True]}):
            track = {'id': 't', 'events': []}
            track.update(change)
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    build([track])

        with self.assertRaises(ValueError):
            build([{'id': 't', 'events': [E('', 0, 1)],
                    'crop': [100, 200]}])
        with self.assertRaises(ValueError):
            build([{'id': 't', 'events': [E('x', 1, 2)],
                    'transform': {'stretch': [3, 2]},
                    'crop': [100, 200]}])

    def test_by_track_uses_global_measure_semantics(self):
        notes = [
            {'id': 'z', 'start': 10, 'duration': 2, 'pitch': 62,
             'velocity': 64, 'track': 'b', 'cycle': 0},
            {'id': 'a', 'start': 2, 'duration': 3, 'pitch': 60,
             'velocity': 64, 'track': 'a', 'cycle': 0},
        ]
        result = by_track(notes, ['b', 'empty', 'a'])
        self.assertEqual(list(result), ['a', 'b', 'empty'])
        self.assertEqual(result['a'],
                         {'notes': 1, 'end': 5, 'duration_sum': 3,
                          'pitches': [60]})
        self.assertEqual(result['b'],
                         {'notes': 1, 'end': 12, 'duration_sum': 2,
                          'pitches': [62]})
        self.assertEqual(result['empty'],
                         {'notes': 0, 'end': 0, 'duration_sum': 0,
                          'pitches': []})

    def test_api_arrange_matches_direct_build_and_preserves_inputs(self):
        tracks = [{
            'id': 'api',
            'events': [E('e', 0, 2)],
            'repeat': 2,
        }]
        direct = build(tracks)
        self.assertEqual(api.arrange(tracks), direct)

        observed = observe.third({'tracks': tracks})
        self.assertTrue(observed['preserved'])
        self.assertTrue(observed['detached'])
        self.assertTrue(observed['result']['direct_consistent'])
        self.assertEqual(observed['result']['report'], direct)


if __name__ == '__main__':
    unittest.main()
