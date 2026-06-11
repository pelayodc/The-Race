#!/usr/bin/env python3
import json
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, SRC_DIR)

from storage import export_state  # noqa: E402
from utils.commonUtils import jsonFile  # noqa: E402


def main():
    state = export_state(jsonFile)
    if state is None:
        raise SystemExit("No state is available to export.")
    json.dump(state, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
