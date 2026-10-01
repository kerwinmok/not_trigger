#!/usr/bin/env python3
"""Entry point: python main.py

Kept as a plain top-level script (not `python -m nottrigger.main`) so the
launch command stays exactly `python main.py`, matching how this has
always been run.
"""

from nottrigger.app import run

if __name__ == "__main__":
    run()
