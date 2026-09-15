"""Ideal Sionna RT corridor smoke test; it is not calibrated CSI or training data."""

import json
import traceback
from pathlib import Path


LENGTH_M, WIDTH_M, HEIGHT_M, FREQUENCY_HZ = 10.0, 2.0, 3.0, 2.462e9


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def tensor_to_numpy(value: object, np):
    """Convert one tensor/array only; tuples must be unpacked by the caller."""
    if isinstance(value, tuple):
        raise TypeError("Expected one tensor/array, not a tuple. Unpack the API result first.")
    try:
        if hasattr(value, "numpy"):
            return np.asarray(value.numpy())
        return np.asarray(value)
    except (TypeError, ValueError, RuntimeError) as error:
        raise TypeError(f"Cannot convert {type(value).__name__} to a NumPy array") from error


def coefficient_array(channel_coefficients: object, np):
    """Support Sionna CIR coefficients returned as complex or real/imag tensors."""
    if isinstance(channel_coefficients, tuple):
        if len(channel_coefficients) != 2:
            raise TypeError("Expected a (real, imaginary) coefficient pair from paths.cir().")
        real, imaginary = channel_coefficients
        return tensor_to_numpy(real, np) + 1j * tensor_to_numpy(imaginary, np)
    return tensor_to_numpy(channel_coefficients, np)


def main() -> None:
    try:
        import mitsuba as mi
        mi.set_variant("llvm_ad_mono_polarized")
    except (ImportError, RuntimeError, ValueError) as error:
        print(f"Mitsuba CPU/LLVM backend cannot start: {error}")
        traceback.print_exc()
        raise SystemExit(1) from error

    try:
        import numpy as np
        from sionna.rt import (PathSolver, PlanarArray, RadioMaterial, Receiver,
                               Scene, Transmitter)
    except ImportError as error:
        print(f"Sionna RT smoke test cannot start: {error}")
        print("Install/activate the environment containing Sionna RT 2.0.1 and Mitsuba.")
        traceback.print_exc()
        raise SystemExit(1) from error

    output_dir = project_root() / "simulation" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    print("Radio smoke test đang dùng CPU/LLVM; GPU OptiX chưa được cấu hình trong WSL.")
    # Four enclosing slabs form a 10 m x 2 m x 3 m ideal empty corridor.
    material = RadioMaterial(name="corridor_material", relative_permittivity=2.5,
                             conductivity=0.02, thickness=0.1)
    transform = mi.ScalarTransform4f
    slabs = {
        "floor": ([5, 0, -0.05], [5, 1, 0.05]),
        "ceiling": ([5, 0, 3.05], [5, 1, 0.05]),
        "left_wall": ([5, -1.05, 1.5], [5, 0.05, 1.5]),
        "right_wall": ([5, 1.05, 1.5], [5, 0.05, 1.5]),
    }
    try:
        mi_scene = mi.load_dict({
            "type": "scene",
            **{name: {"type": "cube", "to_world": transform.translate(center) @ transform.scale(scale),
                       "bsdf": material} for name, (center, scale) in slabs.items()},
        })
        scene = Scene(mi_scene)
        scene.frequency = FREQUENCY_HZ
        array = PlanarArray(num_rows=1, num_cols=1, vertical_spacing=0.5,
                            horizontal_spacing=0.5, pattern="iso", polarization="V")
        scene.tx_array = array
        scene.rx_array = array
        tx = Transmitter(name="tx", position=[0.6, 0.0, 1.5])
        rx = Receiver(name="rx", position=[9.4, 0.0, 1.5])
        scene.add(tx)
        scene.add(rx)
        tx.look_at(rx)
        paths = PathSolver()(scene=scene, max_depth=3, los=True,
                             specular_reflection=True, diffuse_reflection=False,
                             refraction=False, samples_per_src=100_000, seed=2026)
        channel_coefficients, path_delays = paths.cir()
        coefficients = coefficient_array(channel_coefficients, np)
        delays_s = tensor_to_numpy(path_delays, np).astype(float, copy=False)
        valid = tensor_to_numpy(paths.valid, np).astype(bool, copy=False)
        gains = np.abs(coefficients) ** 2
        valid_delays = delays_s[np.isfinite(delays_s) & (delays_s >= 0.0)]
        valid_gains = gains[np.isfinite(gains) & (gains > 0.0)]
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        print(f"Sionna RT API/simulation error: {error}")
        print("This smoke test expects the Sionna RT 2.0.1 Scene and PathSolver APIs.")
        traceback.print_exc()
        raise SystemExit(1) from error

    backend = mi.variant()
    summary = {
        "scene_type": "ideal_empty_corridor_smoke_test",
        "purpose": "Ideal radio smoke test; not corridor/ESP calibrated CSI and not training data.",
        "corridor_m": {"length": LENGTH_M, "width": WIDTH_M, "height": HEIGHT_M},
        "carrier_frequency_hz": FREQUENCY_HZ, "max_depth": 3, "backend": backend,
        "tx_position_m": [0.6, 0.0, 1.5], "rx_position_m": [9.4, 0.0, 1.5],
        "valid_path_count": int(valid.sum()),
        "coefficients_shape": list(coefficients.shape), "delays_shape": list(delays_s.shape),
        "delay_ns": {"min": float(valid_delays.min() * 1e9), "max": float(valid_delays.max() * 1e9)} if valid_delays.size else None,
        "path_gain_linear": {"min": float(valid_gains.min()), "max": float(valid_gains.max())} if valid_gains.size else None,
    }
    (output_dir / "radio_smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("Ideal radio smoke test; not corridor/ESP calibrated CSI and not training data.")
    print(f"Backend: {backend}; valid paths: {summary['valid_path_count']}")
    if summary["valid_path_count"] == 0:
        print("WARNING: No valid propagation paths were reported; JSON summary was still written.")
    print(f"Delay statistics (ns): {summary['delay_ns']}")
    print(f"Path-gain statistics (linear): {summary['path_gain_linear']}")
    print("Preview image not written: no camera is configured for this non-interactive smoke test.")


if __name__ == "__main__":
    main()
