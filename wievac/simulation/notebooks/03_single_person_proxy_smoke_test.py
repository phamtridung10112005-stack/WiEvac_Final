"""Ideal CSI smoke test only: a geometric proxy is not a real person or ESP32 CSI."""

import json
import random
import traceback
from pathlib import Path


LENGTH_M, WIDTH_M, HEIGHT_M = 10.0, 2.0, 3.0
TX_POSITION, RX_POSITION = [0.6, 0.0, 1.5], [9.4, 0.0, 1.5]
CENTER_FREQUENCY_HZ, BANDWIDTH_HZ, TONE_COUNT = 2.462e9, 20e6, 64
SEED, PERSON_AGENT_RADIUS_M = 2026, 0.22
PERSON_PROXY_WIDTH_M, PERSON_PROXY_DEPTH_M, PERSON_PROXY_HEIGHT_M = 0.44, 0.32, 1.70


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def tensor_to_numpy(value: object, np):
    """Convert one tensor/array; callers must unpack tuples first."""
    if isinstance(value, tuple):
        raise TypeError("Expected a tensor/array, not a tuple; unpack the API result first.")
    try:
        return np.asarray(value.numpy()) if hasattr(value, "numpy") else np.asarray(value)
    except (TypeError, ValueError, RuntimeError) as error:
        raise TypeError(f"Cannot convert {type(value).__name__} to NumPy") from error


def complex_array(coefficients: object, np):
    """Handle a complex tensor or Sionna's explicit (real, imaginary) pair."""
    if isinstance(coefficients, tuple):
        if len(coefficients) != 2:
            raise TypeError("Expected (real, imaginary) channel coefficients.")
        real, imaginary = coefficients
        return tensor_to_numpy(real, np) + 1j * tensor_to_numpy(imaginary, np)
    return tensor_to_numpy(coefficients, np)


def write_person_proxy_obj(mesh_path: Path) -> None:
    """Write a closed 8-vertex, 12-triangle rectangular-prism proxy mesh."""
    half_width, half_depth = PERSON_PROXY_WIDTH_M / 2, PERSON_PROXY_DEPTH_M / 2
    vertices = [
        (-half_width, -half_depth, 0.0), (half_width, -half_depth, 0.0),
        (half_width, half_depth, 0.0), (-half_width, half_depth, 0.0),
        (-half_width, -half_depth, PERSON_PROXY_HEIGHT_M),
        (half_width, -half_depth, PERSON_PROXY_HEIGHT_M),
        (half_width, half_depth, PERSON_PROXY_HEIGHT_M),
        (-half_width, half_depth, PERSON_PROXY_HEIGHT_M),
    ]
    faces = [(1, 3, 2), (1, 4, 3), (5, 6, 7), (5, 7, 8), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 4, 8), (3, 8, 7), (4, 1, 5), (4, 5, 8)]
    lines = ["# Uncalibrated triangular person-proxy mesh", *(f"v {x} {y} {z}" for x, y, z in vertices),
             *(f"f {a} {b} {c}" for a, b, c in faces)]
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    mesh_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select_person_positions(jps) -> list[tuple[float, float]]:
    """Run one real JuPedSim agent and select trajectory samples nearest three targets."""
    walkable = [(0.0, 0.0), (LENGTH_M, 0.0), (LENGTH_M, WIDTH_M), (0.0, WIDTH_M)]
    exit_area = [(9.8, 0.0), (LENGTH_M, 0.0), (LENGTH_M, WIDTH_M), (9.8, WIDTH_M)]
    simulation = jps.Simulation(model=jps.CollisionFreeSpeedModel(), geometry=walkable, dt=0.05)
    exit_id = simulation.add_exit_stage(exit_area)
    journey_id = simulation.add_journey(jps.JourneyDescription([exit_id]))
    rng = random.Random(SEED)
    agent_id = simulation.add_agent(jps.CollisionFreeSpeedModelAgentParameters(
        journey_id=journey_id, stage_id=exit_id, position=(0.7, 1.0 + rng.uniform(-0.05, 0.05)),
        radius=PERSON_AGENT_RADIUS_M, desired_speed=1.15, time_gap=1.0,
    ))
    trajectory: list[tuple[float, float]] = []
    for _ in range(220):
        for agent in simulation.agents():
            if agent.id == agent_id:
                trajectory.append(tuple(agent.position))
        simulation.iterate()
    if not trajectory:
        raise RuntimeError("JuPedSim returned no trajectory samples for the single person.")
    return [min(trajectory, key=lambda point, target=target: abs(point[0] - target))
            for target in (1.5, 5.0, 8.5)]


