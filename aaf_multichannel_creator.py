#!/usr/bin/env python3
"""
aaf-multichannel-creator — Create Pro Tools-compatible multichannel AAF files.

Supports stereo, 5.1 surround, and 7.1 surround tracks using the Audio
Channel Combiner OperationGroup pattern that Pro Tools uses internally.

Each multichannel track uses ONE composition slot containing an OperationGroup
that wraps N mono SourceClips (one per channel). Each channel has its own
independent mob chain: MasterMob -> SourceMob (PCMDescriptor, mono) ->
SourceMob (TapeDescriptor). A _TRACK_FORMAT tagged value on the slot
indicates the channel layout.

Requires: pyaaf2

Examples:
    # Stereo from WAV file
    $ aaf-multichannel-creator stereo input.wav -o output.aaf

    # 5.1 from 6 mono WAV files (L, C, R, Ls, Rs, LFE order)
    $ aaf-multichannel-creator 5.1 L.wav C.wav R.wav Ls.wav Rs.wav LFE.wav -o surround.aaf

    # 7.1 from 8 mono WAV files
    $ aaf-multichannel-creator 7.1 L.wav C.wav R.wav Lss.wav Rss.wav Lsr.wav Rsr.wav LFE.wav -o surround.aaf
"""

import argparse
import os
import sys
import wave

import aaf2
import aaf2.auid

__version__ = "1.0.0"

# ─── Channel Layout Definitions ───────────────────────────────────────────────

TRACK_FORMAT_STEREO = 2
TRACK_FORMAT_51 = 3
TRACK_FORMAT_71 = 4

CHANNEL_SUFFIXES = {
    "stereo": ["L", "R"],
    "5.1": ["L", "C", "R", "Ls", "Rs", "LFE"],
    "7.1": ["L", "C", "R", "Lss", "Rss", "Lsr", "Rsr", "LFE"],
}

TRACK_FORMATS = {
    "stereo": TRACK_FORMAT_STEREO,
    "5.1": TRACK_FORMAT_51,
    "7.1": TRACK_FORMAT_71,
}

CHANNEL_COUNTS = {
    "stereo": 2,
    "5.1": 6,
    "7.1": 8,
}

# ─── Audio Channel Combiner UUIDs ───────────────────────────────────────────
#
# These are the exact AUIDs Pro Tools writes when exporting multichannel tracks.
# The same OperationDef, parameters, and structure are used for stereo, 5.1,
# and 7.1 — only the number of InputSegments and _TRACK_FORMAT value differ.

AUDIO_CHANNEL_COMBINER_AUID = "6b46dd7a-132d-4856-ab21-8b751d8462ec"
AVID_PARAM_BYTE_ORDER_AUID = "c0038672-a8cf-11d3-a05b-006094eb75cb"
AVID_EFFECT_ID_AUID = "93994bd6-a81d-11d3-a05b-006094eb75cb"

EFFECT_ID_BYTES = list(b"EFF2_AUDIO_CHANNEL_COMBINER\x00")
BYTE_ORDER_VALUE = 18761  # 0x4949 = little-endian


# ─── WAV Processing ─────────────────────────────────────────────────────────


def read_wav_split_channels(wav_path: str) -> tuple[bytes, bytes, int, int, int]:
    """Read a stereo WAV file and deinterleave into separate L/R byte streams.

    Args:
        wav_path: Path to a stereo WAV file (16-bit or 24-bit).

    Returns:
        Tuple of (left_pcm, right_pcm, sample_rate, sample_width, num_frames).

    Raises:
        ValueError: If the WAV file is not stereo.
        FileNotFoundError: If the WAV file doesn't exist.
    """
    with wave.open(wav_path, "r") as w:
        channels = w.getnchannels()
        sample_width = w.getsampwidth()
        sample_rate = w.getframerate()
        num_frames = w.getnframes()

        if channels != 2:
            raise ValueError(f"Expected stereo WAV, got {channels} channel(s)")

        raw = w.readframes(num_frames)

    frame_size = sample_width * channels
    left = bytearray()
    right = bytearray()

    for i in range(num_frames):
        offset = i * frame_size
        left.extend(raw[offset : offset + sample_width])
        right.extend(raw[offset + sample_width : offset + frame_size])

    return bytes(left), bytes(right), sample_rate, sample_width, num_frames


