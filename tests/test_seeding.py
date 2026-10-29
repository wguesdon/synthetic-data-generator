"""Tests of the keyed random streams."""

from __future__ import annotations

import numpy as np

from sdg.seeding import key_entropy, stream


def draws(seed: int, *keys: str) -> np.ndarray:
    """Draw ten numbers from one stream.

    Args:
        seed: The config seed.
        *keys: The stream keys.

    Returns:
        Ten uniform draws.
    """
    return stream(seed, *keys).random(10)


def test_same_seed_and_keys_repeat() -> None:
    """The same seed and keys give the same numbers."""
    np.testing.assert_array_equal(draws(7, "clinical", "feature_01"), draws(7, "clinical", "feature_01"))


def test_streams_differ_by_seed_and_key() -> None:
    """A different seed or key gives a different stream."""
    base = draws(7, "clinical", "feature_01")
    assert not np.array_equal(base, draws(8, "clinical", "feature_01"))
    assert not np.array_equal(base, draws(7, "clinical", "feature_02"))


def test_key_boundaries_matter() -> None:
    """Keys are hashed one by one, so ("ab", "c") and ("a", "bc") differ."""
    assert not np.array_equal(draws(1, "ab", "c"), draws(1, "a", "bc"))
    assert not np.array_equal(draws(1, "a", "b"), draws(1, "b", "a"))


def test_key_entropy_is_pinned() -> None:
    """The key hash does not change between releases, or old configs regenerate differently."""
    assert key_entropy("sdg") == 5899324949346937673
