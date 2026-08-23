"""Command-line interface for the product recommendation agent."""

import argparse
import asyncio

from prod_rec import agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prod-rec",
        description="Recommend products for a given product type.",
    )
    parser.add_argument(
        "product_type",
        help='The kind of product to get recommendations for, e.g. "running shoes".',
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(agent.get_recommendations(args.product_type))
    print(result)


if __name__ == "__main__":
    main()
