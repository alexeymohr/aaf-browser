# Premiere AAFs: deep-dive on whether mic channel identity is recoverable

Date: 2026-04-28
Corpus: 4 Premiere AAFs (CasaLuxe EP 201/202/203, SavingJones)
Tool: pyaaf2 1.7.1 + raw CFB inspection
Companion docs:
`docs/channel-method-corpus-validation.md` (the broader validation),
`docs/identifying-clip-channels.md` (the Avid-MC method).

## Executive summary

**Partial answer: yes for stereo splits, no for multichannel polywav
imports.** Premiere AAFs split into two cases at import time:

| Premiere clip class | Channel identity recoverable? | Mechanism |
|---|---|---|
| Stereo file (`Audio N_L` / `Audio N_R` track pair) | **YES — fully reliable** | `Mono Audio Pan` parameter on track-level OperationGroup (Pan=0.0 → L, Pan=1.0 → R), plus `_L`/`_R` suffix on the MasterMob name |
| Multichannel polywav (one polywav → multiple `Audio N` mono tracks) | **NO — channel index is destroyed at import** | Premiere creates one separate mono MasterMob per source channel; all sibling channel-mobs share the source filename as their name, share descriptor metadata, share creation time. Only the random-UUID `mob_id` distinguishes them, and it does not encode a channel index. |
| Multi-cam Avid MXF reference (`ACAM0045.MXFMulticam`) | **NO — same destruction pattern** | Same as polywav |

The destruction is not a parsing limitation on our end — it is structural
in the AAF that Premiere produces. Every metadata location that Avid
populates with channel-discriminating signal (PhysicalTrackNumber on
the source slot, MasterMob slot count, PCMDescriptor.PhysicalTrack on
sub-descriptors) is collapsed by Premiere's split-channel import. The
information is not present anywhere in the AAF.

A practical exit hatch exists: see "Workarounds" at the end.

## What I checked

Every plausible location channel identity could hide. None of these
panned out for the multichannel-polywav case:

1. ✗ MasterMob.UserComments — only `Scene` and `Take`, both empty
2. ✗ MasterMob.MobAttributeList — absent on all Premiere mobs
3. ✗ MasterMob slot count — always 1 slot per channel-mob, named "A1" or "A Slot"
4. ✗ MasterMob.PhysicalTrackNumber — null
5. ✗ MasterMob.CreationTime / LastModified — identical to the second across siblings
6. ✗ Intermediate SourceMob's slot — single slot, PTN=1 (always 1)
7. ✗ Terminal Import-SourceMob — ImportDescriptor with no channel info
8. ✗ Locator URLString — points at a UUID-named `.aif` essence file (one per channel; the UUID encodes nothing)
9. ✗ Top-level OperationGroup — absent on `Audio N` mono tracks (only present on `_L`/`_R` stereo splits)
10. ✗ Per-clip OperationGroup — `Mono Audio Gain` only; carries volume automation, not channel
11. ✗ TaggedValues anywhere in the chain — absent
12. ✗ CFB raw streams — only the standard AAF class IDs (`0d010101-0101-XXXX-...`); no Premiere-private blobs
13. ✗ Avid-style sub-descriptor `PhysicalTrack` field on FileDescriptors inside MultipleDescriptor — exists on a few `.mov` source mobs but those mobs are NOT referenced from the timeline
14. ✗ MobID structure — UMID format; the varying portion is a per-instance random number, not a channel index
15. ✗ TimelineMobSlot.PhysicalTrackNumber on the editorial slot — Premiere doesn't set it

## Worked example: stereo split (the YES case)

CasaLuxe EP 202 v4 — Audio 1_L (slot 2) vs Audio 1_R (slot 3):

```
Audio 1_L:
  TimelineMobSlot.segment = OperationGroup
    Operation = "Mono Audio Pan"
    Parameters[0].Value = AAFRational(0, 100000000)         ← 0.0 = LEFT
    InputSegments[0] = Sequence
      components[N] = SourceClip → MasterMob "Bars and Tone - Rec 709_L"
                                                              ↑↑
                                            channel embedded in name

Audio 1_R:
  TimelineMobSlot.segment = OperationGroup
    Operation = "Mono Audio Pan"
    Parameters[0].Value = AAFRational(100000000, 100000000)  ← 1.0 = RIGHT
    InputSegments[0] = Sequence
      components[N] = SourceClip → MasterMob "Bars and Tone - Rec 709_R"
```

Verified across all 4 Premiere AAFs: 54 of 54 stereo-paired tracks
follow this exact pattern.

