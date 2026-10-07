"""Entry point for `python3 -m etrace`."""

import sys


def main(argv=None):
    """Placeholder until the CLI lands (US-019)."""
    from etrace import __version__

    print(f"etrace {__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
