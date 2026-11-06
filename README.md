# Glancer

Glancer is a private, fast Windows viewer for videos, photos, and audio, with
frame-by-frame video stepping. It keeps no history, shows no gallery, and stores
no file paths. It uses mpv for playback and PySide6 (Qt) for the interface.

> Glancer is under active development (formerly **FrameStepper**). The current
> build is the original frame-stepping video player under the new name.

## Controls

| Action | Button | Keyboard |
| --- | --- | --- |
| Open video | Open video | Ctrl+O |
| Play or pause | Play/Pause | Space |
| Previous frame | One frame left | Left arrow or comma |
| Next frame | One frame right | Right arrow or period |
| Seek | Drag the scrub bar | — |

## Third-party binaries

Large binaries are not stored in git. Put these in `bin\` before running or building:

| File | Source |
| --- | --- |
| `mpv.exe`, `libmpv-2.dll` | [mpv-winbuild-cmake releases](https://github.com/shinchiro/mpv-winbuild-cmake/releases) (`mpv-x86_64-*.7z` and `mpv-dev-x86_64-*.7z`) |
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
python -m pytest -q
```

## Build the Windows application

```powershell
.\build.ps1
```

The standalone application is written to `dist\Glancer.exe`.
