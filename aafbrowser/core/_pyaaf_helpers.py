"""
Tiny package-private helpers wrapping pyaaf2 slot and rational access.

Lives here (not in chain.py or operator.py) so chain → operator dependency
direction stays clean while both modules call the same implementation.
"""
from __future__ import annotations

from typing import Any, Optional


def slot_property(slot: Any, name: str) -> Any:
    """
    Look up a slot Property by name via the properties() iterator.

    pyaaf2 does not auto-expose every AAF property as a Python attribute —
    PhysicalTrackNumber and similar slot Properties silently return None
    from getattr even when set. Iterating slot.properties() is the only
    reliable accessor.
    """
    if slot is None:
        return None
    prop_iter = getattr(slot, "properties", None)
    if not callable(prop_iter):
        return None
    for p in prop_iter():
        if p.name == name:
            return p.value
    return None


def format_rational(r: Any) -> Optional[str]:
    """Render an AAFRational (or anything with num/den) as 'N/D'."""
    if r is None:
        return None
    num = getattr(r, "numerator", None)
    den = getattr(r, "denominator", None)
    if num is None or den is None:
        return str(r)
    return f"{int(num)}/{int(den)}"


def rational_to_float(r: Any) -> Optional[float]:
    if r is None:
        return None
    num = getattr(r, "numerator", None)
    den = getattr(r, "denominator", None)
    if num is None or den is None or int(den) == 0:
        return None
    return float(num) / float(den)
