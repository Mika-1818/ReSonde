#!/usr/bin/env python3
"""
WSGI entry point for ReSonde Dashboard
Used by Gunicorn in production
"""

from app import app, socketio

if __name__ == "__main__":
    socketio.run(app)
