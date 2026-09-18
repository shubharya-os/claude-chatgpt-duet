"""Shim for pip < 21.3, which cannot do an editable install from pyproject.toml
alone and would otherwise install the package with no `duet` command. Metadata
lives in pyproject.toml; this file only makes the legacy path work."""

from setuptools import setup

setup()
