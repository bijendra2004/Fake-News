"""WSGI/ASGI entry point for Render deployment.

When Render runs `uvicorn wsgi:app` from inside the backend/ directory,
this module adds the parent directory to sys.path so that relative imports
within the backend package resolve correctly.

Locally, you can continue using `uvicorn backend.main:app` from the project root.
"""
import os
import sys

# Add the project root (parent of backend/) to sys.path so Python
# recognizes 'backend' as a package and relative imports work.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from backend.main import app  # noqa: E402, F401
