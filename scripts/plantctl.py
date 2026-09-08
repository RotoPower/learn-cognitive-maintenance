"""Shim so `python scripts/plantctl.py ...` works; the real CLI is plant/cli.py
(installed as the `plantctl` console script: `uv run plantctl ...`)."""

from plant.cli import main

if __name__ == "__main__":
    main()
