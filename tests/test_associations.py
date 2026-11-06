from __future__ import annotations

from pathlib import Path

from glancer.media_types import (AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS,
                                 VIDEO_EXTENSIONS)
from glancer.win import associations


class FakeRegistry:
    """A dict standing in for HKCU, so the test never touches the real registry."""

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, str]] = {
            r"Software\Classes\.mp4\OpenWithProgids": {"Other.App": ""},  # someone else's
            r"Software\Glancer\Glancer": {"volume": "80"},  # Glancer's settings
        }

    def set(self, key: str, name: str, data: str) -> None:
        self.keys.setdefault(key, {})[name] = data

    def delete_tree(self, key: str) -> None:
        for k in [k for k in self.keys if k.lower() == key.lower() or k.lower().startswith(key.lower() + "\\")]:
            del self.keys[k]

    def delete_value(self, key: str, name: str) -> None:
        self.keys.get(key, {}).pop(name, None)


def test_register_round_trip_and_installer_lines() -> None:
    exe = Path(r"C:\Apps\Glancer\Glancer.exe")
    reg = FakeRegistry()
    before = {k: dict(v) for k, v in reg.keys.items()}
    associations.register(exe, reg)

    command = r'"C:\Apps\Glancer\Glancer.exe" "%1"'
    assert reg.keys[r"Software\Classes\Glancer.Video\shell\open\command"][""] == command
    assert reg.keys[r"Software\Classes\Applications\Glancer.exe\shell\open\command"][""] == command
    assert reg.keys[r"Software\RegisteredApplications"]["Glancer"] == associations.CAPABILITIES
    file_assoc = reg.keys[associations.CAPABILITIES + r"\FileAssociations"]
    assert set(file_assoc) == set(SUPPORTED_EXTENSIONS)
    assert file_assoc[".mp3"] == "Glancer.Audio" and file_assoc[".png"] == "Glancer.Image"
    for ext in SUPPORTED_EXTENSIONS:
        assert any(n.startswith("Glancer.") for n in reg.keys[rf"Software\Classes\{ext}\OpenWithProgids"])

    associations.unregister(reg)
    # Exactly what was there before, apart from now-empty shared keys.
    assert {k: v for k, v in reg.keys.items() if v} == before

    inno = associations.inno_registry()
    assert r'ValueData: """{app}\Glancer.exe"" ""%1"""' in inno
    assert 'Subkey: "Software\\Classes\\.mp4\\OpenWithProgids"; ValueType: string; ' \
           'ValueName: "Glancer.Video"; ValueData: ""; Flags: uninsdeletevalue' in inno
    assert 'Subkey: "Software\\Glancer\\Capabilities"; ValueType: string; ValueName: "ApplicationName"' in inno
    # Only Glancer's own keys are deleted whole on uninstall (never Software\Glancer: settings).
    deleted = {line.split('Subkey: "')[1].split('"')[0] for line in inno.splitlines() if "uninsdeletekey" in line}
    assert deleted == {r"Software\Classes\Glancer.Video", r"Software\Classes\Glancer.Audio",
                       r"Software\Classes\Glancer.Image", r"Software\Classes\Applications\Glancer.exe",
                       associations.CAPABILITIES}

    reg_text = associations.reg_file(exe)
    assert '@="\\"C:\\\\Apps\\\\Glancer\\\\Glancer.exe\\" \\"%1\\""' in reg_text

    # The README's "Supported file types" table matches what Glancer registers.
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip(): set(line.split("|")[2].split())
            for line in readme.split("## Supported file types")[1].split("##")[0].splitlines()
            if line.startswith("| ") and line.split("|")[1].strip() in ("Video", "Audio", "Photo")}
    assert rows == {"Video": set(VIDEO_EXTENSIONS), "Audio": set(AUDIO_EXTENSIONS),
                    "Photo": set(IMAGE_EXTENSIONS)}
