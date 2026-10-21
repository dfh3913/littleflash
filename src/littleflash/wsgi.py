"""WSGI entry point for gunicorn / uwsgi / nginx deployments."""

from __future__ import annotations

from .app import create_app

app = create_app()

__all__ = ["app"]
