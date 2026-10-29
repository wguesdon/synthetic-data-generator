"""``sdg init``: write a commented template config for one or more formats.

An agent or a person starts from the template, then edits each value. The
template always validates, so the first ``sdg check`` passes.
"""

from __future__ import annotations

import yaml

from sdg.config import parse_config
from sdg.formats import module_for_format
from sdg.version import SDG_VERSION

HEADER = """\
# Written by sdg init {version}. Edit each value, then run sdg check.
version: 1
seed: 1                             # 0 or more; the same seed and config give the same data
# mode: resample draws each column on its own; model draws from the design, with effects and artefacts
mode: {mode}
"""

DESIGN = """
design:
  subjects:
    prefix: "SUBJ-"                 # a subject identifier is the prefix and a number
    groups:                         # each group and its subject count
      group_a: 10
      group_b: 10
  timepoints: [baseline, week_4]    # in study order; omit for one sample per subject
  icc: 0.6                          # share of the variance between subjects
  missing_samples: 0.0              # share of samples after the first timepoint that are absent
"""

EFFECTS = """
effects:                            # known effects; truth/effects.csv records each
  - dataset: {dataset}
    feature: {feature}
    contrast: [group_a, group_b]    # the moved group first
    fold_change: 2.0                # 1.0 is no effect
    # timepoints: [week_4]          # default: every timepoint

artefacts:
  lower_limit_quantile: 0.0         # share of values censored at a lower limit
  # replicates: {{n: 3, cv_pct: 8.0}}
"""


def init_config(formats: list[str], mode: str = "resample") -> str:
    """Write a template config with one dataset per format.

    Args:
        formats: Format names, such as ``excel`` or ``olink``.
        mode: ``resample`` or ``model``.

    Returns:
        The commented YAML text. It validates.

    Raises:
        ValueError: If a format is unknown.
    """
    snippets, effect = [], None
    for index, fmt in enumerate(formats, start=1):
        name = f"dataset_{index}"
        snippet, feature = module_for_format(fmt).template(fmt, mode, name)
        snippets.append(snippet)
        if effect is None and feature is not None:
            effect = (name, feature)
    text = HEADER.format(version=SDG_VERSION, mode=mode)
    if mode == "model":
        text += DESIGN
    text += "\ndatasets:\n" + "".join(snippets)
    if mode == "model" and effect is not None:
        text += EFFECTS.format(dataset=effect[0], feature=effect[1])
    parse_config(yaml.safe_load(text))
    return text
