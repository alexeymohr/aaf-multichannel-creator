"""Round-trip tests for multichannel AAF creation (requires pyaaf2)."""

import math
import os
import struct
import tempfile
import wave

import aaf2
import aaf2.components
import aaf2.essence
import aaf2.mobs

from aaf_multichannel_creator import (
    create_stereo_aaf,
    create_multichannel_aaf,
    create_surround_aaf_from_mono_wavs,
    AUDIO_CHANNEL_COMBINER_AUID,
    BYTE_ORDER_VALUE,
    CHANNEL_SUFFIXES,
    TRACK_FORMATS,
)


def _make_stereo_wav(path, sample_rate=48000, num_frames=4800, sample_width=2):
    with wave.open(path, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        for _ in range(num_frames):
            w.writeframes(struct.pack("<hh", 1000, 2000))


def _make_mono_wav(path, sample_rate=48000, num_frames=4800, sample_width=2, value=1000):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        for _ in range(num_frames):
            w.writeframes(struct.pack("<h", value))


def _generate_mono_pcm(num_samples, sample_rate=48000, freq=440):
    data = bytearray()
    max_val = 2**15 - 1
    for i in range(num_samples):
        t = i / sample_rate
        val = int(0.5 * max_val * math.sin(2 * math.pi * freq * t))
        data.extend(struct.pack("<h", val))
    return bytes(data)


def _verify_multichannel_aaf(aaf_path, layout, num_channels, track_format):
    with aaf2.open(aaf_path, "r") as f:
        comp = next(f.content.toplevel())
        assert isinstance(comp, aaf2.mobs.CompositionMob)

        slots = list(comp.slots)
        assert len(slots) == 2

        audio_slot = slots[0]

        attr_list = audio_slot["TimelineMobAttributeList"].value
        track_format_tag = None
        for tv in attr_list:
            if tv.name == "_TRACK_FORMAT":
                track_format_tag = tv
        assert track_format_tag is not None
        assert track_format_tag.value == track_format

        seq = audio_slot.segment
        assert isinstance(seq, aaf2.components.Sequence)

        op_group = None
        for comp_seg in seq.components:
            if isinstance(comp_seg, aaf2.components.OperationGroup):
                op_group = comp_seg
                break
        assert op_group is not None
        assert op_group.operation.name == "Audio Channel Combiner"
        assert str(op_group.operation.auid) == AUDIO_CHANNEL_COMBINER_AUID

        params = list(op_group.parameters)
        assert len(params) == 2
        for p in params:
            pd = p.parameterdef
            if pd.name == "AvidParameterByteOrder":
                assert p.value == BYTE_ORDER_VALUE
            elif pd.name == "AvidEffectID":
                decoded = bytes(p.value).rstrip(b"\x00").decode("utf-8")
                assert decoded == "EFF2_AUDIO_CHANNEL_COMBINER"

        inputs = list(op_group.segments)
        assert len(inputs) == num_channels

        all_mobs = list(f.content.mobs)
        mob_lookup = {m.mob_id: m for m in all_mobs}
        suffixes = CHANNEL_SUFFIXES[layout]

        for i, inp in enumerate(inputs):
            assert isinstance(inp, aaf2.components.SourceClip)
            mm = mob_lookup.get(inp.mob_id)
            assert mm is not None, f"InputSegment[{i}] MasterMob not found"
            assert isinstance(mm, aaf2.mobs.MasterMob)
            assert mm.name.endswith(f".{suffixes[i]}"), \
                f"Expected suffix .{suffixes[i]}, got {mm.name!r}"

            mm_slot = list(mm.slots)[0]
            sc = mm_slot.segment
            sm = mob_lookup.get(sc.mob_id)
            assert sm is not None
            assert isinstance(sm, aaf2.mobs.SourceMob)
            desc = sm.descriptor
            assert isinstance(desc, aaf2.essence.PCMDescriptor)
            assert desc["Channels"].value == 1

        tc_slot = slots[1]
        assert isinstance(tc_slot.segment, aaf2.components.Timecode)


# ── Stereo ──


def test_stereo_from_wav():
    with tempfile.TemporaryDirectory() as d:
        wav_path = os.path.join(d, "stereo.wav")
        aaf_path = os.path.join(d, "stereo.aaf")
        _make_stereo_wav(wav_path)
        create_stereo_aaf(wav_path, aaf_path)
        assert os.path.exists(aaf_path)
        _verify_multichannel_aaf(aaf_path, "stereo", 2, TRACK_FORMATS["stereo"])


def test_stereo_from_pcm():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "stereo.aaf")
        pcm_l = _generate_mono_pcm(4800)
        pcm_r = _generate_mono_pcm(4800, freq=880)
        create_multichannel_aaf(
            [pcm_l, pcm_r], aaf_path,
            layout="stereo", sample_rate=48000, sample_width=2, num_frames=4800,
        )
        assert os.path.exists(aaf_path)
        _verify_multichannel_aaf(aaf_path, "stereo", 2, TRACK_FORMATS["stereo"])


# ── 5.1 ──


def test_51_from_pcm():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "surround_51.aaf")
        channels = [_generate_mono_pcm(4800, freq=440 + i * 100) for i in range(6)]
        create_multichannel_aaf(
            channels, aaf_path,
            layout="5.1", sample_rate=48000, sample_width=2, num_frames=4800,
        )
        assert os.path.exists(aaf_path)
        _verify_multichannel_aaf(aaf_path, "5.1", 6, TRACK_FORMATS["5.1"])


def test_51_from_mono_wavs():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "surround_51.aaf")
        wav_paths = []
        for i, suffix in enumerate(CHANNEL_SUFFIXES["5.1"]):
            path = os.path.join(d, f"{suffix}.wav")
            _make_mono_wav(path, value=1000 + i * 100)
            wav_paths.append(path)
        create_surround_aaf_from_mono_wavs(wav_paths, aaf_path, layout="5.1")
        assert os.path.exists(aaf_path)
        _verify_multichannel_aaf(aaf_path, "5.1", 6, TRACK_FORMATS["5.1"])


# ── 7.1 ──


def test_71_from_pcm():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "surround_71.aaf")
        channels = [_generate_mono_pcm(4800, freq=440 + i * 100) for i in range(8)]
        create_multichannel_aaf(
            channels, aaf_path,
            layout="7.1", sample_rate=48000, sample_width=2, num_frames=4800,
        )
        assert os.path.exists(aaf_path)
        _verify_multichannel_aaf(aaf_path, "7.1", 8, TRACK_FORMATS["7.1"])


# ── Validation ──


def test_wrong_channel_count_raises():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "bad.aaf")
        try:
            create_multichannel_aaf(
                [b"x"] * 3, aaf_path,
                layout="stereo", sample_rate=48000, sample_width=2, num_frames=100,
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "2 channels" in str(e)


def test_unknown_layout_raises():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "bad.aaf")
        try:
            create_multichannel_aaf(
                [b"x"] * 4, aaf_path,
                layout="quad", sample_rate=48000, sample_width=2, num_frames=100,
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "quad" in str(e)


def test_mismatched_mono_wavs_raises():
    with tempfile.TemporaryDirectory() as d:
        aaf_path = os.path.join(d, "bad.aaf")
        paths = []
        for i in range(6):
            p = os.path.join(d, f"ch{i}.wav")
            sr = 48000 if i < 5 else 44100
            _make_mono_wav(p, sample_rate=sr)
            paths.append(p)
        try:
            create_surround_aaf_from_mono_wavs(paths, aaf_path, layout="5.1")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "sample rate" in str(e).lower()
