"""Idempotent bootstrap of canonical product configuration.

Only definitions are seeded. Evaluation rates, training curves, assets,
telemetry, and source counts are always produced by real runs/integrations.
"""
from __future__ import annotations

from sqlalchemy import select

from .db import SessionLocal
from .models import Skill, Variant, World
from .services import evaluator


SCENE_TREE = [
    {
        "id": "world",
        "name": "Articulated Door Validation Lab",
        "icon": "worlds",
        "children": [
            {
                "id": "room",
                "name": "Environment",
                "icon": "cube",
                "children": [
                    {"id": "floor", "name": "Collision Floor", "icon": "floor", "locked": True},
                    {"id": "lighting", "name": "Preview Lighting", "icon": "lighting"},
                ],
            },
            {
                "id": "robot",
                "name": "MuJoCo Manipulator",
                "icon": "robot",
                "children": [
                    {"id": "robot-base", "name": "Fixed Base", "icon": "robot", "locked": True},
                    {"id": "robot-arm", "name": "4-DOF Arm", "icon": "joint"},
                    {"id": "robot-gripper", "name": "Parallel Gripper", "icon": "gripper"},
                ],
            },
            {
                "id": "task-assets",
                "name": "Task Assets",
                "icon": "cube",
                "children": [
                    {"id": "fridge", "name": "Articulated Door Asset", "icon": "fridge", "tag": "USD + MJCF"},
                    {"id": "handle", "name": "Grasp Handle", "icon": "joint"},
                ],
            },
        ],
    }
]


async def seed_definitions() -> None:
    async with SessionLocal() as session:
        skill = await session.get(Skill, "open-refrigerator")
        if skill is None:
            skill = Skill(
                id="open-refrigerator",
                name="Open Refrigerator",
                category="Manipulation",
                description="Reach, grasp, and open an articulated refrigerator door across physical variations.",
                icon="fridge",
                target=85.0,
            )
            session.add(skill)

        world = await session.get(World, "door-validation-lab")
        if world is None:
            session.add(World(id="door-validation-lab", name="Articulated Door Validation Lab", scene_tree=SCENE_TREE, active=True))
        variant = (
            await session.execute(select(Variant).where(Variant.world_id == "door-validation-lab", Variant.name == "Nominal"))
        ).scalar_one_or_none()
        if variant is None:
            session.add(
                Variant(
                    id="var_nominal",
                    world_id="door-validation-lab",
                    name="Nominal",
                    desc="Baseline sampled physical parameters",
                    active=True,
                )
            )
        await session.commit()

    # Scenario rows are deterministic, persisted physical parameter sets.
    async with SessionLocal() as session:
        await evaluator.ensure_families(session, "open-refrigerator")
