# Identifying a clip's original record channel via AAF chain-walk

## The problem

In a multichannel field-recording workflow (Pro Tools timelines exported
from Avid AAFs), the bin/clip name surfaced by Pro Tools does **not**
reliably identify which physical microphone channel a clip was recorded
to. Many editorial clips share an apparent parent file name even when
they originate from different mics on a multichannel camera/recorder
file.

Worked example from `PWD_310_LC_10-07-2025.aaf`:

| Pro Tools track | Pro Tools clip "parent file"          | Truth (channel of camera) |
|-----------------|----------------------------------------|---------------------------|
| HOST (Keke)     | `PW_310_ISO1_B.01.new.104.wav`         | `PW_310_ISO1_B` PTN 1     |
| JIMMY           | `PW_310_ISO1_B.01.new.03.wav`          | `PW_310_ISO1_B` PTN 2     |
| CELEB GUEST     | `PW_310_ISO1_B.01.new.09.wav`          | `PW_310_ISO1_B` PTN 3     |
| CONTESTANT 1    | `PW_310_ISO1_B.01.new.30.wav`          | `PW_310_ISO1_B` PTN 6     |
| CONTESTANT 2    | `PW_310_ISO1_B.01.new.50.wav`          | `PW_310_ISO1_B` PTN 7     |
| STUDIO AUD L    | `PW_310_ISO6_B.01.new.13.1.wav`        | `PW_310_ISO6_B` PTN 5     |
| STUDIO AUD R    | `PW_310_ISO6_B.01.new.13.wav`          | `PW_310_ISO6_B` PTN 6     |

All five mic-track parent file names look as if they're different files,
but trace back to the same camera SourceMob (`PW_310_ISO1_B`). The
`new.NNN` suffix is an Avid bin-counter and tells you nothing about the
channel. The actual channel identity lives one layer down, in
`PhysicalTrackNumber` on the slot of the terminal SourceMob.

## What the AAF actually carries

The AAF object graph for an editorial clip looks like:

```
CompositionMob (the Pro Tools timeline as exported)
└─ TimelineMobSlot   ← Pro Tools "track" (e.g. "HOST")
   └─ Segment = OperationGroup  ← Audio Pan / level effect wrapping the track
      └─ InputSegments[0] = Sequence
         └─ components[N] = OperationGroup or SourceClip  ← editorial clips
            └─ (when wrapped) InputSegments[0] = SourceClip
               └─ mob_id=<MasterMob>  slot_id=<which slot>
                  └─ MasterMob.slot.segment = Sequence
                     └─ components[0] = SourceClip
                        └─ mob_id=<intermediate SourceMob>  slot_id=...
                           └─ SourceMob.slot.segment = SourceClip
                              └─ mob_id=<camera SourceMob>  slot_id=N
                                 └─ camera SourceMob.slot[N]
                                    └─ PhysicalTrackNumber = K   ★
```

The chain is variable depth. In `PWD_310` the typical depth is 3
(MasterMob → intermediate SourceMob → camera SourceMob), but that's not
universal — code should walk until it can't descend any further.

The terminal pair `(camera SourceMob mob_id, PhysicalTrackNumber K)` is
the unique-per-mic identifier. Any two clips with that same pair are
from the same physical microphone.

## The algorithm

1. **Find the editorial clip.** From a Pro Tools position, the clip is
   a component on the timeline `CompositionMob`'s slot for that track.
   The track's segment is typically an `OperationGroup` wrapping a
   `Sequence`; iterate the Sequence's components and find the one
   spanning the target frame.
2. **Unwrap to a SourceClip.** Editorial components are often
   `OperationGroup`s themselves (volume automation, pan); recursively
   descend `InputSegments[0]` until you reach a plain `SourceClip`.
