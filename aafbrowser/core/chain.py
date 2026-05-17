"""
Chain-walk: follow the SourceClip / SourceMobSlotID chain hop-by-hop
until it terminates at non-chainable essence (Filler, Timecode,
EssenceGroup, OperationGroup, broken reference, cycle, or a SourceMob
whose slot doesn't carry a further SourceClip).

Each hop captures (mob_class, mob_name, mob_id, slot_id, slot_name,
segment_class, physical_track_number, edit_rate, start_time, length,
terminal, terminal_reason). The hop list is ordered start -> terminal;
the last hop has terminal=True and a non-null terminal_reason.

Cycle-safe: the visited set is keyed by (mob_id_str, slot_id) and the
walker terminates with reason="cycle" rather than recursing.

API surface verified against pyaaf2 1.7.1: SourceClip exposes
.mob_id / .slot_id / .start (not .source_id / .source_mob_slot_id /
.start_time as some older docs suggest).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional, Union

from ._pyaaf_helpers import format_rational as _format_rational
from ._pyaaf_helpers import slot_property as _slot_property
from .resolver import _try_parse_mob_id


@dataclass(frozen=True)
class Hop:
    mob_class: str
    mob_name: Optional[str]
    mob_id: str
    slot_id: Optional[int]
    slot_name: Optional[str]
    segment_class: str
    physical_track_number: Optional[int]
    edit_rate: Optional[str]
    start_time: Optional[int]
    length: Optional[int]
    terminal: bool
    terminal_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slot_at(mob: Any, slot_id: int) -> Optional[Any]:
    """mob.slot_at returns the slot with that slot_id or None."""
    fn = getattr(mob, "slot_at", None)
    if callable(fn):
        try:
            return fn(slot_id)
        except Exception:
            return None
    # Fallback: linear search.
    for slot in getattr(mob, "slots", []) or []:
        if getattr(slot, "slot_id", None) == slot_id:
            return slot
    return None


def _first_slot(mob: Any) -> Optional[Any]:
    for slot in getattr(mob, "slots", []) or []:
        return slot
    return None


def _chainable_source_clip(segment: Any) -> Optional[Any]:
    """
    If `segment` chains forward via a single SourceClip, return that clip.
    Otherwise return None.

    Rules:
    - SourceClip with non-zero mob_id -> the clip itself.
    - Sequence whose components reduce to exactly one SourceClip
      after filtering out Filler/Timecode -> that clip.
    - OperationGroup with exactly one input segment that is a SourceClip
      (after filtering) -> that clip.
    - Anything else -> None.
    """
    cls = type(segment).__name__
    if cls == "SourceClip":
        mob_id = getattr(segment, "mob_id", None)
        if mob_id is None or getattr(mob_id, "int", 0) == 0:
            return None
        return segment
    if cls == "Sequence":
        children = list(getattr(segment, "components", None) or [])
        clips = [c for c in children if type(c).__name__ == "SourceClip"]
        if len(clips) == 1:
            mob_id = getattr(clips[0], "mob_id", None)
            if mob_id is not None and getattr(mob_id, "int", 0) != 0:
                return clips[0]
        return None
    if cls == "OperationGroup":
        inputs = list(getattr(segment, "input_segments", None) or [])
        clips = [c for c in inputs if type(c).__name__ == "SourceClip"]
        if len(clips) == 1:
            mob_id = getattr(clips[0], "mob_id", None)
            if mob_id is not None and getattr(mob_id, "int", 0) != 0:
                return clips[0]
        return None
    return None


def _terminal_reason(mob: Any, segment: Any) -> str:
    """
    Pick a reason string for a hop that doesn't chain forward. Real
    multi-component shapes get distinct reasons so callers can decide
    whether to dispatch a per-child sub-walk.
    """
    cls = type(segment).__name__
    if cls == "Filler":
        return "filler"
    if cls == "Timecode":
        return "timecode"
    if cls == "EssenceGroup":
        return "essence_group"
    if cls == "Pulldown":
        return "pulldown"
    if cls == "OperationGroup":
        return "operation_group"
    if cls == "Sequence":
        children = list(getattr(segment, "components", None) or [])
        clips = [c for c in children if type(c).__name__ == "SourceClip"]
        if len(clips) >= 2:
            return "multi_segment_sequence"
        if not clips:
            # No SourceClip at all. SourceMobs land here as the natural
            # essence terminal; other mobs land here on weird structure.
            if type(mob).__name__ == "SourceMob":
                return "essence"
            return "empty_sequence" if not children else "filler"
        # Exactly one clip but not chainable (zero/missing mob_id).
        mid = getattr(clips[0], "mob_id", None)
        return "no_source_id" if mid is None or getattr(mid, "int", 0) == 0 else "broken_ref"
    if cls == "SourceClip":
        mid = getattr(segment, "mob_id", None)
        if mid is None or getattr(mid, "int", 0) == 0:
            return "no_source_id"
        return "broken_ref"
    return f"non_clip_segment:{cls}"


def _make_hop(
    mob: Any,
    slot_id: Optional[int],
    slot: Optional[Any],
    segment: Any,
    *,
    terminal: bool,
    terminal_reason: Optional[str],
) -> Hop:
    mob_id = getattr(mob, "mob_id", None)
    return Hop(
        mob_class=type(mob).__name__,
        mob_name=(getattr(mob, "name", None) if isinstance(getattr(mob, "name", None), str) else None),
        mob_id=str(mob_id) if mob_id is not None else "",
        slot_id=slot_id,
        slot_name=(
            getattr(slot, "name", None)
            if slot is not None and isinstance(getattr(slot, "name", None), str)
            else None
        ),
        segment_class=type(segment).__name__ if segment is not None else "<no-segment>",
        physical_track_number=_slot_property(slot, "PhysicalTrackNumber"),
        edit_rate=_format_rational(getattr(slot, "edit_rate", None)) if slot is not None else None,
        start_time=getattr(segment, "start", None) if segment is not None else None,
        length=getattr(segment, "length", None) if segment is not None else None,
        terminal=terminal,
        terminal_reason=terminal_reason,
    )


def _resolve_start(
    handle: Any,
    start: Union[Any, str],
    slot_id: Optional[int],
) -> tuple[Any, int]:
    """
    Resolve `start` to a (mob, slot_id) pair. `start` may be:
      - a Mob -> use slot_id arg or default to the first slot's id
      - a SourceClip -> use clip.mob and clip.slot_id (slot_id arg ignored)
      - a MobID instance -> look up in handle.content.mobs, use slot_id arg
      - a string (URN / dotted-hex / plain-hex) -> parse as MobID, look up
    """
    cls = type(start).__name__
    if cls == "SourceClip":
        target_mob = start.mob
        if target_mob is None:
            raise ValueError(
                f"SourceClip points to mob_id={start.mob_id!s} which is not in this file"
            )
        sid = start.slot_id
        if sid is None:
            raise ValueError("SourceClip has no slot_id")
        return target_mob, int(sid)

    # Mob-like: look for slots
    if hasattr(start, "slots"):
        if slot_id is not None:
            return start, int(slot_id)
        first = _first_slot(start)
        if first is None:
            raise ValueError(f"{cls} has no slots")
        return start, int(first.slot_id)

    # MobID instance
    if hasattr(start, "int") and hasattr(start, "bytes_le"):
        mob = handle.content.mobs.get(start, None)
        if mob is None:
            raise ValueError(f"mob_id {start} not found in file")
        return _resolve_start(handle, mob, slot_id)

    # String: parse as MobID
    if isinstance(start, str):
        parsed = _try_parse_mob_id(start)
        if parsed is None:
            raise ValueError(f"could not parse mob_id from {start!r}")
        mob = handle.content.mobs.get(parsed, None)
        if mob is None:
            raise ValueError(f"mob_id {start} not found in file")
        return _resolve_start(handle, mob, slot_id)

    raise TypeError(f"unsupported start type: {cls}")


# ---------- tree-walk variant for multi-input combiners ----------


@dataclass(frozen=True)
class HopBranch:
    """
    Terminal marker for an OperationGroup that combines multiple
    SourceClip inputs (Avid-style audio mix-down, stereo-bus combiner,
    etc.). Each input is itself a fully-walked sub-chain — list of
    Hop or HopBranch — so the result is a tree.

    The flat walk_chain treats these as opaque terminals with reason
    "operation_group". walk_chain_tree recurses into each input so
    consumers can recover per-input mic identity.
    """
    combiner_class: str             # "OperationGroup"
    operation_def_name: Optional[str]   # "Audio Gain", "Mono Audio Mix", etc.
    mob_class: str                  # parent mob class
    mob_name: Optional[str]
    mob_id: str
    slot_id: Optional[int]
    inputs: tuple[tuple, ...]       # tuple of sub-chains (each a tuple of hops/branches)
    truncated_input_count: int = 0  # >0 if max_combiner_inputs limited the recursion
    terminal: bool = True
    terminal_reason: str = "operation_group_branched"

    def to_dict(self) -> dict[str, Any]:
        return {
            "_type": "hop_branch",
            "combiner_class": self.combiner_class,
            "operation_def_name": self.operation_def_name,
            "mob_class": self.mob_class,
            "mob_name": self.mob_name,
            "mob_id": self.mob_id,
            "slot_id": self.slot_id,
            "inputs": [
                [_node_to_dict(h) for h in chain]
                for chain in self.inputs
            ],
            "truncated_input_count": self.truncated_input_count,
            "terminal": self.terminal,
            "terminal_reason": self.terminal_reason,
        }


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Serialize either a Hop or a HopBranch."""
    if isinstance(node, HopBranch):
        return node.to_dict()
    return node.to_dict()


