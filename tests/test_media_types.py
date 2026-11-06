from pathlib import Path

from glancer.media_types import (
    MediaKind,
    detect_kind,
    is_supported,
    kind_from_extension,
    open_dialog_filter,
)


def test_extension_rules() -> None:
    expected = {
        "a.mp4": MediaKind.VIDEO, "a.MOV": MediaKind.VIDEO,
        "a.mp3": MediaKind.AUDIO, "a.wav": MediaKind.AUDIO,
        "a.JPG": MediaKind.IMAGE, "a.jpeg": MediaKind.IMAGE, "a.png": MediaKind.IMAGE,
    }
    for name, kind in expected.items():
        assert kind_from_extension(Path(name)) is kind, name
        assert is_supported(Path(name))
    for name in ("model.3mf", "notes.txt", "noext"):
        assert not is_supported(Path(name)) and kind_from_extension(Path(name)) is None

    assert MediaKind.VIDEO.has_frames and MediaKind.ANIMATED.has_frames
    assert MediaKind.AUDIO.has_timeline and not MediaKind.AUDIO.has_frames
    assert not MediaKind.IMAGE.has_timeline

    all_media = open_dialog_filter().split(";;")[0]
    for ext in ("*.mp4", "*.mp3", "*.wav", "*.jpg", "*.jpeg", "*.png"):
        assert ext in all_media


def test_animation_detection(media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    still = tmp_path / "still.gif"
    Image.new("RGB", (8, 8), "red").save(still)
    assert detect_kind(still) is MediaKind.IMAGE
    assert detect_kind(media("animated.gif")) is MediaKind.ANIMATED
    assert detect_kind(media("animated.webp")) is MediaKind.ANIMATED
    assert detect_kind(media("corners.png")) is MediaKind.IMAGE
