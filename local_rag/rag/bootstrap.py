"""Ensure llama-cpp-python loads even when the system wheel is CUDA-only."""

from __future__ import annotations

import sys
from pathlib import Path

_BOOTSTRAPPED = False


def bootstrap_llama_cpp(base_dir: Path) -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    vendor = base_dir / ".cpu_llama"
    if vendor.exists():
        vendor_str = str(vendor)
        if vendor_str not in sys.path:
            sys.path.insert(0, vendor_str)

    _BOOTSTRAPPED = True