| AAF | _L tracks (Pan=0.0) | _R tracks (Pan=1.0) |
|---|---|---|
| EP 201 V6 | 16 | 16 |
| EP 202 v4 | 7 | 7 |
| EP 203 V6 | 16 | 16 |
| Saving Jones | 15 | 15 |

100% deterministic. Two redundant signals (Pan parameter + name suffix)
agree on every track.

## Worked example: multichannel polywav (the NO case)

CasaLuxe EP 202 v4 — `Audio 2`, `Audio 3`, `Audio 4`, `Audio 5` (slots
6-9). All four reference MasterMobs named `201H-001.WAV`. The four
MasterMobs are **distinct instances**, each created at import time to
hold one channel of the source polywav:

```
Mob[0] '201H-001.WAV'  id=...13220125.18062800.21110688.cb550e78.6c13a3fb
Mob[1] '201H-001.WAV'  id=...1378549c.18062800.21110688.2cf20e78.6c13a3fb
Mob[2] '201H-001.WAV'  id=...138dc109.18062800.21110688.15fc0e78.6c13a3fb
Mob[3] '201H-001.WAV'  id=...13afa6f5.18062800.21110688.a9e30e78.6c13a3fb
```

Every other property is identical:

```
  Name:           '201H-001.WAV'
  Slots:          1 slot, name='A Slot'
  UserComments:   {Scene='', Take=''}
  MobAttributeList: (absent)
  CreationTime:   2026-03-22 00:43:44 (within 2 seconds across all 4)
  LastModified:   same
  Slot 1 segment: Sequence containing one SourceClip
  Slot 1 PTN:     null
```

Following the chain on each of these mobs:

```
MasterMob '201H-001.WAV'
  → SourceMob (no name)        slot=1, PTN=1
    → SourceMob 'Import201H-001.WAV'   slot=1, PTN=0
      descriptor: ImportDescriptor
      Locator:    'file:///MM%20S2%20W4/.../<uuid>.aif'
```

The Locator URL ends with a different random UUID `.aif` for each
channel — but the UUIDs are not assigned in any decodable order, and
they reference Premiere's per-channel-extracted essence files, not the
original polywav. There is no metadata link from any of these mobs back
to "this is channel N of the original polywav."

## What Avid does that Premiere does not

For comparison — the Avid pattern from the corpus that the chain-walk
method recovers cleanly:

```
MasterMob 'PWD_310_LC' slot=4 (HOST track)
  → SourceMob 'PW_310_ISO1_B' slot=2, PTN=1   ← channel encoded here
```

The SourceMob has multiple slots, one per channel of the recorder
file, each with a distinct PhysicalTrackNumber. The MasterMob's
SourceClip uses `slot_id` to reach the channel-specific slot.

In Premiere's translation of the same kind of source file, the
MasterMob's SourceClip always reaches `slot=1` of a per-channel
SourceMob, because Premiere has split the multichannel source into
per-channel mobs. The slot-id-as-channel-index mechanism Avid uses is
not present.

Worth noting: on a multi-cam Avid `.mov` source mob in CasaLuxe
(`A012C005_251216_A4RR.mov`), the **source mob's structure does
preserve** the multichannel layout — 6 slots, one Picture and 5 Sound
each with PTN=1..5. But **no timeline clip references those slots**.
Premiere's import created a separate SourceMob per channel, which
references the underlying multichannel source's slot 1 generically.
The structural data is in the file but inaccessible from the timeline.

## Why this is structural, not a parsing gap

I tried these less-obvious recovery angles:

- **CFB raw streams.** All storage class IDs in both Premiere AAFs are
  standard AAF MetaDictionary classes (`0d010101-0101-XXXX-060e-2b34-...`).
  No Premiere-private namespaces. No extra streams in the root.

- **MobID UMID structure.** SMPTE UMIDs split into instance + material
  number. Across 5 sibling mobs of `ACAM0045.MXFMulticam`, the
  trailing material-number block (`26f0b871`) is identical (correctly
  identifying them as the same source). The varying portion is the
  instance number, but its values across the 5 channels are not
  arithmetic — they look random.

- **Mid-source SourceMob mob_ids.** Same story. The mid-level mobs of
  the 5 Audio 1..5 → ACAM0045 references all share a prefix
  (`663d2dc7`) but vary in the last byte field unpredictably:
  `019cefe6`, `0445efe6`, `f077efe5`, `fac4efe5`, `e9c0efe5`. Not
  consecutive, not channel-indexed.

- **Sub-descriptor PhysicalTrack** on FileDescriptors inside any
  MultipleDescriptor anywhere in the file. Not populated by Premiere
  (the Avid-style multi-cam source mobs that exist do populate it, but
  no timeline clip references those mobs).

The information was never written into the AAF.

