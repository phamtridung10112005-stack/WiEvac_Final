"""JuPedSim smoke test only; this is not a real evacuation model."""

import json
import random
from pathlib import Path


LENGTH_M, WIDTH_M, PEOPLE, SEED, ITERATIONS, DT_S = 10.0, 2.0, 10, 2026, 80, 0.05


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    try:
        import jupedsim as jps
        import matplotlib.pyplot as plt
    except ImportError as error:
        print(f"JuPedSim smoke test cannot start: {error}")
        print("Install/activate the environment containing JuPedSim and matplotlib.")
        return

    output_dir = project_root() / "simulation" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    walkable_area = [(0.0, 0.0), (LENGTH_M, 0.0), (LENGTH_M, WIDTH_M), (0.0, WIDTH_M)]
    exit_area = [(9.8, 0.0), (LENGTH_M, 0.0), (LENGTH_M, WIDTH_M), (9.8, WIDTH_M)]

    try:
        simulation = jps.Simulation(
            model=jps.CollisionFreeSpeedModel(), geometry=walkable_area, dt=DT_S
        )
        exit_id = simulation.add_exit_stage(exit_area)
        journey_id = simulation.add_journey(jps.JourneyDescription([exit_id]))
        rng = random.Random(SEED)
        agent_ids: list[int] = []
        for row in range(5):
            for column in range(2):
                position = (0.5 + column * 0.45, 0.25 + row * 0.36 + rng.uniform(-0.03, 0.03))
                agent_ids.append(simulation.add_agent(jps.CollisionFreeSpeedModelAgentParameters(
                    journey_id=journey_id, stage_id=exit_id, position=position,
                    radius=0.12, v0=1.2, time_gap=1.0,
                )))
        initial = {agent.id: tuple(agent.position) for agent in simulation.agents()}
        tracks = {agent_id: [initial[agent_id]] for agent_id in agent_ids}
        for _ in range(ITERATIONS):
            simulation.iterate()
            for agent in simulation.agents():
                if agent.id in tracks:
                    tracks[agent.id].append(tuple(agent.position))
        final = {agent.id: tuple(agent.position) for agent in simulation.agents()}
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        print(f"JuPedSim API/simulation error: {error}")
        print("This smoke test expects the JuPedSim 1.4.x Simulation, exit-stage, and agent APIs.")
        return

    figure, axis = plt.subplots(figsize=(10, 2.6))
    axis.add_patch(plt.Rectangle((0, 0), LENGTH_M, WIDTH_M, fill=False, color="black"))
    for agent_id, points in tracks.items():
        x_values, y_values = zip(*points)
        axis.plot(x_values, y_values, linewidth=1, label=str(agent_id))
        axis.scatter(x_values[0], y_values[0], s=12, color="black")
    axis.set(xlim=(0, LENGTH_M), ylim=(0, WIDTH_M), aspect="equal", title="JuPedSim smoke test (not a real evacuation model)")
    axis.set_xlabel("corridor length (m)")
    axis.set_ylabel("corridor width (m)")
    figure.tight_layout()
    figure.savefig(output_dir / "pedestrian_smoke.png", dpi=160)
    plt.close(figure)

    examples = [
        {"agent_id": agent_id, "start_m": initial[agent_id], "end_m": final.get(agent_id)}
        for agent_id in agent_ids[:3]
    ]
    summary = {
        "purpose": "Smoke test only; not a real evacuation model.",
        "geometry_m": {"length": LENGTH_M, "width": WIDTH_M}, "seed": SEED,
        "people_created": len(agent_ids), "iterations": ITERATIONS,
        "simulation_time_s": ITERATIONS * DT_S, "example_positions": examples,
    }
    (output_dir / "pedestrian_smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("JuPedSim pedestrian smoke test only; this is not a real evacuation model.")
    print(f"People created: {len(agent_ids)}")
    print(f"Iterations: {ITERATIONS}; simulation time: {ITERATIONS * DT_S:.2f} s")
    for item in examples:
        print(f"Agent {item['agent_id']}: {item['start_m']} -> {item['end_m']}")


if __name__ == "__main__":
    main()
