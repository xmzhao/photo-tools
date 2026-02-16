"""Shared subprocess helpers."""

from __future__ import annotations

import json
import subprocess
from typing import Any


def run_json_command(command: list[str], *, require_success: bool = True) -> Any | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if require_success and result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

