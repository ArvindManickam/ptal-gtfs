"""Configuration profiles: validated YAML that parameterises a PTAL run.

A *profile* selects the method — walk speed, the reliability model (``static`` TfL or
``deviation`` for India), and the band table. ``default`` reproduces TfL; ``india`` adapts
it. See :func:`load_profile`.
"""

from __future__ import annotations

from .schema import Bands, Profile, Reliability, load_profile

__all__ = ["Bands", "Profile", "Reliability", "load_profile"]
