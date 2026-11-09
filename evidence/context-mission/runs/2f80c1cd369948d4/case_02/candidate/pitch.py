"""Spelled pitches or integers in the 0..127 palette."""
import re
def parse(value):
    if type(value) is int:
        if 0<=value<=127: return value
        raise ValueError('pitch out of range')
    if not isinstance(value,str): raise ValueError('invalid pitch')
    found=re.fullmatch(r'([A-Ga-g])([#b]?)(-?[0-9]+)',value)
    if found is None: raise ValueError('invalid pitch spelling')
    letter,accidental,octave=found.groups()
    semitone={'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[letter.upper()]
    alteration={'':0,'#':1,'b':-1}[accidental]
    # Apply the accidental to the absolute pitch before validating its range;
    # this lets sharps/flats carry or borrow into adjacent octaves.
    result=12*(int(octave)+1)+semitone+alteration
    if not 0<=result<=127: raise ValueError('pitch out of range')
    return result


def transpose(value,semitones):
    if type(semitones) is not int: raise ValueError('invalid semitones')
    result=parse(value)+semitones
    if not 0<=result<=127: raise ValueError('pitch out of range')
    return result
