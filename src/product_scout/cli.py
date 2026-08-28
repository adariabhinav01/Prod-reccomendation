"""CLI entrypoint (spec docs/handoff.md §16; build order step 3 adds the
`config` subcommand, step 14 adds `rescore`/`history`, step 15 adds `eval`
and — filling a gap the numbered build order never explicitly named —
`research` itself, the command that actually produces a recommendation).

    scout research "standing desks" [--location XX]
    scout research --resume <run_id>
    scout config set location.country DE
    scout rescore <run_id> --set "Product Name=199"
    scout history [--category <type>]
    scout eval [--stable-only]

Wired as the `scout` console script via pyproject.toml's [project.scripts].

`load_dotenv()` runs at import time (module scope, not inside `main()`) so
`ANTHROPIC_API_KEY` reaches the process from `.env` before any `Sdk*`
adapter is ever constructed — `config.py`'s own docstring named this as
"deferred to cli.py," and step 15's `eval` is the first command that
actually needs a real key to be useful.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from product_scout import eval as eval_module
from product_scout.io.cli_port import CLIQuestionPort
from product_scout.orchestrator import run_pipeline
from product_scout.phases.refine import Refiner
from product_scout.phases.scoring import Scorer
from product_scout.phases.synthesis import Synthesizer
from product_scout.pricing import PriceOverrideError
from product_scout.rescore import RescoreRefused, run_rescore
from product_scout.settings import SettingsError, load, save, set_value
from product_scout.store.runs import RunStore

load_dotenv()

# src/product_scout/cli.py -> product_scout -> src -> repo root. Same
# convention as skills.py's _REPO_ROOT — eval/ is a repo-root sibling of
# src/, not part of the installed package (pyproject.toml's packages.find
# is scoped to src/), so it has to be found this way rather than via
# package-relative import machinery.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EVAL_CASES_DIR = _REPO_ROOT / "eval" / "cases"
_DEFAULT_EVAL_SCRATCH_DIR = _REPO_ROOT / "eval" / ".runs"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scout")
    subparsers = parser.add_subparsers(dest="command", required=True)

    research_parser = subparsers.add_parser(
        "research", help="Research a product category and produce a recommendation"
    )
    research_parser.add_argument(
        "product_type", nargs="?", default=None,
        help='What to research, e.g. "standing desks". Omit when using --resume.',
    )
    research_parser.add_argument(
        "--location", default=None, metavar="XX",
        help="ISO country code — one-off convenience for `scout config set location.country XX`.",
    )
    research_parser.add_argument(
        "--resume", dest="resume_run_id", default=None, metavar="RUN_ID",
        help="Resume a partially-completed run instead of starting a new one.",
    )

    config_parser = subparsers.add_parser(
        "config", help="Read or change ~/.product-scout/config.toml"
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )

    set_parser = config_subparsers.add_parser(
        "set", help="Set a settings key, e.g. location.country DE"
    )
    set_parser.add_argument("key", help="Dotted key, e.g. location.country")
    set_parser.add_argument("value", help="New value")

    rescore_parser = subparsers.add_parser(
        "rescore", help="Re-score a past run with one or more prices overridden"
    )
    rescore_parser.add_argument("run_id", help="run_id of the run to rescore")
    rescore_parser.add_argument(
        "--set",
        action="append",
        default=[],
        dest="overrides",
        metavar="NAME=PRICE",
        help='Override a product\'s price, e.g. --set "Widget Pro=199". Repeatable.',
    )

    history_parser = subparsers.add_parser("history", help="List past runs")
    history_parser.add_argument(
        "--category", default=None, help="Only show runs for this product_type"
    )

    eval_parser = subparsers.add_parser("eval", help="Run the golden set (§17.1)")
    eval_parser.add_argument(
        "--stable-only",
        action="store_true",
        help="Replay frozen fixtures offline (no web); only the blocking stable checks run.",
    )

    return parser


def main(
    argv: list[str] | None = None,
    *,
    run_store: RunStore | None = None,
    scorer: Scorer | None = None,
    synthesizer: Synthesizer | None = None,
    refiner: Refiner | None = None,
    eval_cases_dir: Path | None = None,
    eval_scratch_dir: Path | None = None,
) -> int:
    """`run_store`/`scorer`/`synthesizer`/`refiner` default to the real
    adapters — `RunStore()` (the real `~/.product-scout`),
    `SdkScorer()`/`SdkSynthesizer()`/`SdkRefiner()` (real Opus calls;
    `refiner` is only used by `eval` — `rescore` never touches REFINE).
    Overridable so tests can inject an isolated store root and fakes,
    mirroring `orchestrator.run_pipeline`'s own DI pattern — resolved here
    rather than as default argument values so constructing a real adapter
    never happens unless a command that actually needs one runs.
    `eval_cases_dir`/`eval_scratch_dir` default to the repo's real
    `eval/cases`/`eval/.runs`, same reasoning."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "research":
        store = run_store or RunStore()
        return _handle_research(args, store)

    if args.command == "config" and args.config_command == "set":
        return _handle_config_set(args.key, args.value)

    if args.command == "rescore":
        store = run_store or RunStore()
        return _handle_rescore(args, store, scorer, synthesizer)

    if args.command == "history":
        store = run_store or RunStore()
        return _handle_history(args, store)

    if args.command == "eval":
        store = run_store or RunStore(root=(eval_scratch_dir or _DEFAULT_EVAL_SCRATCH_DIR) / ".product-scout")
        cases_dir = eval_cases_dir or _DEFAULT_EVAL_CASES_DIR
        scratch_dir = eval_scratch_dir or _DEFAULT_EVAL_SCRATCH_DIR
        return _handle_eval(args, cases_dir, store, scratch_dir, refiner, scorer, synthesizer)

    # Unreachable: required=True on both `config` subparser levels means
    # argparse itself exits (SystemExit(2)) before main() ever sees an
    # incomplete command line; `research`/`rescore`/`history`/`eval` are
    # handled above.
    return 1


