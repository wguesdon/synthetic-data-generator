"""Draw helpers of the resample mode, shared by the format modules.

In the resample mode each column or feature draws on its own from the summary
that the config holds: quantiles, shares, a value shape or a length range.
"""

from __future__ import annotations

import uuid

import numpy as np

#: Letters and digits of an invented identifier or text.
UPPER = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
LOWER = np.array(list("abcdefghijklmnopqrstuvwxyz"))
DIGITS = np.array(list("0123456789"))

#: Room of a shape beyond any real identifier count, which keeps the counts in 64 bits.
CAPACITY_LIMIT = 10 ** 12

#: The pattern of a column of UUID keys, such as the Synthea ``Id`` column. A
#: shape never equals it, because a shape holds ``A`` and ``9``.
UUID_PATTERN = "uuid"


def invented_identifiers(pattern: str, count: int, rng: np.random.Generator) -> list[str]:
    """Invent distinct identifiers that follow a shape.

    Real identifiers often share a prefix and count up, as ``AB-0001`` and
    ``AB-0002``. So the letters are drawn once for the whole column and the
    digit positions hold a running number. When the digits cannot hold the
    count, the letters change too.

    A shape of digits only, such as ``999``, is an integer, so its running
    number has no leading zero and stays within 100 to 999. A code shape with
    leading zeros, such as ``09999`` for a ZIP code, keeps its zeros and
    counts the same way in its other digits. Shapes ``9``,
    ``99`` and ``999`` of one column then give distinct integers, as the
    penguin sample numbers do. The run starts at a drawn number, because real
    integer keys often count up from the first number of their width: MIMIC
    subject 10000032 would have been a synthetic key of a run from 10000000.

    Args:
        pattern: Shape with ``A`` for a letter and ``9`` for a digit.
        count: Number of identifiers.
        rng: Random stream of the column.

    Returns:
        ``count`` distinct identifiers.
    """
    width = pattern.count("9")
    first = 1
    if width and set(pattern.lstrip("0")) == {"9"}:
        room = pattern_capacity(pattern) - count
        first = 10 ** (width - 1) + (int(rng.integers(0, room + 1)) if room > 0 else 0)
    letters = "".join(rng.choice(UPPER, size=pattern.count("A")))
    found: list[str] = []
    seen: set[str] = set()
    attempts = 0
    while len(found) < count:
        attempts += 1
        if width and first + attempts - 1 < 10 ** width:
            digits = str(first + attempts - 1).zfill(width)
        else:
            letters = "".join(rng.choice(UPPER, size=pattern.count("A")))
            digits = "".join(rng.choice(DIGITS, size=width))
        value = fill_pattern(pattern, letters, digits)
        if value in seen:
            if attempts <= 100 * count:
                continue
            value = f"{value}_{len(found) + 1}"
        seen.add(value)
        found.append(value)
    return found


def pattern_capacity(pattern: str) -> int:
    """Count the distinct identifiers that a shape can hold.

    Args:
        pattern: Shape with ``A`` for a letter and ``9`` for a digit.

    Returns:
        The count, at most ``CAPACITY_LIMIT``. A shape of digits only has no
        leading zero.
    """
    if pattern == UUID_PATTERN:
        return CAPACITY_LIMIT
    width = pattern.count("9")
    if width and set(pattern.lstrip("0")) == {"9"}:
        return min(9 * 10 ** (width - 1), CAPACITY_LIMIT)
    return min(26 ** pattern.count("A") * 10 ** width, CAPACITY_LIMIT)


def pattern_counts(patterns: dict[str, float], count: int) -> list[int]:
    """Split a number of distinct identifiers over shapes, within the room of each shape.

    Each shape gets its share, rounded by the largest remainder method. A share
    that a shape cannot hold, such as 12 values for the shape ``9``, goes to the
    shapes with room left, the roomiest first.

    Args:
        patterns: Each shape and its share of the distinct identifiers.
        count: Number of distinct identifiers.

    Returns:
        The count of each shape, in the order of ``patterns``.
    """
    shares = np.array(list(patterns.values()), dtype=float)
    counts = share_counts(list(shares / shares.sum()), count)[:-1]
    room = np.array([pattern_capacity(pattern) for pattern in patterns]) - counts
    excess = int(-room[room < 0].sum())
    counts = np.minimum(counts, counts + room)
    for index in np.argsort(-room, kind="stable"):
        moved = min(excess, max(int(room[index]), 0))
        counts[index] += moved
        excess -= moved
    # When no shape has room, the most common shape takes the rest with suffixes.
    counts[0] += excess
    return [int(value) for value in counts]


