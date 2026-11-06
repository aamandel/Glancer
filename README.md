# Glancer

Glancer is a private, fast Windows viewer for videos, photos, and audio, with
frame-by-frame video stepping. It keeps no history, shows no gallery, and stores
no file paths. It uses mpv for playback and PySide6 (Qt) for the interface.

> Glancer is under active development (formerly **FrameStepper**). It currently
> plays videos, photos and audio with frame stepping; the new interface is next.

## Controls

| Action | Button | Keyboard |
| --- | --- | --- |
| Open file | Open file | Ctrl+O |
| Play or pause | Play/Pause | Space |
| Previous / next frame (video) | One frame | Left / Right arrow, comma / period |
| Seek 5 s back / forward (audio) | — | Left / Right arrow |
| Seek | Drag the scrub bar | — |

## Third-party binaries

Large binaries are not stored in git. Put these in `bin\` before running or building:

| File | Source |
| --- | --- |
| `libmpv-2.dll` | [mpv-winbuild-cmake releases](https://github.com/shinchiro/mpv-winbuild-cmake/releases) (`mpv-dev-x86_64-*.7z`) or the official [mpv releases](https://github.com/mpv-player/mpv/releases) (`libmpv-*-x86_64-w64-mingw32-gpl.zip`) |
| `ffmpeg.exe`, `ffprobe.exe` | A Windows FFmpeg build with libx264, FFV1, FLAC and AAC, e.g. [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases) |

## Run from source

1. Install Python 3.11 or newer.
2. Add the binaries above to `bin\`.
3. Run:

   ```powershell
   python -m pip install -r requirements.txt
   python run_glancer.py
   ```

You can also pass a file directly:

```powershell
python run_glancer.py "C:\Videos\example.mp4"
```

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python tools/make_test_media.py      # generates tests\media\ (git-ignored)
python -m pytest -q   # 14 tests, ~10 s; needs a desktop session (opens windows)
```

## Build the Windows application

```powershell
.\build.ps1
```

The standalone application is written to `dist\Glancer.exe`.
