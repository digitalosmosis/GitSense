"""GitSense: a Git and GitHub client for local repositories."""

__version__ = "1.0.0"


def main() -> None:
    import sys

    from .cli import main as cli_main

    sys.exit(cli_main())
