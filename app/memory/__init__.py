"""Durable memory plane (FR-7): cycle store, checkpoints, evidence registry.

Owned by the core_developer agent. A thin repository over SQLite; higher memory
layers (episodic, semantic, procedural) build on top of this in later waves.
"""