def invented_uuids(count: int, rng: np.random.Generator) -> list[str]:
    """Invent random version 4 UUIDs from the random stream of the column.

    Args:
        count: Number of UUIDs.
        rng: Random stream of the column.

    Returns:
        ``count`` UUIDs as text, such as ``1d604da9-9a81-4ba9-80c2-de3375d59b40``.
    """
    return [str(uuid.UUID(bytes=rng.bytes(16), version=4)) for _ in range(count)]


def fill_pattern(pattern: str, letters: str, digits: str) -> str:
    """Put letters and digits into the positions of a shape.

    Args:
        pattern: Shape with ``A`` for a letter and ``9`` for a digit.
        letters: One letter per ``A``, in order.
        digits: One digit per ``9``, in order.

    Returns:
        The filled value. Other characters of the shape stay as written.
    """
    letter_iter, digit_iter = iter(letters), iter(digits)
    return "".join(next(letter_iter) if char == "A" else next(digit_iter) if char == "9" else char
                   for char in pattern)


def shaped_text(pattern: str, rng: np.random.Generator) -> str:
    """Invent one text of a shape, with a random letter or digit in each position.

    Args:
        pattern: Shape with ``A`` for a letter and ``9`` for a digit, such as
            ``AA 99999`` for a ticket.
        rng: Random stream of the column.

    Returns:
        The text, in upper-case letters. Other characters of the shape stay as written.
    """
    letters = "".join(rng.choice(UPPER, size=pattern.count("A")))
    digits = "".join(rng.choice(DIGITS, size=pattern.count("9")))
    return fill_pattern(pattern, letters, digits)


def invented_text(length: int, rng: np.random.Generator) -> str:
    """Invent a text of lower-case words with an exact length.

    Args:
        length: Number of characters.
        rng: Random stream of the column.

    Returns:
        The text.
    """
    if length == 0:
        return ""
    chars = rng.choice(LOWER, size=length)
    spaces = rng.random(length) < 0.17
    spaces[0] = spaces[-1] = False
    return "".join(" " if space else str(char) for char, space in zip(chars, spaces, strict=True))


def quantile_draws(quantiles: list[float], uniforms: np.ndarray) -> np.ndarray:
    """Map uniform draws through the piecewise linear quantile function.

    Args:
        quantiles: Values at evenly spaced probabilities from 0 to 1.
        uniforms: Draws between 0 and 1.

    Returns:
        The values.
    """
    return np.interp(uniforms, np.linspace(0.0, 1.0, len(quantiles)), quantiles)


def share_counts(shares: list[float], count: int) -> np.ndarray:
    """Split a number of cells over shares by the largest remainder method.

    Args:
        shares: Share of each option. The rest of 1 is the share of no option.
        count: Number of cells.

    Returns:
        The cell count of each option, then of no option. The counts sum to
        ``count``.
    """
    weights = np.append(np.asarray(shares, dtype=float), max(0.0, 1.0 - float(np.sum(shares))))
    exact = weights / weights.sum() * count
    counts = np.floor(exact).astype(int)
    order = np.argsort(counts - exact, kind="stable")
    counts[order[:count - int(counts.sum())]] += 1
    return counts


def pick_shares(shares: list[float], count: int, rng: np.random.Generator) -> np.ndarray:
    """Give each cell one of several options by share, or none.

    Each option gets its share of the cells, rounded by the largest remainder
    method, in random positions. A random draw per cell missed a token that one
    cell in 17 holds in a summary sheet of the TCGA-CDR workbook in about a
    third of the seeds.

    Args:
        shares: Share of each option. The rest of 1 is the share of no option.
        count: Number of cells.
        rng: Random stream of the column.

    Returns:
        The option index of each cell, or ``len(shares)`` for no option.
    """
    counts = share_counts(shares, count)
    return rng.permutation(np.repeat(np.arange(len(counts)), counts))
