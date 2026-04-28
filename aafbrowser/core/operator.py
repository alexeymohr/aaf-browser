"""
Operator-first view of an AAF: tracks (audio + video slots on the
topmost CompositionMob) and clips (Components on a slot's Sequence).

This is the wedge that establishes the post-production-sound-operator
default surface (Phase 6). It composes core.chain.walk_chain to
recover mic identity per clip, but adds no new pyaaf2 walking
primitives of its own — track/clip enumeration is straightforward
iteration over a single CompositionMob's slots.

Cycle safety: the chain-walk handles cycles internally via its own
visited-set. Track and clip enumeration walks one CompositionMob's
slots, which is by construction non-cyclic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from . import chain as chain_mod


# Map pyaaf2 slot.media_kind values to operator-meaningful kinds.
# Anything not in this map is filtered out of the v1 Tracks list
# (Timecode, DataEssence, Edgecode, etc. are housekeeping for the
# operator workflow).
_KIND_FROM_MEDIA: dict[str, str] = {
    "Sound": "audio",
    "Picture": "video",
}


def _format_rational(r: Any) -> Optional[str]:
    """Same shape as core/chain._format_rational; duplicated to keep this
    module's dependency graph one-directional (operator → chain only)."""
    if r is None:
        return None
    num = getattr(r, "numerator", None)
    den = getattr(r, "denominator", None)
    if num is None or den is None:
        return str(r)
    return f"{int(num)}/{int(den)}"


def _rational_to_float(r: Any) -> Optional[float]:
    if r is None:
        return None
    num = getattr(r, "numerator", None)
    den = getattr(r, "denominator", None)
    if num is None or den is None or int(den) == 0:
        return None
    return float(num) / float(den)


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


def _slot_property(slot: Any, name: str) -> Any:
    """
    Look up a slot Property by name via the properties() iterator.

    PhysicalTrackNumber and similar slot Properties are not auto-exposed
    as Python attributes by pyaaf2 — getattr returns None even when the
    property is set. Iterating slot.properties() is the only reliable
    accessor. (Same pattern as core/chain._slot_property.)
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


@dataclass(frozen=True)
class Track:
    ordinal: int                     # PhysicalTrackNumber where set (>0), else slot_id
    slot_id: int
    name: Optional[str]              # slot.name if non-empty
    kind: str                        # "audio" | "video"
    edit_rate: Optional[str]         # "num/den"
    length: Optional[int]            # slot.segment.length in edit_rate units
    clip_count: int                  # number of components on the slot's Sequence

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_track"
        return d


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
    is_recorder_source: bool                # Gotcha-3 filter (see identifying-clip-channels.md)
    terminal_reason: Optional[str]          # from chain-walk terminal hop
    physical_track_number: Optional[int]    # PTN at the terminal hop's slot
    chain_length: int                       # number of hops walked (0 for non-SourceClip)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_clip"
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
                            timeline_start: int, length: Optional[int]) -> "Clip":
    """Build a Clip for a SourceClip component, including chain-walk
    derived mic identity and recorder-source filter."""
    source_mob_id = None
    source_mob_name = None
    source_mob_slot_id = None
    mic_identity = None
    is_recorder = False
    terminal_reason = None
    ptn = None
    chain_length = 0

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
    except ValueError:
        # walk_chain raises ValueError when the SourceClip points at a
        # mob not in this file. Surface that without aborting the listing.
        terminal_reason = "broken_ref"

    return Clip(
        index=index,
        component_class="SourceClip",
        timeline_start=timeline_start,
        length=length,
        source_mob_id=source_mob_id,
        source_mob_name=source_mob_name,
        source_mob_slot_id=source_mob_slot_id,
        mic_identity=mic_identity,
        is_recorder_source=is_recorder,
        terminal_reason=terminal_reason,
        physical_track_number=ptn,
        chain_length=chain_length,
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = "operator_authoring"
        return d


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
    return AuthoringInfo(
        product_name=(_prop("ProductName") if isinstance(_prop("ProductName"), str) else None),
        company_name=(_prop("CompanyName") if isinstance(_prop("CompanyName"), str) else None),
        platform=(_prop("Platform") if isinstance(_prop("Platform"), str) else None),
        date=_isoformat(_prop("Date")),
        identification_count=len(ident_list),
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
    out: list[Clip] = []
    cursor = 0
    for i, comp_obj in enumerate(components):
        cls = type(comp_obj).__name__
        ln_raw = getattr(comp_obj, "length", None)
        length = int(ln_raw) if isinstance(ln_raw, int) else None

        # Per-clip OperationGroup unwrap: an Avid clip is typically wrapped
        # in an OperationGroup carrying audio-level automation. If we can
        # peel a single SourceClip out, treat it as that SourceClip so
        # the chain walk recovers the recorder identity.
        if cls == "OperationGroup":
            inner = _unwrap_operation_group(comp_obj)
            if inner is not None:
                clip = _build_source_clip_clip(handle, i, inner, cursor, length)
                out.append(clip)
                if isinstance(ln_raw, int):
                    cursor += ln_raw
                continue

        if cls == "SourceClip":
            clip = _build_source_clip_clip(handle, i, comp_obj, cursor, length)
        else:
            # Filler, multi-input OperationGroup, Timecode, EssenceGroup,
            # Transition, etc. — surface as a clip row but don't try to
            # chain-walk. The frontend visually de-emphasizes these.
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
            )

        out.append(clip)
        if isinstance(ln_raw, int):
            cursor += ln_raw
    return out
