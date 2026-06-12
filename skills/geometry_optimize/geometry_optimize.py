#!/usr/bin/env python3
"""Standalone entry point for the `geometry_optimize` skill.

Self-contained: imports only the bundled `_engine` package in this folder, so
this folder runs with nothing else on the path. Delegates to the chemkit CLI
pinned to the `opt` subcommand, preserving the full argument contract.

Usage:  python geometry_optimize.py [args...]      (see --help)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _engine.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["opt", *sys.argv[1:]]))
