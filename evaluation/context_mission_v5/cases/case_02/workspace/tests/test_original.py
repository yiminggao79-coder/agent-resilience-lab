import copy
import unittest
from pitch import parse
from score import canonical,measure
from api import normalize
def E(key='n',start=0,duration=4,pitch='C4',velocity=64): return dict(id=key,start=start,duration=duration,pitch=pitch,velocity=velocity)
class OriginalContracts(unittest.TestCase):
    def test_central_pitch(self): self.assertEqual(parse('C4'),60)
    def test_sharp_carries(self): self.assertEqual(parse('B#3'),60)
    def test_flat_borrows(self): self.assertEqual(parse('Cb4'),59)
    def test_lowercase(self): self.assertEqual(parse('db4'),61)
    def test_chronological_order(self):
        notes=canonical([E('a',8),E('z',0),E('b',0,pitch='B3')])
        self.assertEqual([n['id'] for n in notes],['b','z','a'])
    def test_integer_pitch(self): self.assertEqual(parse(127),127)
    def test_metrics(self):
        self.assertEqual(measure(canonical([E('a',0,4),E('b',2,3,pitch='D4')])),
            {'notes':2,'end':5,'duration_sum':7,'pitches':[60,62]})
    def test_owned_output(self):
        events=[E()]
        before=copy.deepcopy(events)
        normalize(events)['events'][0]['start']=99
        self.assertEqual(events,before)
    def test_duplicate_identity(self):
        with self.assertRaises(ValueError): canonical([E(),E()])
    def test_invalid_pitch(self):
        for value in [True,128,'H4','C#b4','C99']:
            with self.assertRaises(ValueError): parse(value)
    def test_invalid_time_velocity(self):
        for event in [E(start=-1),E(duration=0),E(velocity=128),E(start=True)]:
            with self.assertRaises(ValueError): canonical([event])
    def test_empty_score(self): self.assertEqual(normalize([]),{'events':[],'metrics':{'notes':0,'end':0,'duration_sum':0,'pitches':[]}})
if __name__=='__main__': unittest.main()
