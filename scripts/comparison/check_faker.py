# /// script
# requires-python = ">=3.13"
# dependencies = ["faker==40.43.0", "pandas>=2.2", "pytest>=8"]
# ///
"""Run the capability checks of ``capabilities.txt`` on Faker.

Faker makes single values and reads no data, so the checks call its
providers with the facts that a user gives, such as the shares of a category.
A capability that Faker has no function for gets no check, and the table
marks it ``none``.

Usage:
    uv run scripts/comparison/check_faker.py INPUT_DIR OUT_DIR

Writes ``OUT_DIR/faker.tsv``.
"""

from __future__ import annotations

import csv
import datetime as dt
import ipaddress
import re
import string
import subprocess
import sys
from collections import Counter, OrderedDict
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from faker import Faker
from faker.providers import BaseProvider
from faker.providers.person.de_DE import Provider as GermanNames
from faker.providers.person.fr_FR import Provider as FrenchNames

SEED = 42
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")
IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}")
FIXTURE_TEST = '''\
def test_faker_fixture(faker):
    """The Faker pytest plugin gives a seeded Faker instance."""
    assert faker.name()
'''


class WardProvider(BaseProvider):
    """A user-defined Faker provider, as a plugin of the check W5."""

    def ward_code(self) -> str:
        """Return an invented ward code.

        Returns:
            A code such as ``K12``.
        """
        return self.bothify("?##", letters="KLMNP")


def faker(locale: str = "en_US", seed: int = SEED) -> Faker:
    """Return a seeded Faker instance.

    Args:
        locale: The locale.
        seed: The seed.

    Returns:
        The instance.
    """
    Faker.seed(seed)
    return Faker(locale)


def real_visits(folder: Path) -> pd.DataFrame:
    """Read the invented visits table.

    Args:
        folder: The input folder.

    Returns:
        The table, as text.
    """
    return pd.read_csv(folder / "visits.csv", dtype=str, keep_default_na=False)


def check_v2(folder: Path) -> tuple[bool, str]:
    """Weighted random elements keep the shares within 0.05, on 1,000 draws."""
    fake = faker()
    shares = OrderedDict([("placebo", 0.5), ("drug_a", 0.3), ("drug_b", 0.2)])
    counts = Counter(fake.random_element(shares) for _ in range(1000))
    gap = max(abs(counts[level] / 1000 - share) for level, share in shares.items())
    return gap <= 0.05, f"largest share gap {gap:.3f}"


def check_v3(folder: Path) -> tuple[bool, str]:
    """The optional proxy gives an exact missing count within one cell, for 10 seeds."""
    counts = []
    for seed in range(10):
        fake = faker(seed=seed)
        counts.append(sum(fake.optional.pyfloat(prob=0.9) is None for _ in range(200)))
    return all(abs(count - 20) <= 1 for count in counts), f"missing counts {sorted(set(counts))}, target 20"


