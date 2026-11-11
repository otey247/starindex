"""Hardware detection and local-model guidance.

Reads only: CPU count, RAM, NVIDIA GPUs via ``nvidia-smi`` (no shell, short timeout) and
the execution providers ONNX Runtime reports. Nothing is installed or changed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from ..api.schemas import GpuInfo, HardwareInfo

_NVIDIA_QUERY = ["--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"]


def _memory_total_mb() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.ullTotalPhys // (1024 * 1024))
            return None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3, check=False
            ).stdout.strip()
            return int(out) // (1024 * 1024) if out.isdigit() else None
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size // (1024 * 1024))
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return None


def _nvidia_smi_path() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def parse_nvidia_smi(output: str) -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue

        def number(value: str) -> int | None:
            try:
                return int(float(value))
            except ValueError:
                return None

        gpus.append(
            GpuInfo(
                name=parts[0],
                memory_total_mb=number(parts[1]),
                memory_free_mb=number(parts[2]),
                driver=parts[3] if len(parts) > 3 and parts[3] else None,
            )
        )
    return gpus


def detect_gpus() -> list[GpuInfo]:
    exe = _nvidia_smi_path()
    if not exe:
        return []
    try:
        result = subprocess.run(
            [exe, *_NVIDIA_QUERY], capture_output=True, text=True, timeout=8, check=False, shell=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return parse_nvidia_smi(result.stdout)


def _onnx_providers() -> list[str]:
    try:
        import onnxruntime

        return list(onnxruntime.get_available_providers())
    except Exception:
        return []


def recommend(gpus: list[GpuInfo], memory_total_mb: int | None, cpu_count: int, onnx_providers: list[str]) -> list[str]:
    """Plain-language guidance. Model names come from the local server's own list, not from here."""
    tips: list[str] = []
    vram = max((g.memory_total_mb or 0 for g in gpus), default=0)
    ram_gb = (memory_total_mb or 0) / 1024
    if gpus:
        tips.append(
            f"NVIDIA GPU detected ({gpus[0].name}, about {vram / 1024:.0f} GB VRAM). Ollama, LM Studio and vLLM use "
            "CUDA automatically when the NVIDIA driver is current."
        )
        if vram >= 22_000:
            tips.append(
                "With 24 GB or more of VRAM, 4-bit quantised models in the 20-34 billion parameter range fit on the "
                "GPU and suit multi-repository research; a 7-9 billion parameter model is a fast choice for "
                "classification and summaries."
            )
        elif vram >= 15_000:
            tips.append(
                "With about 16 GB of VRAM, 4-bit quantised models around 12-14 billion parameters fit on the GPU "
                "for answers and research; use a 7-9 billion parameter model for fast tasks."
            )
        elif vram >= 11_000:
            tips.append(
                "With about 12 GB of VRAM, 4-bit quantised 7-9 billion parameter models run fully on the GPU with "
                "room for a useful context window; larger models will spill to system RAM and slow down."
            )
        elif vram >= 7_000:
            tips.append(
                "With about 8 GB of VRAM, use a 4-bit quantised 7-8 billion parameter model, or a 3-4 billion "
                "parameter model when you want long contexts to stay on the GPU."
            )
        else:
            tips.append(
                "With under 8 GB of VRAM, prefer 3-4 billion parameter models; larger ones will run partly on CPU."
            )
        if "CUDAExecutionProvider" in onnx_providers:
            tips.append("ONNX Runtime reports CUDA, so the built-in embedding and reranking models can run on the GPU.")
        else:
            tips.append(
                "Embeddings and reranking currently run on CPU, which is fine for a starred-repository library. "
                "For GPU embeddings install the optional extra: pip install \"starindex[gpu]\" (needs CUDA and cuDNN "
                "runtimes), then set the embedding device to CUDA in Settings."
            )
    else:
        tips.append(
            "No NVIDIA GPU was detected, so local models will run on CPU. Search, embeddings and reranking work "
            "well on CPU; text generation is the slow part."
        )
        if ram_gb >= 30:
            tips.append(
                "With 32 GB or more of RAM, 4-bit quantised 7-8 billion parameter models are usable on CPU for "
                "answers; expect research runs to take minutes. Use a 3-4 billion parameter model for fast tasks."
            )
        elif ram_gb >= 15:
            tips.append(
                "With about 16 GB of RAM, prefer 3-4 billion parameter models on CPU; 7-8 billion parameter models "
                "run but slowly."
            )
        else:
            tips.append(
                "With under 16 GB of RAM, use the smallest instruction-tuned models (1-3 billion parameters), or "
                "keep StarIndex in search-only mode, which needs no language model."
            )
    tips.append(
        "Keep the embedding model small and separate from the chat model: the default ONNX embedding model needs "
        "well under 1 GB of memory, and changing it later re-embeds the library in the background."
    )
    if cpu_count <= 4:
        tips.append("With four or fewer CPU threads, lower the indexing worker concurrency in Settings to keep the desktop responsive.")
    tips.append("Models are discovered from your local server; pull or load one there and it appears in the model picker.")
    return tips


def detect_hardware() -> HardwareInfo:
    gpus = detect_gpus()
    memory = _memory_total_mb()
    cpus = os.cpu_count() or 1
    providers = _onnx_providers()
    return HardwareInfo(
        os=f"{platform.system()} {platform.release()} ({platform.machine()})",
        cpu_count=cpus,
        memory_total_mb=memory,
        gpus=gpus,
        cuda_available=bool(gpus),
        onnx_providers=providers,
        recommendations=recommend(gpus, memory, cpus, providers),
    )