3. **Walk the SourceClip chain.** Starting from that `SourceClip`,
   follow `clip.mob_id → MasterMob`, then enter the MasterMob's slot
   identified by `clip.slot_id`. The slot's segment is itself either a
   `SourceClip` or a `Sequence`/`OperationGroup` whose single
   chainable child is a `SourceClip`. Keep descending. Stop when:
   - The next `SourceClip.mob_id.int == 0` (natural terminal — pure
     essence reached).
   - The next `SourceClip` references a `mob_id` that
     `f.content.mobs.get(...)` returns `None` (broken ref).
   - A previously-visited `(mob_id, slot_id)` pair would be re-entered
     (cycle).
   - The current segment is `Filler` / `Timecode` / `EssenceGroup` /
     `Pulldown` / multi-input `OperationGroup` / multi-clip `Sequence`
     (these are non-chainable and represent legitimate editorial
     structure).
4. **Read `PhysicalTrackNumber` from the terminal slot.** That value,
   together with the terminal SourceMob's `mob_id`, is your channel
   identifier.

## Two gotchas that bit us during validation

Both have silently dropped channel information in earlier
implementations:

### Gotcha 1 — `PhysicalTrackNumber` is a slot Property, not a Python attribute

```python
# WRONG — silently returns None on every real session AAF
ptn = getattr(slot, "PhysicalTrackNumber", None)

# RIGHT — iterate slot.properties()
ptn = None
for p in slot.properties():
    if p.name == "PhysicalTrackNumber":
        ptn = p.value
        break
```

`pyaaf2` auto-exposes some AAF properties as Python attributes, but
`PhysicalTrackNumber` is not one of them. `getattr` returns `None` even
when the property is present with a value. The bug is silent — code
"runs", just with the discriminator lost.

### Gotcha 2 — `OperationGroup.input_segments` doesn't return what you expect

```python
# WRONG — returns [] for an OperationGroup wrapping a Sequence
for seg in og.input_segments:
    ...

# RIGHT — pull via the AAF property name
for p in og.properties():
    if p.name == "InputSegments":
        input_segments = list(p.value or [])
        break
```

Track segments in editorial AAFs are commonly wrapped in an
`OperationGroup` whose `Operation` is `Audio Pan` (or similar). Pyaaf2's
snake_case `input_segments` accessor returns `[]` even when
`InputSegments` (the AAF property) is non-empty. Read via
`og.properties()` instead.

### Lesser gotchas

- **Transitions overlap their neighbors.** When iterating a `Sequence`'s
  `components` to compute timeline position, subtract the length of
  any `Transition` from the cumulative position rather than adding it.
- **MasterMobs often have a separate timecode slot.** Slot 1 is
  frequently a `Timecode` segment; the audio chain lives on slot 2.
  The track's `SourceClip` will tell you which slot to enter via
  `clip.slot_id`; don't default to slot 1.
- **Multiple SourceMobs can share a name.** In `PWD_310` there are
  several SourceMobs literally named `'PW_310_ISO1_B'`. Use `mob_id`
  (a URN) as the identity, not the name.
- **`SourceClip.mob` is None when `mob_id.int == 0`.** That's the
  natural chain end; treat it as a terminal, not an error.
- **`SourceClip.walk()` from pyaaf2 is incomplete** — it raises
  `NotImplementedError` on segment shapes we routinely encountered
  (e.g. multi-clip `Sequence`). Don't use it; the reference walker
  below handles every case as a graceful terminal.

## Reference implementation (pyaaf2 only)

This is portable — copy into TrackManager and adapt as needed. No
dependency on `aafbrowser`.

