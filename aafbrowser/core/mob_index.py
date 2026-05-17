"""
Enumerate top-level Mobs with optional filters.

This module owns the shape of a "mob index entry" (mob_id, class, name,
slot_count) and the filter semantics (case-insensitive substring on name,
exact match on class). Both the CLI `mobs` command and the web `/api/mobs`
route consume the same interface so filter behaviour cannot drift between
adapters.

Cycle safety: top-level mob iteration via `handle.content.mobs` is by
construction non-cyclic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MobIndexEntry:
    mob_id: str          # URN (e.g. "urn:smpte:umid:...")
    mob_class: str       # CompositionMob | MasterMob | SourceMob | ...
    name: Optional[str]
    slot_count: int

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Web API has shipped the field name "class" rather than
        # "mob_class". Preserve that on the wire.
        d["class"] = d.pop("mob_class")
        return d


def list_mobs(
    handle: Any,
    *,
    name_contains: Optional[str] = None,
    mob_class: Optional[str] = None,
) -> list[MobIndexEntry]:
    """Enumerate top-level Mobs in `handle.content.mobs`, optionally filtered.

    Filters:
    - name_contains: case-insensitive substring match on Mob.name
      (Mobs with no name are excluded when this filter is set).
    - mob_class: exact match on the Python class name (e.g.
      "CompositionMob"). Useful for slicing inventories.

    Returns a list (not a generator) because callers tend to want length.
    """
    out: list[MobIndexEntry] = []
    for mob in handle.content.mobs:
        cls = type(mob).__name__
        nm = getattr(mob, "name", None)
        nm = nm if isinstance(nm, str) else None
        slots = getattr(mob, "slots", None)
        try:
            slot_count = len(list(slots)) if slots is not None else 0
        except Exception:
            slot_count = 0
        out.append(MobIndexEntry(
            mob_id=str(mob.mob_id),
            mob_class=cls,
            name=nm,
            slot_count=slot_count,
        ))
    return filter_entries(out, name_contains=name_contains, mob_class=mob_class)


def filter_entries(
    entries: list[MobIndexEntry],
    *,
    name_contains: Optional[str] = None,
    mob_class: Optional[str] = None,
) -> list[MobIndexEntry]:
    """Apply the same filter semantics as list_mobs against a pre-built index.

    The web layer caches an unfiltered index at /open time and applies
    filters per /api/mobs request via this function — same predicate,
    different timing.
    """
    name_lc = name_contains.lower() if name_contains else None
    out: list[MobIndexEntry] = []
    for e in entries:
        if mob_class and e.mob_class != mob_class:
            continue
        if name_lc is not None:
            if not e.name or name_lc not in e.name.lower():
                continue
        out.append(e)
    return out