def _operation_group_input_segments(og: Any) -> list[Any]:
    """
    Read OperationGroup.InputSegments via the property API.

    Mirrors core/operator._operation_group_inputs (kept duplicated to
    keep the dependency graph one-directional: chain has no operator
    import). The standard `getattr(og, "input_segments", None)` path
    silently returns None on real Avid files because pyaaf2 doesn't
    auto-expose this property — the dictionary access works.
    """
    try:
        prop = og["InputSegments"]
    except KeyError:
        return []
    val = getattr(prop, "value", None)
    if val is None:
        return []
    return list(val)


def _operation_group_source_clip_inputs(og: Any) -> list[Any]:
    """Inputs that are SourceClips with non-zero mob_id — i.e. clips
    that can be chain-walked. Filler / Timecode inputs are dropped."""
    out: list[Any] = []
    for seg in _operation_group_input_segments(og):
        if type(seg).__name__ != "SourceClip":
            continue
        mid = getattr(seg, "mob_id", None)
        if mid is None or getattr(mid, "int", 0) == 0:
            continue
        out.append(seg)
    return out


def walk_chain_tree(
    handle: Any,
    start: Union[Any, str],
    *,
    slot_id: Optional[int] = None,
    max_hops: int = 64,
    max_depth: int = 8,
    max_combiner_inputs: int = 32,
) -> list[Any]:
    """
    Tree variant of walk_chain. Walks the SourceClip chain hop-by-hop
    like walk_chain, but on a terminal that's an OperationGroup with
    >= 2 SourceClip inputs, replaces the terminal Hop with a HopBranch
    and recurses into each input (up to max_depth nested combiners,
    max_combiner_inputs per combiner).

    Returns list[Hop | HopBranch]. The last element is always
    terminal=True; if it's a HopBranch, its inputs each end in their
    own terminal.

    Cycle-safe via a single visited set shared across all branches:
    if walking input N re-enters a (mob_id, slot_id) that any other
    branch already visited, that branch terminates with reason "cycle".

    Single-input OperationGroups still terminate via walk_chain's
    existing logic — this function only branches on multi-input
    combiners. Single-input unwrap is handled at the operator layer
    before walk_chain is called (see core/operator._unwrap_operation_group).
    """
    visited: set[tuple[str, int]] = set()
    return _walk_tree_inner(
        handle, start, slot_id, max_hops, max_depth, max_combiner_inputs,
        visited, depth=0,
    )


