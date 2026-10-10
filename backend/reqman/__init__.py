"""ReqManTool backend package.

``__version__`` is the single source of the tool version: the backend
reports it from ``GET /version`` (see ``version.py``) and the frontend build
reads it from this file (see ``frontend/vite.config.ts``). Keep it a plain
string literal on its own line so that build-time parse stays trivial.
"""

# Bound before the submodule imports so that `from . import __version__`
# (version.py) works even if a submodule imports `.version` during init.
__version__ = "0.1.0"

from .models import (
    AttributeDefinition,
    LinkConfiguration,
    LinkPair,
    Module,
    Project,
    Requirement,
)
from .service import ProjectService
from .store import ProjectStore

__all__ = [
    "AttributeDefinition",
    "LinkConfiguration",
    "LinkPair",
    "Module",
    "Project",
    "ProjectService",
    "ProjectStore",
    "Requirement",
    "__version__",
]
