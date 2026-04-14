# aaf-multichannel-creator

Create Pro Tools-compatible multichannel AAF files with embedded audio. Supports **stereo**, **5.1 surround**, and **7.1 surround** tracks using the exact Audio Channel Combiner OperationGroup pattern that Pro Tools uses internally.

Builds on the discovery documented in [aaf-stereo-creator](https://github.com/alexeymohr/aaf-stereo-creator), extended to all multichannel formats.

## Why this exists

Pro Tools represents multichannel audio tracks in AAF using an `OperationGroup` called the **Audio Channel Combiner**. N mono `SourceClip` objects are linked through this effect to form a multichannel group. Without the exact UUIDs, parameter definitions, channel ordering, `_TRACK_FORMAT` tagged values, and mob chain structure, other tools can't create AAFs that Pro Tools will import as proper multichannel tracks.

This library handles all of that. Give it per-channel audio data, get back a valid AAF.

## Install

```bash
pip install aaf-multichannel-creator
```

Requires [pyaaf2](https://github.com/markreidvfx/pyaaf2).

## Usage

### Command line

```bash
# Stereo from a stereo WAV file
aaf-multichannel-creator stereo music.wav -o music_stereo.aaf

# 5.1 from 6 mono WAV files (L, C, R, Ls, Rs, LFE order)
aaf-multichannel-creator 5.1 L.wav C.wav R.wav Ls.wav Rs.wav LFE.wav -o surround_51.aaf

# 7.1 from 8 mono WAV files (L, C, R, Lss, Rss, Lsr, Rsr, LFE order)
aaf-multichannel-creator 7.1 L.wav C.wav R.wav Lss.wav Rss.wav Lsr.wav Rsr.wav LFE.wav -o surround_71.aaf

# Custom track name, comp name, edit rate
aaf-multichannel-creator stereo dialog.wav -o dialog.aaf --track-name "DX 1" --comp-name "Dialog" --edit-rate 24/1

# Custom timecode start and clip placement
aaf-multichannel-creator stereo music.wav -o music.aaf --tc-start 86400 --clip-offset 300 --clip-length 150
```

### As a library

#### Stereo (from WAV)

```python
from aaf_multichannel_creator import create_stereo_aaf

create_stereo_aaf("input.wav", "output.aaf")

# Full control
create_stereo_aaf(
    "input.wav",
    "output.aaf",
    comp_name="My_Composition",
    track_name="MX 1",
    edit_rate_num=30000,
    edit_rate_den=1001,
    tc_start=107892,
    clip_offset=300,
    clip_length=150,
)
```

#### 5.1 / 7.1 (from mono WAVs)

```python
from aaf_multichannel_creator import create_surround_aaf_from_mono_wavs

# 5.1 from 6 mono files
create_surround_aaf_from_mono_wavs(
    ["L.wav", "C.wav", "R.wav", "Ls.wav", "Rs.wav", "LFE.wav"],
    "surround_51.aaf",
    layout="5.1",
    track_name="SFX 5.1",
)

# 7.1 from 8 mono files
create_surround_aaf_from_mono_wavs(
    ["L.wav", "C.wav", "R.wav", "Lss.wav", "Rss.wav", "Lsr.wav", "Rsr.wav", "LFE.wav"],
    "surround_71.aaf",
    layout="7.1",
    track_name="Atmos Bed 7.1",
)
```

#### Any format (from raw PCM bytes)

```python
from aaf_multichannel_creator import create_multichannel_aaf

create_multichannel_aaf(
    channel_pcm=[left_bytes, right_bytes],
    output_path="stereo.aaf",
    layout="stereo",
    sample_rate=48000,
    sample_width=3,       # 24-bit
    num_frames=240000,
)

# 5.1 from raw PCM
create_multichannel_aaf(
    channel_pcm=[l, c, r, ls, rs, lfe],
    output_path="surround.aaf",
    layout="5.1",
    sample_rate=48000,
    sample_width=3,
    num_frames=240000,
    track_name="BG 5.1",
)
```

### Utility functions

```python
from aaf_multichannel_creator import (
    read_wav_split_channels,
    read_wav_mono,
    samples_to_edit_units,
)

# Deinterleave a stereo WAV
left, right, sr, sw, nf = read_wav_split_channels("stereo.wav")

# Read a mono WAV
pcm, sr, sw, nf = read_wav_mono("mono.wav")

# Convert audio samples to video frames
frames = samples_to_edit_units(nf, sr, edit_rate_num=30000, edit_rate_den=1001)
```

## Channel order

The Audio Channel Combiner InputSegments must be in Pro Tools' expected order:

| Format | Channels | `_TRACK_FORMAT` |
|--------|----------|----------------|
| Stereo | L, R | 2 |
| 5.1 | L, C, R, Ls, Rs, LFE | 3 |
| 7.1 | L, C, R, Lss, Rss, Lsr, Rsr, LFE | 4 |

## AAF structure

All multichannel formats use the same pattern — only the channel count and `_TRACK_FORMAT` value differ:

```
CompositionMob ("Usage_TopLevel")
  └─ TimelineMobSlot (_TRACK_FORMAT = 2|3|4)
       └─ Sequence
            ├─ Filler (pre-roll gap)
            └─ OperationGroup (Audio Channel Combiner)
                 ├─ Input[0]: SourceClip → MasterMob (channel 0)
                 ├─ Input[1]: SourceClip → MasterMob (channel 1)
                 └─ ...

Each MasterMob → SourceMob (embedded mono PCM) → TapeDescriptor SourceMob
```

## Common edit rates

| Frame rate | `--edit-rate` value |
|-----------|-------------------|
| 23.976 fps | `24000/1001` |
| 24 fps | `24/1` |
| 25 fps | `25/1` |
| 29.97 fps DF | `30000/1001` (default) |
| 30 fps | `30/1` |

## Background

Pro Tools uses an Avid-proprietary OperationDef called "Audio Channel Combiner" (`6b46dd7a-132d-4856-ab21-8b751d8462ec`) for all multichannel formats. This was discovered by analyzing Pro Tools AAF exports with "Enforce Media Composer Compatibility" and "Export stereo, 5.1 and 7.1 tracks as multi-channel" enabled. The pattern has been verified to round-trip through both pyaaf2 and Pro Tools for stereo, 5.1, and 7.1.

See also: pyaaf2 issues [#145](https://github.com/markreidvfx/pyaaf2/issues/145), [#102](https://github.com/markreidvfx/pyaaf2/issues/102), [#93](https://github.com/markreidvfx/pyaaf2/issues/93), and [PR #150](https://github.com/markreidvfx/pyaaf2/pull/150).

## Requirements

- Python 3.10+
- [pyaaf2](https://github.com/markreidvfx/pyaaf2) >= 1.7

## License

MIT
