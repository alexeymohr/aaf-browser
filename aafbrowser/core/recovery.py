"""
Classify a clip's mic-identity recoverability.

Given (authoring_kind, pan_channel, chain-walk terminal info, names), return
one of "recoverable" / "ambiguous" / "unrecoverable" plus a short method
label naming HOW the classification was reached.

This module owns the format-aware decision (Avid PTN chain-walk, Premiere
stereo-split pan, Premiere polywav indeterminacy, generic chain-walk
fallback). Keeping it separate from operator.list_clips gives the
classification its own test surface — call classify(...) with synthetic
inputs and assert against the decision, without building fixture AAFs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional


RecoveryStatus = Literal["recoverable", "ambiguous", "unrecoverable"]


@dataclass(frozen=True)
class RecoveryResult:
    status: RecoveryStatus
    method: Optional[str]


# Premiere polywav-style name pattern: "Audio N" with no _L/_R suffix
# and no other channel discriminator. Matches the corpus's documented
# unrecoverable case.
_POLYWAV_NAME_RE = re.compile(r"^Audio\s+\d+$")


def classify(
    *,
    authoring_kind: str,
    pan_channel: Optional[str],
    is_recorder: bool,
    terminal_class: Optional[str],
    terminal_reason: Optional[str],
    source_mob_name: Optional[str],
    mic_identity: Optional[str],
) -> RecoveryResult:
    """Classify a clip's mic-identity recoverability.

    Dispatch order:
    1. Premiere stereo-split (pan_channel set, then _L/_R name suffix).
    2. Premiere polywav indeterminacy (name like "Audio N" exactly).
    3. Avid / generic chain-walk that reached a recorder SourceMob.
    4. Chain ended at a non-recorder SourceMob → ambiguous.
    5. Chain terminated elsewhere → unrecoverable with terminal_reason.
    """
    if authoring_kind == "premiere":
        if pan_channel:
            return RecoveryResult(
                "recoverable",
                f"premiere_stereo_split_pan_{pan_channel.lower()}",
            )
        if source_mob_name and (
            source_mob_name.endswith("_L") or source_mob_name.endswith("_R")
        ):
            return RecoveryResult("recoverable", "premiere_stereo_split_name")
        if source_mob_name and _POLYWAV_NAME_RE.match(source_mob_name):
            return RecoveryResult("unrecoverable", "premiere_polywav_indeterminate")

    if is_recorder:
        return RecoveryResult("recoverable", "avid_chain_walk")

    if terminal_class == "SourceMob":
        return RecoveryResult("ambiguous", "no_recorder_signal")

    return RecoveryResult(
        "unrecoverable",
        f"chain_terminated_{terminal_reason or 'unknown'}",
    )
