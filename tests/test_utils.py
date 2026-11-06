from pathlib import Path

from glancer.utils import first_file_argument, format_time


def test_format_time() -> None:
    assert format_time(65.432) == "01:05.432"
    assert format_time(3661.007) == "1:01:01.007"
    assert format_time(-2) == "00:00.000"


def test_first_file_argument_ignores_missing_files(tmp_path: Path) -> None:
    video = tmp_path / "clip with spaces.mp4"
    video.touch()
    assert first_file_argument(["missing.mp4", str(video)]) == video.resolve()
