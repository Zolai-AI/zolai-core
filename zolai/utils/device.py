"""Device helpers — torch is imported lazily so the CPU-light base install
(`pip install -e .` without the [ml]/[gpu] extras) never hard-requires torch."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import torch


def _torch():
    """Import torch on demand; raise a clear error when the ml extra is missing."""
    try:
        import torch
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "torch is required for device helpers. Install the CPU ML stack with "
            "`./scripts/install.sh` or `pip install -e '.[ml]'`."
        ) from exc
    return torch


def get_device() -> "torch.device":
    """Get optimal device (GPU if available, else CPU with optimizations)."""
    torch = _torch()
    if torch.cuda.is_available():
        device = torch.device("cuda")
        print(f"Using GPU: {torch.cuda.get_device_name(0)}", file=sys.stderr)
        return device

    print("Using CPU — enable gradient checkpointing", file=sys.stderr)
    return torch.device("cpu")


def get_torch_dtype() -> "torch.dtype":
    """Get optimal dtype for device."""
    torch = _torch()
    if torch.cuda.is_available():
        return torch.float16
    return torch.float32  # CPU: use float32


def get_batch_size(base_size: int = 8) -> int:
    """Get safe batch size for device."""
    torch = _torch()
    if torch.cuda.is_available():
        return base_size * 2
    return max(1, base_size // 4)  # CPU: reduce batch size


def get_num_workers() -> int:
    """Get optimal num_workers for DataLoader."""
    return min(4, os.cpu_count() or 1)


def setup_cpu_optimization() -> None:
    """Apply CPU-specific optimizations."""
    torch = _torch()
    if not torch.cuda.is_available():
        # Disable CUDA overhead
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        # Enable CPU threading optimization
        torch.set_num_threads(os.cpu_count() or 4)
        torch.set_num_interop_threads(1)