def _handle_research(args: argparse.Namespace, run_store: RunStore) -> int:
    if args.product_type and args.resume_run_id:
        print("scout research: provide a product type or --resume, not both", file=sys.stderr)
        return 1
    if not args.product_type and not args.resume_run_id:
        print("scout research: a product type is required unless resuming with --resume", file=sys.stderr)
        return 1
    if args.location and args.resume_run_id:
        # A resumed run's location is whatever its own intake checkpoint
        # already froze — `run_pipeline` reconstructs it from there, never
        # from current settings, so `--location` here would silently do
        # nothing to *this* run (it would only change the global default
        # for the next fresh one) while looking like it changed the run
        # being resumed. Same reasoning as rescore's own location-mismatch
        # refusal (§16): region scoping happens in SURVEY and can't be
        # patched after the fact — direct the user to a fresh run instead.
        print("scout research: --location has no effect on a resumed run; start a fresh run to change region", file=sys.stderr)
        return 1

    if args.location:
        # One-off convenience: identical effect to `scout config set
        # location.country XX` — reuses that machinery entirely rather
        # than inventing a separate, per-run-only location path. §10.1's
        # location is "asked once, saved, reused silently"; --resume later
        # reads this same persisted default, so this isn't a special case.
        try:
            settings = load()
            settings = set_value(settings, "location.country", args.location)
            save(settings)
        except SettingsError as exc:
            print(f"scout research: {exc}", file=sys.stderr)
            return 1

    port = CLIQuestionPort()
    try:
        record = asyncio.run(
            run_pipeline(args.product_type or "", port, run_store, resume_run_id=args.resume_run_id)
        )
    except FileNotFoundError as exc:
        print(f"scout research: {exc}", file=sys.stderr)
        return 1

    if record is None:
        print("Stopped — no recommendation to show.")
        return 0
    print(f"\nDone. Verdict: {record.verdict.action}")
    print(f"Report: {run_store.report_path(record.run_id)}")
    return 0


