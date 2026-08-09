"""Test package marker.

Present so `from tests.conftest import ...` resolves deterministically. Without
it, pytest's default prepend import mode puts `tests/` itself on sys.path and
the package import only works by accident -- and conftest.py can end up loaded
twice under two module names, which duplicates fixtures in confusing ways.
"""
