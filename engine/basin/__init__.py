"""Basin engine — context configuration management for AI collaboration.

Files are the source of truth (append-only JSONL in .basin/); SQLite is a
rebuildable index. See design/BLUEPRINT.md.
"""
from .core import VERSION

__all__ = ["VERSION"]
