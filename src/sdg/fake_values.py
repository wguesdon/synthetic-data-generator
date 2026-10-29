"""Real-looking values from Faker, an optional dependency of sdg.

A text column with a ``provider`` field, such as ``email``, gets its values
from Faker instead of invented words, so code that parses an email or a name
reads the synthetic cells as it reads the real ones. Faker invents each value,
so it adds no privacy risk. A profile chooses the provider from the column
name only, never from the cells.

Install the extra with ``uv add "sdg[faker]"``. A ``provider`` of the form
``module:Class.method`` names a user Faker provider, which works as a plugin.
"""

from __future__ import annotations

import importlib
import re
from collections import Counter
from functools import lru_cache, partial
from typing import Any

import numpy as np

#: Locale of a provider with no ``locale`` field.
DEFAULT_LOCALE = "en_US"

#: Text of the error when the extra is not installed.
MISSING_EXTRA = 'provider: Faker is not installed; run uv add "sdg[faker]"'

#: Column name patterns, on the lower-case name with ``_`` between words, and
#: the Faker provider of each. The first match wins, so ``company_name`` is a
#: company and ``ip_address`` is an IP address, not a street address.
PROVIDER_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(pattern), provider) for pattern, provider in (
    (r"e_?mail", "email"),
    (r"phone|mobile|telephone|(^|_)tel(_|$)|(^|_)fax(_|$)", "phone_number"),
    (r"company|employer|organi[sz]ation", "company"),
    (r"first_?name|given_?name|forename|^first$", "first_name"),
    (r"last_?name|surname|family_?name|maiden|^last$", "last_name"),
    (r"(^|_)ssn(_|$)|social_security", "ssn"),
    (r"passport", "passport_number"),
    (r"^(full_?)?name$|(customer|patient|person|contact|client|user|employee|owner|member|guest|passenger)_?name$",
     "name"),
    (r"(^|_)ip(v4)?(_address)?$", "ipv4"),
    (r"street|address", "street_address"),
    (r"(^|_)(city|town)(_|$)", "city"),
    (r"(^|_)country(_|$)", "country"),
    (r"zip|post_?code|postal", "postcode"),
    (r"website|(^|_)url(_|$)|homepage", "url"),
    (r"(^|_)job(_|$)|occupation", "job"),
    (r"(^|_)iban(_|$)", "iban"),
))


def provider_for(name: object) -> str | None:
    """Choose a Faker provider from a column name only.

    Args:
        name: The real column name.

    Returns:
        The provider, such as ``email``, or None when no rule matches.
    """
    words = re.sub(r"[^a-z0-9]+", "_", re.sub(r"([a-z])([A-Z])", r"\1_\2", str(name)).lower()).strip("_")
    return next((provider for pattern, provider in PROVIDER_RULES if pattern.search(words)), None)


#: A UUID, which marks a column of keys, such as the ORGANIZATION column of
#: the Synthea encounters, whose name alone reads as a company.
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

#: A column of keys is a column where at least this share of the values are UUIDs.
KEY_SHARE = 0.9

#: Providers of codes, such as an SSN or a passport number. The real shapes of
#: such a column, when they fill half of its cells, give the real form, such
#: as the Synthea passport ``X19277260X``, which Faker writes as ``154464663``.
CODE_PROVIDERS = frozenset({"ssn", "passport_number", "postcode", "phone_number", "iban", "ipv4"})

#: Name forms and the Faker template of each. The Titanic names have the form
#: ``Braund, Mr. Owen Harris``.
TITLED_NAME = re.compile(r"[^,]+, (Mr|Mrs|Miss|Ms|Master|Dr|Rev|Mme|Mlle|Major|Col|Capt|Don|Dona|Lady|Sir)\.? .+")
COMMA_NAME = re.compile(r"[^,]+, [^,]+")
NAME_FORMS = ((TITLED_NAME, "{{last_name}}, {{prefix}} {{first_name}}"), (COMMA_NAME, "{{last_name}}, {{first_name}}"))

