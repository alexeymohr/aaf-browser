# TPIR_AAF_Test1.aaf — investigation findings

Date: 2026-04-28
File: `/Users/amohr/Desktop/AAF_Work_Elements/TPIR_AAF_Test1.aaf` (823 MB)
Authoring: Adobe Premiere Pro 26.2.0 on macOS

## Bottom line

This is the canonical "Premiere multichannel polywav imported as N
mono mobs" pattern from the deep-dive in
`docs/premiere-aaf-channel-recovery.md`. The AAF metadata destruction
is total — there is no signal in the file that maps a particular
timeline track to a particular source channel of the original
`1361L A1-A2_06A.mov`.

But there *is* a clean signal in the **audio content itself**: the 8
streams form **4 stereo-like pairs** with very high pairwise
correlation, and one of those pairs (Audio 7 + Audio 8) is ~13 dB
quieter than the others. So I can tell you the pair structure of the
recording even though I can't tell you which mic is on which pair.

## What's in the AAF

| Field | Value |
|---|---|
| Authoring tool | Adobe Premiere Pro 26.2.0 |
| Top-level CompositionMob | `Sequence 06 Copy 01` |
| Audio slots | 8 (`Audio 1` through `Audio 8`) — all mono, no `_L`/`_R` |
| Picture slot | 1 (`NestedScope`) |
| Timecode slot | 1 (`TC1`) |
| Embedded essence streams | 8, all RIFF/WAVE, all 16-bit 48kHz mono, all 106,897,236 bytes (= 1113.5 s = 18m 33s) |
| MasterMobs | 8 separate mobs all named `1361L A1-A2_06A.mov` |
| Mid SourceMobs | 8 (unnamed, hold the actual essence stream) |
| Terminal SourceMobs | 8 named `Import1361L A1-A2_06A.mov` (all `ImportDescriptor` with locator URLs to `/Silver 4TB/...`) |
| Multichannel SourceMob with PCMDescriptor | **none** — the original 8-channel structure has no representation in the file |
| Track-level OperationGroups | none on `Audio N` slots (only `Sequence` segments) |

The 10 timeline clip positions (sample counts: 4031, 14030, 14574,
16911, 18095, ...) and lengths (3229, 511, 1811, 1184, 79, ...) are
**identical across all 8 tracks**. This proves the editor placed the
same temporal regions on all 8 tracks — i.e. the 8 streams are
sample-aligned channels of one source recording.

## What was checked and ruled out

Same exhaustive scan as in `docs/premiere-aaf-channel-recovery.md`:

| Location checked | Result |
|---|---|
| MasterMob/SourceMob `PhysicalTrackNumber` | absent (mid mobs all have PTN=1, terminals PTN=0) |
| MasterMob.UserComments | absent |
| MasterMob.MobAttributeList | absent |
| MasterMob.CreationTime ordering | spread across 3 seconds (19:16:19–19:16:21), no clean per-channel ordering |
| Slot count / slot names | 1 slot each, all named `'A Slot'` |
| Locator URL patterns | UUID-named WAVs at `/Silver 4TB/.../AAF Media/<uuid>0000007c.wav` — no channel-index encoding |
| MobID UMID structure | shared `477e2ab4` material number; varying instance bytes look random |
| RIFF chunks in essence | only `fmt` + `data` (no `bext`, no `iXML`, no `chna`, no `INFO`, no `LIST`) |
| CFB raw streams | only standard AAF objects, no proprietary blobs |
| Multichannel SourceMob with multi-slot/PCM channel structure | not present |

So the AAF tells me there are **8 essence streams** referenced by
**8 timeline tracks** in **1:1 correspondence**. It does not tell me
which essence stream is channel 1 vs channel 2 vs … of the source.

## What the audio itself reveals

I extracted all 8 essence streams to
`tmp/channel_validation/tpir_extracted/track02_Audio_1.wav` …
`track09_Audio_8.wav` and computed stats + pairwise correlation of
1-second RMS envelopes.

