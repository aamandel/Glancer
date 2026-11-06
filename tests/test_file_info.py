from __future__ import annotations

from pathlib import Path

from PIL import Image

from glancer.metadata import read_info
from glancer.win.file_dialog import FOS_DONTADDTORECENT, create_open_dialog


def test_file_info_basic_fields_and_raw_tags(media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    video = read_info(media("frames_30fps.mp4"))
    assert video.value("Dimensions") == "1280 × 720"
    assert video.value("Frame rate") == "30 fps" and video.value("Duration") == "0:10"
    assert video.value("Video codec") == "H264" and "AAC" in (video.value("Audio") or "")
    assert read_info(media("rotated_portrait.mp4")).value("Dimensions") == "720 × 1280"

    song = read_info(media("tone_cover.mp3"))
    assert song.value("Title") == "Test Tone" and song.value("Artist") == "Glancer"
    assert ("Format: title", "Test Tone") in song.raw

    photo = read_info(media("exif_orient6.jpg"))
    assert photo.value("Dimensions") == "1200 × 1600"  # after EXIF orientation
    assert photo.value("Format") == "JPG image"

    # A photo with a camera and a location: location only in the raw tags.
    image = Image.new("RGB", (64, 48), "gray")
    exif = Image.Exif()
    exif[0x010F] = "Glancer Cam"  # Make
    exif[0x0110] = "Model 1"  # Model
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}  # GPS IFD
    path = tmp_path / "located.jpg"
    image.save(path, exif=exif)
    located = read_info(path)
    assert located.value("Camera") == "Glancer Cam Model 1"
    assert not any("GPS" in k or "51" in v for k, v in located.basic)
    assert any(k == "GPS: GPSLatitude" for k, _ in located.raw)


def test_open_dialog_does_not_add_to_recent(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    dialog = create_open_dialog(tmp_path)
    assert dialog.GetOptions() & FOS_DONTADDTORECENT
