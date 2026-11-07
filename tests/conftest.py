"""Shared test setup.

Flask 2.3's test client builds its User-Agent from `werkzeug.__version__`,
which Werkzeug 3 removed, so `app.test_client()` raises AttributeError with
the Werkzeug the app actually runs on. Supply the attribute from the package
metadata instead of pinning Werkzeug below 3 (that would cost the app the 3.x
security fixes for a test-only problem). Drop this when Flask moves to 3.x.
"""
from importlib.metadata import version

import werkzeug

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = version("werkzeug")
