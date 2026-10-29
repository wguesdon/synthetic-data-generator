"""Tests of the Faker provider of a text column (the extra sdg[faker])."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from pathlib import Path

import pytest
import yaml
from faker.providers.person.fr_FR import Provider as FrenchNames

from sdg.audit import find_leaks, sensitive_values
from sdg.config import ConfigError, parse_config
from sdg.fake_values import column_provider, provider_for
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.tables import read_tables

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")
PLUGIN = '''\
from faker.providers import BaseProvider


class WardProvider(BaseProvider):
    """A user provider of ward codes."""

    def ward_code(self):
        """Return a ward code such as K12."""
        return self.bothify("?##", letters="KLMNP")
'''


@pytest.mark.parametrize(("name", "provider"), [
    ("Email", "email"), ("email_address", "email"), ("Phone 1", "phone_number"), ("mobile", "phone_number"),
    ("First Name", "first_name"), ("lastName", "last_name"), ("Name", "name"), ("customer_name", "name"),
    ("Company", "company"), ("company_name", "company"), ("City", "city"), ("Country", "country"),
    ("Website", "url"), ("ip_address", "ipv4"), ("Street", "street_address"), ("zip", "postcode"),
    ("FIRST", "first_name"), ("LAST", "last_name"), ("MAIDEN", "last_name"), ("SSN", "ssn"),
    ("PASSPORT", "passport_number"), ("last_visit", None),
    ("studyName", None), ("Comments", None), ("Sample Number", None), ("Electricity", None),
])
def test_provider_comes_from_the_column_name(name: str, provider: str | None) -> None:
    """The column name alone chooses the provider."""
    assert provider_for(name) == provider


def test_column_of_uuid_keys_gets_no_provider() -> None:
    """A column named ORGANIZATION that holds UUID keys is a key column, not a company name."""
    keys = Counter({str(uuid.UUID(int=index)): 1 for index in range(50)})
    assert column_provider("ORGANIZATION", keys) is None
    assert column_provider("Company", Counter({"Acme Ltd": 3, "Globex": 2})) == "company"


def write_customers(path: Path) -> list[list[str]]:
    """Write an invented customer table with person columns.

    Args:
        path: The CSV file.

    Returns:
        The rows.
    """
    rows = [[f"C{1000 + index}", f"Ann{index} Lee{index}", f"ann{index}@shop-example.org", f"+44 7700 9{index:05d}",
             f"Town{index % 40}"] for index in range(150)]
    path.write_text("Customer Id,Name,Email,Phone,City\n" + "\n".join(",".join(row) for row in rows) + "\n",
                    encoding="utf-8")
    return rows


def test_profile_and_report_choose_providers(tmp_path: Path) -> None:
    """The report prints the providers, and the synthetic cells are real-looking invented values."""
    path = tmp_path / "customers.csv"
    rows = write_customers(path)
    report = glimpse([path], Options())
    providers = {column.name: fact_map(column.facts).get("provider") for column in report.tables[0].columns}
    # City repeats 40 towns, so it is a category column, which keeps its levels and gets no provider.
    # The phones share one shape, which gives their real form, so the code provider gives way to it.
    assert providers == {"Customer Id": None, "Name": "'name'", "Email": "'email'", "Phone": None, "City": None}
    result = profile([path], Options())
    text = to_yaml(result)
    assert find_leaks(text, sensitive_values([path], Options())) == []
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    generate(config, tmp_path / "out")
    table = read_tables(tmp_path / "out" / "file_1.csv")[0]
    names, emails, phones = table.column(1), table.column(2), table.column(3)
    assert len({name for name in names if " " in name}) > 100
    assert all(EMAIL.fullmatch(email) for email in emails)
    assert all(len(re.sub(r"\D", "", phone)) >= 10 for phone in phones)
    real = {cell for row in rows for cell in row}
    assert not real & set(names + emails + phones)


def text_config(provider: str, locale: str | None = None) -> dict:
    """Build a config of one text column with a provider.

    Args:
        provider: The provider.
        locale: The locale, or None.

    Returns:
        The config.
    """
    column = {"name": "value", "kind": "text", "lengths": [1, 20], "provider": provider}
    if locale:
        column["locale"] = locale
    return {"version": 1, "seed": 4, "datasets": [{"name": "t", "format": "csv", "file": "t.csv",
                                                   "sheets": [{"rows": 100, "columns": [column]}]}]}


def generated_values(config: dict, tmp_path: Path) -> list[str]:
    """Generate a config of one text column and return its cells.

    Args:
        config: The config.
        tmp_path: The test folder.

    Returns:
        The cells.
    """
    path = tmp_path / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    generate(path, tmp_path / "out")
    return [str(cell) for cell in read_tables(tmp_path / "out" / "t.csv")[0].column(0)]


def test_locale_gives_local_names(tmp_path: Path) -> None:
    """A French locale draws first names from the French list, with the same values for the same seed."""
    values = generated_values(text_config("first_name", "fr_FR"), tmp_path)
    assert all(value in FrenchNames.first_names for value in values)
    assert values == generated_values(text_config("first_name", "fr_FR"), tmp_path / "again")


def test_user_provider_works_as_a_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider module:Class.method adds a user Faker provider."""
    (tmp_path / "wards_plugin.py").write_text(PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    values = generated_values(text_config("wards_plugin:WardProvider.ward_code"), tmp_path)
    assert all(re.fullmatch(r"[KLMNP]\d\d", value) for value in values)


def test_unknown_provider_and_lone_locale_are_config_errors() -> None:
    """An unknown provider, an unknown locale and a locale with no provider fail the check."""
    with pytest.raises(ConfigError, match="no provider 'emial'"):
        parse_config(text_config("emial"))
    with pytest.raises(ConfigError, match="no locale"):
        parse_config(text_config("name", "xx_XX"))
    config = text_config("name")
    del config["datasets"][0]["sheets"][0]["columns"][0]["provider"]
    config["datasets"][0]["sheets"][0]["columns"][0]["locale"] = "fr_FR"
    with pytest.raises(ConfigError, match="a locale needs a provider"):
        parse_config(config)


def test_titled_names_keep_their_form_in_a_private_table(tmp_path: Path) -> None:
    """Titanic-like names of a private table get a Faker template of their form, and no real name leaks."""
    titles = ["Mr", "Mrs", "Miss", "Master"]
    rows = [f"{index},{'female' if index % 2 else 'male'},\"Lastname{index}, {titles[index % 4]}. First{index} Mid\","
            f"X{index:08d}Y" for index in range(60)]
    path = tmp_path / "passengers.csv"
    path.write_text("PassengerId,Sex,Name,PASSPORT\n" + "\n".join(rows) + "\n", encoding="utf-8")
    report = glimpse([path], Options())
    name, passport = (fact_map(column.facts) for column in report.tables[0].columns[2:])
    assert name["provider"] == "'{{last_name}}, {{prefix}} {{first_name}}'"
    assert passport["shapes"] == "'A99999999A' 60" and "provider" not in passport
    result = profile([path], Options())
    text = to_yaml(result)
    assert find_leaks(text, sensitive_values([path], Options())) == []
    assert find_leaks(render(report), sensitive_values([path], Options())) == []
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    generate(config, tmp_path / "out")
    table = read_tables(tmp_path / "out" / "file_1.csv")[0]
    assert all(re.fullmatch(r"[^,]+, \S+ .+", cell) for cell in table.column(2))
    assert all(re.fullmatch(r"[A-Z]\d{8}[A-Z]", cell) for cell in table.column(3))
