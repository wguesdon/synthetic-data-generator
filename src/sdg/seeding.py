"""Deterministic random streams, one per named part of a config.

Each dataset, sheet and column draws from its own stream, derived from the
config seed and a stable key. A new column therefore leaves the values of every
other column unchanged, and two runs of the same config give the same data.
"""

from __future__ import annotations

import hashlib

import numpy as np


def key_entropy(key: str) -> int:
    """Map a key to a stable 64-bit integer.

    The built-in ``hash`` is salted per process, so it cannot seed a stream
    that must be the same on the next run.

    Args:
        key: A name, such as a dataset or column name.

    Returns:
        The first 8 bytes of the SHA-256 digest of the key, as an integer.
    """
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "little")


def stream(seed: int, *keys: str) -> np.random.Generator:
    """Return the random generator of one named part of a config.

    Args:
        seed: The config seed, 0 or more.
        *keys: Names that locate the part, outermost first, such as
            ``("clinical", "sheet_1", "feature_01")``.

    Returns:
        A generator that depends only on the seed and the keys.
    """
    return np.random.default_rng([seed, *(key_entropy(key) for key in keys)])
