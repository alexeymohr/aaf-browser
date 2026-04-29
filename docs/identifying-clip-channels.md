# Identifying a clip's original record channel via AAF chain-walk

## TL;DR

The Pro Tools clip name does not identify the physical mic channel.
The chain-walk described here does, with high accuracy, on Avid Media
Composer AAFs from multi-camera reality and scripted workflows. It
returns "no answer" rather than a wrong answer on Premiere Pro AAFs.

The method has been validated on 50 AAFs across 16 shows
(`docs/channel-method-corpus-validation.md`), including a ground-truth
check against MatchGame slot names that explicitly encode the
recorder channel: 48 of 52 such labels are recovered exactly; the 4
disagreements look like editor mislabels rather than method failures.

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

Corpus note: across multiple takes/episodes, the camera SourceMob
`mob_id` rotates per recording (each card / each call sheet entry has
its own camera mob), but the PhysicalTrackNumber for a given mic
remains stable. So `(camera_mob_id, PTN)` is the per-take fingerprint
and `PTN` alone — within a single recorder configuration — is the
per-mic fingerprint. See "Real production reracks" below for what
"single configuration" means in practice.

## Range of applicability

The chain-walk only works when the AAF actually contains the recorder
source mobs. Validated workflow classes from the corpus run:

| Authoring workflow | Recovers PTN | Notes |
|---|---|---|
| Avid Media Composer, multi-cam reality / game show | yes | Strongest case. Per-mic PTN is stable across episodes; `(camera_mob_id, PTN)` is per-take. |
| Avid Media Composer, scripted episodic | yes | Per-actor lavs/booms come out clean (TBAS validation). |
| Avid Media Composer with PT inserts (promos, music beds) | yes, with filter | Inserted assets terminate at non-recorder mobs; filter on terminal mob class + PTN > 0. |
| Premiere Pro picture lock — stereo splits | partial | `_L`/`_R` channel identity recoverable via `Mono Audio Pan` parameter (0.0 vs 1.0); see `docs/premiere-aaf-channel-recovery.md`. |
| Premiere Pro picture lock — multichannel polywav | **no (verified)** | Channel index is destroyed at import; siblings share name/descriptor/timestamps. Only mob_id differs, with no channel encoding. Verified independently by pyaaf2 and LibAAF — the destruction is on the AAF write side, not a parser limitation. Use the audio-content fallback below. |
| Pro Tools transit / template AAFs | n/a | Blank tracks — no clips to walk. |

Cheap signals to detect "method does not apply" early:
- Slot names match `^Audio\s+\d+(_[LR])?$`.
- `Mono Audio Gain` operation defs registered (Premiere-specific).
- All recovered PTNs are 0 or null across the file.

When any of these holds, route to a filename-based classifier
fallback rather than treating the chain-walk's null result as a
positive signal.

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

## Three gotchas that bit us during validation

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

### Gotcha 3 — A non-`SourceMob` terminal is not a recorder; do not read PTN from it

The chain can legitimately terminate at a `CompositionMob` (an inserted
audio asset like an MP3 promo or a music bed) or at an `OperationGroup`
(a multi-input audio combiner / pan node). These are real editorial
structure, not bugs. They are not recorder sources, and reading PTN
from them is meaningless — typically you'll get `None` or `0`.

```python
# In the consumer code that decides whether to trust the result:
def is_recorder_source(channel_id: ChannelId) -> bool:
    return (
        channel_id.terminal_reason in ("essence", "no_source_id", "broken_ref")
        and channel_id.physical_track_number is not None
        and int(channel_id.physical_track_number) > 0
        # And, if you carry it, terminal mob_class == "SourceMob"
    )
```

