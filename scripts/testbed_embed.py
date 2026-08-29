#!/usr/bin/env python3
"""Deterministic embedder for the seeded test bed (NOT for real data).

Maps query keywords onto the seed vectors used by scripts/seed_testbed.py so
canary queries retrieve the intended chunks. stdin: query text; stdout: JSON
vector.
"""
import json
import math
import random
import sys

DIM = 32


def vec(seed: int):
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    n = math.sqrt(sum(x * x for x in v)) or 1
    return [x / n for x in v]


q = sys.stdin.read().lower()
seed = (200 if "refund" in q else
        400 if "vacation" in q else
        100 if "onboard" in q else 999)
print(json.dumps(vec(seed)))