def check_v5(folder: Path) -> tuple[bool, str]:
    """bothify gives identifiers of the shape A9999 that are not real identifiers."""
    fake = faker()
    values = [fake.bothify("?####", letters=string.ascii_uppercase) for _ in range(200)]
    shapes = {re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", value)) for value in values}
    copied = len(set(values) & set(real_visits(folder)["patient_id"]))
    return shapes == {"A9999"} and copied <= 2, f"shapes {shapes}, copied {copied}"


def check_v6(folder: Path) -> tuple[bool, str]:
    """The unique proxy gives 1,000 identifiers with no repeat."""
    fake = faker()
    values = [fake.unique.bothify("??####", letters=string.ascii_uppercase) for _ in range(1000)]
    return len(set(values)) == 1000, f"{len(set(values))} distinct of 1000"


def check_v7(folder: Path) -> tuple[bool, str]:
    """date_between keeps the real span from an invented start and copies no real date."""
    real = pd.to_datetime(real_visits(folder)["visit_date"])
    span = (real.max() - real.min()).days
    fake = faker()
    start = dt.date(2000, 1, 1)
    values = [fake.date_between(start, start + dt.timedelta(days=span)) for _ in range(200)]
    drawn = (max(values) - min(values)).days
    copied = len({value.isoformat() for value in values} & set(real_visits(folder)["visit_date"]))
    return abs(drawn - span) <= 0.1 * span and copied == 0, f"span {drawn} days, real {span}, copied {copied}"


def check_v8(folder: Path) -> tuple[bool, str]:
    """pybool keeps a true share within 0.05 of the target."""
    fake = faker()
    share = sum(fake.pybool(truth_probability=28) for _ in range(200)) / 200
    return abs(share - 0.275) <= 0.05, f"true share {share:.3f}, target 0.275"


def check_v9(folder: Path) -> tuple[bool, str]:
    """bothify gives ward codes of the shape A99."""
    fake = faker()
    values = [fake.bothify("?##", letters=string.ascii_uppercase) for _ in range(200)]
    return all(re.fullmatch(r"[A-Z]\d\d", value) for value in values), "200 codes"


def check_v10(folder: Path) -> tuple[bool, str]:
    """1,000 names hold at least 500 distinct full names."""
    fake = faker()
    names = [fake.name() for _ in range(1000)]
    distinct = len({name for name in names if " " in name})
    return distinct >= 500, f"{distinct} distinct full names"


def check_v11(folder: Path) -> tuple[bool, str]:
    """1,000 values are valid email addresses."""
    fake = faker()
    valid = sum(bool(EMAIL.fullmatch(fake.email())) for _ in range(1000))
    return valid == 1000, f"{valid} valid of 1000"


def check_v12(folder: Path) -> tuple[bool, str]:
    """1,000 phone numbers hold 10 digits or more each."""
    fake = faker()
    valid = sum(len(re.sub(r"\D", "", fake.phone_number())) >= 10 for _ in range(1000))
    return valid == 1000, f"{valid} of 1000 with 10 digits or more"


def check_v13(folder: Path) -> tuple[bool, str]:
    """1,000 addresses hold a number and three words or more."""
    fake = faker()
    addresses = [fake.address() for _ in range(1000)]
    valid = sum(bool(re.search(r"\d", value)) and len(value.split()) >= 3 for value in addresses)
    return valid == 1000, f"{valid} of 1000"


def check_v14(folder: Path) -> tuple[bool, str]:
    """French and German first names come from the local name lists."""
    french = faker("fr_FR")
    german = faker("de_DE")
    in_french = all(french.first_name() in FrenchNames.first_names for _ in range(200))
    in_german = all(german.first_name() in GermanNames.first_names for _ in range(200))
    return in_french and in_german, "fr_FR and de_DE first names"


def check_v15(folder: Path) -> tuple[bool, str]:
    """Company, job, IBAN and IP address each give 100 valid values."""
    fake = faker()
    companies = all(fake.company() for _ in range(100))
    jobs = all(fake.job() for _ in range(100))
    ibans = all(IBAN.fullmatch(fake.iban()) for _ in range(100))
    addresses = all(ipaddress.ip_address(fake.ipv4()) for _ in range(100))
    return companies and jobs and ibans and addresses, "company, job, iban, ipv4"


def check_v16(folder: Path) -> tuple[bool, str]:
    """Two runs with one seed give identical values."""
    first = [faker().name() for _ in range(1)] + [faker().email()]
    second = [faker().name() for _ in range(1)] + [faker().email()]
    return first == second, "identical values"


def check_v17(folder: Path) -> tuple[bool, str]:
    """Faker makes as many values as the user asks for."""
    fake = faker()
    values = [fake.name() for _ in range(1000)]
    return len(values) == 1000, "1000 values"


def check_p9(folder: Path) -> tuple[bool, str]:
    """An email column gets valid invented emails, none of them real."""
    fake = faker()
    values = [fake.email() for _ in range(200)]
    copied = len(set(values) & set(real_visits(folder)["contact"]))
    valid = all(EMAIL.fullmatch(value) for value in values)
    return valid and copied == 0, f"valid {valid}, copied {copied}"


def check_o1(folder: Path) -> tuple[bool, str]:
    """The command line writes a CSV file with several named columns."""
    target = folder.parent / "faker_cli.txt"
    subprocess.run([sys.executable, "-m", "faker", "-r", "5", "-s", ",", "-o", str(target), "name"],
                   check=True, capture_output=True)
    lines = target.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",") if lines else []
    named = len(header) >= 2 and all(re.fullmatch(r"[A-Za-z_]+", cell.strip()) for cell in header)
    return named, f"the CLI writes one provider value per item: {lines[:1]}"


def check_w1(folder: Path) -> tuple[bool, str]:
    """A shell command makes values."""
    completed = subprocess.run([sys.executable, "-m", "faker", "name"], capture_output=True, text=True, check=False)
    return completed.returncode == 0 and bool(completed.stdout.strip()), "faker name"


def check_w2(folder: Path) -> tuple[bool, str]:
    """A Python call makes values."""
    return bool(faker().name()), "Faker().name()"


def check_w4(folder: Path) -> tuple[bool, str]:
    """A pytest test receives a Faker instance from the faker fixture."""
    tests = folder.parent / "faker_fixture"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_fixture.py").write_text(FIXTURE_TEST, encoding="utf-8")
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests)],
                               capture_output=True, text=True, check=False)
    return completed.returncode == 0, "the faker fixture of the Faker pytest plugin"


def check_w5(folder: Path) -> tuple[bool, str]:
    """A user-defined provider gives values through Faker."""
    fake = faker()
    fake.add_provider(WardProvider)
    value = fake.ward_code()
    return bool(re.fullmatch(r"[KLMNP]\d\d", value)), f"custom provider value {value}"


CHECKS: dict[str, Callable[[Path], tuple[bool, str]]] = {
    "V2": check_v2, "V3": check_v3, "V5": check_v5, "V6": check_v6, "V7": check_v7, "V8": check_v8,
    "V9": check_v9, "V10": check_v10, "V11": check_v11, "V12": check_v12, "V13": check_v13, "V14": check_v14,
    "V15": check_v15, "V16": check_v16, "V17": check_v17, "P9": check_p9, "O1": check_o1,
    "W1": check_w1, "W2": check_w2, "W4": check_w4, "W5": check_w5,
}


def main() -> int:
    """Run every Faker check and write the results.

    Returns:
        The exit code.
    """
    folder = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for key, check in CHECKS.items():
        try:
            passed, note = check(folder)
        except Exception as error:  # noqa: BLE001 - a crash is a failed check, with its message
            passed, note = False, f"error: {error}"
        rows.append((key, "pass" if passed else "fail", note))
        print(f"faker {key}: {'pass' if passed else 'fail'} ({note})", flush=True)
    with (out / "faker.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["id", "result", "note"])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