def _walk_tree_inner(
    handle: Any,
    start: Union[Any, str],
    slot_id: Optional[int],
    max_hops: int,
    max_depth: int,
    max_combiner_inputs: int,
    visited: set[tuple[str, int]],
    depth: int,
) -> list[Any]:
    try:
        mob, current_slot_id = _resolve_start(handle, start, slot_id)
    except (ValueError, TypeError):
        return []

    hops: list[Any] = []

    for _ in range(max_hops):
        mob_id_str = str(getattr(mob, "mob_id", ""))
        key = (mob_id_str, int(current_slot_id))
        if key in visited:
            hops.append(_make_hop(mob, current_slot_id, None, None,
                                  terminal=True, terminal_reason="cycle"))
            return hops
        visited.add(key)

        slot = _slot_at(mob, current_slot_id)
        if slot is None:
            hops.append(_make_hop(mob, current_slot_id, None, None,
                                  terminal=True, terminal_reason="invalid_slot"))
            return hops

        segment = slot.segment

        # Multi-input combiner branch: terminate with a HopBranch and
        # recurse into each input.
        if (
            type(segment).__name__ == "OperationGroup"
            and depth < max_depth
        ):
            input_clips = _operation_group_source_clip_inputs(segment)
            if len(input_clips) >= 2:
                taken = input_clips[:max_combiner_inputs]
                truncated = max(0, len(input_clips) - max_combiner_inputs)
                op_def = getattr(segment, "operation", None)
                op_name = getattr(op_def, "name", None) if op_def else None
                branched: list[tuple] = []
                for inp in taken:
                    sub = _walk_tree_inner(
                        handle, inp, None, max_hops, max_depth,
                        max_combiner_inputs, visited, depth + 1,
                    )
                    branched.append(tuple(sub))
                mob_id_v = getattr(mob, "mob_id", None)
                mob_name_v = getattr(mob, "name", None)
                hops.append(HopBranch(
                    combiner_class=type(segment).__name__,
                    operation_def_name=op_name if isinstance(op_name, str) else None,
                    mob_class=type(mob).__name__,
                    mob_name=mob_name_v if isinstance(mob_name_v, str) else None,
                    mob_id=str(mob_id_v) if mob_id_v is not None else "",
                    slot_id=current_slot_id,
                    inputs=tuple(branched),
                    truncated_input_count=truncated,
                ))
                return hops

        # Otherwise: same as walk_chain — single-clip chain step or
        # single-shape terminal.
        next_clip = _chainable_source_clip(segment)
        if next_clip is None:
            reason = _terminal_reason(mob, segment)
            hops.append(_make_hop(mob, current_slot_id, slot, segment,
                                  terminal=True, terminal_reason=reason))
            return hops

        next_mob = next_clip.mob
        if next_mob is None:
            hops.append(_make_hop(mob, current_slot_id, slot, segment,
                                  terminal=True, terminal_reason="broken_ref"))
            return hops

        hops.append(_make_hop(mob, current_slot_id, slot, segment,
                              terminal=False, terminal_reason=None))
        next_slot_id = next_clip.slot_id
        if next_slot_id is None:
            hops[-1] = _make_hop(
                mob, current_slot_id, slot, segment,
                terminal=True, terminal_reason="no_slot_id",
            )
            return hops
        mob = next_mob
        current_slot_id = int(next_slot_id)

    hops.append(_make_hop(mob, current_slot_id, None, None,
                          terminal=True, terminal_reason="max_hops_reached"))
    return hops


