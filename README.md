# FrameStepper

FrameStepper is a deliberately small Windows video player for inspecting a video
one frame at a time. It uses mpv for accurate forward and backward frame-step
commands and PySide6 for the interface.

## Controls

| Action | Button | Keyboard |
| --- | --- | --- |
| Open video | Open video | Ctrl+O |
| Play or pause | Play/Pause | Space |
| Previous frame | One frame left | Left arrow or comma |
| Next frame | One frame right | Right arrow or period |
| Seek | Drag the scrub bar | — |

Frame controls automatically pause the video. Backward stepping can take longer
with highly compressed videos because mpv may need to decode forward from an
earlier keyframe.

## Run from source

1. Install Python 3.11 or newer.
2. Put a Windows `mpv.exe` at `bin\mpv.exe`, or install mpv on `PATH`.
3. Run:

   ```powershell
   python -m pip install -r requirements.txt
   python run_framestepper.py
   ```

You can also pass a video directly:

```powershell
python run_framestepper.py "C:\Videos\example.mp4"
```

## Build the Windows application

With `bin\mpv.exe` present, run:

```powershell
.\build.ps1
```

The standalone application is written to `dist\FrameStepper.exe`.

## Use from Explorer

1. Right-click a video and select **Open with → Choose another app**.
2. Choose **Choose an app on your PC**.
3. Select `FrameStepper.exe`.

Windows will pass the selected video path to FrameStepper. Selecting “Always” is
optional and makes it the default app for that file extension.