def read_wav_mono(wav_path: str) -> tuple[bytes, int, int, int]:
    """Read a mono WAV file and return raw PCM bytes.

    Args:
        wav_path: Path to a mono WAV file.

    Returns:
        Tuple of (pcm_bytes, sample_rate, sample_width, num_frames).

    Raises:
        ValueError: If the WAV file is not mono.
    """
    with wave.open(wav_path, "r") as w:
        channels = w.getnchannels()
        sample_width = w.getsampwidth()
        sample_rate = w.getframerate()
        num_frames = w.getnframes()

        if channels != 1:
            raise ValueError(f"Expected mono WAV, got {channels} channel(s)")

        raw = w.readframes(num_frames)

    return raw, sample_rate, sample_width, num_frames


def samples_to_edit_units(
    num_samples: int, sample_rate: int, edit_rate_num: int, edit_rate_den: int
) -> int:
    """Convert an audio sample count to video edit units (frames).

    Args:
        num_samples: Number of audio samples.
        sample_rate: Audio sample rate in Hz.
        edit_rate_num: Edit rate numerator (e.g. 30000).
        edit_rate_den: Edit rate denominator (e.g. 1001).

    Returns:
        Number of video frames (edit units).
    """
    return int(num_samples * edit_rate_num / (sample_rate * edit_rate_den))


# ─── AAF Construction ───────────────────────────────────────────────────────


def _register_channel_combiner(f):
    """Register the Audio Channel Combiner OperationDef and ParameterDefs.

    Pro Tools uses this OperationGroup structure for ALL multichannel formats
    (stereo, 5.1, 7.1). The same OperationDef, parameters, and wiring pattern
    apply regardless of channel count.
    """
    pd_byte_order = f.create.ParameterDef()
    pd_byte_order.auid = aaf2.auid.AUID(AVID_PARAM_BYTE_ORDER_AUID)
    pd_byte_order["Name"].value = "AvidParameterByteOrder"
    pd_byte_order["Description"].value = ""
    pd_byte_order.typedef = f.dictionary.lookup_typedef("aafInt16")
    f.dictionary.register_def(pd_byte_order)

    pd_effect_id = f.create.ParameterDef()
    pd_effect_id.auid = aaf2.auid.AUID(AVID_EFFECT_ID_AUID)
    pd_effect_id["Name"].value = "AvidEffectID"
    pd_effect_id["Description"].value = ""
    pd_effect_id.typedef = f.dictionary.lookup_typedef("AvidBagOfBits")
    f.dictionary.register_def(pd_effect_id)

    op_def = f.create.OperationDef()
    op_def.auid = aaf2.auid.AUID(AUDIO_CHANNEL_COMBINER_AUID)
    op_def["Name"].value = "Audio Channel Combiner"
    op_def["Description"].value = ""
    op_def.media_kind = "sound"
    op_def["NumberInputs"].value = 1  # matches Pro Tools exports
    op_def["IsTimeWarp"].value = False
    op_def["Bypass"].value = 0
    op_def["OperationCategory"].value = "OperationCategory_Effect"
    op_def["ParametersDefined"].value = [pd_byte_order, pd_effect_id]
    f.dictionary.register_def(op_def)

    return op_def, pd_byte_order, pd_effect_id


def _create_tape_mob(f, name, edit_rate, length_eu, tc_start, tc_fps, tc_drop):
    """Create a TapeDescriptor SourceMob (end of the mob chain)."""
    tape_mob = f.create.SourceMob()
    tape_mob.name = name
    tape_mob.descriptor = f.create.TapeDescriptor()

    sound_slot = tape_mob.create_timeline_slot(edit_rate=edit_rate)
    sound_clip = f.create.SourceClip(media_kind="sound")
    sound_clip.length = length_eu
    sound_slot.segment = sound_clip
    sound_slot["PhysicalTrackNumber"].value = 1
    sound_slot["SlotName"].value = "A1"

    tc_slot = tape_mob.create_timeline_slot(edit_rate=edit_rate)
    tc = f.create.Timecode(fps=tc_fps, drop=tc_drop)
    tc.start = tc_start
    tc.length = length_eu
    tc_slot.segment = tc

    f.content.mobs.append(tape_mob)
    return tape_mob


