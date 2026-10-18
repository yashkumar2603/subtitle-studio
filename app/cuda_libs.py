"""Make pip-installed NVIDIA libraries (cuBLAS / cuDNN) visible to CTranslate2.

The optional ``gpu`` extra installs ``nvidia-cublas-cu12`` and ``nvidia-cudnn-cu12``
into site-packages. CTranslate2 looks for them through the normal system loader,
so we register their folders before the first CUDA call.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

_done = False


def _nvidia_dirs() -> list[Path]:
    dirs = []
    for entry in sys.path:
        root = Path(entry) / "nvidia"
        if root.is_dir():
            for pkg in root.iterdir():
                for sub in ("bin", "lib"):
                    d = pkg / sub
                    if d.is_dir():
                        dirs.append(d)
    return dirs


def setup() -> None:
    global _done
    if _done:
        return
    _done = True
    dirs = _nvidia_dirs()
    if os.name == "nt":
        for d in dirs:
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
        os.environ["PATH"] = os.pathsep.join([str(d) for d in dirs] + [os.environ.get("PATH", "")])
        return
    # Linux: preload in dependency order with RTLD_GLOBAL so later dlopen() calls resolve.
    order = ["libcublasLt.so", "libcublas.so", "libcudnn_graph.so", "libcudnn_ops.so", "libcudnn.so"]
    files = [f for d in dirs for f in d.glob("*.so*")]
    for prefix in order:
        for f in files:
            if f.name.startswith(prefix + "."):
                try:
                    ctypes.CDLL(str(f), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def cuda_available() -> bool:
    setup()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
