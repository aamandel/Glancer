# Third-party notices

Glancer plays media with libmpv, mpv's library form, loaded in-process through
[python-mpv](https://github.com/jaseg/python-mpv).

The bundled Windows `libmpv-2.dll` comes from the
[mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake) project. mpv
and the libraries compiled into that build retain their respective open-source
licenses. mpv's license and source information are available from the
[mpv project](https://github.com/mpv-player/mpv).

The graphical interface uses [Qt for Python / PySide6](https://www.qt.io/qt-for-python),
which retains its applicable open-source license terms.

Editing and file info use FFmpeg's `ffmpeg.exe` and `ffprobe.exe`, run as
separate programs. The bundled build is a GPL "shared" build from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds); its license is
included as `bin\ffmpeg-LICENSE.txt`. FFmpeg source is available from
[ffmpeg.org](https://ffmpeg.org/download.html) and the build scripts from the
BtbN repository.