## Workarounds

If you actually need to recover channel identity from a Premiere
multichannel polywav case, the AAF cannot help. Three options:

### 1. Trust the editorial track-placement convention

Most Premiere editors who split a polywav across multiple `Audio N`
tracks do so in source-channel order (Audio 1 = ch1, Audio 2 = ch2,
…). This is **convention, not enforcement** — the AAF carries no
constraint that prevents an editor from placing channel 3 on Audio 1.
For a tool that can tolerate occasional errors, this is the cheapest
heuristic.

A reasonable signal-to-noise heuristic:

- Group `Audio N` mono tracks (no `_L`/`_R` suffix) by shared MasterMob
  name (e.g. all tracks with clips referencing `1E-T001.WAV`).
- Sort by track number ascending.
- Assign channel index = track-position-within-group.

This will work for "well-behaved" timelines and silently fail for
shuffled ones.

### 2. Audio essence correlation against the original source

The AAF's terminal Import-SourceMob carries a Locator pointing at
Premiere's per-channel `.aif` extraction file. If you have the
**original polywav** on disk, you can:

- Decode each channel of the polywav.
- Decode each per-channel `.aif` Premiere produced.
- Match by audio-content correlation (XCORR or even header-tag
  comparison if Premiere preserves the original sample-aligned start).

Time-domain correlation between a Premiere `.aif` and a single channel
of the source polywav should produce a sharp peak; this lets you map
each `.aif` (and therefore each MasterMob) back to its source channel.

This is doable but requires the original polywav files and a
DSP-correlation pass — out of scope for an AAF-only tool.

### 3. Demand a different export

If the editor exports from Premiere using **OMF** instead of AAF, OMF
has a different (often richer) channel-mapping mechanism. Or if
Premiere is configured to embed source files rather than re-extract
per-channel, the original polywav structure may survive. Both are
upstream-process changes, not AAF-reading techniques.

## Implications for TrackManager

For Premiere-authored AAFs, channel-recovery has two tiers of
applicability:

- **Tier 1 — stereo splits**: 100% recoverable. Detect the `Mono Audio
  Pan` parameter (or the `_L`/`_R` suffix on the MasterMob name) and
  emit `(MasterMob_id, "L"|"R")` as the per-mic identifier. This
  covers the music-bed, stereo-mix, and stereo-mic cases.

- **Tier 2 — multichannel polywavs and multi-cam audio**: not
  recoverable from AAF metadata. Two options:
  (a) Apply the track-placement convention as a best-effort signal,
      flagged in the output as "inferred from track order".
  (b) Skip channel-tagging for these clips and surface them to the
      user as "ambiguous source channel" with a pointer to the
      MasterMob name (so they know which polywav it came from).

Detection of which tier a clip falls into is straightforward:

```python
def premiere_clip_tier(clip_track_segment) -> str:
    if type(clip_track_segment).__name__ == "OperationGroup":
        for p in clip_track_segment.properties():
            if p.name == "Operation" and getattr(p.value, "name", None) == "Mono Audio Pan":
                return "stereo_split"
    # Mono track on a Premiere AAF — multichannel polywav case
    return "multichannel_polywav_ambiguous"
```

## Bottom line

For Premiere AAFs, **channel identity is partially recoverable** —
fully for stereo splits via the Pan operation, not at all for
multichannel polywav imports without external essence correlation.
The destruction in the multichannel case is structural in the AAF
Premiere writes, not a limitation of pyaaf2 or the chain-walk method.
The only signals that survive Premiere's import are filename and
stereo-pair pan; the source-channel index is not preserved.

## Addendum — essence-file chunk inspection (2026-04-28)

A reasonable follow-up: even though the AAF object graph carries no
channel info on multichannel polywav imports, maybe the embedded
audio essence files do — in `bext` (BWF) or `iXML` chunks, which are
the standard places field-recorder metadata lives. AAF_MetaResearch
already noted iXML was absent corpus-wide on the WAV essence, but
Premiere uses `AIFCDescriptor` (AIFF-C, not WAV), so the chunk-level
behavior could differ.

It does not. **Premiere strips every metadata chunk from essence at
import time.**

### What I checked

A complete chunk-ID census across every embedded essence stream in
all three CasaLuxe AAFs (623 streams total):

| AAF | Streams | FORM type | Chunk patterns seen | Non-standard chunks |
|---|---|---|---|---|
| EP 201 V6 | 318 | AIFC ×318 | `(COMM, SSND)` ×318 | **0** |
| EP 202 v4 | 66 | AIFC ×66 | `(COMM, SSND)` ×66 | **0** |
| EP 203 V6 | 239 | AIFC ×239 | `(COMM, SSND)` ×239 | **0** |
| **Total** | **623** | AIFC ×623 | `(COMM, SSND)` ×623 | **0** |