def make_scene(mi, Scene, RadioMaterial, person_xy: tuple[float, float] | None, mesh_path: Path):
    """Build a corridor, optionally with one translated triangular prism proxy mesh."""
    transform = mi.ScalarTransform4f
    def material(material_id, relative_permittivity, conductivity, thickness):
        return {"type": "radio-material", "id": material_id,
                "relative_permittivity": relative_permittivity,
                "conductivity": conductivity, "thickness": thickness}

    def slab(center, scale, material_id):
        return {"type": "cube", "to_world": transform.translate(center) @ transform.scale(scale),
                "bsdf": {"type": "ref", "id": material_id}}

    scene_dict = {
        "type": "scene",
        # Register materials at scene scope so their Mitsuba BSDF ids stay unique.
        "floor_radio_material": material("floor_radio_material", 2.5, 0.02, 0.1),
        "ceiling_radio_material": material("ceiling_radio_material", 2.5, 0.02, 0.1),
        "left_wall_radio_material": material("left_wall_radio_material", 2.5, 0.02, 0.1),
        "right_wall_radio_material": material("right_wall_radio_material", 2.5, 0.02, 0.1),
        "floor": slab([5, 0, -0.05], [5, 1, 0.05], "floor_radio_material"),
        "ceiling": slab([5, 0, 3.05], [5, 1, 0.05], "ceiling_radio_material"),
        "left_wall": slab([5, -1.05, 1.5], [5, 0.05, 1.5], "left_wall_radio_material"),
        "right_wall": slab([5, 1.05, 1.5], [5, 0.05, 1.5], "right_wall_radio_material"),
    }
    if person_xy is not None:
        scene_dict["person_proxy_radio_material"] = material(
            "person_proxy_radio_material", 38.0, 1.0, 0.20
        )
        scene_dict["person_proxy"] = {
            "type": "obj", "filename": str(mesh_path.resolve()), "face_normals": True,
            "to_world": transform.translate([person_xy[0], person_xy[1], 0.0]),
            "bsdf": {"type": "ref", "id": "person_proxy_radio_material"},
        }
    return Scene(mi.load_dict(scene_dict))


def csi_for_scene(scene, mi, np, PathSolver, PlanarArray, Transmitter, Receiver,
                  tone_offsets_hz):
    scene.frequency = CENTER_FREQUENCY_HZ
    scene.bandwidth = BANDWIDTH_HZ
    array = PlanarArray(num_rows=1, num_cols=1, vertical_spacing=0.5,
                        horizontal_spacing=0.5, pattern="iso", polarization="V")
    scene.tx_array = array
    scene.rx_array = array
    tx, rx = Transmitter(name="tx", position=TX_POSITION), Receiver(name="rx", position=RX_POSITION)
    scene.add(tx)
    scene.add(rx)
    tx.look_at(rx)
    paths = PathSolver()(scene=scene, max_depth=3, los=True, specular_reflection=True,
                         diffuse_reflection=False, refraction=False, samples_per_src=100_000,
                         seed=SEED)
    coefficients, path_delays = paths.cir()
    # `cir()` is unpacked because it returns channel coefficients and path delays.
    _ = complex_array(coefficients, np)
    _ = tensor_to_numpy(path_delays, np)
    valid_count = int(tensor_to_numpy(paths.valid, np).astype(bool, copy=False).sum())
    if valid_count == 0:
        raise RuntimeError("Sionna RT found no valid paths; ideal CSI cannot be produced.")
    cfr_result = paths.cfr(frequencies=mi.Float(tone_offsets_hz), normalize_delays=False)
    # Sionna RT 2.x may return CFR directly or as (CFR, delays); never call .numpy() on a tuple.
    if isinstance(cfr_result, tuple):
        channel_frequency_response, _cfr_delays = cfr_result
    else:
        channel_frequency_response = cfr_result
    cfr = complex_array(channel_frequency_response, np)
    if cfr.shape[-1] != TONE_COUNT:
        raise RuntimeError(f"Expected {TONE_COUNT} CFR tones, received shape {cfr.shape}.")
    h = cfr.sum(axis=tuple(range(cfr.ndim - 1)))
    if h.shape != (TONE_COUNT,) or not np.isfinite(h).all():
        raise RuntimeError("CFR did not yield one finite complex H[k] value per tone.")
    return h, valid_count