#: A name form holds when at least this share of the values have it.
FORM_SHARE = 0.5


def column_provider(name: object, values: Counter) -> str | None:
    """Choose the provider of a text column from its name, unless its values are keys.

    A name column whose values have the form ``Last, First`` or ``Last, Title.
    First`` gets a Faker template of that form. The provider tells the form
    only, never a value.

    Args:
        name: The real column name.
        values: Each filled value and its count.

    Returns:
        The provider, or None for a column of UUID keys or a name with no rule.
    """
    provider = provider_for(name)
    total = sum(values.values())
    keys = sum(count for value, count in values.items() if UUID.fullmatch(value.strip()))
    if provider is None or (total and keys >= KEY_SHARE * total):
        return None
    if provider == "name" and total:
        for form, template in NAME_FORMS:
            if sum(count for value, count in values.items() if form.fullmatch(value.strip())) >= FORM_SHARE * total:
                return template
    return provider


def faker_class() -> Any:
    """Import the Faker class.

    Returns:
        The class.

    Raises:
        ValueError: If the extra is not installed.
    """
    try:
        from faker import Faker
    except ImportError as error:
        raise ValueError(MISSING_EXTRA) from error
    return Faker


@lru_cache(maxsize=32)
def faker_for(locale: str) -> Any:
    """Return one Faker instance per locale.

    Args:
        locale: A Faker locale, such as ``fr_FR``.

    Returns:
        The instance.

    Raises:
        ValueError: If the extra is not installed or the locale is unknown.
    """
    try:
        return faker_class()(locale)
    except AttributeError as error:
        raise ValueError(f"locale: Faker has no locale {locale!r}") from error


def provider_method(fake: Any, provider: str) -> Any:
    """Return the method that makes one value of a provider.

    Args:
        fake: A Faker instance.
        provider: A Faker method name, such as ``email``, a template such as
            ``{{last_name}}, {{first_name}}``, or ``module:Class.method`` for a
            user provider, which this call adds to the instance.

    Returns:
        The bound method.

    Raises:
        ValueError: If the provider does not exist.
    """
    if "{{" in provider:
        try:
            fake.parse(provider)
        except AttributeError as error:
            raise ValueError(f"provider: the template {provider!r} names an unknown provider") from error
        return partial(fake.parse, provider)
    if ":" in provider:
        module_name, _, path = provider.partition(":")
        class_name, _, method = path.partition(".")
        try:
            fake.add_provider(getattr(importlib.import_module(module_name), class_name))
        except (ImportError, AttributeError) as error:
            raise ValueError(f"provider: cannot import {module_name}:{class_name}") from error
        provider = method
    method = getattr(fake, provider, None)
    if not callable(method):
        raise ValueError(f"provider: Faker has no provider {provider!r}")
    return method


def check_provider(provider: str, locale: str | None) -> None:
    """Check that a provider and a locale exist, when Faker is installed.

    A config with a provider stays valid on a machine without Faker, such as
    the secure machine that wrote it. The generation then gives the error.

    Args:
        provider: The provider.
        locale: The locale, or None for the default.

    Raises:
        ValueError: If Faker is installed and the provider or the locale does not exist.
    """
    try:
        fake = faker_for(locale or DEFAULT_LOCALE)
    except ValueError as error:
        if str(error) == MISSING_EXTRA:
            return
        raise
    provider_method(fake, provider)


def provider_values(provider: str, locale: str | None, count: int, rng: np.random.Generator) -> list[str]:
    """Draw values of a provider, on one line each.

    Args:
        provider: The provider.
        locale: The locale, or None for the default.
        count: Number of values.
        rng: Random stream of the column, which seeds Faker.

    Returns:
        The values. A multi-line value, such as an address, joins its lines with ``, ``.
    """
    fake = faker_for(locale or DEFAULT_LOCALE)
    method = provider_method(fake, provider)
    fake.seed_instance(int(rng.integers(0, 2**31 - 1)))
    return [str(method()).replace("\n", ", ") for _ in range(count)]
