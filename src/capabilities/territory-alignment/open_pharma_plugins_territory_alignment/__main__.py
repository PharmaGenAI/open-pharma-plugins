from pathlib import Path

from mcp_framework import run_main


def main() -> None:
    run_main(__package__ or Path(__file__).resolve().parent.name)


if __name__ == "__main__":
    main()
