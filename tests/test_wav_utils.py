"""Tests for WAV utility functions (no pyaaf2 required)."""

import struct
import tempfile
import wave

from aaf_multichannel_creator import (
    read_wav_split_channels,
    read_wav_mono,
    samples_to_edit_units,
)


def _make_stereo_wav(path, sample_rate=48000, num_frames=4800, sample_width=2):
    with wave.open(path, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        for _ in range(num_frames):
            if sample_width == 2:
                w.writeframes(struct.pack("<hh", 1000, 2000))
            elif sample_width == 3:
                for val in (1000, 2000):
                    b = val.to_bytes(3, byteorder="little", signed=True)
                    w.writeframes(b)


def _make_mono_wav(path, sample_rate=48000, num_frames=4800, sample_width=2, value=1000):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        for _ in range(num_frames):
            if sample_width == 2:
                w.writeframes(struct.pack("<h", value))
            elif sample_width == 3:
                b = value.to_bytes(3, byteorder="little", signed=True)
                w.writeframes(b)


def test_deinterleave_16bit():
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        _make_stereo_wav(f.name, num_frames=100, sample_width=2)
        left, right, sr, sw, nf = read_wav_split_channels(f.name)

        assert sr == 48000
        assert sw == 2
        assert nf == 100
        assert len(left) == 100 * 2
        assert len(right) == 100 * 2

        assert struct.unpack_from("<h", left, 0)[0] == 1000
        assert struct.unpack_from("<h", right, 0)[0] == 2000


def test_deinterleave_preserves_all_frames():
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        _make_stereo_wav(f.name, num_frames=4800)
        left, right, sr, sw, nf = read_wav_split_channels(f.name)

        for i in range(nf):
            val = struct.unpack_from("<h", left, i * 2)[0]
            assert val == 1000, f"Frame {i}: expected 1000, got {val}"


def test_stereo_rejects_mono():
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        _make_mono_wav(f.name, num_frames=100)
        try:
            read_wav_split_channels(f.name)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "stereo" in str(e).lower()


def test_read_mono():
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        _make_mono_wav(f.name, num_frames=100, value=500)
        pcm, sr, sw, nf = read_wav_mono(f.name)

        assert sr == 48000
        assert sw == 2
        assert nf == 100
        assert len(pcm) == 100 * 2
        assert struct.unpack_from("<h", pcm, 0)[0] == 500


def test_mono_rejects_stereo():
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        _make_stereo_wav(f.name, num_frames=100)
        try:
            read_wav_mono(f.name)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "mono" in str(e).lower()


def test_samples_to_edit_units_2997():
    eu = samples_to_edit_units(48000, 48000, 30000, 1001)
    assert eu == 29


def test_samples_to_edit_units_24fps():
    eu = samples_to_edit_units(48000, 48000, 24, 1)
    assert eu == 24


def test_samples_to_edit_units_25fps():
    eu = samples_to_edit_units(48000, 48000, 25, 1)
    assert eu == 25