def _create_essence_mob(
    f, name, edit_rate, pcm_bytes, sample_rate, sample_width, num_audio_frames,
    tape_mob, tape_slot_id, edit_rate_num, edit_rate_den,
):
    """Create a SourceMob with embedded mono PCM audio data."""
    source_mob = f.create.SourceMob()
    source_mob.name = name
    f.content.mobs.append(source_mob)

    desc = f.create.PCMDescriptor()
    desc["Channels"].value = 1
    desc["BlockAlign"].value = sample_width
    desc["SampleRate"].value = sample_rate
    desc["AverageBPS"].value = sample_rate * sample_width
    desc["QuantizationBits"].value = sample_width * 8
    desc["AudioSamplingRate"].value = sample_rate
    desc.length = num_audio_frames
    source_mob.descriptor = desc

    essencedata = f.create.EssenceData()
    essencedata.mob_id = source_mob.mob_id
    f.content.essencedata.append(essencedata)
    stream = essencedata.open("w")
    stream.write(pcm_bytes)

    length_eu = samples_to_edit_units(
        num_audio_frames, sample_rate, edit_rate_num, edit_rate_den
    )
    sound_slot = source_mob.create_timeline_slot(edit_rate=edit_rate)
    tape_clip = tape_mob.create_source_clip(slot_id=tape_slot_id, length=length_eu)
    sound_slot.segment = tape_clip

    return source_mob, sound_slot


def _create_master_mob(f, name, edit_rate, source_mob, source_slot, comp_name):
    """Create a MasterMob referencing a SourceMob."""
    master_mob = f.create.MasterMob()
    master_mob.name = name
    f.content.mobs.append(master_mob)

    slot = master_mob.create_timeline_slot(edit_rate=edit_rate)
    clip = source_mob.create_source_clip(
        slot_id=source_slot.slot_id, media_kind="sound"
    )
    slot.segment = clip
    slot["PhysicalTrackNumber"].value = 1
    slot["SlotName"].value = comp_name

    return master_mob, slot


def _build_channel_mob_chain(
    f, clip_base_name, channel_suffix, comp_name, edit_rate,
    pcm_bytes, sample_rate, sample_width, num_audio_frames,
    total_media_eu, tc_start, tc_fps, tc_drop, edit_rate_num, edit_rate_den,
):
    """Build a complete per-channel mob chain: Tape -> Source -> Master."""
    tape_name = f"Pro Tools:{comp_name}.ptx"
    tape_mob = _create_tape_mob(
        f, tape_name, edit_rate, total_media_eu, tc_start, tc_fps, tc_drop
    )
    tape_slot_id = list(tape_mob.slots)[0].slot_id

    source_mob, source_slot = _create_essence_mob(
        f, comp_name, edit_rate, pcm_bytes, sample_rate, sample_width,
        num_audio_frames, tape_mob, tape_slot_id, edit_rate_num, edit_rate_den,
    )

    master_name = f"{clip_base_name}.{channel_suffix}"
    master_mob, master_slot = _create_master_mob(
        f, master_name, edit_rate, source_mob, source_slot, comp_name
    )

    return master_mob, master_slot


def _build_composition(
    f, comp_name, track_name, track_format, edit_rate,
    master_mobs_and_slots, clip_offset, clip_length, clip_start_in_media,
    tc_start, tc_fps, tc_drop, op_def, pd_byte_order, pd_effect_id,
):
    """Build the CompositionMob with Audio Channel Combiner OperationGroup."""
    comp_mob = f.create.CompositionMob()
    comp_mob.name = comp_name
    comp_mob["UsageCode"].value = "Usage_TopLevel"
    f.content.mobs.append(comp_mob)

    total_seq_length = clip_offset + clip_length
    audio_slot = comp_mob.create_timeline_slot(edit_rate=edit_rate)
    audio_slot["SlotName"].value = track_name
    audio_slot["PhysicalTrackNumber"].value = 1

    tv = f.create.TaggedValue("_TRACK_FORMAT", track_format, "aafInt32")
    audio_slot["TimelineMobAttributeList"].value = [tv]

    seq = f.create.Sequence(media_kind="sound")
    audio_slot.segment = seq

    if clip_offset > 0:
        filler = f.create.Filler(media_kind="sound", length=clip_offset)
        seq.components.append(filler)

    op_group = f.create.OperationGroup(
        op_def, length=clip_length, media_kind="sound"
    )
    tv2 = f.create.TaggedValue("_IGNORE_TRACKING", 0, "aafInt32")
    op_group["ComponentAttributeList"].value = [tv2]

    cv_byte_order = f.create.ConstantValue(pd_byte_order, BYTE_ORDER_VALUE)
    cv_effect_id = f.create.ConstantValue(pd_effect_id, EFFECT_ID_BYTES)
    op_group.parameters.append(cv_byte_order)
    op_group.parameters.append(cv_effect_id)

    for master_mob, master_slot in master_mobs_and_slots:
        clip = master_mob.create_source_clip(
            slot_id=master_slot.slot_id, start=clip_start_in_media,
            length=clip_length, media_kind="sound",
        )
        op_group.segments.append(clip)

    seq.components.append(op_group)
    seq.length = total_seq_length

    tc_slot = comp_mob.create_timeline_slot(edit_rate=edit_rate)
    tc = f.create.Timecode(fps=tc_fps, drop=tc_drop)
    tc.start = tc_start
    tc.length = total_seq_length
    tc_slot.segment = tc

    return comp_mob


