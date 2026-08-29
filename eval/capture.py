"""Golden-set case capture (docs/handoff.md §17.1, build order step 15).

Runs the real live pipeline exactly once via `product_scout.eval.capture_case`
and freezes the result into `eval/cases/<name>/` — but only if the captured
`RunRecord` actually exercises the classifier the case's `kind` targets
(`check_verdict_shape`, reused here rather than reimplemented). A capture
that misses is never promoted: it's left staged under
`eval/.runs/_capture_staging/<name>/` for inspection, so a bad or partial
run can never silently become a committed fixture.

Needed again for §17.1's "refresh quarterly" decaying-data maintenance,
not just this one-time capture — hence committed rather than thrown away.

Usage:
    python eval/capture.py <name> <category> <product_type> <kind> [candidates]
        [--country US] [--currency USD] [--units imperial]

`candidates`, if given, is a single raw string (may itself contain commas,
e.g. "1Password, Bitwarden") passed through untouched as the last entry of
`intake_script` — the candidates-under-consideration answer. Deliberately
NOT folded into a comma-joined --intake flag: splitting the whole intake
script on "," would shatter a multi-name candidates string into extra list
entries and desync `EvalQuestionPort.ask_text`'s answer sequence.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from product_scout.eval import EvalCase, ExpectedShape, capture_case, check_verdict_shape  # noqa: E402
from product_scout.models import Location  # noqa: E402
from product_scout.store.runs import RunStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "eval" / "cases"
SCRATCH_ROOT = REPO_ROOT / "eval" / ".runs"
STAGING_ROOT = SCRATCH_ROOT / "_capture_staging"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="case directory name, e.g. 'software'")
    parser.add_argument("category", help="human label, e.g. 'software'")
    parser.add_argument("product_type", help="e.g. 'password managers'")
    parser.add_argument(
        "kind",
        choices=["rich", "sparse", "commodity", "cross_border", "software"],
        help="which check_verdict_shape classifier this case targets",
    )
    parser.add_argument(
        "candidates", nargs="?", default="",
        help="raw candidates-under-consideration string (may contain commas)",
    )
    parser.add_argument("--country", default="US")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--units", default="imperial", choices=["imperial", "metric"])
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    case = EvalCase(
        name=args.name,
        category=args.category,
        product_type=args.product_type,
        location=Location(country=args.country, currency=args.currency),
        units=args.units,
        intake_script=["no", "no limit", "", args.candidates],
        expected_shape=ExpectedShape(kind=args.kind),
    )

    staging_dir = STAGING_ROOT / args.name
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)

    store = RunStore(root=STAGING_ROOT / ".product-scout")

    print(f"Capturing {args.name!r} ({args.product_type!r}, kind={args.kind!r}) "
          f"to staging ({staging_dir}) — NOT eval/cases/ yet...")
    try:
        record = await capture_case(staging_dir, case, store, STAGING_ROOT)
    except RuntimeError as exc:
        print(f"CAPTURE FAILED (declined or partial run): {exc}", file=sys.stderr)
        return 1

    print(f"verdict: {record.verdict.action} | low_evidence_mode: {record.low_evidence_mode} | "
          f"commodity_category: {record.commodity_category} | "
          f"category_kind: {record.survey.category_kind}")
    cross_border_hits = [
        p.name for p in record.products if p.availability.landed_price_native is not None
    ]
    print(f"cross_border products: {cross_border_hits}")
    if record.truncated_at_phase is not None:
        print(f"truncated_at_phase: {record.truncated_at_phase}", file=sys.stderr)

    violations = check_verdict_shape(record, case)
    if violations:
        print(f"CLASSIFIER MISS — expected_shape.kind={args.kind!r} not exercised: "
              f"{violations}", file=sys.stderr)
        print(f"Staged run left at {staging_dir} for inspection. NOT promoted to "
              "eval/cases/.", file=sys.stderr)
        return 1

    target = CASES_DIR / args.name
    if target.exists():
        shutil.rmtree(target)
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging_dir, target)
    print(f"CAPTURE OK — promoted to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
