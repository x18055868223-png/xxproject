"""Stdlib-only local memory probe; not a Linux/server acceptance claim."""
import argparse
import csv
import ctypes
import json
import platform
import sys
import time
from pathlib import Path

from astra_joint_v11_inference import predict_row


def peak_mib():
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize / 1048576
    import resource
    factor = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor / 1048576


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    start = time.monotonic()
    before = peak_mib()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    count = 0
    with args.rows.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if float(row["target_width"]) != 2000:
                continue
            prediction = predict_row(row, artifact)
            if prediction.get("status") != "available":
                raise ValueError("probe input unavailable")
            if prediction.get("tail_probability") is not None or prediction.get("tail_probability_status") != "research_only":
                raise ValueError("tail publication containment failed")
            count += 1
            if count == 64:
                break
    after = peak_mib()
    forbidden = [name for name in ("numpy", "pandas", "scipy", "sklearn", "catboost") if name in sys.modules]
    report = {"schema": "astra_joint_v12_local_resource@1.0.0", "platform": platform.system(), "python": platform.python_version(),
              "probe_rows": count, "elapsed_seconds": time.monotonic() - start, "baseline_peak_mib": before,
              "peak_mib": after, "incremental_peak_mib": after - before, "training_modules_loaded": forbidden,
              "incremental_below_96_mib": after - before <= 96, "tail_percentages_withheld": True,
              "scope": "local frozen qualified model probe; not server resource acceptance or natural-card consumption"}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
