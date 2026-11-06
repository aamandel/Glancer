# Glancer

Glancer is a private, fast Windows viewer for videos, photos, and audio, with
frame-by-frame video stepping. It keeps no history, shows no gallery, and stores
no file paths. It uses mpv for playback and PySide6 (Qt) for the interface.

> Glancer is under active development (formerly **FrameStepper**). It plays
> videos, photos and audio with frame stepping, a floating overlay, zoom,
> folder navigation and file operations; editing is next.

## Controls

The controls float over the media and fade out after 2 seconds without mouse
movement (audio controls stay visible).

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Open file | Open file button, ≡ menu | Ctrl+O |
| Play or pause | Click the media, ▶ button | Space |
| Previous / next frame (video) | Frame buttons | Left / Right arrow, comma / period |
| Seek 5 s back / forward (audio) | ⏪ / ⏩ buttons | Left / Right arrow |
| Previous / next file in the folder | ‹ / › buttons (photos) | Shift+Left / Right (Left / Right on photos) |
| Sort order for previous / next | ≡ menu → Sort order | — |
| File info (raw tags behind "Show all tags") | ≡ menu | I (Esc closes) |
| Show in Explorer | ≡ menu | — |
| Save the current frame as PNG (next to the video) | ≡ menu | — |
| Delete (to the Recycle Bin, after confirming) | ≡ menu | Del |
| Seek | Click or drag the scrub bar | — |
| Fullscreen | Double-click the media, ⛶ button | F11 (Esc exits) |
| Hide controls until H or a click | — | H |
| Volume / mute | Slider, speaker button, Ctrl+wheel | M (mute) |
| Speed | 1× button | [ and ] (Backspace resets) |
| Loop | Loop button | L |
| Rotate view | ⟳ button (photos), ≡ menu | R / Shift+R |
| Zoom (100–1600%, around the cursor) | Mouse wheel | 0 resets to fit |
| Pan while zoomed | Drag | — |

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
python -m pytest -q   # 30 tests, ~22 s; needs a desktop session (opens windows)
```

## Build the Windows application

```powershell
.\build.ps1
```

The standalone application is written to `dist\Glancer.exe`.

## Folder order

Previous / next follows the sort of an open Explorer window showing the same
folder (Name, Date created, Date modified, Size or Type). Otherwise it uses the
default order, Date created, which you can change with ≡ → Sort order → Set as
default. Hidden files and subfolders are skipped.

## Privacy

- Glancer stores no history, recent files or thumbnails. Its settings hold only
  volume, mute, window position, default sort order and editor defaults.
- Glancer's Open dialog is Windows' own, told not to add the file to Recent
  items, Quick Access or the dialog's recent-file lists.
- Known limit: when you double-click a file in Explorer, Explorer itself may
  record it in Recent items. Glancer can't prevent that.
- Delete only moves files to the Recycle Bin. On drives without one (network
  shares, most USB sticks) Glancer refuses to delete rather than delete
  permanently.
