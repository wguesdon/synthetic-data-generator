"""The ``sdg`` command line.

Every command can run without a person: it reads its inputs from arguments,
writes plain text or, with ``--json``, one JSON document, and exits 0 on
success and 1 on a failure. An agent drives the tool through this surface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from sdg import privacy
from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.config import Config, ConfigError, load_config
from sdg.describe import ROLES, describe_file
from sdg.from_schema import DEFAULT_ROWS, from_schema
from sdg.generate import generate
from sdg.glimpse import glimpse, report_path, write_report
from sdg.profile import profile, to_yaml
from sdg.quarto import render_quarto, write_quarto
from sdg.relabel import relabel_files
from sdg.report import glimpse_tables, parse, render
from sdg.secure_report import secure_report
from sdg.templates import init_config
from sdg.validate import Validation, summary, validate
from sdg.version import SDG_VERSION


def summarize(config: Config) -> dict[str, Any]:
    """Summarize a valid config for ``sdg check``.

    Args:
        config: The validated config.

    Returns:
        The seed, the mode, the counts of each section and one summary per
        dataset.
    """
    return {
        "seed": config.seed,
        "mode": config.mode,
        "null": config.null,
        "groups": len(config.design.subjects.groups) if config.design else 0,
        "effects": len(config.effects),
        "datasets": [dataset.describe() for dataset in config.datasets],
    }


def print_summary(summary: dict[str, Any]) -> None:
    """Print the summary of a valid config as text.

    Args:
        summary: The output of :func:`summarize`.
    """
    datasets = summary["datasets"]
    print(f"Valid config: {len(datasets)} dataset(s), seed {summary['seed']}, "
          f"mode {summary['mode']}, {summary['effects']} effect(s).")
    for dataset in datasets:
        details = ", ".join(f"{key} {value}" for key, value in dataset.items()
                            if key not in {"name", "format", "file"})
        print(f"  {dataset['name']}: {dataset['format']} -> {dataset['file']}"
              + (f" ({details})" if details else ""))


def run_schema(args: argparse.Namespace) -> int:
    """Print or write the JSON schema of the config.

    Args:
        args: Parsed arguments with ``output``.

    Returns:
        The exit code, 0.
    """
    text = json.dumps(Config.model_json_schema(), indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def run_check(args: argparse.Namespace) -> int:
    """Validate a config and report the result.

    Args:
        args: Parsed arguments with ``config`` and ``json``.

    Returns:
        The exit code: 0 for a valid config, 1 for an invalid one.
    """
    try:
        config = load_config(args.config)
    except ConfigError as error:
        issues = [{"path": issue.path, "message": issue.message} for issue in error.issues]
        if args.json:
            print(json.dumps({"valid": False, "issues": issues}, indent=2))
        else:
            print(f"Invalid config: {len(issues)} issue(s).")
            for issue in issues:
                print(f"  {issue['path'] or '(file)'}: {issue['message']}")
        return 1
    summary = summarize(config)
    if args.json:
        print(json.dumps({"valid": True, "summary": summary}, indent=2))
    else:
        print_summary(summary)
    return 0


def read_options(args: argparse.Namespace) -> privacy.Options:
    """Build the privacy options from the shared arguments.

    Args:
        args: Parsed arguments of ``glimpse`` or ``profile``.

    Returns:
        The options.
    """
    extra = dict(item.split("=", 1) for item in getattr(args, "set", None) or [])
    key = privacy.LabelKey() if getattr(args, "label_key", None) else None
    return privacy.Options(private_files=re.compile(args.private, re.IGNORECASE),
                           generic_labels=args.generic_labels, max_categories=args.max_categories,
                           format=args.format, recursive=args.recursive, extra=extra, key=key,
                           drop=drop_pattern(args))


def drop_pattern(args: argparse.Namespace) -> re.Pattern[str] | None:
    """Compile the ``--drop`` pattern.

    Args:
        args: Parsed arguments.

    Returns:
        The pattern, or None when the option is not set.
    """
    drop = getattr(args, "drop", None)
    return re.compile(drop, re.IGNORECASE) if drop else None


def write_key(options: privacy.Options, path: str | None) -> None:
    """Write the label key that a glimpse or a profile collected.

    Args:
        options: The options, which hold the key.
        path: The key file, or None when no key was asked for.
    """
    if options.key is None or path is None:
        return
    options.key.write(Path(path))
    print(f"Wrote the label key {path} with {len(options.key.rows)} name(s). "
          "It holds real names: keep it on the secure machine.", file=sys.stderr)


def report_prefix(output: str) -> Path:
    """Read the ``-o`` value of a report command.

    A value that ends with a path separator names a folder, which is made when
    it does not exist, so ``-o reports/`` writes ``reports/<stem>_<timestamp>.txt``.

    Args:
        output: The ``-o`` value.

    Returns:
        A folder, or a path prefix.
    """
    prefix = Path(output)
    if output.endswith(("/", os.sep)):
        prefix.mkdir(parents=True, exist_ok=True)
    return prefix


def run_glimpse(args: argparse.Namespace) -> int:
    """Print or write the glimpse report of the targets.

    Args:
        args: Parsed arguments with ``targets``, the shared options and ``output``.

    Returns:
        The exit code: 0, or 1 when a target cannot be read.
    """
    options = read_options(args)
    try:
        report = glimpse([Path(target) for target in args.targets], options)
    except (FileNotFoundError, ValueError) as error:
        print(f"sdg glimpse: {error}", file=sys.stderr)
        return 1
    write_key(options, args.label_key)
    if args.output:
        print(f"Wrote {write_report(report, report_prefix(args.output))}")
    else:
        sys.stdout.write(render(report))
    return 0


def run_describe(args: argparse.Namespace) -> int:
    """Print or write the design or the group description of one table.

    Args:
        args: Parsed arguments with ``what``, ``file``, the role columns, the
            privacy options and ``output``.

    Returns:
        The exit code: 0, or 1 when the file or a named column cannot be read.
    """
    options = privacy.Options(private_files=re.compile(args.private, re.IGNORECASE),
                              generic_labels=args.generic_labels, max_categories=args.max_categories,
                              drop=drop_pattern(args))
    given = {role: getattr(args, role) for role in ROLES}
    try:
        report = describe_file(args.what, Path(args.file), given, options, args.sheet,
                               getattr(args, "reference", None))
    except (FileNotFoundError, ValueError) as error:
        print(f"sdg describe {args.what}: {error}", file=sys.stderr)
        return 1
    if args.output:
        print(f"Wrote {write_report(report, report_prefix(args.output), args.what)}")
    else:
        sys.stdout.write(render(report))
    return 0


def run_report(args: argparse.Namespace) -> int:
    """Print or write one audited report: the glimpse and the descriptions.

    Args:
        args: Parsed arguments with ``targets``, the shared options and ``output``.

    Returns:
        The exit code: 0, or 1 when a target cannot be read or the audit fails.
    """
    options = read_options(args)
    try:
        result = secure_report([Path(target) for target in args.targets], options)
    except (FileNotFoundError, ValueError) as error:
        print(f"sdg report: {error}", file=sys.stderr)
        return 1
    if result.findings:
        print(f"Audit failed: {len(result.findings)} real value(s) in the report. No report was written.",
              file=sys.stderr)
        for finding in result.findings:
            print(f"  line {finding.line}: {finding.kind} {finding.value!r}", file=sys.stderr)
        return 1
    write_key(options, args.label_key)
    if args.output:
        path = report_path(report_prefix(args.output), "report")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.text, encoding="utf-8")
        print(f"Wrote {path}. The audit passed; read the report before you copy it.")
    else:
        sys.stdout.write(result.text)
    return 0


def run_quarto(args: argparse.Namespace) -> int:
    """Write the Quarto report of the targets, and render it when Quarto is on the PATH.

    Args:
        args: Parsed arguments with ``targets``, ``synthetic``, ``rows``,
            ``output``, ``no_render`` and the shared options.

    Returns:
        The exit code: 0, or 1 when a target cannot be read.
    """
    options = read_options(args)
    synthetic = [Path(target) for target in args.synthetic] if args.synthetic else None
    try:
        result = write_quarto([Path(target) for target in args.targets], options, Path(args.output),
                              synthetic, args.rows)
    except (FileNotFoundError, ValueError) as error:
        print(f"sdg quarto: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {result.path}.")
    if result.secure:
        print("The report holds real values: keep it on the secure machine.", file=sys.stderr)
    if args.no_render:
        return 0
    rendered = render_quarto(result.path)
    if rendered is None:
        print(f"No HTML was rendered. Run: quarto render {result.path}")
    else:
        print(f"Rendered {rendered}.")
    return 0


def run_relabel(args: argparse.Namespace) -> int:
    """Copy real files with the generic names of a label key.

    Args:
        args: Parsed arguments with ``key``, ``targets``, ``recursive`` and ``output``.

    Returns:
        The exit code: 0, or 1 when the key or a file cannot be read, or no
        file was copied.
    """
    try:
        written, skipped = relabel_files([Path(target) for target in args.targets], Path(args.key),
                                         Path(args.output), args.recursive)
    except (FileNotFoundError, ValueError) as error:
        print(f"sdg relabel: {error}", file=sys.stderr)
        return 1
    for note in skipped:
        print(f"Skipped {note}", file=sys.stderr)
    for path, copy in written:
        print(f"Wrote {copy} from {path}")
    if not written:
        print("sdg relabel: no file of the label key was found", file=sys.stderr)
        return 1
    print("The copies hold real data: keep them on the secure machine.", file=sys.stderr)
    return 0


def run_profile(args: argparse.Namespace) -> int:
    """Write a config from real files.

    The mapping from each generic file label to its real file goes to the
    standard error stream, for the person on the secure machine. It never goes
    into the config.

    Args:
        args: Parsed arguments with ``targets``, the shared options, ``seed``
            and ``output``.

    Returns:
        The exit code: 0, or 1 when a target cannot be read.
    """
    options = read_options(args)
    try:
        result = profile([Path(target) for target in args.targets], options, args.seed)
    except (FileNotFoundError, ValueError, ConfigError) as error:
        print(f"sdg profile: {error}", file=sys.stderr)
        return 1
    for label, path in result.mapping:
        print(f"{label} = {path}", file=sys.stderr)
    write_key(options, args.label_key)
    text = to_yaml(result)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output} with {len(result.defaults)} default field(s).", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def run_audit(args: argparse.Namespace) -> int:
    """Check a config or a glimpse report against the real data.

    Args:
        args: Parsed arguments with ``checked``, ``targets``, the shared options
            and ``json``.

    Returns:
        The exit code: 0 when the text holds no real value, 1 otherwise.
    """
    try:
        values = sensitive_values([Path(target) for target in args.targets], read_options(args))
        text = Path(args.checked).read_text(encoding="utf-8")
    except (FileNotFoundError, ValueError) as error:
        print(f"sdg audit: {error}", file=sys.stderr)
        return 1
    findings = find_leaks(text, values)
    if args.json:
        print(json.dumps({"clean": not findings, "checked_values": len(values),
                          "findings": [vars(finding) for finding in findings]}, indent=2))
    elif findings:
        print(f"Audit failed: {len(findings)} real value(s) in {args.checked}.")
        for finding in findings:
            print(f"  line {finding.line}: {finding.kind} {finding.value!r}")
    else:
        print(f"Audit passed: none of {len(values)} sensitive value(s) is in {args.checked}.")
    return 1 if findings else 0


def run_generate(args: argparse.Namespace) -> int:
    """Write the synthetic data of a config.

    Args:
        args: Parsed arguments with ``config``, ``output`` and ``json``.

    Returns:
        The exit code: 0, or 1 when the config is invalid or the folder is not
        an sdg folder.
    """
    out_dir = Path(args.output)
    try:
        files = generate(Path(args.config), out_dir)
    except ConfigError as error:
        print(f"sdg generate: invalid config: {error}", file=sys.stderr)
        return 1
    except (FileExistsError, ValueError) as error:
        print(f"sdg generate: {error}", file=sys.stderr)
        return 1
    relative = [path.relative_to(out_dir).as_posix() for path in files]
    if args.json:
        print(json.dumps({"output": str(out_dir), "files": relative}, indent=2))
    else:
        print(f"Wrote {len(files)} file(s) to {out_dir}:")
        for name in relative:
            print(f"  {name}")
    return 0


def print_validation(result: Validation, alpha: float) -> None:
    """Print a validation as text.

    Args:
        result: The validation.
        alpha: Significance level of the effect tests.
    """
    print(f"Files: {result.files} checked against the manifest.")
    if result.issues:
        print(f"Problems: {len(result.issues)}.")
        for issue in result.issues:
            print(f"  {issue}")
    else:
        print("Structure: each file matches the config.")
    if not result.effects:
        return
    data = summary(result)
    if not data["effects_tested"]:
        print("Effects: none checked, because no value of an effect dataset reads back.")
    elif result.null:
        print(f"Null dataset: {sum(bool(check.found) for check in result.effects)} of {data['effects_tested']} "
              f"tests are significant at alpha {alpha}, a false positive share of {data['false_positive_share']}.")
    else:
        print(f"Effects: {data['effects_found']} of {data['effects_planted']} planted effects found at alpha {alpha}.")
    for check in result.effects:
        if check.found is None:
            print(f"  {check.dataset} {check.feature} {check.timepoint or '-'}: not checked, {check.note}")
        else:
            print(f"  {check.dataset} {check.feature} {check.timepoint or '-'}: planted log2 {check.planted:g}, "
                  f"estimated {check.estimated:g}, p {check.p_value:.3g}, {'found' if check.found else 'not found'}")


def run_validate(args: argparse.Namespace) -> int:
    """Check an output folder against its manifest, its config and its truth.

    Args:
        args: Parsed arguments with ``output``, ``alpha``, ``require_effects`` and ``json``.

    Returns:
        The exit code: 1 on a file or structure problem, or on a planted effect
        not found with ``--require-effects``; else 0.
    """
    try:
        result = validate(Path(args.output), args.alpha)
    except ConfigError as error:
        print(f"sdg validate: invalid config copy: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary(result), indent=2))
    else:
        print_validation(result, args.alpha)
    missed = [check for check in result.effects if check.found is False and check.planted != 0 and not result.null]
    return 1 if result.issues or (args.require_effects and missed) else 0


def run_init(args: argparse.Namespace) -> int:
    """Print or write a template config for the given formats, or the config of a database schema.

    Args:
        args: Parsed arguments with ``formats``, ``mode``, ``from_schema``,
            ``rows`` and ``output``.

    Returns:
        The exit code: 0, or 1 for an unknown format or a schema that does not read.
    """
    try:
        if args.from_schema:
            text = from_schema(Path(args.from_schema), args.rows)
        elif args.formats:
            text = init_config(args.formats, args.mode)
        else:
            raise ValueError("give one format name or more, or --from-schema FILE")
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"sdg init: {error}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def run_compare(args: argparse.Namespace) -> int:
    """Compare the glimpse report of the real data with that of the synthetic data.

    Args:
        args: Parsed arguments with ``real``, ``synthetic`` and ``json``.

    Returns:
        The exit code: 0 when no error differs, 1 otherwise.
    """
    try:
        real = parse(Path(args.real).read_text(encoding="utf-8"))
        synthetic = parse(Path(args.synthetic).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        print(f"sdg compare: {error}", file=sys.stderr)
        return 1
    differences = compare(real, synthetic)
    errors = [item for item in differences if item.level == "error"]
    if args.json:
        print(json.dumps({"errors": len(errors), "notes": len(differences) - len(errors),
                          "differences": [vars(item) for item in differences]}, indent=2))
    else:
        for item in differences:
            print(f"{item.level}: {item.where}: {item.message}")
        print(f"{len(errors)} error(s), {len(differences) - len(errors)} note(s) "
              f"over {len(glimpse_tables(real))} real and {len(glimpse_tables(synthetic))} synthetic table(s).")
    return 1 if errors else 0


def add_reading_options(parser: argparse.ArgumentParser) -> None:
    """Add the options that ``glimpse`` and ``profile`` share.

    Args:
        parser: The subcommand parser.
    """
    parser.add_argument("targets", nargs="+", help="Files or directories to read.")
    parser.add_argument("--format", help="Format of every target, such as olink or counts. "
                                         "The default detects it from the file suffix.")
    parser.add_argument("--private", default=privacy.DEFAULT_PRIVATE_FILES,
                        help="A file whose name matches this pattern is private: safe facts only, "
                             "and counts only for its private columns.")
    parser.add_argument("--generic-labels", action="store_true",
                        help="Replace column names, sheet names and category labels with generic names.")
    parser.add_argument("--max-categories", type=int, default=privacy.MAX_LEVELS,
                        help="Most categories, shapes or tokens listed per column.")
    parser.add_argument("--recursive", action="store_true", help="Read the subdirectories as well.")
    parser.add_argument("--drop", metavar="PATTERN",
                        help="Leave out each column of a plain table whose name matches this pattern.")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="A format setting, such as reference=genome.fa. Repeat as needed.")


def add_key_option(parser: argparse.ArgumentParser) -> None:
    """Add the ``--label-key`` option of ``glimpse`` and ``profile``.

    Args:
        parser: The subcommand parser.
    """
    parser.add_argument("--label-key", metavar="FILE",
                        help="Write a CSV of each generic name and the real name behind it. "
                             "The key holds real names: keep it on the secure machine.")


def add_describe_parser(commands: argparse._SubParsersAction) -> None:
    """Add ``sdg describe design`` and ``sdg describe groups``.

    Args:
        commands: The subcommand parsers of ``sdg``.
    """
    describe = commands.add_parser(
        "describe", help="Describe the study design or the group differences of one table, with no real value.")
    kinds = describe.add_subparsers(dest="what", required=True)
    design = kinds.add_parser("design", help="Subjects per group, timepoints, missing samples, replicates and ICC.")
    groups = kinds.add_parser("groups", help="Median and quartiles per group, and the ratio between groups.")
    groups.add_argument("--reference", help="Real name of the reference group. The default is a control level, "
                                            "else the largest group.")
    for parser in (design, groups):
        parser.add_argument("file", help="A CSV, TSV or Excel file.")
        parser.add_argument("--sheet", help="Sheet of a workbook. The default is the first sheet with a header.")
        for role in ROLES:
            parser.add_argument(f"--{role}", metavar="COLUMN",
                                help=f"The {role} column. The default finds it by name; \"\" sets none.")
        parser.add_argument("--private", default=privacy.DEFAULT_PRIVATE_FILES,
                            help="A file whose name matches this pattern records counts only.")
        parser.add_argument("--drop", metavar="PATTERN",
                            help="Leave out each column whose name matches this pattern.")
        parser.add_argument("--generic-labels", action="store_true",
                            help="Replace column names and category labels with generic names.")
        parser.add_argument("--max-categories", type=int, default=privacy.MAX_LEVELS,
                            help="Most categories per column.")
        parser.add_argument("-o", "--output",
                            help="Write the report to <OUTPUT>_<timestamp>.txt, or into a directory.")
        parser.set_defaults(handler=run_describe)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subparser per command.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="sdg", description="Synthetic datasets from a YAML config.")
    parser.add_argument("--version", action="version", version=f"sdg {SDG_VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    glimpse_parser = commands.add_parser(
        "glimpse", help="Print a structure report of real files, with no real value.")
    add_reading_options(glimpse_parser)
    glimpse_parser.add_argument("-o", "--output",
                                help="Write the report to <OUTPUT>_<timestamp>.txt, or into a directory.")
    add_key_option(glimpse_parser)
    glimpse_parser.set_defaults(handler=run_glimpse)

    add_describe_parser(commands)

    report_parser = commands.add_parser(
        "report", help="Write one audited report of real files: the glimpse, the designs and the group differences.")
    add_reading_options(report_parser)
    report_parser.add_argument("-o", "--output",
                               help="Write the report to <OUTPUT>_<timestamp>.txt, or into a directory.")
    add_key_option(report_parser)
    report_parser.set_defaults(handler=run_report)

    quarto_parser = commands.add_parser(
        "quarto", help="Write a Quarto report of a folder: columns, head, tail and plots. It holds real values.")
    quarto_parser.add_argument("targets", nargs="+", help="Files or directories, real or synthetic.")
    quarto_parser.add_argument("--synthetic", nargs="+", metavar="TARGET",
                               help="The synthetic files or the sdg output folder to put beside the targets.")
    quarto_parser.add_argument("--rows", type=int, default=5, help="Rows of the head and of the tail. 0 prints none.")
    quarto_parser.add_argument("-o", "--output", required=True, help="Folder of the report.")
    quarto_parser.add_argument("--no-render", action="store_true", help="Write the .qmd file only.")
    quarto_parser.add_argument("--format", help="Format of every target. The default detects it from the file suffix.")
    quarto_parser.add_argument("--private", default=privacy.DEFAULT_PRIVATE_FILES,
                               help="A file whose name matches this pattern is private in the column table.")
    quarto_parser.add_argument("--max-categories", type=int, default=privacy.MAX_LEVELS,
                               help="Most categories, shapes or tokens listed per column.")
    quarto_parser.add_argument("--recursive", action="store_true", help="Read the subdirectories as well.")
    quarto_parser.add_argument("--drop", metavar="PATTERN",
                               help="Leave out each column of a plain table whose name matches this pattern.")
    quarto_parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="A format setting. Repeat as needed.")
    quarto_parser.set_defaults(handler=run_quarto, generic_labels=False)

    relabel_parser = commands.add_parser(
        "relabel", help="Copy real CSV, TSV and Excel files with the generic names of a label key.")
    relabel_parser.add_argument("key", help="The label key that --label-key wrote.")
    relabel_parser.add_argument("targets", nargs="+", help="Real files or directories.")
    relabel_parser.add_argument("--recursive", action="store_true", help="Read the subdirectories as well.")
    relabel_parser.add_argument("-o", "--output", required=True,
                                help="Folder of the copies. Each copy has the generic file label as its name.")
    relabel_parser.set_defaults(handler=run_relabel)

    profile_parser = commands.add_parser("profile", help="Write a config from real files.")
    add_reading_options(profile_parser)
    profile_parser.add_argument("--seed", type=int, default=0, help="Seed to write into the config.")
    profile_parser.add_argument("-o", "--output", help="Write the config to this file.")
    add_key_option(profile_parser)
    profile_parser.set_defaults(handler=run_profile)

    audit_parser = commands.add_parser(
        "audit", help="Check a config or a glimpse report for real values.")
    audit_parser.add_argument("checked", help="The config or the glimpse report to check.")
    add_reading_options(audit_parser)
    audit_parser.add_argument("--json", action="store_true", help="Print one JSON document.")
    audit_parser.set_defaults(handler=run_audit)

    generate_parser = commands.add_parser("generate", help="Write synthetic data from a config.")
    generate_parser.add_argument("config", help="Path of the YAML config.")
    generate_parser.add_argument("-o", "--output", required=True,
                                 help="Output folder. It must be empty or an earlier sdg output.")
    generate_parser.add_argument("--json", action="store_true", help="Print one JSON document.")
    generate_parser.set_defaults(handler=run_generate)

    compare_parser = commands.add_parser(
        "compare", help="List the differences between a real and a synthetic glimpse report.")
    compare_parser.add_argument("real", help="Glimpse report of the real data.")
    compare_parser.add_argument("synthetic", help="Glimpse report of the synthetic output.")
    compare_parser.add_argument("--json", action="store_true", help="Print one JSON document.")
    compare_parser.set_defaults(handler=run_compare)

    validate_parser = commands.add_parser(
        "validate", help="Check an output folder against its manifest, its config and its truth tables.")
    validate_parser.add_argument("output", help="The output folder of sdg generate.")
    validate_parser.add_argument("--alpha", type=float, default=0.05, help="Significance level of the effect tests.")
    validate_parser.add_argument("--require-effects", action="store_true",
                                 help="Exit 1 when a planted effect is not found.")
    validate_parser.add_argument("--json", action="store_true", help="Print one JSON document.")
    validate_parser.set_defaults(handler=run_validate)

    schema = commands.add_parser("schema", help="Print the JSON schema of the config.")
    schema.add_argument("-o", "--output", help="Write the schema to this file.")
    schema.set_defaults(handler=run_schema)

    init_parser = commands.add_parser("init", help="Write a template config with a comment on each field.")
    init_parser.add_argument("formats", nargs="*", help="One format name per dataset, such as excel or olink.")
    init_parser.add_argument("--from-schema", metavar="FILE",
                             help="Write the config of a database schema: a SQLite file or CREATE TABLE statements.")
    init_parser.add_argument("--rows", type=int, default=DEFAULT_ROWS,
                             help="Rows of each table of a schema config.")
    init_parser.add_argument("--mode", choices=["resample", "model"], default="resample",
                             help="resample draws each column on its own; model adds a design and effects.")
    init_parser.add_argument("-o", "--output", help="Write the template to this file.")
    init_parser.set_defaults(handler=run_init)

    check = commands.add_parser("check", help="Validate a config without generating data.")
    check.add_argument("config", help="Path of the YAML config.")
    check.add_argument("--json", action="store_true", help="Print one JSON document.")
    check.set_defaults(handler=run_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``sdg`` command line.

    Args:
        argv: Arguments without the program name. ``None`` reads ``sys.argv``.

    Returns:
        The exit code of the command.
    """
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
