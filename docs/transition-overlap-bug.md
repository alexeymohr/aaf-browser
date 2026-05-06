# Transition-overlap bug (v0.1.0 → v0.1.1)

A bug fixed shortly after the v0.1.0 release. Important enough to
write down because (a) the symptom was "clips silently missing from
AAF Browser", which was easy to misdiagnose as a recovery problem,
and (b) the same trap exists for any future tool that walks AAF /
EDL / MXF sequences.

## Symptom

A user opened an Avid-authored AAF for a multi-track music-cue-heavy
TV show in AAF Browser, then compared what AAF Browser surfaced
against what DaVinci Resolve showed when importing the same AAF.
Many clips that Resolve placed at specific timecodes (e.g. an
audio clip starting at `01:00:08:01` on a stereo pair) **could not
be found** in AAF Browser at the same timecodes.

The clips were not literally missing from the AAF Browser data —
they were present in the right slots and with the right names, but
displayed at timecodes 1+ seconds later than where Resolve placed
them. With ~1000 transitions across the file's 38 audio tracks, the
cumulative offset error grew large enough that operators searching
for a clip "around 01:00:08" wouldn't notice it sitting at
"01:00:09:17" instead.

## Root cause

In an AAF Sequence, a `Transition` component is the **overlap
region** between the two adjacent non-Transition components, not
extra timeline duration. A Transition of length L means:

- The previous component's last L frames AND
- The next component's first L frames

both occupy the same L frames of timeline. Equivalently:

```
Sequence.length = sum(non-Transition lengths) - sum(Transition lengths)
```

Verified against the user's file: `seq.length = 33810`,
`sum(all components) = 34202`, `sum(Transitions) = 196`, and
`34202 - 33810 = 392 = 2 × 196` — exactly the doubled transition
length, consistent with each transition being counted as overlap
between two surrounding components.

The previous walk in `aafbrowser/core/operator.py::list_clips` did:

```python
cursor = 0
for comp in components:
    emit(comp, timeline_start=cursor)
    cursor += comp.length          # WRONG for Transition
```

For Transitions, this advances the cursor by the transition's length
when it should *retreat* by that length (or equivalently, advance by
zero net for the transition pair: -L when entering, +L for the
transition's own emit). Every clip following a transition then
appears at `cursor + (sum of preceding transition lengths)` — too
far to the right.

## The fix

Special-case Transition handling in the Sequence walk:

```python
cursor = 0
for comp in components:
    if type(comp).__name__ == "Transition":
        # Transition occupies [cursor - L, cursor] — overlapping the
        # previous component's tail. The next non-Transition component
        # starts at cursor - L.
        emit(comp, timeline_start=cursor - comp.length)
        cursor -= comp.length
    else:
        emit(comp, timeline_start=cursor)
        cursor += comp.length
```

Worked example with `[SourceClip A (1000), Transition (100), SourceClip B (1000)]`:

| Step | Component         | Emit at | Cursor after |
|------|-------------------|---------|--------------|
| 0    | (start)           | —       | 0            |
| 1    | SourceClip A      | 0       | 1000         |
| 2    | Transition        | 900     | 900          |
| 3    | SourceClip B      | 900     | 1900         |

Total timeline span: 1900 (= 2000 + 100 − 200, accounting for
Transition once as "self" and once as "overlap" to subtract).
Matches `Sequence.length` from the AAF. SourceClip B starts at
900, overlapping the tail of A and all of the Transition.

## User-facing impact

- **Pre-fix**: any clip at slot offset `X` *after* a Transition was
  reported at offset `X + sum_of_preceding_transitions`. A track
  with 30 transitions and 100-frame transition lengths would show
  the last clip ~3000 frames (~2 minutes at 23.976) past where the
  NLE puts it.
- **Post-fix**: offsets match the NLE's display exactly.

The user's specific example: clip `CW01_103_120820_101_6.new.02` on
slot 12, two transitions preceding it (lengths 20 and 20, total 40
frames). Pre-fix: `ts=3113` → TC `01:00:09:17`. Post-fix: `ts=3073`
→ TC `01:00:08:01`. Resolve's display: `01:00:08:01`. Exact match.

## Regression test

`tests/test_operator.py::test_transition_does_not_inflate_timeline_offsets`
backed by a new synthetic fixture `transition_aaf` in
`tests/conftest.py` (canonical 3-component layout: SourceClip,
Transition, SourceClip). The test asserts:

- The Transition's `timeline_start` is `clip_a.length - transition.length`.
- The post-Transition SourceClip's `timeline_start` is `clip_a.length - transition.length`
  (NOT `clip_a.length + transition.length`).

The test's failure message is explicit about the bug pattern
("off-by-transition-length") so a future regression is unambiguous.

## Why this didn't surface during Phases 1-9

The synthetic fixtures (Password show data, the Phase 7 combiner
fixture, the Phase 8 Premiere stereo-split / polywav fixtures) had
either no transitions or so few that the cumulative offset error
stayed below operator-noticeable thresholds for the kinds of
operations we tested. The bug only became blatant with a real-world
heavily-edited Avid AAF where transitions are ubiquitous.

**Generalizable lesson:** any future AAF / EDL / MXF / OTIO walking
code in this codebase or adjacent projects should be tested against
a fixture that contains Transitions. The unit-test cost of one
synthetic fixture is far smaller than the discovery cost of finding
this bug in the wild after shipping.

## References

- Fixed: `aafbrowser/core/operator.py::list_clips`
- Test: `tests/test_operator.py::test_transition_does_not_inflate_timeline_offsets`
- Fixture: `tests/conftest.py::_write_transition_aaf` / `transition_aaf`
- Memory note: `~/.claude/projects/.../memory/aaf-transition-overlap.md`
- AAF spec reference: SMPTE ST 377-1, "Transition" object semantics.