**Per-track levels and silence:**

| Track | Peak (dBFS) | RMS (dBFS) | Silent windows |
|---|---|---|---|
| Audio 1 | -4.5 | -28.8 | 21.0% |
| Audio 2 | -4.1 | -28.9 | 20.9% |
| Audio 3 | -4.8 | -29.1 | 23.2% |
| Audio 4 | -4.3 | -29.3 | 23.2% |
| Audio 5 | -4.7 | -28.6 | 18.4% |
| Audio 6 | -5.9 | -29.6 | 17.8% |
| **Audio 7** | **-17.9** | **-41.6** | **63.5%** |
| **Audio 8** | **-16.8** | **-41.3** | **63.5%** |

Audio 7 and 8 are **~13 dB quieter** than the other six and are silent
**three times more often** — they're a different class of mic from the
other six. Most likely an audience or ambient pair (or backup mic
positions that were recorded but unused for this segment).

**Pairwise correlation matrix** (1-second RMS envelopes, Pearson):

```
         A 1     A 2     A 3     A 4     A 5     A 6     A 7     A 8
A 1:   -----  +0.997  +0.971  +0.967  +0.558  +0.553  +0.504  +0.497
A 2:  +0.997   -----  +0.971  +0.973  +0.534  +0.539  +0.502  +0.496
A 3:  +0.971  +0.971   -----  +0.997  +0.562  +0.557  +0.457  +0.451
A 4:  +0.967  +0.973  +0.997   -----  +0.532  +0.538  +0.446  +0.439
A 5:  +0.558  +0.534  +0.562  +0.532   -----  +0.973  +0.530  +0.530
A 6:  +0.553  +0.539  +0.557  +0.538  +0.973   -----  +0.537  +0.537
A 7:  +0.504  +0.502  +0.457  +0.446  +0.530  +0.537   -----  +0.995
A 8:  +0.497  +0.496  +0.451  +0.439  +0.530  +0.537  +0.995   -----
```

Reading the structure:

- **Audio 1 ↔ Audio 2 (0.997)**: tight pair
- **Audio 3 ↔ Audio 4 (0.997)**: tight pair
- **Audio 5 ↔ Audio 6 (0.973)**: pair (slightly looser)
- **Audio 7 ↔ Audio 8 (0.995)**: tight pair, the quiet one

There's also a **cross-pair correlation (~0.97) between pairs
{1,2} and {3,4}**, suggesting those four mics are picking up the same
source from similar positions (e.g. multiple boom mics on the same
talent area, or a 4-mic stage cluster).

Pair {5,6} correlates only ~0.55 with the {1,2,3,4} cluster — it's
picking up a different sonic space (different talent, different stage
position, or a different submix).

Pair {7,8} correlates only ~0.45–0.55 with everything else — this is
the audience/ambient pair signature (audience-clap response is loud
when the show is reacting but otherwise unrelated to the mic-side
talent).

## What you can conclude

Confidence-graded:

**Very high confidence:**
- The 8 timeline tracks come from one 8-channel source recording.
  All are sample-aligned and identical length.
- The 8 channels are organized as 4 stereo-like pairs:
  `(A1, A2)`, `(A3, A4)`, `(A5, A6)`, `(A7, A8)`.
- The `(A7, A8)` pair is acoustically distinct from the other six
  (much quieter, more silent) — almost certainly an audience or
  ambient pair, possibly a backup pair if production didn't use it.
- The `(A1, A2)` and `(A3, A4)` pairs share content (≈ 0.97), so
  these four mics are in similar acoustic positions.

**Cannot determine from the AAF alone:**
- Which physical channel (1..8) of `1361L A1-A2_06A.mov` is on each
  timeline track. The mob_ids are random UUIDs, the CreationTimes
  are essentially simultaneous, and there's no PhysicalTrackNumber
  or descriptor metadata that would map mob → channel index.

## How to identify each track conclusively