# ─── Public API ────────────────────────────────────────────────────────────


def create_multichannel_aaf(
    channel_pcm: list[bytes],
    output_path: str,
    *,
    layout: str,
    sample_rate: int,
    sample_width: int,
    num_frames: int,
    comp_name: str = "Multichannel_Comp",
    track_name: str = "Audio 1",
    clip_base_name: str = "Clip",
    edit_rate_num: int = 30000,
    edit_rate_den: int = 1001,
    tc_start: int = 107892,
    tc_fps: int = 30,
    tc_drop: bool = True,
    clip_offset: int | None = None,
    clip_length: int | None = None,
    clip_start_in_media: int | None = None,
    verbose: bool = False,
) -> str:
    """Create a Pro Tools-compatible multichannel AAF from per-channel PCM data.

    Each element in channel_pcm is a bytes object containing raw mono PCM audio
    for one channel. Channels must be in the standard order for the layout:
        stereo: [L, R]
        5.1:    [L, C, R, Ls, Rs, LFE]
        7.1:    [L, C, R, Lss, Rss, Lsr, Rsr, LFE]

    Args:
        channel_pcm: List of raw mono PCM byte streams, one per channel.
        output_path: Path for the output AAF file.
        layout: Channel layout — "stereo", "5.1", or "7.1".
        sample_rate: Audio sample rate in Hz (e.g. 48000).
        sample_width: Bytes per sample (2 for 16-bit, 3 for 24-bit).
        num_frames: Number of audio frames per channel.
        comp_name: Composition name in the AAF.
        track_name: Track name (e.g. "MX 1", "SFX 5.1").
        clip_base_name: Base name for the clip (channel suffixes appended).
        edit_rate_num: Edit rate numerator (default 30000 for 29.97fps).
        edit_rate_den: Edit rate denominator (default 1001 for 29.97fps).
        tc_start: Timecode start in frames (default 107892 = 01:00:00;00 DF).
        tc_fps: Timecode frame rate (default 30).
        tc_drop: Drop-frame timecode (default True).
        clip_offset: Gap before clip in edit units (default 0).
        clip_length: Clip length in edit units (default: full file).
        clip_start_in_media: Where in the media the clip starts (default 0).
        verbose: Print progress messages.

    Returns:
        Path to the created AAF file.

    Raises:
        ValueError: If layout is unknown or channel count doesn't match.
    """
    if layout not in CHANNEL_SUFFIXES:
        raise ValueError(
            f"Unknown layout: {layout!r}. Use: {', '.join(CHANNEL_SUFFIXES.keys())}"
        )

    expected_channels = CHANNEL_COUNTS[layout]
    if len(channel_pcm) != expected_channels:
        raise ValueError(
            f"Layout {layout!r} requires {expected_channels} channels, "
            f"got {len(channel_pcm)}"
        )

    suffixes = CHANNEL_SUFFIXES[layout]
    track_format = TRACK_FORMATS[layout]
    edit_rate = f"{edit_rate_num}/{edit_rate_den}"
    total_media_eu = samples_to_edit_units(
        num_frames, sample_rate, edit_rate_num, edit_rate_den
    )

    if clip_offset is None:
        clip_offset = 0
    if clip_length is None:
        clip_length = total_media_eu
    if clip_start_in_media is None:
        clip_start_in_media = 0

    if verbose:
        print(f"Layout: {layout} ({expected_channels}ch, _TRACK_FORMAT={track_format})")
        print(f"Audio: {num_frames} samples @ {sample_rate}Hz, {sample_width * 8}-bit")
        print(f"Media: {total_media_eu} edit units")
        print(f"Clip: offset={clip_offset}, length={clip_length}, "
              f"media_start={clip_start_in_media}")

    with aaf2.open(output_path, "w") as f:
        op_def, pd_byte_order, pd_effect_id = _register_channel_combiner(f)

        master_mobs_and_slots = []
        for i, (suffix, pcm) in enumerate(zip(suffixes, channel_pcm)):
            mm, ms = _build_channel_mob_chain(
                f, clip_base_name, suffix, comp_name, edit_rate,
                pcm, sample_rate, sample_width, num_frames,
                total_media_eu, tc_start, tc_fps, tc_drop,
                edit_rate_num, edit_rate_den,
            )
            master_mobs_and_slots.append((mm, ms))
            if verbose:
                print(f"  Channel {suffix}: {mm.name}")

        _build_composition(
            f, comp_name, track_name, track_format, edit_rate,
            master_mobs_and_slots, clip_offset, clip_length, clip_start_in_media,
            tc_start, tc_fps, tc_drop, op_def, pd_byte_order, pd_effect_id,
        )

    if verbose:
        size = os.path.getsize(output_path)
        print(f"Done: {output_path} ({size:,} bytes)")

    return output_path


