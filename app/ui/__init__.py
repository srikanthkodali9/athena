"""Judge-facing demo UI and demo control plane (demo_engineer owned).

This package renders the six-minute demo and orchestrates it, but it never
re-implements core investigation, security, or metric logic: it *composes* the
existing app components (heartbeat service, security gateway, OpenShell engine,
durable store) and reads PERSISTED records. See ``app/ui/server.py``.
"""