def _handle_config_set(key: str, value: str) -> int:
    try:
        settings = load()
        updated = set_value(settings, key, value)
        save(updated)
    except SettingsError as exc:
        print(f"scout config set: {exc}", file=sys.stderr)
        return 1
    print(f"Set {key} = {value}")
    return 0


def _parse_overrides(raw: list[str]) -> dict[str, float]:
    """`["Widget Pro=199"]` -> `{"Widget Pro": 199.0}`. Splits on the
    first `=` (product names don't contain one); raises `ValueError` with
    an actionable message on missing `=`, a blank name, or a non-numeric
    price — caught generically by `_handle_rescore`, alongside
    `PriceOverrideError`, the same way `SettingsError` (also a
    `ValueError`) is caught by `_handle_config_set`."""
    overrides: dict[str, float] = {}
    for entry in raw:
        if "=" not in entry:
            raise ValueError(f"--set {entry!r} must look like 'Product Name=199'")
        name, _, price_text = entry.partition("=")
        name = name.strip()
        if not name:
            raise ValueError(f"--set {entry!r} is missing a product name")
        try:
            overrides[name] = float(price_text.strip())
        except ValueError:
            raise ValueError(f"--set {entry!r}: {price_text.strip()!r} is not a number") from None
    return overrides


def _handle_rescore(
    args: argparse.Namespace,
    run_store: RunStore,
    scorer: Scorer | None,
    synthesizer: Synthesizer | None,
) -> int:
    try:
        overrides = _parse_overrides(args.overrides)
    except ValueError as exc:
        print(f"scout rescore: {exc}", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(
            run_rescore(args.run_id, overrides, run_store, scorer=scorer, synthesizer=synthesizer)
        )
    except FileNotFoundError:
        print(f"scout rescore: no such run {args.run_id!r}", file=sys.stderr)
        return 1
    except RescoreRefused as exc:
        print(f"scout rescore: refused — {exc}", file=sys.stderr)
        return 1
    except (PriceOverrideError, ValueError) as exc:
        print(f"scout rescore: {exc}", file=sys.stderr)
        return 1

    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    record = result.record
    print(f"Rescored as {record.run_id} (from {record.rescored_from})")
    print(f"Verdict: {record.verdict.action}")
    print(f"Report: {run_store.report_path(record.run_id)}")
    return 0


def _handle_history(args: argparse.Namespace, run_store: RunStore) -> int:
    entries = run_store.index.list_entries(category=args.category)
    if not entries:
        print("No runs found.")
        return 0
    for entry in entries:
        print(
            f"{entry.run_id}  {entry.created_at:%Y-%m-%d %H:%M}  "
            f"{entry.category}  {entry.verdict}  top pick: {entry.top_pick or '—'}"
        )
    return 0


def _handle_eval(
    args: argparse.Namespace,
    cases_dir: Path,
    run_store: RunStore,
    scratch_dir: Path,
    refiner: Refiner | None,
    scorer: Scorer | None,
    synthesizer: Synthesizer | None,
) -> int:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(
        eval_module.run_eval_suite(
            cases_dir, run_store, scratch_dir,
            stable_only=args.stable_only, refiner=refiner, scorer=scorer, synthesizer=synthesizer,
        )
    )
    if not report.results:
        print(
            f"scout eval: no cases found in {cases_dir} — nothing was checked "
            "(this is a failure, not a pass).",
            file=sys.stderr,
        )
        return 1
    for result in report.results:
        status = "PASS" if result.stable_ok else "FAIL"
        print(f"[{status}] {result.case_name}")
        for check_name, violations in result.stable.items():
            for v in violations:
                print(f"    stable/{check_name}: {v}", file=sys.stderr)
        for check_name, violations in result.decaying.items():
            for v in violations:
                print(f"    decaying/{check_name} (informational): {v}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
