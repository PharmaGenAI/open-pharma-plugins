"""Batch enrichment for hcp-intelligence accounts.

Calls tool handle() functions directly (no MCP server) to gather data from
PubMed, ClinicalTrials.gov, ORCID, NIH RePORTER, and web search, then
optionally synthesizes structured profiles through OpenRouter's OpenAI-compatible API.

Usage:
  # Dry run — list what would be processed
  python3 scripts/batch_enrich.py --dry-run

  # Enrich one account (raw data only)
  python3 scripts/batch_enrich.py --ids HCP-AU-001

  # Enrich all Singapore HCPs with schema-validated DeepSeek synthesis via OpenRouter
  python3 scripts/batch_enrich.py --country Singapore --account-type HCP \\
      --synthesize

  # Process a user-curated CSV into a private output directory
  python3 scripts/batch_enrich.py --input-file ./accounts.csv \\
      --output-dir ./data/hcp-intelligence --synthesize

  # Resume after interruption
  python3 scripts/batch_enrich.py --resume --synthesize
"""

from __future__ import annotations

import argparse
import sys
from typing import Literal, Sequence, cast

from open_pharma_plugins_hcp_intelligence.batch import (
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SYNTHESIS_MODEL,
    DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    HCO_TOOLS,
    HCP_TOOLS,
    BatchOptions,
    BatchOutcome,
    BatchPlan,
    BatchUsageError,
    plan_batch,
    run_batch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch enrichment for hcp-intelligence accounts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    filtering = parser.add_argument_group("filtering")
    filtering.add_argument(
        "--input-file",
        help="Account CSV using the bundled fixture columns (default: bundled sample_accounts.csv)",
    )
    filtering.add_argument("--country", help="Filter by country")
    filtering.add_argument(
        "--account-type",
        type=str.upper,
        choices=("HCP", "HCO"),
        help="Filter by HCP or HCO",
    )
    filtering.add_argument("--ids", nargs="+", help="Process specific account IDs only")

    execution = parser.add_argument_group("execution")
    execution.add_argument("--concurrency", type=int, default=5, help="Parallel accounts (default: 5)")
    execution.add_argument("--resume", action="store_true", help="Skip accounts whose output JSON already exists")
    execution.add_argument("--dry-run", action="store_true", help="List accounts without processing")
    execution.add_argument(
        "--write-back",
        action="store_true",
        help="Persist results to the demo enrichment store (off by default for --input-file)",
    )

    synthesis = parser.add_argument_group("synthesis")
    synthesis.add_argument("--synthesize", action="store_true", help="Enable LLM profile synthesis")
    synthesis.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible API base URL (default: OPENROUTER_BASE_URL or OpenRouter)",
    )
    synthesis.add_argument(
        "--api-key-env",
        default="OPENROUTER_API_KEY",
        help="Config/environment key holding the API key (default: OPENROUTER_API_KEY)",
    )
    synthesis.add_argument(
        "--model",
        default=DEFAULT_SYNTHESIS_MODEL,
        help=f"Model name (default: {DEFAULT_SYNTHESIS_MODEL})",
    )
    synthesis.add_argument(
        "--reasoning-effort",
        choices=("high", "xhigh"),
        default=DEFAULT_REASONING_EFFORT,
        help=f"Reasoning effort for extraction/synthesis (default: {DEFAULT_REASONING_EFFORT})",
    )
    synthesis.add_argument(
        "--synthesis-timeout-seconds",
        type=float,
        default=DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
        help=f"Per-request synthesis timeout (default: {DEFAULT_SYNTHESIS_TIMEOUT_SECONDS:g})",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--output-dir",
        default="./batch_output",
        help="Directory for raw results (default: ./batch_output)",
    )
    return parser


def _options_from_args(args: argparse.Namespace) -> BatchOptions:
    from shared.env import get_env

    base_url = args.base_url or get_env("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)
    return BatchOptions(
        input_file=args.input_file,
        output_dir=args.output_dir,
        country=args.country,
        account_type=args.account_type,
        ids=tuple(args.ids or ()),
        concurrency=args.concurrency,
        resume=args.resume,
        write_back=args.write_back,
        synthesize=args.synthesize,
        base_url=base_url or DEFAULT_OPENROUTER_BASE_URL,
        api_key_env=args.api_key_env,
        model=args.model,
        reasoning_effort=cast(Literal["high", "xhigh"], args.reasoning_effort),
        synthesis_timeout_seconds=args.synthesis_timeout_seconds,
    )


def render_dry_run(plan: BatchPlan) -> None:
    hcp_count = sum(account["account_type"] == "HCP" for account in plan.accounts)
    hco_count = sum(account["account_type"] == "HCO" for account in plan.accounts)
    print(f"Input: {plan.input_path}")
    print(f"Output: {plan.output_dir}")
    print(f"Selected: {len(plan.accounts)} total (HCP: {hcp_count}, HCO: {hco_count})")
    print(f"Would process {len(plan.accounts)} account(s):\n")
    for account in plan.accounts:
        tools = HCP_TOOLS if account.get("account_type", "").upper() == "HCP" else HCO_TOOLS
        print(
            f"  {account['id']:16s} {account['name']:40s} "
            f"{account['account_type']:4s} {account['country']:12s} ({len(tools)} tools)"
        )
    if plan.options.synthesize:
        print(f"\nSynthesis: {plan.options.model} via {plan.options.base_url}")
        print(f"Provider: {plan.options.base_url}")
        print(f"Model: {plan.options.model}")
        print(
            f"Reasoning effort: {plan.options.reasoning_effort}; "
            f"timeout: {plan.options.synthesis_timeout_seconds:g}s; SDK retries: 0"
        )
        print(f"Timeout: {plan.options.synthesis_timeout_seconds:g}s")
    else:
        print("\nSynthesis: disabled")
        print(f"Provider: {plan.options.base_url}")
        print(f"Model: {plan.options.model}")
        print(f"Reasoning effort: {plan.options.reasoning_effort}")
        print(f"Timeout: {plan.options.synthesis_timeout_seconds:g}s")
        print("SDK retries: 0")

    print("\nPlanned artifacts:")
    for account in plan.accounts:
        print(f"  {account['id']}.json")
    print("  batch_summary.csv")
    print("  batch_manifest.json")
    print("\nNo external calls were made.")
    print("Data sharing: execution would send search query terms to configured public data providers.")
    if plan.options.synthesize:
        print(
            "Data sharing (synthesis): selected account fields and gathered evidence "
            f"would be sent to {plan.options.base_url}."
        )
    if not plan.accounts:
        print("No accounts match the filters.")


def render_outcome(outcome: BatchOutcome) -> None:
    if not outcome.manifest:
        print("No accounts match the filters.")
        return

    summary = outcome.manifest["summary"]
    print(
        "\nDone. "
        f"completed={summary['completed']}  partial={summary['partial']}  "
        f"failed={summary['failed']}  skipped={summary['skipped']}"
    )
    summary_csv = outcome.manifest["outputs"]["summary_csv"]
    print(f"Summary CSV: {summary_csv['path']}")
    print(f"Manifest: {(outcome.output_dir / 'batch_manifest.json').resolve()}")
    print(f"Output directory: {outcome.output_dir.resolve()}")
    if summary_csv["status"] == "failed":
        print(summary_csv["error"], file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        plan = plan_batch(_options_from_args(args))
        if args.dry_run:
            render_dry_run(plan)
            return 0
        outcome = run_batch(plan)
    except BatchUsageError as exc:
        parser.error(str(exc))
    render_outcome(outcome)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