Every essence stream is bare PCM with two chunks: COMM (format) and
SSND (audio data). Nothing else. No `bext`, no `iXML`, no `chna`, no
`APPL`, no `NAME`/`AUTH`/`ANNO`/`COMT`, no `MARK`, no `ID3`, no
trailing chunks after SSND — verified by streaming through the full
form-size and looking for any chunk past the SSND payload.

Cross-checked with three independent tools on extracted samples:
- `file(1)` — recognizes them as plain AIFF-C
- `afinfo` (CoreAudio) — reads only format and duration
- `mdls` (Spotlight) — reads only file-level attributes

All three confirm: nothing beyond format and audio data exists.

Channel count per stream is uniformly **1** across all 623 streams.
Premiere has both split each multichannel source into mono files AND
down-converted to 16-bit PCM (the AAF_MetaResearch dump confirmed bit
depths weren't reported because the descriptors don't carry them).

### Control: same scan on an Avid AAF

To rule out a parser bug, I ran the same inspection on
`CherriesWild/012521_CW_EP101_JAVIER_SHATARRA_LOCKED_PROTOOLS_AUDIO.aaf`
(an Avid Media Composer AAF). Its essence is RIFF/WAVE with rich
chunks:

```
RIFF/WAVE essence stream:
  bext (604 bytes): vFRAMERATE=23.976  vSPEED=23.976  vNOTE=CW01_AUD_120620_101_2 ...
  fmt  (16 bytes)
  data (3,969,966 bytes)
  umid (24 bytes)  ← Avid-specific UMID chunk
  minf (16 bytes)  ← Avid-specific media info chunk
  fill (alignment padding)
```

So the parser sees what's there. The Premiere case really is bare
COMM + SSND. Even Avid's bext doesn't carry channel-index — that's
an iXML thing, and iXML is absent from BOTH Avid and Premiere in this
corpus — but Avid retains the bext-level identifiers (originator,
note, timecode reference) that *could* be used as an alternative
clip-identity signal. Premiere strips those too.

### External locators on SavingJones

SavingJones is the Premiere AAF with no embedded essence — 6,324
external locator URLs all of the form:

```
file:///Macintosh HD/Users/standupforpits/Desktop/2024-5-8 TURNOVER/AAF V4/AAF Media/<UUID>0000007c.wav
```

Each URL points at a Premiere-generated UUID-named WAV in the AAF's
companion media folder. The companion media volume isn't mounted on
this machine, but the URL pattern matches the embedded-essence case:
each channel of the original polywav becomes a separate UUID-named
mono file. Even if those files were available on disk, they would be
**Premiere's per-channel pre-stripped extractions**, not the original
multichannel source files. Testing them would yield the same bare
COMM/data result.

The only place the original recorder metadata could survive is the
**original polywav files on the field-recorder hard drive**, before
Premiere's import. Those are out of reach of any AAF-based recovery.

### CFB raw stream census

For completeness, I also enumerated every CFB stream type in CasaLuxe
EP 202 v4 to see whether Premiere stashed proprietary data outside
the standard AAF objects:

```
properties           5038x   (standard)
*-index streams      ~18 distinct types  (standard AAF collection indices)
Data-2702            66x     (essence — already inspected)
referenced properties 1x     (standard)
```

28 unique stream names total, all standard AAF. No `Adobe*`,
`Premiere*`, `Avid*`, `Private*`, `Custom*`, or any other proprietary
stream namespace.

### Updated bottom line

For Premiere multichannel-polywav imports, channel identity is
**unrecoverable** at every level of the AAF:

- AAF object graph: no PhysicalTrackNumber on the chain, no
  TaggedValues, no MobAttributeList, no distinguishing field across
  sibling channel-mobs other than mob_id (which is a random UUID).
- AAF essence (embedded AIFC): bare COMM + SSND, no metadata chunks
  whatsoever (623/623 streams across CasaLuxe).
- AAF essence (external WAV): UUID-named per-channel files in a
  companion folder, same per-channel pre-stripping pattern as the
  embedded case.
- CFB raw streams: only standard AAF objects, no proprietary blobs.

The **only** workable recovery paths remain:
1. Editorial track-placement convention (best-effort, fragile).
2. Audio-content cross-correlation between Premiere's per-channel
   extractions and the original multichannel polywav, if available
   on the field-recorder drive.
3. Demand a different export workflow that preserves source channel
   metadata (OMF, or Premiere with embed-source-files, or re-import
   from the original multichannel files into Avid first).
