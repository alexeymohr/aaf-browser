"""
Operator-first view of an AAF: tracks (audio + video slots on the
topmost CompositionMob) and clips (Components on a slot's Sequence).

This module establishes the post-production-sound-operator default
surface. It composes core.chain.walk_chain to recover mic identity
per clip, but adds no new pyaaf2 walking primitives of its own —
track/clip enumeration is straightforward iteration over a single
CompositionMob's slots.

Per-clip information surfaced here:
- Source file path + online/offline detection (from terminal
  SourceMob locators).
- Head/tail handle frames + seconds.
- Audio specs (sample rate / bit depth / channels).
- Multi-input OperationGroup fan-out via chain.walk_chain_tree
  populating Clip.sub_clips.

Plus source_inventory: cross-track deduplicated source-mob list.

Cycle safety: the chain-walk handles cycles internally via its own
visited-set. Track and clip enumeration walks one CompositionMob's
slots, which is by construction non-cyclic.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from . import chain as chain_mod
from . import recovery as recovery_mod
from . import resolver as resolver_mod
from ._pyaaf_helpers import (
    format_rational as _format_rational,
    rational_to_float as _rational_to_float,
    slot_property as _slot_property,
)


# Map pyaaf2 slot.media_kind values to operator-meaningful kinds.
# Anything not in this map is filtered out of the v1 Tracks list
# (Timecode, DataEssence, Edgecode, etc. are housekeeping for the
# operator workflow).
_KIND_FROM_MEDIA: dict[str, str] = {
    "Sound": "audio",
    "Picture": "video",
}


def format_timecode(frame_count: Optional[int], fps_nominal: Optional[int],
                    drop: bool = False) -> Optional[str]:
    """
    Render an absolute frame count as a SMPTE timecode string.

    Non-drop: "HH:MM:SS:FF". Drop-frame (29.97 / 59.94): "HH:MM:SS;FF"
    using the standard SMPTE 12M drop-frame algorithm — at every
    minute boundary except every tenth minute, the first two
    (or four for 60-fps) frame numbers are dropped.

    Returns None if frame_count or fps_nominal is missing.
    """
    if frame_count is None or fps_nominal is None:
        return None
    f = int(frame_count)
    fps = int(fps_nominal)
    if fps <= 0:
        return None
    sep = ":"
    if drop and fps in (30, 60):
        drop_frames = 2 if fps == 30 else 4
        frames_per_min = (60 * fps) - drop_frames
        frames_per_10min = (10 * 60 * fps) - (9 * drop_frames)
        d = f // frames_per_10min
        m = f % frames_per_10min
        if m > drop_frames:
            f = f + (drop_frames * 9 * d) + drop_frames * (
                (m - drop_frames) // frames_per_min
            )
        else:
            f = f + (drop_frames * 9 * d)
        sep = ";"
    frames = f % fps
    secs = (f // fps) % 60
    mins = (f // (fps * 60)) % 60
    hrs = f // (fps * 60 * 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}{sep}{frames:02d}"


def format_seconds(seconds: Optional[float]) -> Optional[str]:
    """Render seconds as MM:SS or HH:MM:SS (depending on magnitude)."""
    if seconds is None:
        return None
    total = abs(seconds)
    sign = "-" if seconds < 0 else ""
    hrs = int(total // 3600)
    mins = int((total % 3600) // 60)
    secs = total - (hrs * 3600 + mins * 60)
    if hrs > 0:
        return f"{sign}{hrs:d}:{mins:02d}:{secs:06.3f}"
    return f"{sign}{mins:d}:{secs:06.3f}"


@dataclass(frozen=True)
class Track:
    ordinal: int                     # PhysicalTrackNumber where set (>0), else slot_id
    slot_id: int
    name: Optional[str]              # slot.name if non-empty
    kind: str                        # "audio" | "video"
    edit_rate: Optional[str]         # "num/den"
    length: Optional[int]            # slot.segment.length in edit_rate units
    clip_count: int                  # number of components on the slot's Sequence
    pan_channel: Optional[str] = None  # "L" / "R" — Premiere stereo-split signal

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_track"
        return d


def _track_pan_channel(segment: Any) -> Optional[str]:
    """
    Premiere stereo-split detection.

    If `segment` is a "Mono Audio Pan" OperationGroup carrying a
    ConstantValue AAFRational parameter at the LEFT (≈0.0) or
    RIGHT (≈1.0) extreme, return "L" or "R". Returns None for any
    other segment shape, any non-Mono-Audio-Pan operation, or a pan
    position other than hard L/R.

    Restricted to OPERATION NAME == "Mono Audio Pan" exactly (the
    Premiere-specific operation used for stereo-split panning).
    Avid uses a different operation called "Audio Pan" (note: no
    "Mono") that carries different parameter shapes; matching the
    full name avoids false positives. Required parameter must be a
    ConstantValue wrapping an AAFRational with a num/den ratio in
    {≈0.0, ≈1.0}.
    """
    if segment is None:
        return None
    if type(segment).__name__ != "OperationGroup":
        return None
    op_def = getattr(segment, "operation", None)
    op_name = getattr(op_def, "name", None) if op_def else None
    if op_name != "Mono Audio Pan":
        return None
    try:
        params = list(getattr(segment, "parameters", None) or [])
    except Exception:
        return None
    for param in params:
        # Only ConstantValue parameters are eligible — VaryingValue
        # carries automation that we don't reduce to a single
        # channel. AAFRational required.
        if type(param).__name__ != "ConstantValue":
            continue
        v = getattr(param, "value", None)
        if v is None:
            continue
        # Must be an AAFRational (not a bare Python int / str).
        if type(v).__name__ != "AAFRational":
            continue
        num = getattr(v, "numerator", None)
        den = getattr(v, "denominator", None)
        if not isinstance(num, int) or not isinstance(den, int) or den == 0:
            continue
        ratio = float(num) / float(den)
        if ratio < 0.1:
            return "L"
        if ratio > 0.9:
            return "R"
        # Pan position other than hard L/R — defer (no channel hint)
        return None
    return None


def _composition_total_slot_length(mob: Any) -> int:
    """Sum of segment lengths across all slots of a CompositionMob.
    Used as the second-tier tiebreak in pick_topmost_composition."""
    total = 0
    for slot in getattr(mob, "slots", []) or []:
        seg = getattr(slot, "segment", None)
        ln = getattr(seg, "length", None) if seg is not None else None
        if isinstance(ln, int):
            total += ln
    return total


def pick_topmost_composition(handle: Any) -> Optional[Any]:
    """
    Pick the CompositionMob most likely to represent the picture-edit
    timeline. Returns None if the file has no CompositionMobs (essence-
    only AAFs); caller renders an empty Tracks state in that case.

    Heuristic, in order:
      1. Exactly one CompositionMob → use it.
      2. Most slots wins (timelines have many; reference comps have few).
      3. Tied on slot count → longest aggregate slot length wins.
      4. Still tied → first by iteration order (deterministic in pyaaf2).
    """
    comps = list(handle.content.compositionmobs())
    if not comps:
        return None
    if len(comps) == 1:
        return comps[0]

    def slot_count(m: Any) -> int:
        return len(list(getattr(m, "slots", []) or []))

    max_slots = max(slot_count(m) for m in comps)
    candidates = [m for m in comps if slot_count(m) == max_slots]
    if len(candidates) == 1:
        return candidates[0]

    max_len = max(_composition_total_slot_length(m) for m in candidates)
    candidates = [m for m in candidates if _composition_total_slot_length(m) == max_len]
    return candidates[0]


def _operation_group_inputs(og: Any) -> list[Any]:
    """
    Read OperationGroup.InputSegments via property iteration.

    pyaaf2 doesn't expose InputSegments as a Python attribute on
    OperationGroup, so getattr returns nothing and chain.py's existing
    OperationGroup branch silently fails on real files. We pull it out
    via the property-name dictionary access pattern here.
    """
    try:
        prop = og["InputSegments"]
    except KeyError:
        return []
    val = getattr(prop, "value", None)
    if val is None:
        return []
    return list(val)


def _unwrap_operation_group(og: Any) -> Optional[Any]:
    """If `og` wraps exactly one SourceClip with a non-zero mob_id, return
    that SourceClip. Otherwise None.

    Avid wraps individual clips in audio-level OperationGroups; without
    unwrapping, every clip in a real session AAF appears as a single
    opaque OperationGroup blob with no recoverable mic identity."""
    inputs = _operation_group_inputs(og)
    clips = [c for c in inputs if type(c).__name__ == "SourceClip"]
    if len(clips) == 1:
        mid = getattr(clips[0], "mob_id", None)
        if mid is not None and getattr(mid, "int", 0) != 0:
            return clips[0]
    return None


def _peel_track_wrapper(segment: Any) -> Any:
    """If a slot's segment is an OperationGroup whose single InputSegments
    entry is a Sequence, return that inner Sequence. Otherwise return
    segment unchanged.

    Avid wraps each track's Sequence in a track-level OperationGroup
    (audio level / EQ automation). The operator-meaningful clip list
    lives one level inside."""
    if type(segment).__name__ != "OperationGroup":
        return segment
    inputs = _operation_group_inputs(segment)
    if len(inputs) == 1 and type(inputs[0]).__name__ == "Sequence":
        return inputs[0]
    return segment


def _segment_clip_count(segment: Any) -> int:
    """A Sequence's clip count is len(components); any other component
    on the slot counts as one clip. Track-level OperationGroup wrappers
    are peeled first so the count reflects the operator-visible clips,
    not the wrapper."""
    if segment is None:
        return 0
    peeled = _peel_track_wrapper(segment)
    if type(peeled).__name__ == "Sequence":
        components = getattr(peeled, "components", None) or []
        return len(list(components))
    return 1


def list_tracks(handle: Any) -> list[Track]:
    """
    Enumerate audio + video tracks on the topmost CompositionMob,
    ordered by slot_id ascending. Empty list if no CompositionMobs.

    Each Track also carries pan_channel ("L"/"R"/None) when the
    slot's segment is a Mono Audio Pan OperationGroup with a
    hard-L or hard-R parameter (Premiere stereo-split signal).
    """
    comp = pick_topmost_composition(handle)
    if comp is None:
        return []

    out: list[Track] = []
    slots = sorted(
        list(getattr(comp, "slots", []) or []),
        key=lambda s: int(getattr(s, "slot_id", 0) or 0),
    )
    for slot in slots:
        media_kind = getattr(slot, "media_kind", None)
        kind = _KIND_FROM_MEDIA.get(media_kind)
        if kind is None:
            continue
        slot_id = int(getattr(slot, "slot_id", 0) or 0)
        ptn = _slot_property(slot, "PhysicalTrackNumber")
        ordinal = int(ptn) if isinstance(ptn, int) and ptn > 0 else slot_id
        nm = getattr(slot, "name", None)
        name = nm if isinstance(nm, str) and nm else None
        seg = getattr(slot, "segment", None)
        length = getattr(seg, "length", None) if seg is not None else None
        out.append(
            Track(
                ordinal=ordinal,
                slot_id=slot_id,
                name=name,
                kind=kind,
                edit_rate=_format_rational(getattr(slot, "edit_rate", None)),
                length=int(length) if isinstance(length, int) else None,
                clip_count=_segment_clip_count(seg),
                pan_channel=_track_pan_channel(seg),
            )
        )
    return out


@dataclass(frozen=True)
class Clip:
    index: int                              # position on the slot's Sequence
    component_class: str                    # "SourceClip" | "Filler" | "OperationGroup" | ...
    timeline_start: Optional[int]           # in slot edit-rate units
    length: Optional[int]
    source_mob_id: Optional[str]            # what the SourceClip points at, if any
    source_mob_name: Optional[str]
    source_mob_slot_id: Optional[int]
    mic_identity: Optional[str]             # terminal SourceMob.name when is_recorder_source
    is_recorder_source: bool                # filter for the recorder-source-only fast path
    terminal_reason: Optional[str]          # from chain-walk terminal hop
    physical_track_number: Optional[int]    # PTN at the terminal hop's slot
    chain_length: int                       # number of hops walked (0 for non-SourceClip)

    # Per-clip operator info
    source_locators: tuple = ()             # tuple of {url, kind, online} dicts
    head_handle_frames: Optional[int] = None
    tail_handle_frames: Optional[int] = None
    head_handle_seconds: Optional[float] = None
    tail_handle_seconds: Optional[float] = None
    audio_sample_rate: Optional[str] = None      # "48000/1"
    audio_bits_per_sample: Optional[int] = None
    audio_channels: Optional[int] = None
    terminal_mob_id: Optional[str] = None        # terminal SourceMob's mob_id (URN)
    terminal_mob_class: Optional[str] = None     # convenience: terminal hop's mob class
    sub_clips: tuple = ()                   # tuple of Clip — multi-input combiner inputs
    # Format-aware recovery status. "recoverable" means the operator
    # can trust mic_identity (or sub_clips); "unrecoverable" means
    # the AAF doesn't carry the channel info (e.g. Premiere
    # multichannel polywav imports); "ambiguous" means the chain
    # surfaced something but not enough to be sure.
    recovery_status: str = "recoverable"
    recovery_method: Optional[str] = None        # short label for how (or why not)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_clip"
        # Tuples become lists in asdict; ensure sub_clips and locators
        # are list-of-dict for JSON consumers.
        d["source_locators"] = list(self.source_locators)
        d["sub_clips"] = [c.to_dict() if isinstance(c, Clip) else c
                          for c in self.sub_clips]
        return d


def _find_slot(comp: Any, slot_id: int) -> Optional[Any]:
    for s in getattr(comp, "slots", []) or []:
        if int(getattr(s, "slot_id", 0) or 0) == int(slot_id):
            return s
    return None


def _segment_components(segment: Any) -> list[Any]:
    """A slot's clip-list. Peels the track-level OperationGroup wrapper
    first (Avid pattern). If the result is a Sequence, return its
    components; otherwise treat the whole segment as a single clip."""
    if segment is None:
        return []
    peeled = _peel_track_wrapper(segment)
    if type(peeled).__name__ == "Sequence":
        return list(getattr(peeled, "components", None) or [])
    return [peeled]


def _build_source_clip_clip(handle: Any, index: int, comp_obj: Any,
                            timeline_start: int, length: Optional[int],
                            comp_slot_edit_rate: Any = None,
                            authoring_kind: str = "unknown",
                            pan_channel: Optional[str] = None) -> "Clip":
    """Build a Clip for a SourceClip component, including chain-walk
    derived mic identity, recorder-source filter, and per-clip
    operator info (source locators, head/tail handles, per-clip audio
    specs).

    comp_slot_edit_rate is the AAFRational from the COMP slot containing
    this clip — needed to convert head/tail handle math from comp slot
    units to source-file (audio sample) units when the rates differ
    (Avid: 29.97 timeline rate vs 48000 audio rate)."""
    source_mob_id = None
    source_mob_name = None
    source_mob_slot_id = None
    mic_identity = None
    is_recorder = False
    terminal_reason = None
    ptn = None
    chain_length = 0
    source_locators: tuple = ()
    head_h: Optional[int] = None
    tail_h: Optional[int] = None
    head_s: Optional[float] = None
    tail_s: Optional[float] = None
    audio_sr: Optional[str] = None
    audio_bps: Optional[int] = None
    audio_ch: Optional[int] = None
    terminal_mob_id_v: Optional[str] = None
    terminal_mob_class_v: Optional[str] = None

    mid = getattr(comp_obj, "mob_id", None)
    if mid is None or getattr(mid, "int", 0) == 0:
        # Zero-MobID SourceClip = silence/leader; behaves like Filler.
        terminal_reason = "no_source_id"
        return Clip(
            index=index, component_class="SourceClip",
            timeline_start=timeline_start, length=length,
            source_mob_id=None, source_mob_name=None, source_mob_slot_id=None,
            mic_identity=None, is_recorder_source=False,
            terminal_reason=terminal_reason, physical_track_number=None,
            chain_length=0,
            recovery_status="unrecoverable",
            recovery_method="no_source_id",
        )

    source_mob_id = str(mid)
    target_mob = getattr(comp_obj, "mob", None)
    if target_mob is not None:
        tn = getattr(target_mob, "name", None)
        source_mob_name = tn if isinstance(tn, str) else None
    smid = getattr(comp_obj, "slot_id", None)
    source_mob_slot_id = int(smid) if smid is not None else None

    try:
        hops = chain_mod.walk_chain(handle, comp_obj)
        chain_length = len(hops)
        terminal = hops[-1]
        terminal_reason = terminal.terminal_reason
        ptn = terminal.physical_track_number
        # Recorder-source filter (Gotcha 3 from identifying-clip-channels.md):
        # any terminal SourceMob with a real PhysicalTrackNumber. We don't
        # require terminal_reason=="essence" because real-world Avid AAFs
        # often terminate the SourceMob slot with a zero-mob SourceClip
        # (terminal_reason=="no_source_id") rather than an empty Sequence
        # (terminal_reason=="essence"). Both shapes mean "we hit the
        # recorder mob; nothing further to walk." Excludes OperationGroup,
        # CompositionMob, Filler, Timecode terminals naturally.
        if (
            terminal.mob_class == "SourceMob"
            and isinstance(terminal.physical_track_number, int)
            and terminal.physical_track_number > 0
        ):
            is_recorder = True
            # Prefer the named SourceMob — but in real Avid sessions the
            # immediate-terminal SourceMob is sometimes an unnamed
            # intermediate (a "physical source mob" pointing at the file
            # source mob). Walk back to the most recent named SourceMob
            # in the chain so the operator sees a meaningful label.
            mic_identity = terminal.mob_name
            if not mic_identity:
                for h in reversed(hops):
                    if h.mob_class == "SourceMob" and h.mob_name:
                        mic_identity = h.mob_name
                        break

        # Per-clip operator info derived from the chain hops.
        #
        # In real Avid AAFs the chain is typically:
        #   MasterMob → file SourceMob (WAVE/PCM/AIFC, with Locator)
        #             → tape SourceMob (TapeDescriptor, NAMED with the
        #               recorder channel identifier)
        #
        # The terminal mob is the named tape mob (drives mic_identity).
        # But the file path + audio specs + handle math live on the
        # MID-CHAIN file mob with the WAVE/PCM/AIFC descriptor. Scan
        # every hop for the first SourceMob whose descriptor carries
        # audio info; use it for locators / audio specs / handles.
        terminal_mob_id_v = (
            terminal.mob_id if isinstance(terminal.mob_id, str) and terminal.mob_id else None
        )
        terminal_mob_class_v = terminal.mob_class

        # In real Avid AAFs the chain typically goes
        #   MasterMob → file SourceMob (WAVE/PCM/AIFC, with audio info)
        #             → tape SourceMob (TapeDescriptor, NAMED with the
        #               recorder identifier — drives mic_identity)
        # Handles AND audio info both belong to the FILE mob: its slot
        # edit-rate is the audio sample rate, its slot length is the
        # full source-file length. The terminal tape mob's slot is in
        # video frames and its lengths aren't operator-meaningful for
        # handle math. Scan all hops for the first SourceMob whose
        # descriptor has audio info; use it for handles + audio specs +
        # locators. Fall back to the terminal mob's slot when no
        # audio-bearing mob is present (ImportDescriptor cases).
        audio_mob = None
        audio_slot_id = None
        for h in hops:
            if h.mob_class != "SourceMob":
                continue
            mob_obj = _resolve_mob_by_id_str(handle, h.mob_id)
            if mob_obj is None:
                continue
            desc = getattr(mob_obj, "descriptor", None)
            audio_info = _audio_descriptor_info(desc)
            if audio_info is None:
                continue
            source_locators = _collect_locators(desc)
            audio_sr = audio_info.get("sample_rate")
            audio_bps = audio_info.get("bits_per_sample")
            audio_ch = audio_info.get("channels")
            audio_mob = mob_obj
            audio_slot_id = h.slot_id
            break

        if audio_mob is not None:
            head_h, tail_h, head_s, tail_s = _compute_handles(
                comp_obj, comp_slot_edit_rate, audio_mob, audio_slot_id,
            )
        else:
            terminal_mob = _resolve_mob_by_id_str(handle, terminal.mob_id)
            if terminal_mob is not None and type(terminal_mob).__name__ == "SourceMob":
                head_h, tail_h, head_s, tail_s = _compute_handles(
                    comp_obj, comp_slot_edit_rate, terminal_mob, terminal.slot_id,
                )
    except ValueError:
        # walk_chain raises ValueError when the SourceClip points at a
        # mob not in this file. Surface that without aborting the listing.
        terminal_reason = "broken_ref"

    rec = recovery_mod.classify(
        authoring_kind=authoring_kind,
        pan_channel=pan_channel,
        is_recorder=is_recorder,
        terminal_class=terminal_mob_class_v,
        terminal_reason=terminal_reason,
        source_mob_name=source_mob_name,
        mic_identity=mic_identity,
    )
    rec_status, rec_method = rec.status, rec.method

    # Premiere stereo-split: enrich the visible mic_identity with
    # the (L)/(R) suffix when we recovered via pan but the source mob
    # name doesn't already include _L/_R. Operator-meaningful display.
    display_mic = mic_identity
    if (rec_method or "").startswith("premiere_stereo_split_pan_") and pan_channel:
        if display_mic is None:
            display_mic = source_mob_name
        if display_mic and not (
            display_mic.endswith("_L") or display_mic.endswith("_R")
        ):
            display_mic = f"{display_mic} ({pan_channel})"

    return Clip(
        index=index,
        component_class="SourceClip",
        timeline_start=timeline_start,
        length=length,
        source_mob_id=source_mob_id,
        source_mob_name=source_mob_name,
        source_mob_slot_id=source_mob_slot_id,
        mic_identity=display_mic,
        is_recorder_source=is_recorder,
        terminal_reason=terminal_reason,
        physical_track_number=ptn,
        chain_length=chain_length,
        source_locators=source_locators,
        head_handle_frames=head_h,
        tail_handle_frames=tail_h,
        head_handle_seconds=head_s,
        tail_handle_seconds=tail_s,
        audio_sample_rate=audio_sr,
        audio_bits_per_sample=audio_bps,
        audio_channels=audio_ch,
        terminal_mob_id=terminal_mob_id_v,
        terminal_mob_class=terminal_mob_class_v,
        recovery_status=rec_status,
        recovery_method=rec_method,
    )


@dataclass(frozen=True)
class TimecodeInfo:
    edit_rate: Optional[str]   # "30000/1001"
    edit_rate_value: Optional[float]  # 29.97002997...
    fps_nominal: Optional[int]  # 30 (for 29.97), 25 (for 25), etc.
    drop: Optional[bool]
    start_frames: Optional[int]
    start_timecode: Optional[str]   # "01:00:00;00" pre-rendered

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_timecode"
        return d


@dataclass(frozen=True)
class AuthoringInfo:
    """The latest entry in Header.IdentificationList — answers
    'who wrote this file, with what tool, when?'"""
    product_name: Optional[str]          # "Avid Media Composer 24.12.1"
    company_name: Optional[str]          # "Avid Technology, Inc."
    platform: Optional[str]              # "AAFSDK (Win64)"
    date: Optional[str]                  # ISO timestamp
    identification_count: int            # 1 typically; >1 means multiple revisions
    kind: str = "unknown"                # "avid" | "premiere" | "protools" | "unknown"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_authoring"
        return d


def _classify_product_name(name: Optional[str]) -> str:
    """Normalize a Header.IdentificationList ProductName into one of
    the kinds we branch on for format-aware recovery."""
    if not name or not isinstance(name, str):
        return "unknown"
    lower = name.lower()
    if "avid" in lower:
        return "avid"
    if "premiere" in lower:
        return "premiere"
    if "pro tools" in lower or "protools" in lower:
        return "protools"
    return "unknown"


def detect_authoring_kind(handle: Any) -> str:
    """Read Header.IdentificationList[-1].ProductName and classify
    into 'avid' / 'premiere' / 'protools' / 'unknown'. Used by the
    operator layer to dispatch format-specific recovery rules."""
    try:
        ident_list = list(handle.header["IdentificationList"].value)
    except Exception:
        return "unknown"
    if not ident_list:
        return "unknown"
    last = ident_list[-1]
    try:
        nm = last["ProductName"].value
    except Exception:
        return "unknown"
    return _classify_product_name(nm if isinstance(nm, str) else None)


@dataclass(frozen=True)
class SessionSummary:
    file_size_bytes: Optional[int]
    topmost_composition_name: Optional[str]
    topmost_composition_mob_id: Optional[str]
    audio_track_count: int
    video_track_count: int
    timecode_track_count: int
    total_clip_count: int           # sum across audio + video tracks
    composition_mob_count: int
    master_mob_count: int
    source_mob_count: int
    timecode: Optional[TimecodeInfo]
    duration_frames: Optional[int]   # in timecode edit_rate units
    duration_seconds: Optional[float]
    duration_timecode: Optional[str]   # end TC ("HH:MM:SS;FF") = start+duration
    authoring: Optional[AuthoringInfo]
    last_modified: Optional[str]       # Header.LastModified, ISO timestamp
    audio: Optional[AudioSummary]      # sample rate / bit depth / channel breakdown

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_session_summary"
        if self.timecode is not None:
            d["timecode"] = self.timecode.to_dict()
        if self.authoring is not None:
            d["authoring"] = self.authoring.to_dict()
        if self.audio is not None:
            d["audio"] = self.audio.to_dict()
        return d


def _isoformat(v: Any) -> Optional[str]:
    if v is None:
        return None
    fn = getattr(v, "isoformat", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return str(v)
    return str(v)


def _build_authoring_info(handle: Any) -> Optional[AuthoringInfo]:
    """Pull the most recent Identification entry from the file's
    Header.IdentificationList. Returns None if the list is missing.

    The list is ordered creation → most-recent-modification by AAF
    convention; we surface the LAST entry as 'authored by' so the UI
    reflects the tool that wrote the file most recently."""
    try:
        ident_list = list(handle.header["IdentificationList"].value)
    except Exception:
        return None
    if not ident_list:
        return None
    last = ident_list[-1]
    def _prop(name: str) -> Any:
        try:
            return last[name].value
        except Exception:
            return None
    pn = _prop("ProductName")
    return AuthoringInfo(
        product_name=(pn if isinstance(pn, str) else None),
        company_name=(_prop("CompanyName") if isinstance(_prop("CompanyName"), str) else None),
        platform=(_prop("Platform") if isinstance(_prop("Platform"), str) else None),
        date=_isoformat(_prop("Date")),
        identification_count=len(ident_list),
        kind=_classify_product_name(pn if isinstance(pn, str) else None),
    )


def _header_last_modified(handle: Any) -> Optional[str]:
    try:
        return _isoformat(handle.header["LastModified"].value)
    except Exception:
        return None


# ---------- Audio descriptor extraction ----------


def _parse_wave_summary(buf: Any) -> Optional[dict[str, int]]:
    """
    Parse a WAVEDescriptor.Summary blob (a RIFF/WAVE container) and
    return {"channels", "sample_rate", "bits_per_sample"} from the
    'fmt ' chunk. Returns None on malformed input.

    pyaaf2 yields Summary as a list[int]; bytes() promotes that.
    """
    if buf is None:
        return None
    try:
        b = bytes(buf)
    except Exception:
        return None
    # Need at least the RIFF header (12 bytes) + a fmt-chunk header
    # (8 bytes) + minimum 16-byte fmt body. The chunk loop further
    # validates bounds before reading.
    if len(b) < 12 or b[0:4] != b"RIFF" or b[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(b):
        chunk_id = b[pos:pos + 4]
        chunk_size = int.from_bytes(b[pos + 4:pos + 8], "little")
        if chunk_id == b"fmt ":
            data_start = pos + 8
            if data_start + 16 > len(b):
                return None
            fmt = b[data_start:data_start + 16]
            return {
                "channels": int.from_bytes(fmt[2:4], "little"),
                "sample_rate": int.from_bytes(fmt[4:8], "little"),
                "bits_per_sample": int.from_bytes(fmt[14:16], "little"),
            }
        # RIFF chunks are padded to even length.
        pos += 8 + chunk_size + (chunk_size & 1)
    return None


def _audio_descriptor_info(desc: Any) -> Optional[dict[str, Any]]:
    """
    Read audio format info from a SourceMob descriptor, if it carries
    one. Returns {sample_rate (str "n/d"), bits_per_sample, channels}
    or None.

    Handles:
      - WAVEDescriptor: parses the embedded RIFF/WAVE Summary blob.
        SampleRate property is also exposed directly; use it as the
        primary source and fall back to the Summary fmt chunk for
        bit depth and channel count.
      - PCMDescriptor: properties are exposed directly per AAF spec.
      - Other descriptors (ImportDescriptor, TapeDescriptor,
        NetworkLocator-only): return None.
    """
    if desc is None:
        return None
    cls = type(desc).__name__

    def _prop(name: str) -> Any:
        try:
            return desc[name].value
        except Exception:
            return None

    if cls == "PCMDescriptor":
        rate = _prop("AudioSamplingRate")
        bits = _prop("QuantizationBits")
        ch = _prop("Channels")
        return {
            "sample_rate": _format_rational(rate),
            "bits_per_sample": int(bits) if isinstance(bits, int) else None,
            "channels": int(ch) if isinstance(ch, int) else None,
        }

    if cls == "WAVEDescriptor":
        rate = _prop("SampleRate")
        wav = _parse_wave_summary(_prop("Summary"))
        sample_rate = _format_rational(rate)
        if sample_rate is None and wav is not None:
            sample_rate = f"{wav['sample_rate']}/1"
        return {
            "sample_rate": sample_rate,
            "bits_per_sample": (wav or {}).get("bits_per_sample"),
            "channels": (wav or {}).get("channels"),
        }

    return None


@dataclass(frozen=True)
class AudioSummary:
    """Aggregated audio-format breakdown across SourceMob descriptors."""
    audio_source_count: int            # source mobs where we found a usable descriptor
    sample_rates: dict[str, int]       # {"48000/1": 2014}
    bit_depths: dict[int, int]         # {24: 2014}
    channel_counts: dict[int, int]     # {1: 2014, 2: 18}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_audio_summary"
        return d


# ---------- per-clip locator + handle helpers ----------


def _locator_url(loc: Any) -> Optional[str]:
    """Read the URL out of a NetworkLocator or TextLocator."""
    cls = type(loc).__name__
    try:
        if cls == "NetworkLocator":
            v = loc["URLString"].value
            return v if isinstance(v, str) else None
        if cls == "TextLocator":
            v = loc["Name"].value
            return v if isinstance(v, str) else None
    except Exception:
        return None
    return None


def _url_to_local_path(url: str) -> Optional[str]:
    """
    Best-effort URL -> local filesystem path conversion.

    Handles:
      - file:///path/to/file → /path/to/file
      - bare /absolute/path → /absolute/path
      - relative paths → None (we don't second-guess the working dir)
      - urn:smpte:... → None (a UMID, not a path)
      - other schemes (http, smb, ...) → None
    """
    if not url:
        return None
    if url.startswith("/"):
        return url
    parsed = urlparse(url)
    # Only accept absolute file:// URLs. Bare relative paths (no scheme,
    # no leading slash) are ambiguous without a working directory and
    # we don't second-guess.
    if parsed.scheme == "file":
        path = unquote(parsed.path or "")
        if not path:
            return None
        return path
    return None


def _is_online(url: str) -> Optional[bool]:
    """
    Local-only online check. None = unknown (non-local URL).
    True = file://-resolvable path that exists on disk.
    False = file://-resolvable path that doesn't exist.
    """
    local = _url_to_local_path(url)
    if local is None:
        return None
    try:
        return os.path.exists(local)
    except OSError:
        return False


def _collect_locators(desc: Any) -> tuple:
    """
    Pull the Locator entries off a SourceMob descriptor and return
    them as a tuple of {url, kind, online} dicts. Empty tuple if no
    descriptor or no Locator property.
    """
    if desc is None:
        return ()
    try:
        prop = desc["Locator"]
    except Exception:
        return ()
    val = getattr(prop, "value", None)
    if val is None:
        return ()
    out = []
    for loc in val:
        url = _locator_url(loc)
        if not url:
            continue
        cls = type(loc).__name__
        kind = "network" if cls == "NetworkLocator" else (
            "text" if cls == "TextLocator" else cls
        )
        out.append({"url": url, "kind": kind, "online": _is_online(url)})
    return tuple(out)


def _resolve_mob_by_id_str(handle: Any, mob_id_str: Optional[str]) -> Optional[Any]:
    """Look up a Mob by its URN/hex string. None on parse failure or
    not-in-file. Wrapper around resolver._try_parse_mob_id +
    handle.content.mobs.get."""
    if not mob_id_str:
        return None
    parsed = resolver_mod._try_parse_mob_id(mob_id_str)
    if parsed is None:
        return None
    try:
        return handle.content.mobs.get(parsed, None)
    except Exception:
        return None


def _compute_handles(
    source_clip: Any,
    source_clip_edit_rate: Optional[Any],
    target_mob: Any,
    target_slot_id: Optional[int],
) -> tuple:
    """
    Compute (head_frames, tail_frames, head_seconds, tail_seconds) for
    a clip given its originating SourceClip + the comp slot's edit
    rate, and a target SourceMob (typically the file SourceMob).

    head = source_clip.start (offset into the source) — converted
    from comp slot edit-rate units to target-slot edit-rate units when
    the rates differ. tail = target_slot_length - (start + length),
    all in the target slot's edit-rate units (typically the audio
    sample rate).

    Returns frames in TARGET slot units + derived seconds. seconds
    variants populated only when the target slot's edit_rate is known.

    The Avid case has comp slot at the timeline rate (29.97 fps) and
    the file SourceMob slot at the audio sample rate (48000/1). Without
    the rate conversion the numbers come out as bare counts in
    mismatched units and aren't operator-meaningful.
    """
    head_frames: Optional[int] = None
    tail_frames: Optional[int] = None
    head_seconds: Optional[float] = None
    tail_seconds: Optional[float] = None

    if source_clip is None or target_mob is None:
        return (head_frames, tail_frames, head_seconds, tail_seconds)

    start = getattr(source_clip, "start", None)
    length = getattr(source_clip, "length", None)

    # Find the target slot to read its total length and edit rate.
    fn = getattr(target_mob, "slot_at", None)
    slot = None
    if callable(fn) and target_slot_id is not None:
        try:
            slot = fn(target_slot_id)
        except Exception:
            slot = None
    if slot is None:
        return (head_frames, tail_frames, head_seconds, tail_seconds)

    total_length = getattr(getattr(slot, "segment", None), "length", None)
    target_rate = _rational_to_float(getattr(slot, "edit_rate", None))
    src_rate = _rational_to_float(source_clip_edit_rate)

    # Convert comp-clip start/length into target slot units.
    def _to_target(n: Any) -> Optional[float]:
        if not isinstance(n, int):
            return None
        if src_rate is None or target_rate is None or src_rate <= 0:
            return float(n)  # assume same rate when we can't compare
        return n * (target_rate / src_rate)

    start_t = _to_target(start)
    length_t = _to_target(length)

    if start_t is not None:
        head_frames = int(round(start_t))
    if (
        isinstance(total_length, int)
        and start_t is not None
        and length_t is not None
    ):
        tail_frames = int(round(total_length - (start_t + length_t)))

    if target_rate and target_rate > 0:
        if head_frames is not None:
            head_seconds = head_frames / target_rate
        if tail_frames is not None:
            tail_seconds = tail_frames / target_rate

    return (head_frames, tail_frames, head_seconds, tail_seconds)


def _build_audio_summary(handle: Any) -> AudioSummary:
    sample_rates: dict[str, int] = {}
    bit_depths: dict[int, int] = {}
    channel_counts: dict[int, int] = {}
    audio_source_count = 0
    for sm in handle.content.sourcemobs():
        info = _audio_descriptor_info(getattr(sm, "descriptor", None))
        if info is None:
            continue
        audio_source_count += 1
        if info.get("sample_rate"):
            sr = info["sample_rate"]
            sample_rates[sr] = sample_rates.get(sr, 0) + 1
        if isinstance(info.get("bits_per_sample"), int) and info["bits_per_sample"] > 0:
            b = info["bits_per_sample"]
            bit_depths[b] = bit_depths.get(b, 0) + 1
        if isinstance(info.get("channels"), int) and info["channels"] > 0:
            c = info["channels"]
            channel_counts[c] = channel_counts.get(c, 0) + 1
    return AudioSummary(
        audio_source_count=audio_source_count,
        sample_rates=sample_rates,
        bit_depths=bit_depths,
        channel_counts=channel_counts,
    )


def _pick_timecode_slot(comp: Any) -> Optional[Any]:
    """Pick the Timecode slot on a CompositionMob. Prefer one whose
    segment is a Timecode; fall back to one whose segment is a Pulldown
    wrapping a Timecode."""
    candidates: list[tuple[int, Any, Any]] = []  # (priority, slot, timecode_seg)
    for slot in getattr(comp, "slots", []) or []:
        if getattr(slot, "media_kind", None) != "Timecode":
            continue
        seg = getattr(slot, "segment", None)
        if seg is None:
            continue
        cls = type(seg).__name__
        if cls == "Timecode":
            candidates.append((0, slot, seg))
            continue
        if cls == "Pulldown":
            inner = None
            try:
                inner = seg["InputSegment"].value
            except Exception:
                inner = getattr(seg, "input_segment", None)
            if inner is not None and type(inner).__name__ == "Timecode":
                candidates.append((1, slot, inner))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[0]  # (priority, slot, timecode_seg)


def _build_timecode_info(comp: Any) -> Optional[TimecodeInfo]:
    pick = _pick_timecode_slot(comp)
    if pick is None:
        return None
    _prio, slot, tc = pick
    edit_rate_obj = getattr(slot, "edit_rate", None)
    fps_nominal = getattr(tc, "fps", None)
    if fps_nominal is None:
        try:
            fps_nominal = tc["FPS"].value
        except Exception:
            fps_nominal = None
    drop = getattr(tc, "drop", None)
    if drop is None:
        try:
            drop = tc["Drop"].value
        except Exception:
            drop = None
    start = getattr(tc, "start", None)
    if start is None:
        try:
            start = tc["Start"].value
        except Exception:
            start = None
    return TimecodeInfo(
        edit_rate=_format_rational(edit_rate_obj),
        edit_rate_value=_rational_to_float(edit_rate_obj),
        fps_nominal=int(fps_nominal) if isinstance(fps_nominal, int) else None,
        drop=bool(drop) if drop is not None else None,
        start_frames=int(start) if isinstance(start, int) else None,
        start_timecode=format_timecode(
            int(start) if isinstance(start, int) else None,
            int(fps_nominal) if isinstance(fps_nominal, int) else None,
            bool(drop) if drop is not None else False,
        ),
    )


@dataclass(frozen=True)
class SourceInventoryEntry:
    """Per-SourceMob entry in the cross-track source pull list."""
    mob_id: str
    name: Optional[str]
    descriptor_class: Optional[str]
    sample_rate: Optional[str]
    bits_per_sample: Optional[int]
    channels: Optional[int]
    locators: tuple = ()                # tuple of {url, kind, online} dicts
    use_count: int = 0                  # number of clip references in topmost composition
    used_by: tuple = ()                 # tuple of {track_slot_id, track_name, clip_index, timeline_start}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_source_entry"
        d["locators"] = list(self.locators)
        d["used_by"] = list(self.used_by)
        return d


def _flatten_clip_terminal_mob_ids(clip: "Clip") -> list[str]:
    """Walk a Clip and any sub_clips, collecting terminal SourceMob IDs."""
    out: list[str] = []
    if clip.terminal_mob_id and clip.terminal_mob_class == "SourceMob":
        out.append(clip.terminal_mob_id)
    for sub in clip.sub_clips:
        if isinstance(sub, Clip):
            out.extend(_flatten_clip_terminal_mob_ids(sub))
    return out


def source_inventory(handle: Any, *, max_used_by_per_source: int = 50) -> list[SourceInventoryEntry]:
    """
    Cross-track deduplicated source-mob inventory.

    Iterates every SourceMob in the file (descriptor info, locators)
    then walks every audio + video clip on the topmost CompositionMob
    to populate per-mob use_count and used_by lists.

    Cost note: walks every clip in the topmost composition (~6000 on
    PWD_310). At sub-ms per chain walk this is a few seconds. Cache
    at the call site (web/state) — don't recompute per request.
    """
    by_mob_id: dict[str, dict[str, Any]] = {}
    for sm in handle.content.sourcemobs():
        desc = getattr(sm, "descriptor", None)
        info = _audio_descriptor_info(desc) or {}
        nm = getattr(sm, "name", None)
        by_mob_id[str(sm.mob_id)] = {
            "mob_id": str(sm.mob_id),
            "name": nm if isinstance(nm, str) else None,
            "descriptor_class": type(desc).__name__ if desc is not None else None,
            "sample_rate": info.get("sample_rate"),
            "bits_per_sample": info.get("bits_per_sample"),
            "channels": info.get("channels"),
            "locators": _collect_locators(desc),
            "use_count": 0,
            "used_by": [],
        }

    comp = pick_topmost_composition(handle)
    if comp is not None:
        for slot in getattr(comp, "slots", []) or []:
            kind = _KIND_FROM_MEDIA.get(getattr(slot, "media_kind", None))
            if kind is None:
                continue
            slot_id = int(getattr(slot, "slot_id", 0) or 0)
            slot_name_raw = getattr(slot, "name", None)
            slot_name = slot_name_raw if isinstance(slot_name_raw, str) and slot_name_raw else None
            try:
                clips = list_clips(handle, slot_id)
            except Exception:
                continue
            for clip in clips:
                for hit in _flatten_clip_terminal_mob_ids(clip):
                    entry = by_mob_id.get(hit)
                    if entry is None:
                        continue
                    entry["use_count"] += 1
                    if len(entry["used_by"]) < max_used_by_per_source:
                        entry["used_by"].append({
                            "track_slot_id": slot_id,
                            "track_name": slot_name,
                            "clip_index": clip.index,
                            "timeline_start": clip.timeline_start,
                        })

    out: list[SourceInventoryEntry] = []
    for raw in by_mob_id.values():
        out.append(SourceInventoryEntry(
            mob_id=raw["mob_id"],
            name=raw["name"],
            descriptor_class=raw["descriptor_class"],
            sample_rate=raw["sample_rate"],
            bits_per_sample=raw["bits_per_sample"],
            channels=raw["channels"],
            locators=tuple(raw["locators"]),
            use_count=raw["use_count"],
            used_by=tuple(raw["used_by"]),
        ))
    # Sort: most-used first, then alphabetical by name. Keeps the pull
    # list operator-meaningful (sources actually in use float to top).
    out.sort(key=lambda e: (-e.use_count, (e.name or "").lower()))
    return out


def session_summary(handle: Any, *, file_size_bytes: Optional[int] = None) -> SessionSummary:
    """Headline summary of the open file: track + clip counts, mob
    counts, timecode info, and duration. The frontend renders this in
    a top horizontal info bar."""
    comp = pick_topmost_composition(handle)
    audio_count = 0
    video_count = 0
    timecode_count = 0
    total_clips = 0
    duration_frames: Optional[int] = None

    # Track counts and duration via list_tracks + raw slot iteration
    if comp is not None:
        for slot in getattr(comp, "slots", []) or []:
            mk = getattr(slot, "media_kind", None)
            if mk == "Sound":
                audio_count += 1
            elif mk == "Picture":
                video_count += 1
            elif mk == "Timecode":
                timecode_count += 1
            seg = getattr(slot, "segment", None)
            ln = getattr(seg, "length", None) if seg is not None else None
            if isinstance(ln, int):
                duration_frames = ln if duration_frames is None else max(duration_frames, ln)
        for t in list_tracks(handle):
            total_clips += t.clip_count

    # Mob counts (cheap — just iteration counts)
    composition_mob_count = sum(1 for _ in handle.content.compositionmobs())
    master_mob_count = sum(1 for _ in handle.content.mastermobs())
    source_mob_count = sum(1 for _ in handle.content.sourcemobs())

    tc_info = _build_timecode_info(comp) if comp is not None else None

    duration_seconds: Optional[float] = None
    duration_tc: Optional[str] = None
    if duration_frames is not None and tc_info is not None:
        if tc_info.edit_rate_value:
            duration_seconds = duration_frames / tc_info.edit_rate_value
        if tc_info.fps_nominal is not None:
            # Render the duration as an offset TC starting from 00:00:00:00
            duration_tc = format_timecode(
                duration_frames, tc_info.fps_nominal, bool(tc_info.drop)
            )

    nm = getattr(comp, "name", None) if comp is not None else None
    mob_id = getattr(comp, "mob_id", None) if comp is not None else None
    return SessionSummary(
        file_size_bytes=file_size_bytes,
        topmost_composition_name=nm if isinstance(nm, str) else None,
        topmost_composition_mob_id=str(mob_id) if mob_id is not None else None,
        audio_track_count=audio_count,
        video_track_count=video_count,
        timecode_track_count=timecode_count,
        total_clip_count=total_clips,
        composition_mob_count=composition_mob_count,
        master_mob_count=master_mob_count,
        source_mob_count=source_mob_count,
        timecode=tc_info,
        duration_frames=duration_frames,
        duration_seconds=duration_seconds,
        duration_timecode=duration_tc,
        authoring=_build_authoring_info(handle),
        last_modified=_header_last_modified(handle),
        audio=_build_audio_summary(handle),
    )


def list_clips(handle: Any, slot_id: int) -> list[Clip]:
    """
    Enumerate clips on the given slot of the topmost CompositionMob,
    ordered as they appear on the timeline. Each clip carries
    operator-meaningful summary fields (recovered mic identity from the
    chain walk, source mob name, etc.).

    Raises:
      ValueError when no CompositionMob exists, or when slot_id doesn't
      identify a slot on the topmost composition. The web layer maps
      these to 404s.
    """
    comp = pick_topmost_composition(handle)
    if comp is None:
        raise ValueError("no CompositionMob in this file")

    slot = _find_slot(comp, slot_id)
    if slot is None:
        raise ValueError(
            f"no slot with slot_id={slot_id} on topmost CompositionMob"
        )

    components = _segment_components(getattr(slot, "segment", None))
    comp_slot_edit_rate = getattr(slot, "edit_rate", None)
    pan_channel = _track_pan_channel(getattr(slot, "segment", None))
    authoring_kind = detect_authoring_kind(handle)
    out: list[Clip] = []
    cursor = 0
    for i, comp_obj in enumerate(components):
        cls = type(comp_obj).__name__
        ln_raw = getattr(comp_obj, "length", None)
        length = int(ln_raw) if isinstance(ln_raw, int) else None

        # Per-clip OperationGroup handling:
        #  - Single-input wrapper (Avid audio-level automation): peel
        #    and treat as the inner SourceClip so the chain walk
        #    recovers the recorder identity.
        #  - Multi-input combiner (Avid mix-down, stereo bus, etc.):
        #    fan out into a top-level Clip with sub_clips populated,
        #    one Clip per input. Top-level mic_identity is None (no
        #    single answer); inputs each have their own.
        if cls == "OperationGroup":
            inner = _unwrap_operation_group(comp_obj)
            if inner is not None:
                clip = _build_source_clip_clip(
                    handle, i, inner, cursor, length, comp_slot_edit_rate,
                    authoring_kind, pan_channel,
                )
                out.append(clip)
                if isinstance(ln_raw, int):
                    cursor += ln_raw
                continue
            input_clips = chain_mod._operation_group_source_clip_inputs(comp_obj)
            if len(input_clips) >= 2:
                op_def = getattr(comp_obj, "operation", None)
                op_name = getattr(op_def, "name", None) if op_def else None
                sub_clips = tuple(
                    _build_source_clip_clip(
                        handle, j, inp, cursor,
                        int(getattr(inp, "length", 0))
                        if isinstance(getattr(inp, "length", None), int)
                        else None,
                        comp_slot_edit_rate,
                        authoring_kind, pan_channel,
                    )
                    for j, inp in enumerate(input_clips)
                )
                # Top-level combiner clip's recoverability is the
                # best of its sub_clips: if every input recovered,
                # the combiner is recoverable too; if any input is
                # ambiguous, the combiner is ambiguous; if all
                # unrecoverable, the combiner is unrecoverable.
                statuses = [s.recovery_status for s in sub_clips]
                if statuses and all(s == "recoverable" for s in statuses):
                    combiner_status = "recoverable"
                    combiner_method = "combiner_all_inputs_recovered"
                elif statuses and all(s == "unrecoverable" for s in statuses):
                    combiner_status = "unrecoverable"
                    combiner_method = "combiner_all_inputs_unrecoverable"
                else:
                    combiner_status = "ambiguous"
                    combiner_method = "combiner_mixed_input_recovery"

                clip = Clip(
                    index=i,
                    component_class="OperationGroup",
                    timeline_start=cursor,
                    length=length,
                    source_mob_id=None,
                    source_mob_name=None,
                    source_mob_slot_id=None,
                    mic_identity=None,
                    is_recorder_source=False,
                    terminal_reason=(
                        f"combiner:{op_name}" if isinstance(op_name, str)
                        else "combiner"
                    ),
                    physical_track_number=None,
                    chain_length=0,
                    sub_clips=sub_clips,
                    recovery_status=combiner_status,
                    recovery_method=combiner_method,
                )
                out.append(clip)
                if isinstance(ln_raw, int):
                    cursor += ln_raw
                continue

        if cls == "Transition":
            # Transitions in a Sequence represent the OVERLAP between
            # the two adjacent components, not extra timeline duration.
            # A transition of length L means: the previous component's
            # last L frames AND the next component's first L frames
            # both occupy the same L frames of timeline. So:
            #   - The transition's timeline span = [cursor - L, cursor]
            #   - The next non-transition component starts at cursor - L
            # Equivalently: cursor advances by -L when we hit a
            # transition. Without this, every clip following a
            # transition appears L frames later than it should, which
            # users see as "the clip Resolve shows at TC X is missing
            # from AAF Browser's view" because we put it at TC X+L.
            transition_len = int(ln_raw) if isinstance(ln_raw, int) else 0
            transition_start = cursor - transition_len
            clip = Clip(
                index=i,
                component_class=cls,
                timeline_start=transition_start,
                length=length,
                source_mob_id=None,
                source_mob_name=None,
                source_mob_slot_id=None,
                mic_identity=None,
                is_recorder_source=False,
                terminal_reason=cls.lower(),
                physical_track_number=None,
                chain_length=0,
                recovery_status="unrecoverable",
                recovery_method=f"non_source_clip_{cls.lower()}",
            )
            out.append(clip)
            cursor = transition_start
            continue

        if cls == "SourceClip":
            clip = _build_source_clip_clip(
                handle, i, comp_obj, cursor, length, comp_slot_edit_rate,
                authoring_kind, pan_channel,
            )
        else:
            # Filler, multi-input OperationGroup, Timecode, EssenceGroup,
            # etc. — surface as a clip row but don't try to chain-walk.
            # The frontend visually de-emphasizes these.
            clip = Clip(
                index=i,
                component_class=cls,
                timeline_start=cursor,
                length=length,
                source_mob_id=None,
                source_mob_name=None,
                source_mob_slot_id=None,
                mic_identity=None,
                is_recorder_source=False,
                terminal_reason=cls.lower(),
                physical_track_number=None,
                chain_length=0,
                recovery_status="unrecoverable",
                recovery_method=f"non_source_clip_{cls.lower()}",
            )

        out.append(clip)
        if isinstance(ln_raw, int):
            cursor += ln_raw
    return out