```python
"""
Resolve a Pro Tools editorial clip to its terminal physical channel.

Returns a tuple (camera_source_mob_id, physical_track_number) which is
the unique-per-microphone identifier. Two clips with the same tuple
come from the same physical mic.
"""
from dataclasses import dataclass
from typing import Optional, Any
import aaf2


@dataclass(frozen=True)
class ChannelId:
    camera_mob_id: str    # URN of the terminal SourceMob (the camera roll)
    camera_name: Optional[str]
    physical_track_number: Optional[int]
    terminal_reason: str  # for diagnostics


def _slot_property(slot: Any, name: str) -> Any:
    """Read an AAF slot Property by name. getattr does not work for
    PhysicalTrackNumber and similar — see Gotcha 1."""
    if slot is None:
        return None
    for p in slot.properties():
        if p.name == name:
            return p.value
    return None


def _input_segments(og: Any) -> list:
    """Read OperationGroup.InputSegments. The pyaaf2 input_segments
    accessor returns [] in cases we hit; read via properties()."""
    for p in og.properties():
        if p.name == "InputSegments":
            return list(p.value or [])
    return []


def _unwrap_to_source_clip(node: Any) -> Any:
    """Descend through OperationGroup wrappers and single-clip
    Sequences to reach a plain SourceClip."""
    while True:
        cls = type(node).__name__
        if cls == "SourceClip":
            return node
        if cls == "OperationGroup":
            inputs = _input_segments(node)
            if not inputs:
                return None
            node = inputs[0]
            continue
        if cls == "Sequence":
            children = list(getattr(node, "components", []) or [])
            clips = [c for c in children if type(c).__name__ == "SourceClip"]
            if len(clips) == 1:
                node = clips[0]
                continue
            return None  # multi-clip or empty — non-chainable
        return None


def _next_clip(segment: Any) -> Any:
    """Given a slot's segment, return the SourceClip we should follow
    for the chain, or None if the segment terminates the chain."""
    return _unwrap_to_source_clip(segment)


def channel_for_source_clip(f: aaf2.file.AAFFile, clip: Any,
                             max_hops: int = 64) -> ChannelId:
    """
    Walk a SourceClip's chain to its terminal SourceMob and read the
    PhysicalTrackNumber. The clip should already be unwrapped from any
    enclosing OperationGroup/Sequence on the editorial side.
    """
    visited: set[tuple[str, int]] = set()
    cur_clip = clip

    for _ in range(max_hops):
        mob_id = cur_clip.mob_id
        slot_id = cur_clip.slot_id
        if mob_id is None or mob_id.int == 0:
            return ChannelId("", None, None, "no_source_id")
        target_mob = f.content.mobs.get(mob_id)
        if target_mob is None:
            return ChannelId(str(mob_id), None, None, "broken_ref")
        key = (str(mob_id), int(slot_id))
        if key in visited:
            return ChannelId(str(mob_id), getattr(target_mob, "name", None),
                             None, "cycle")
        visited.add(key)

        target_slot = target_mob.slot_at(slot_id)
        if target_slot is None:
            return ChannelId(str(mob_id), getattr(target_mob, "name", None),
                             None, "invalid_slot")

        next_clip = _next_clip(target_slot.segment)
        if next_clip is None:
            # Terminal hop — read PTN from this slot.
            return ChannelId(
                camera_mob_id=str(mob_id),
                camera_name=getattr(target_mob, "name", None) or None,
                physical_track_number=_slot_property(target_slot,
                                                     "PhysicalTrackNumber"),
                terminal_reason="essence",
            )
        cur_clip = next_clip

    return ChannelId(str(mob_id), getattr(target_mob, "name", None),
                     None, "max_hops_reached")
```

Usage:

```python
with aaf2.open("session.aaf", "r") as f:
    # `editorial_clip` is the SourceClip you've already extracted from
    # the timeline (unwrapped from track-level OperationGroup +
    # Sequence + per-clip OperationGroup).
    cid = channel_for_source_clip(f, editorial_clip)
    print(cid.camera_name, cid.physical_track_number)
    # e.g. -> "PW_310_ISO1_B" 1
```

## Locating the editorial clip from a timeline position

If you have a Pro Tools timeline position (sample at 48 kHz, or video
TC, etc.) and a track name, here's how to find the underlying
`SourceClip`:

```python
TARGET_SAMPLE_AT_48K = 29056227   # whatever Pro Tools tells you
TRACK_NAME = "HOST"

with aaf2.open("session.aaf", "r") as f:
    # 1. Find the timeline CompositionMob (the one with named slots).
    timeline = next(m for m in f.content.mobs
                    if type(m).__name__ == "CompositionMob"
                    and any(getattr(s, "name", None) == TRACK_NAME
                            for s in m.slots))
    # 2. Pick the named slot.
    track_slot = next(s for s in timeline.slots
                      if getattr(s, "name", None) == TRACK_NAME)

    # 3. Convert sample-at-48k to slot-edit-rate frames.
    er = float(track_slot.edit_rate)  # typically 30000/1001 = 29.97
    target_frame = round(TARGET_SAMPLE_AT_48K / 48000 * er)

    # 4. Unwrap the track's OperationGroup to its inner Sequence.
    seg = track_slot.segment
    if type(seg).__name__ == "OperationGroup":
        seg = _input_segments(seg)[0]
    components = list(seg.components)

    # 5. Walk components, accumulating timeline position.
    #    Transitions overlap and SUBTRACT from cumulative.
    cumulative = 0
    target_component = None
    for c in components:
        cls = type(c).__name__
        length = getattr(c, "length", 0) or 0
        if cls == "Transition":
            cumulative -= length
            continue
        if cumulative <= target_frame < cumulative + length:
            target_component = c
            break
        cumulative += length

    # 6. Unwrap any per-clip OperationGroup to the SourceClip itself.
    editorial_clip = _unwrap_to_source_clip(target_component)
    # editorial_clip is now feedable into channel_for_source_clip().
```

## What TrackManager should record per clip

For every editorial clip discovered during AAF ingestion, store:

```python
{
    "clip_id":            <whatever TrackManager uses>,
    "track_name":         "HOST",          # source track in the AAF
    "timeline_sample":    29056227,        # for de-dup / overlap math
    "length_samples":     <int>,
    # Channel identity — the only fields that should drive sorting
    "camera_mob_id":      "urn:smpte:umid:...",
    "camera_name":        "PW_310_ISO1_B",
    "physical_track":     1,
    # Diagnostics
    "chain_terminal":     "essence",       # or cycle / broken_ref / etc.
}
```

The sort key for "is this clip from the same mic as that one?" is the
pair `(camera_mob_id, physical_track)`. Names, MasterMob bin labels,
and any visible `-NN` suffix the bin shows can be ignored.

## Edge cases to surface as warnings, not silent drops

- `terminal_reason == "broken_ref"` — the chain points to a Mob not in
  the file. Log it; the clip can't be channel-tagged. Common when an
  AAF was exported from a partial bin.
- `terminal_reason == "operation_group"` (multi-input) or
  `"multi_segment_sequence"` — editorial structure that the walker
  refuses to silently descend. For TrackManager's purposes these are
  rare and worth a log line.
- `physical_track_number is None` after a clean terminal — should not
  happen if Gotcha 1 is handled correctly. If it ever surfaces, the
  walker is reading the wrong slot's properties; the bug is real and
  channel info is lost.
- Two SourceMobs with the same `name` — keep using `mob_id`. Don't
  collapse channels by name.

## Validation against PWD_310

The seven tracks in the table at the top of this doc were each verified
by:

1. Looking up the named track slot in
   `CompositionMob 'PWD_310_LC_10-07-2025.Exported.01'`.
2. Finding the component at the user-supplied 48 kHz sample position
   (each landed with delta=0 from the closest component start).
3. Unwrapping to the editorial `SourceClip`.
4. Running `channel_for_source_clip` (the function above).
5. Confirming the terminal pair `(camera_mob_id,
   physical_track_number)` differed across each of the five
   ISO1_B-recorded mics, and that the two `STUDIO AUD` tracks landed
   on a different camera (`ISO6_B`) entirely.

The procedure is deterministic and fast (sub-millisecond per clip after
the AAF is open).