def walk_chain(
    handle: Any,
    start: Union[Any, str],
    *,
    slot_id: Optional[int] = None,
    max_hops: int = 64,
) -> list[Hop]:
    """
    Walk the SourceClip chain hop-by-hop. Returns a list of `Hop`s
    ordered start -> terminal. The list is never empty; even a start
    that is itself terminal yields one hop.

    Cycle-safe: a re-entered (mob_id, slot_id) pair terminates with
    reason "cycle".
    """
    mob, current_slot_id = _resolve_start(handle, start, slot_id)
    visited: set[tuple[str, int]] = set()
    hops: list[Hop] = []

    for _ in range(max_hops):
        mob_id_str = str(getattr(mob, "mob_id", ""))
        key = (mob_id_str, int(current_slot_id))
        if key in visited:
            # Emit a synthetic terminal hop describing the re-entry.
            hops.append(
                _make_hop(mob, current_slot_id, None, None,
                          terminal=True, terminal_reason="cycle")
            )
            return hops
        visited.add(key)

        slot = _slot_at(mob, current_slot_id)
        if slot is None:
            hops.append(
                _make_hop(mob, current_slot_id, None, None,
                          terminal=True, terminal_reason="invalid_slot")
            )
            return hops

        segment = slot.segment

        next_clip = _chainable_source_clip(segment)
        if next_clip is None:
            reason = _terminal_reason(mob, segment)
            hops.append(
                _make_hop(mob, current_slot_id, slot, segment,
                          terminal=True, terminal_reason=reason)
            )
            return hops

        # Chainable: descend.
        next_mob = next_clip.mob
        if next_mob is None:
            hops.append(
                _make_hop(mob, current_slot_id, slot, segment,
                          terminal=True, terminal_reason="broken_ref")
            )
            return hops

        # Record this hop as non-terminal and advance.
        hops.append(
            _make_hop(mob, current_slot_id, slot, segment,
                      terminal=False, terminal_reason=None)
        )
        next_slot_id = next_clip.slot_id
        if next_slot_id is None:
            # Shouldn't happen for a chainable clip but guard anyway.
            hops[-1] = _make_hop(
                mob, current_slot_id, slot, segment,
                terminal=True, terminal_reason="no_slot_id",
            )
            return hops
        mob = next_mob
        current_slot_id = int(next_slot_id)

    # Ran out of iterations.
    hops.append(
        _make_hop(mob, current_slot_id, None, None,
                  terminal=True, terminal_reason="max_hops_reached")
    )
    return hops