Three options, in increasing reliability:

### 1. Listen to each extracted track

I extracted all 8 to `tmp/channel_validation/tpir_extracted/`:

```
track02_Audio_1.wav through track09_Audio_8.wav
```

Each is 18:33, 16-bit 48kHz mono. Audition a few minutes of each to
identify the mic by content — host vs contestants vs audience are
usually obvious within seconds.

The pair structure means you only need to identify **one channel of
each pair** — the partner is automatically the other.

### 2. Cross-correlate against the source `.mov`

If you can mount the `Silver 4TB` drive and access the original
`1361L A1-A2_06A.mov`, you can match each extracted Premiere stream
to a specific channel of the source by sample-precise correlation.
The streams are bit-aligned and Premiere doesn't seem to time-shift
on import, so a few seconds of correlation gives an unambiguous
match.

I can write that correlation tool if useful. It would output a clean
mapping: `Audio N → source channel K`.

### 3. Re-export from Premiere with embed-source-files

Premiere has an export option that wraps the original multichannel
source as-is rather than splitting it into per-channel mono. If the
source is preserved as a single multichannel clip with intact bext /
iXML / chna chunks, the existing chain-walk method recovers channel
identity automatically. This is the cleanest path for future AAFs.

OMF export from Premiere also tends to preserve channel structure
better than AAF, but introduces other compatibility headaches.

## Why this happens (recap)

Premiere's audio import pipeline, on multichannel files, does roughly:

1. Open the source (here, an 8-channel `.mov` from camera position 06A).
2. **Demux each audio channel into its own mono `.wav`** with a UUID
   filename.
3. Strip all metadata chunks (`bext`, `iXML`, `chna`, etc.) — the
   resulting mono WAVs have only `fmt` + `data`.
4. Create a separate `MasterMob → SourceMob → ImportSourceMob` chain
   per channel-WAV. The chain references the per-channel mono WAV,
   not the original multichannel source.
5. Write to AAF. The AAF object graph carries the chain but no link
   between sibling channel-mobs and the source.

Even Avid-Premiere's `MultipleDescriptor` (which CAN carry per-channel
PhysicalTrack info on FileDescriptors) is bypassed in this pipeline.
The 8 sibling mobs are structurally indistinguishable except by
random-UUID `mob_id`.

## TL;DR for TrackManager-style consumption of this AAF

Use the chain-walk method to extract the 1:1 mapping `(timeline_track,
master_mob_id, essence_stream)`. Surface to the user that there are
8 streams from one source organized as 4 stereo-like pairs, with
(A7, A8) being the quiet ambient pair. Flag that source-channel
identity is not derivable from the AAF and offer one of the three
recovery paths above (audition, correlate, re-export).

## Addendum — LibAAF cross-check (2026-04-28)

The deep-dive in `docs/premiere-aaf-channel-recovery.md` was based
solely on pyaaf2. To rule out the possibility that pyaaf2 was silently
missing a Premiere-specific field, I ran LibAAF / aaftool
(v1.0-29-gd8fb51c) on this same TPIR file with the most exhaustive
flag set: `--aaf-summary --aaf-essences --aaf-clips --aaf-properties
--aaf-classes --dump-class TaggedValue --trace --cfb-nodes`.

LibAAF and pyaaf2 agree exactly. No metadata field discoverable to
LibAAF that pyaaf2 didn't already see.

What LibAAF surfaces about the 8 channel mobs:

| Layer | What every channel has | Differs across channels? |
|---|---|---|
| Timeline TimelineMobSlot | `Audio 1`..`Audio 8` (DataDef Sound) | **yes — but this is just the editor's track name, not a source-channel index** |
| MasterMob | name `'1361L A1-A2_06A.mov'`, slot 1 named `'A Slot'`, no UserComments populated | no |
| Mid SourceMob | unnamed mob, slot 1 named `'Test'`, PTN=1, WAVEDescriptor with 44-byte Summary | no — all 8 Summary blobs are byte-identical |
| Terminal Import-SourceMob | name `'Import1361L A1-A2_06A.mov'`, slot 1 `'A1'`, slot 2 `'TC1'`, ImportDescriptor with one NetworkLocator URL | only the Locator URL UUID differs (no channel encoding) |
| Class dictionary | only standard `AAFClassID_*` classes | no Adobe-private classes |
| CFB raw nodes | only standard AAF storage tree | no Adobe-private streams |