def create_stereo_aaf(
    wav_path: str,
    output_path: str,
    *,
    comp_name: str = "Stereo_Comp",
    track_name: str = "MX 1",
    edit_rate_num: int = 30000,
    edit_rate_den: int = 1001,
    tc_start: int = 107892,
    tc_fps: int = 30,
    tc_drop: bool = True,
    clip_offset: int | None = None,
    clip_length: int | None = None,
    clip_start_in_media: int | None = None,
    verbose: bool = False,
) -> str:
    """Create a Pro Tools-compatible stereo AAF from a WAV file.

    Convenience wrapper around create_multichannel_aaf for stereo WAV input.
    Reads a stereo WAV, deinterleaves L/R channels, and builds the full AAF.

    Args:
        wav_path: Path to a stereo WAV file.
        output_path: Path for the output AAF file.
        comp_name: Composition name in the AAF.
        track_name: Track name (e.g. "DX 1", "MX 1", "SFX 1").
        edit_rate_num: Edit rate numerator (default 30000 for 29.97fps).
        edit_rate_den: Edit rate denominator (default 1001 for 29.97fps).
        tc_start: Timecode start in frames (default 107892 = 01:00:00;00 DF).
        tc_fps: Timecode frame rate (default 30).
        tc_drop: Drop-frame timecode (default True).
        clip_offset: Gap before clip in edit units (default 0).
        clip_length: Clip length in edit units (default: full file).
        clip_start_in_media: Where in the media the clip starts (default 0).
        verbose: Print progress messages.

    Returns:
        Path to the created AAF file.
    """
    left_pcm, right_pcm, sample_rate, sample_width, num_frames = \
        read_wav_split_channels(wav_path)

    clip_base_name = os.path.splitext(os.path.basename(wav_path))[0]

    if verbose:
        print(f"WAV: stereo, {sample_width * 8}bit, {sample_rate}Hz, {num_frames} samples")

    return create_multichannel_aaf(
        channel_pcm=[left_pcm, right_pcm],
        output_path=output_path,
        layout="stereo",
        sample_rate=sample_rate,
        sample_width=sample_width,
        num_frames=num_frames,
        comp_name=comp_name,
        track_name=track_name,
        clip_base_name=clip_base_name,
        edit_rate_num=edit_rate_num,
        edit_rate_den=edit_rate_den,
        tc_start=tc_start,
        tc_fps=tc_fps,
        tc_drop=tc_drop,
        clip_offset=clip_offset,
        clip_length=clip_length,
        clip_start_in_media=clip_start_in_media,
        verbose=verbose,
    )