Across the validated corpus, the dominant cause of "this labeled mic
track produced multiple distinct PTNs" was inserted non-recorder
content (an MP3 promo on the editorial Host track) and OperationGroup
combiners — not method failures. Filtering on terminal-state lifts
convergence on rich-source shows to 90%+.

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
    camera_mob_id: str          # URN of the terminal mob
    camera_name: Optional[str]
    physical_track_number: Optional[int]
    terminal_reason: str        # for diagnostics
    terminal_mob_class: str     # "SourceMob" for recorder; otherwise filter

    @property
    def is_recorder_source(self) -> bool:
        """True iff this clip terminates at a recorder source with a
        meaningful PhysicalTrackNumber. Non-recorder terminals (inserted
        promos, OperationGroup combiners, missing PTN, PTN=0) all
        return False — they should be excluded from per-mic
        bucketing."""
        return (
            self.terminal_mob_class == "SourceMob"
            and self.terminal_reason in ("essence", "no_source_id", "broken_ref")
            and self.physical_track_number is not None
            and int(self.physical_track_number) > 0
        )


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
            return ChannelId("", None, None, "no_source_id", "")
        target_mob = f.content.mobs.get(mob_id)
        if target_mob is None:
            return ChannelId(str(mob_id), None, None, "broken_ref", "")
        key = (str(mob_id), int(slot_id))
        if key in visited:
            return ChannelId(str(mob_id), getattr(target_mob, "name", None),
                             None, "cycle", type(target_mob).__name__)
        visited.add(key)

        target_slot = target_mob.slot_at(slot_id)
        if target_slot is None:
            return ChannelId(str(mob_id), getattr(target_mob, "name", None),
                             None, "invalid_slot", type(target_mob).__name__)

        next_clip = _next_clip(target_slot.segment)
        if next_clip is None:
            # Terminal hop — read PTN from this slot.
            seg_cls = type(target_slot.segment).__name__
            reason = (
                "operation_group" if seg_cls == "OperationGroup"
                else "filler" if seg_cls == "Filler"
                else "timecode" if seg_cls == "Timecode"
                else "essence"
            )
            return ChannelId(
                camera_mob_id=str(mob_id),
                camera_name=getattr(target_mob, "name", None) or None,
                physical_track_number=_slot_property(target_slot,
                                                     "PhysicalTrackNumber"),
                terminal_reason=reason,
                terminal_mob_class=type(target_mob).__name__,
            )
        cur_clip = next_clip

    return ChannelId(str(mob_id), getattr(target_mob, "name", None),
                     None, "max_hops_reached", type(target_mob).__name__)