def main() -> None:
    try:
        import mitsuba as mi
        mi.set_variant("llvm_ad_mono_polarized")
        import matplotlib.pyplot as plt
        import numpy as np
        import jupedsim as jps
        from sionna.rt import PathSolver, PlanarArray, RadioMaterial, Receiver, Scene, Transmitter
    except (ImportError, RuntimeError, ValueError) as error:
        print(f"CSI smoke test cannot initialize CPU/LLVM dependencies: {error}")
        traceback.print_exc()
        raise SystemExit(1) from error

    try:
        person_positions = select_person_positions(jps)
        output_dir = project_root() / "simulation" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        person_mesh_path = output_dir / "_generated_meshes" / "person_proxy.obj"
        write_person_proxy_obj(person_mesh_path)
        tone_offsets_hz = (np.arange(TONE_COUNT) - (TONE_COUNT - 1) / 2) * BANDWIDTH_HZ / TONE_COUNT
        frequencies_hz = CENTER_FREQUENCY_HZ + tone_offsets_hz
        scenarios = [("empty_baseline", None), ("person_near_tx", person_positions[0]),
                     ("person_middle", person_positions[1]), ("person_near_rx", person_positions[2])]
        csi, path_counts = [], []
        for _name, person_xy in scenarios:
            scene = make_scene(mi, Scene, RadioMaterial, person_xy, person_mesh_path)
            h, count = csi_for_scene(scene, mi, np, PathSolver, PlanarArray, Transmitter, Receiver,
                                     tone_offsets_hz)
            csi.append(h)
            path_counts.append(count)
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        print(f"Ideal CSI smoke-test API/simulation error: {error}")
        traceback.print_exc()
        raise SystemExit(1) from error

    csi_array = np.stack(csi)
    names = [name for name, _ in scenarios]
    baseline_abs = np.abs(csi_array[0])
    scenario_summary = []
    for index, name in enumerate(names):
        h_abs = np.abs(csi_array[index])
        delta = h_abs - baseline_abs
        relative_phase = np.angle(csi_array[index] * np.conj(csi_array[0]))
        scenario_summary.append({
            "name": name, "valid_path_count": path_counts[index],
            "mean_abs_h": float(h_abs.mean()), "std_abs_h": float(h_abs.std()),
            "mean_amplitude_change_vs_baseline": float(delta.mean()),
            "mean_relative_phase_rad_vs_baseline": float(np.angle(np.mean(np.exp(1j * relative_phase)))),
            "warning_no_measurable_change": bool(index > 0 and np.allclose(csi_array[index], csi_array[0])),
        })
    np.savez_compressed(output_dir / "single_person_proxy_ideal_csi.npz",
                        frequencies_hz=frequencies_hz, scenario_names=np.asarray(names),
                        csi_real=csi_array.real, csi_imag=csi_array.imag,
                        person_positions_xy=np.asarray(person_positions),
                        corridor_geometry_m=np.asarray([LENGTH_M, WIDTH_M, HEIGHT_M]),
                        tx_position_m=np.asarray(TX_POSITION), rx_position_m=np.asarray(RX_POSITION))
    summary = {
        "data_origin": "synthetic_smoke_test", "csi_type": "ideal_frequency_response",
        "center_frequency_hz": CENTER_FREQUENCY_HZ, "bandwidth_hz": BANDWIDTH_HZ,
        "tone_count": TONE_COUNT, "backend": mi.variant(),
        "corridor_geometry_m": {"length": LENGTH_M, "width": WIDTH_M, "height": HEIGHT_M},
        "tx_position_m": TX_POSITION, "rx_position_m": RX_POSITION,
        "person_positions_xy": person_positions,
        "person_proxy": {"proxy_geometry": "triangulated_rectangular_prism",
                         "width_m": PERSON_PROXY_WIDTH_M, "depth_m": PERSON_PROXY_DEPTH_M,
                         "height_m": PERSON_PROXY_HEIGHT_M, "material": "person_proxy_radio_material",
                         "material_is_uncalibrated": True,
                         "calibration": "Geometric proxy not calibrated to a real human body or real ESP32 hardware."},
        "scenarios": scenario_summary,
        "limitations": [
            "Ideal geometric proxy only; it is not human CSI and does not prove proxy realism.",
            "This is not raw ESP32 CSI and is not training data.",
            "ESP phase synchronization, AGC, CFO, SFO, and hardware phase effects are not modeled.",
        ],
    }
    (output_dir / "single_person_proxy_smoke_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    figure, axis = plt.subplots(figsize=(9, 4.5))
    for name, h in zip(names, csi_array):
        axis.plot(frequencies_hz / 1e9, np.abs(h), label=name)
    axis.set(title="Ideal simulated CSI |H[k]| — not calibrated to ESP32", xlabel="Frequency (GHz)",
             ylabel="Magnitude |H[k]| (linear)")
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_dir / "single_person_proxy_csi_comparison.png", dpi=160)
    plt.close(figure)
    print("This is ideal CSI across 64 tones, not raw ESP32 CSI, not training data.")
    print("It does not prove that the person proxy matches a real person.")
    print(f"Valid paths: {dict(zip(names, path_counts))}")
    print("Created: single_person_proxy_ideal_csi.npz, single_person_proxy_smoke_summary.json,")
    print("and single_person_proxy_csi_comparison.png")


if __name__ == "__main__":
    main()
