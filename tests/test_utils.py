from pathlib import Path

from glancer.utils import first_video_argument, format_time


def test_format_time_under_an_hour() -> None:
    assert format_time(65.432) == "01:05.432"


def test_format_time_over_an_hour() -> None:
    assert format_time(3661.007) == "1:01:01.007"


def test_format_time_clamps_negative_values() -> None:
    assert format_time(-2) == "00:00.000"


def test_first_video_argument_ignores_missing_files(tmp_path: Path) -> None:
    video = tmp_path / "clip with spaces.mp4"
    video.touch()
    assert first_video_argument(["missing.mp4", str(video)]) == video.resolve()