LibAAF's `--aaf-essences` lists each as `Audio[1..8]` with a
`UniqueName` suffix `_1..._7` plus one bare. That suffix is **LibAAF's
own deduplication**, not a Premiere field — when LibAAF encounters
duplicate names it appends a counter to disambiguate them in its
output. The first essence in CFB iteration order gets the bare name,
the next gets `_1`, etc. The suffix doesn't correspond to source
channel index — it just reflects iteration order, and that order
itself is unrelated to source channel order (verified by tracing the
mid-mob mob_ids back through the timeline-track mapping; iteration
order would be channels 8, 4, 7, 6, 1, 2, 5, 3 if the mapping were
1-based-source-channel-equals-timeline-track, which is meaningless).

LibAAF does decode one thing pyaaf2's `getattr` makes you work for:
the `WAVEDescriptor.Summary` field on the mid SourceMob — a 44-byte
embedded WAVE header. I dumped all 8 across pyaaf2's properties
iterator anyway (as a verification), and they are byte-for-byte
identical:

```
RIFF L\x1f_\x06 WAVE fmt  \x10\x00\x00\x00 \x01\x00\x01\x00 \x80\xbb\x00\x00 \x00w\x01\x00 \x02\x00\x10\x00 data (\x1f_\x06
^^^^                ^^^^                ^^^^             ^^^^             ^^^^             ^^^^
RIFF + size        WAVE+fmt chunk   PCM mono/48kHz   bytes/sec/blockalign   16-bit            data chunk hdr
```

Every channel mob carries the same minimal fmt header and a `data`
chunk pointer. **No `bext`, no `iXML`, no `chna`, no `fact`, no
`INFO`/`LIST` chunks** — neither in the Summary blob nor in the actual
embedded essence stream. This matches my earlier finding that
Premiere's import strips every essence-level metadata chunk before
writing.

LibAAF's `--aaf-clips` does decode the per-clip timeline structure
slightly differently than pyaaf2:

```
AudioTrack[1] :: EditRate: 30000/1001 (29.97) Format: MONO Name: "Audio 1"
├── Clip (1):  Start: 01:00:00;00  Len: 00:01:47;21  SourceOffset: 00:02:14;15  Channels: 1  FadeIn: CURV_PWR
├── Clip (2):  Start: 01:01:47;21  Len: 00:00:17;01  SourceOffset: 00:07:48;04  Channels: 1
...
```

Each clip's `SourceOffset` (where in the source we read from) is
**identical across all 8 timeline tracks** — confirming the editor
placed the same temporal regions on all 8 tracks (i.e. they are
sample-aligned channels of one source recording). This is the same
finding pyaaf2 surfaced via `clip.start` / `clip.length` distributions.

### The verdict on the LibAAF question

LibAAF was worth checking for two reasons: it's a fundamentally
different parser written in C, and pyaaf2 has known unimplemented
decoders. But the destruction we documented is on the **AAF write
side**, not the read side. Premiere does not write per-channel
mappings into the file — and no parser, no matter how thorough, can
recover what was never written.

The remaining recovery paths are unchanged:
1. Audition the 8 extracted streams (instant for a human, not for code).
2. Audio-content cross-correlation against the original
   `1361L A1-A2_06A.mov` if the source disk is mounted.
3. Audio classification ML (YAMNet, PANNs, Whisper, pyannote) running
   on the extracted streams. Documented separately; this is the
   TrackManager-friendly route since it produces deterministic labels
   without needing the source file.