def create_surround_aaf_from_mono_wavs(
    wav_paths: list[str],
    output_path: str,
    *,
    layout: str,
    comp_name: str = "Surround_Comp",
    track_name: str = "Audio 1",
    clip_base_name: str = "Clip",
    edit_rate_num: int = 30000,
    edit_rate_den: int = 1001,
    tc_start: int = 107892,
    tc_fps: int = 30,
    tc_drop: bool = True,
    clip_offset: int | None = None,
    clip_length: int | None = None,
    clip_start_in_media: int | None = None,
    verbose: bool = False,
) -> str:
    """Create a multichannel AAF from multiple mono WAV files.

    Convenience wrapper around create_multichannel_aaf. Reads N mono WAV files
    (one per channel in the layout's channel order) and builds the full AAF.

    Args:
        wav_paths: Paths to mono WAV files, one per channel in layout order.
        output_path: Path for the output AAF file.
        layout: Channel layout — "stereo", "5.1", or "7.1".
        comp_name: Composition name in the AAF.
        track_name: Track name.
        clip_base_name: Base name for the clip.
        edit_rate_num: Edit rate numerator.
        edit_rate_den: Edit rate denominator.
        tc_start: Timecode start in frames.
        tc_fps: Timecode frame rate.
        tc_drop: Drop-frame timecode.
        clip_offset: Gap before clip in edit units.
        clip_length: Clip length in edit units.
        clip_start_in_media: Where in the media the clip starts.
        verbose: Print progress messages.

    Returns:
        Path to the created AAF file.

    Raises:
        ValueError: If channel count doesn't match layout, or WAV files
            have mismatched sample rates/widths/lengths, or files aren't mono.
    """
    expected = CHANNEL_COUNTS.get(layout)
    if expected is None:
        raise ValueError(
            f"Unknown layout: {layout!r}. Use: {', '.join(CHANNEL_COUNTS.keys())}"
        )
    if len(wav_paths) != expected:
        raise ValueError(
            f"Layout {layout!r} requires {expected} WAV files, got {len(wav_paths)}"
        )

    channel_pcm = []
    sample_rate = None
    sample_width = None
    num_frames = None

    for i, path in enumerate(wav_paths):
        pcm, sr, sw, nf = read_wav_mono(path)
        if sample_rate is None:
            sample_rate, sample_width, num_frames = sr, sw, nf
        else:
            if sr != sample_rate:
                raise ValueError(
                    f"Sample rate mismatch: channel 0 is {sample_rate}Hz, "
                    f"channel {i} is {sr}Hz"
                )
            if sw != sample_width:
                raise ValueError(
                    f"Sample width mismatch: channel 0 is {sample_width * 8}-bit, "
                    f"channel {i} is {sw * 8}-bit"
                )
            if nf != num_frames:
                raise ValueError(
                    f"Frame count mismatch: channel 0 has {num_frames} frames, "
                    f"channel {i} has {nf} frames"
                )
        channel_pcm.append(pcm)

    if verbose:
        suffixes = CHANNEL_SUFFIXES[layout]
        for path, suffix in zip(wav_paths, suffixes):
            print(f"  {suffix}: {path}")

    return create_multichannel_aaf(
        channel_pcm=channel_pcm,
        output_path=output_path,
        layout=layout,
        sample_rate=sample_rate,
        sample_width=sample_width,
        num_frames=num_frames,
        comp_name=comp_name,
        track_name=track_name,
        clip_base_name=clip_base_name,
        edit_rate_num=edit_rate_num,
        edit_rate_den=edit_rate_den,
        tc_start=tc_start,
        tc_fps=tc_fps,
        tc_drop=tc_drop,
        clip_offset=clip_offset,
        clip_length=clip_length,
        clip_start_in_media=clip_start_in_media,
        verbose=verbose,
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="aaf-multichannel-creator",
        description=(
            "Create Pro Tools-compatible multichannel AAF files. "
            "Supports stereo (from one stereo WAV) and 5.1/7.1 surround "
            "(from multiple mono WAVs)."
        ),
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # ── stereo subcommand ──
    sp_stereo = sub.add_parser(
        "stereo",
        help="Create a stereo AAF from one stereo WAV file",
    )
    sp_stereo.add_argument("wav", help="Input stereo WAV file")
    sp_stereo.add_argument("-o", "--output", required=True, help="Output AAF file path")
    sp_stereo.add_argument("--comp-name", default="Stereo_Comp")
    sp_stereo.add_argument("--track-name", default="MX 1")
    sp_stereo.add_argument("--edit-rate", default="30000/1001",
                           help="Edit rate as num/den (default: 30000/1001)")
    sp_stereo.add_argument("--tc-start", type=int, default=107892)
    sp_stereo.add_argument("--clip-offset", type=int, default=None)
    sp_stereo.add_argument("--clip-length", type=int, default=None)
    sp_stereo.add_argument("--clip-start", type=int, default=None)
    sp_stereo.add_argument("-q", "--quiet", action="store_true")

    # ── 5.1 subcommand ──
    sp_51 = sub.add_parser(
        "5.1",
        help="Create a 5.1 surround AAF from 6 mono WAV files (L C R Ls Rs LFE)",
    )
    sp_51.add_argument("wavs", nargs=6, help="6 mono WAV files: L C R Ls Rs LFE")
    sp_51.add_argument("-o", "--output", required=True, help="Output AAF file path")
    sp_51.add_argument("--comp-name", default="Surround_51_Comp")
    sp_51.add_argument("--track-name", default="SFX 5.1")
    sp_51.add_argument("--clip-name", default="Clip")
    sp_51.add_argument("--edit-rate", default="30000/1001")
    sp_51.add_argument("--tc-start", type=int, default=107892)
    sp_51.add_argument("--clip-offset", type=int, default=None)
    sp_51.add_argument("--clip-length", type=int, default=None)
    sp_51.add_argument("--clip-start", type=int, default=None)
    sp_51.add_argument("-q", "--quiet", action="store_true")

    # ── 7.1 subcommand ──
    sp_71 = sub.add_parser(
        "7.1",
        help="Create a 7.1 surround AAF from 8 mono WAV files (L C R Lss Rss Lsr Rsr LFE)",
    )
    sp_71.add_argument("wavs", nargs=8, help="8 mono WAV files: L C R Lss Rss Lsr Rsr LFE")
    sp_71.add_argument("-o", "--output", required=True, help="Output AAF file path")
    sp_71.add_argument("--comp-name", default="Surround_71_Comp")
    sp_71.add_argument("--track-name", default="SFX 7.1")
    sp_71.add_argument("--clip-name", default="Clip")
    sp_71.add_argument("--edit-rate", default="30000/1001")
    sp_71.add_argument("--tc-start", type=int, default=107892)
    sp_71.add_argument("--clip-offset", type=int, default=None)
    sp_71.add_argument("--clip-length", type=int, default=None)
    sp_71.add_argument("--clip-start", type=int, default=None)
    sp_71.add_argument("-q", "--quiet", action="store_true")

    # ── legacy (no subcommand, positional WAV) for backward compat ──
    # If someone calls: aaf-multichannel-creator input.wav -o output.aaf
    # argparse will fail with "required: command". We catch this below.

    args = ap.parse_args()

    try:
        num, den = args.edit_rate.split("/")
        edit_rate_num, edit_rate_den = int(num), int(den)
    except ValueError:
        ap.error(f"Invalid edit rate: {args.edit_rate}")

    try:
        if args.command == "stereo":
            create_stereo_aaf(
                wav_path=args.wav,
                output_path=args.output,
                comp_name=args.comp_name,
                track_name=args.track_name,
                edit_rate_num=edit_rate_num,
                edit_rate_den=edit_rate_den,
                tc_start=args.tc_start,
                clip_offset=args.clip_offset,
                clip_length=args.clip_length,
                clip_start_in_media=args.clip_start,
                verbose=not args.quiet,
            )
        elif args.command in ("5.1", "7.1"):
            create_surround_aaf_from_mono_wavs(
                wav_paths=args.wavs,
                output_path=args.output,
                layout=args.command,
                comp_name=args.comp_name,
                track_name=args.track_name,
                clip_base_name=getattr(args, "clip_name", "Clip"),
                edit_rate_num=edit_rate_num,
                edit_rate_den=edit_rate_den,
                tc_start=args.tc_start,
                clip_offset=args.clip_offset,
                clip_length=args.clip_length,
                clip_start_in_media=args.clip_start,
                verbose=not args.quiet,
            )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