```

Usage:

```python
with aaf2.open("session.aaf", "r") as f:
    # `editorial_clip` is the SourceClip you've already extracted from
    # the timeline (unwrapped from track-level OperationGroup +
    # Sequence + per-clip OperationGroup).
    cid = channel_for_source_clip(f, editorial_clip)
    if cid.is_recorder_source:
        print(cid.camera_name, cid.physical_track_number)
        # e.g. -> "PW_310_ISO1_B" 1
    else:
        # Non-recorder terminal (inserted asset, multi-input combiner,
        # missing PTN). Do NOT use it as a per-mic key. Log/skip.
        print("non-recorder terminal:", cid.terminal_reason,
              "mob_class=", cid.terminal_mob_class)
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
    "terminal_mob_class": "SourceMob",     # see Gotcha 3
    "is_recorder_source": True,            # the gate for trusting (mob_id, PTN)
}
```

The sort key for "is this clip from the same mic as that one?" is the
pair `(camera_mob_id, physical_track)`, **but only on clips where
`is_recorder_source == True`**. Clips with non-recorder terminals
(inserted promos, multi-input combiners, Premiere placeholders) need a
separate handling path; do not bucket them by PTN.

Names, MasterMob bin labels, and any visible `-NN` suffix the bin shows
can be ignored.

## Three sub-100% convergence patterns to expect (not bugs)

When you bin clips on a labeled mic track and find that they don't all
produce the same PTN, the cause is almost always one of these three —
all detectable from terminal metadata, none of them method failures.

### 1. Inserted non-recorder audio assets

Editors drop MP3 promos, music beds, and pickup VO onto mic-labeled
tracks. These come into the AAF as their own `CompositionMob`s and the
chain terminates there (not at a SourceMob). Filter via
`terminal_mob_class == "SourceMob"`.

Example: a CherriesWild "Host" track had 7 mic clips at PTN=1 plus one
terminal at `CompositionMob 'Rebecca Riedy.mp3.new.01'`. The mp3 isn't
a recorder source; the 7 mic clips all converged perfectly.

### 2. OperationGroup combiners (multi-mic mix-down)

Some tracks are populated with the output of an audio combiner —
multiple mic inputs mixed to one stereo pair. The chain terminates at
`terminal_reason == "operation_group"` because the walker refuses to
silently pick one of multiple inputs. ~30% of all walk terminations
across the corpus hit this.

Lifting these requires dispatching one sub-walk per
`OperationGroup.input_segment` and merging results — Phase 3 of
aafbrowser flagged this as a known limitation. For TrackManager v1,
either implement the sub-walk dispatch, or treat OG-terminated clips
as "ambiguous" and log them.

### 3. Real production reracks across episodes

When the same mic-label maps to different PTNs across episodes of the
same show, that is sometimes a real production change, not a bug. The
clearest validated example: across 4 Password S3 episodes,
`CONTESTANT 1` resolves to PTN=4 in 16 cases and PTN=6 in 13 cases —
exactly matching the editor's own audio-map AAF, which labels this
track `ISO 1 CH4/6-CONTESTANT 1`. Two different episode-day recording
plans, both correct.

CSgameshow exhibits a uniform off-by-one rerack for the Kids 1-5 mics
between episode 1 and episodes 2/3 — `Kids 1` PTN=3 vs PTN=4, `Kids 2`
PTN=4 vs PTN=5, etc. Same pattern: the production audio-channel
assignment changed.

A classifier should preserve and expose this rather than collapsing
to one winner. The `(camera_mob_id, PTN)` per-clip-take fingerprint
already does the right thing; just don't aggregate to "this label =
this single PTN" without checking.

## When the AAF carries no channel info — audio-content fallback

For Premiere multichannel polywav AAFs, the chain-walk method
correctly returns nothing because nothing is there to return —
verified independently by pyaaf2 and LibAAF on a real 8-channel
multicam game-show AAF (see
`docs/premiere-aaf-channel-recovery.md` and
`docs/tpir-aaf-test-findings.md`).

But the audio essence streams themselves still carry signal. A
deterministic, ML-free fallback gets you most of what you need:

### Step 1 — Extract per-channel essence to disk

Pyaaf2 exposes embedded essence via `f.content.essencedata`. For each
SourceMob with a populated EssenceData stream, read the RIFF/WAVE
bytes (or AIFC, depending on Premiere version) and write to `.wav`.

The 1:1 mapping `(timeline_track → MasterMob → mid SourceMob →
EssenceData)` is preserved by the chain-walk; you already know which
essence belongs to which track.

### Step 2 — Compute per-channel statistics

For each extracted stream, compute over 1-second windows:

- Peak (dBFS), RMS (dBFS), and silent-window fraction
- Spectral centroid + roll-off (lav vs boom vs ambient have
  characteristic spectra)
- Voice-activity ratio (cheap VAD like Silero or WebRTC)

These already separate audience/ambient channels (low level, high
silence%) from dialog channels (high level, sparse silence) without
any model.

### Step 3 — Pairwise correlation of RMS envelopes

Pearson-correlate every channel's 1-second RMS envelope against every
other channel's. Tight pairs (correlation > 0.95) indicate
stereo-like pairings — boom L/R, audience L/R, music bed L/R, etc.
This is independent of ML and very fast.

Validated on TPIR (8 mono tracks from one source): the matrix
revealed 4 clean stereo pairs `{1,2} {3,4} {5,6} {7,8}` with
correlations 0.997, 0.997, 0.973, 0.995. The {7,8} pair was 13 dB
quieter and 3× more silent — unambiguously the audience pair.

### Step 4 — Optional audio-event tagging (light ML)

Run YAMNet or PANNs in 1-second windows over each channel; aggregate
top-class probabilities. Output classes include `Speech`, `Male
speech`, `Female speech`, `Applause`, `Cheering`, `Crowd`,
`Laughter`, `Music`, `Singing` — directly mapping to the functional
roles TrackManager wants to surface. ~4 MB CNN, runs on CPU in
real-time, no per-show training needed.

This works on ANY multichannel polywav case where the AAF gives you
nothing — including TPIR, CasaLuxe, and SavingJones.

### What this fallback gets you

- **Pair structure**: deterministic, always.
- **Functional class** (dialog / audience / music / silent backup):
  deterministic from levels + silence; ML-confirmed via tags.
- **Specific role identification** (host vs contestant 1 vs
  cohost): requires either Whisper+LLM, voice biometrics, or
  human confirmation.
- **Source-channel-of-N index** (which channel of the original
  polywav is on Audio 3): not recoverable from any AAF-level signal
  for this workflow class. Only audio-content correlation against
  the original polywav file gives you this — out of scope for
  AAF-only tooling.

The `(timeline_track, master_mob_id, essence_stream)` fingerprint
plus the audio-content classification together let TrackManager
emit useful per-channel labels even when the chain-walk method
returns null. For a TrackManager-style consumer this is the
"Premiere fallback path" that completes the picture.

## Edge cases to surface as warnings, not silent drops

- `terminal_reason == "broken_ref"` — the chain points to a Mob not in
  the file. Log it; the clip can't be channel-tagged. Common when an
  AAF was exported from a partial bin.
- `terminal_reason == "operation_group"` (multi-input) or
  `"multi_segment_sequence"` — editorial structure that the walker
  refuses to silently descend. ~30% of all walk terminations on the
  validation corpus land here. Either implement sub-walk dispatch
  (recommended) or log and treat as "ambiguous".
- `terminal_mob_class != "SourceMob"` — the chain terminated at a
  CompositionMob (inserted asset). PTN read here is meaningless; do
  not bucket by it. See Gotcha 3.
- `physical_track_number is None` after a clean SourceMob terminal —
  should not happen if Gotcha 1 is handled correctly. If it ever
  surfaces against an Avid AAF, the walker is reading the wrong slot's
  properties. (Against Premiere AAFs, this is the expected behavior;
  see "Range of applicability".)
- `physical_track_number == 0` everywhere across a file — usually
  means the AAF was authored by Premiere or has been re-rendered
  through a tool that strips PhysicalTrackNumber. Detect early and
  route to a different classifier.
- Two SourceMobs with the same `name` — keep using `mob_id`. Don't
  collapse channels by name.

## Validation

### Original PWD_310 validation (Phase 3 of aafbrowser)

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

### Corpus validation (50 AAFs, 16 shows)

Full report: `docs/channel-method-corpus-validation.md`. Headlines:

- **MatchGame ground truth.** The editor named tracks
  `<MIC>-ISO<recorder>-<channel>`, e.g. `MARTY-ISO1-1`,
  `CELEB 4-ISO3-1`, `AUD R-ISO4-5`. We can parse the channel out of the
  name and compare. Across 4 MatchGame AAFs, **48/52 = 92%** of these
  encoded labels are recovered exactly. The 4 disagreements are all
  `MUSIC L-ISO4-2` / `MUSIC R-ISO4-3` consistently resolving to PTN=1
  and PTN=2 — the editor labeled them ISO4-2/3 but the chain finds a
  different recorder; almost certainly editor mislabel of the music
  feed.

- **Cross-AAF stability.** For shows with the same mic-label across
  multiple AAFs:

  | Show | Labels in ≥ 2 AAFs | Stable across AAFs |
  |---|---|---|
  | Password_S3 | 9 | **9/9 = 100%** |
  | MatchGame | 20 | **20/20 = 100%** |
  | TKTS | 4 | **4/4 = 100%** |
  | CSgameshow | 11 | 4/11 (rest are real off-by-one reracks) |

- **Password S3 self-consistency.** The audio-map AAF
  (`PW_301_AUDIO_MAP_A AND B GAMES.aaf`) contains slots like
  `ISO 1 CH1-HOST`, `ISO 1 CH2-JIMMY`, `ISO 6 CH5-STUDIO AUD L`. Every
  one of those 9 explicit mappings was independently recovered by the
  chain-walk against a different episode's AAF. The two `CH4/6` and
  `CH5/7` markers correctly predict the bimodal `CONTESTANT 1` and
  `CONTESTANT 2` distributions across the four episode AAFs.

- **Failure modes.** All explained, none are method-failures: inserted
  non-recorder assets (CompositionMob terminal), OperationGroup
  combiners (multi-mic mix-down), legitimate stereo mics (e.g. camera
  CamMic recorded to channels 3 AND 4), real production reracks
  across episodes.

- **Premiere AAFs (CasaLuxe, SavingJones, TPIR_AAF_Test1).** All
  recovered PTNs are 0 / null — the upstream chain has no
  PhysicalTrackNumber. Method correctly returns "no answer" rather
  than fabricating one. **The "no answer" is independently confirmed
  by LibAAF**: running aaftool with `--aaf-properties --trace
  --cfb-nodes --aaf-classes` on TPIR (8-channel multicam game show)
  surfaces no field, descriptor, sub-descriptor, tagged value, class,
  or CFB stream that pyaaf2 misses. The destruction is on the AAF
  write side, not a parser limitation. Detect Premiere via the cheap
  signals under "Range of applicability" and route to either the
  stereo-split recovery path (Mono Audio Pan parameter) or the
  audio-content fallback (above).

The Phase-3 production-test on Password 310 was not a fluke. It was
representative.
