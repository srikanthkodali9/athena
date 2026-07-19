"""Heartbeat control plane (FR-1, FR-2, FR-3, FR-7).

Owned by the core_developer agent. Periodic and manual triggers share one
internal command handler; the no-change WAIT path makes no model calls; cycles
checkpoint each step so a restart resumes without repeating costly work.
"""
