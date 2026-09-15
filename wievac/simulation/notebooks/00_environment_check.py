"""Minimal environment check for pedestrian and wireless ray-tracing tools."""

import os
import sys


def _import_or_report(package_name, import_path):
    try:
        module = __import__(import_path, fromlist=["*"])
    except ImportError as error:
        print(f"{package_name} ERROR: {error}")
        print(f"  Install or activate an environment containing {package_name}.")
        return None
    return module


def _version(module):
    return getattr(module, "__version__", None)


def main():
    print(f"Python version: {sys.version}")
    print(f"Python executable: {sys.executable}")

    jupedsim = _import_or_report("JuPedSim", "jupedsim")
    if jupedsim:
        version = _version(jupedsim)
        print(f"JuPedSim OK{f' ({version})' if version else ''}")

    sionna_rt = _import_or_report("Sionna RT", "sionna.rt")
    if sionna_rt:
        print("Sionna RT OK")

    mitsuba = _import_or_report("Mitsuba", "mitsuba")
    if mitsuba:
        version = _version(mitsuba)
        print(f"Mitsuba OK{f' ({version})' if version else ''}")
        try:
            variant = mitsuba.variant()
            if "cuda" in variant.lower():
                print(f"Dr.Jit/Mitsuba backend: CUDA ({variant})")
            else:
                print(f"Dr.Jit/Mitsuba backend: {variant} (CUDA GPU not active)")
        except Exception as error:
            print(f"WARNING: Could not determine Dr.Jit/Mitsuba GPU backend: {error}")

    print(f"Working directory: {os.getcwd()}")
    print(f"Script path: {os.path.abspath(__file__)}")


if __name__ == "__main__":
    main()
