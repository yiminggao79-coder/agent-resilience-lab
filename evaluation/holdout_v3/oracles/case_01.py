"""External finite oracle: enumerate occupancy minute by minute, not a sweep."""
import copy
import random


def expected(records, start, stop):
    rooms = sorted({record["room"] for record in records})
    result = []
    for room in rooms:
        counts = [sum(record["room"] == room and not record.get("cancelled", False)
                      and record["start"] <= minute < record["end"]
                      for record in records)
                  for minute in range(start, stop)]
        if any(counts):
            result.append({"room": room, "busy_minutes": sum(value > 0 for value in counts),
                           "peak": max(counts)})
    return result


def cases():
    result = [
        [[], 0, 0],
        [[{"room":"b","start":-4,"end":2},{"room":"a","start":1,"end":8}],0,5],
        [[{"room":"x","start":0,"end":3},{"room":"x","start":3,"end":7}],0,7],
        [[{"room":"x","start":1,"end":6},{"room":"x","start":1,"end":6}],0,8],
        [[{"room":"x","start":0,"end":9,"cancelled":True}],1,5],
        [[{"room":"x","start":2,"end":2},{"room":"outside","start":8,"end":10}],0,5],
        [[{"room":"x","start":-9,"end":20},{"room":"x","start":2,"end":4}],0,6],
        [[{"room":"q","start":0,"end":4}],2,2],
    ]
    rng = random.Random(18047)
    for _ in range(24):
        start = rng.randrange(-4, 3)
        stop = start + rng.randrange(0, 12)
        records = []
        for index in range(rng.randrange(1, 12)):
            left = rng.randrange(-8, 12)
            records.append({"room":rng.choice(["cedar","birch","elm"]),
                            "start":left,"end":left+rng.randrange(0, 10),
                            "cancelled":index % 5 == 0})
        result.append([records,start,stop])
    return copy.deepcopy(result)

