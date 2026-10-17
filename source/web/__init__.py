# -*- coding: utf-8 -*-
"""Web 子包。"""
from .app import create_app, init_runtime, shutdown_runtime, socketio  # noqa: F401

__all__ = ["create_app", "init_runtime", "shutdown_runtime", "socketio"]
