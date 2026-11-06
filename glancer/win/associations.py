"""Register Glancer with Explorer's "Open with" and Settings > Default apps.

Windows 11 doesn't let an app make itself the default; it can only register
as a candidate. That takes these keys (under HKCU\\Software for the portable
build, HKLM\\Software for the installer):

    Classes\\Glancer.Video|Audio|Image          ProgIDs: name, icon, open command
    Classes\\.<ext>\\OpenWithProgids            value Glancer.<Kind> (per extension)
    Classes\\Applications\\Glancer.exe          SupportedTypes, open command
    Glancer\\Capabilities                       name, description, FileAssociations
    RegisteredApplications                     value Glancer -> the Capabilities key

Unregistering removes exactly those keys and values: the shared
.<ext>\\OpenWithProgids keys and Software\\Glancer (which also holds the
settings) are left in place.

The same entries feed three writers:

    register() / unregister()   portable build, menu items (winreg, HKCU)
    inno_registry()             installer/associations.iss for Inno Setup (HKLM)
    reg_file()                  a .reg file, for doing it by hand

Run `python -m glancer.win.associations --inno` to print the Inno lines.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from glancer.media_types import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

APP_NAME = "Glancer"
EXE_NAME = "Glancer.exe"
DESCRIPTION = "A private, fast viewer for videos, music and photos."
CAPABILITIES = r"Software\Glancer\Capabilities"
# ms-settings page that lists the file types Glancer registered for.
DEFAULT_APPS_URI = "ms-settings:defaultapps?registeredAppUser=Glancer"

KINDS = {
    "Video": ("Glancer video", VIDEO_EXTENSIONS),
    "Audio": ("Glancer audio", AUDIO_EXTENSIONS),
    "Image": ("Glancer image", IMAGE_EXTENSIONS),
}


@dataclass(frozen=True)
class Value:
    key: str  # relative to HKCU / HKLM, e.g. Software\Classes\Glancer.Video
    name: str  # "" = the key's default value
    data: str
    owned: bool  # True: Glancer owns the whole key; False: only this value


def entries(exe: str) -> list[Value]:
    """Every registry value Glancer writes. `exe` is the full path (or an Inno constant)."""
    command = f'"{exe}" "%1"'
    icon = f'"{exe}",0'
    out: list[Value] = []
    for kind, (type_name, extensions) in KINDS.items():
        progid = rf"Software\Classes\Glancer.{kind}"
        out += [
            Value(progid, "", type_name, True),
            Value(progid, "FriendlyTypeName", type_name, True),
            Value(progid + r"\DefaultIcon", "", icon, True),
            Value(progid + r"\shell\open\command", "", command, True),
        ]
        for ext in sorted(extensions):
            out.append(Value(rf"Software\Classes\{ext}\OpenWithProgids", f"Glancer.{kind}", "", False))
    app = rf"Software\Classes\Applications\{EXE_NAME}"
    out += [
        Value(app, "FriendlyAppName", APP_NAME, True),
        Value(app + r"\DefaultIcon", "", icon, True),
        Value(app + r"\shell\open\command", "", command, True),
    ]
    for kind, (_, extensions) in KINDS.items():
        out += [Value(app + r"\SupportedTypes", ext, "", True) for ext in sorted(extensions)]
    out += [
        Value(CAPABILITIES, "ApplicationName", APP_NAME, True),
        Value(CAPABILITIES, "ApplicationDescription", DESCRIPTION, True),
        Value(CAPABILITIES, "ApplicationIcon", icon, True),
    ]
    for kind, (_, extensions) in KINDS.items():
        out += [Value(CAPABILITIES + r"\FileAssociations", ext, f"Glancer.{kind}", True)
                for ext in sorted(extensions)]
    out.append(Value(r"Software\RegisteredApplications", APP_NAME, CAPABILITIES, False))
    return out


def owned_roots(values: list[Value]) -> list[str]:
    """The top-most keys Glancer owns outright (deleted whole on unregister)."""
    keys = sorted({v.key for v in values if v.owned}, key=len)
    roots: list[str] = []
    for key in keys:
        if not any(key.lower().startswith(root.lower() + "\\") for root in roots):
            roots.append(key)
    return roots


# -- writers --------------------------------------------------------------------


class Registry(Protocol):
    def set(self, key: str, name: str, data: str) -> None: ...
    def delete_tree(self, key: str) -> None: ...
    def delete_value(self, key: str, name: str) -> None: ...


class WinRegistry:
    """HKEY_CURRENT_USER through winreg; missing keys/values are ignored on delete."""

    def __init__(self) -> None:
        import winreg

        self._w = winreg
        self._root = winreg.HKEY_CURRENT_USER

    def set(self, key: str, name: str, data: str) -> None:
        with self._w.CreateKeyEx(self._root, key, 0, self._w.KEY_SET_VALUE) as handle:
            self._w.SetValueEx(handle, name, 0, self._w.REG_SZ, data)

    def delete_tree(self, key: str) -> None:
        # winreg has no recursive delete; RegDeleteTreeW with a subkey removes
        # the subkey itself too. 2 = ERROR_FILE_NOT_FOUND (already gone).
        status = ctypes.windll.advapi32.RegDeleteTreeW(ctypes.c_void_p(0x80000001), key)  # HKCU
        if status not in (0, 2):
            raise OSError(status, f"RegDeleteTreeW failed for {key}")

    def delete_value(self, key: str, name: str) -> None:
        try:
            with self._w.OpenKey(self._root, key, 0, self._w.KEY_SET_VALUE) as handle:
                self._w.DeleteValue(handle, name)
        except FileNotFoundError:
            pass


def register(exe: Path, registry: Registry | None = None) -> None:
    """Write every entry (HKCU unless another registry is given, as tests do)."""
    target = registry or WinRegistry()
    for value in entries(str(exe)):
        target.set(value.key, value.name, value.data)
    if registry is None:
        _notify_shell()


def unregister(registry: Registry | None = None) -> None:
    target = registry or WinRegistry()
    values = entries(EXE_NAME)
    for root in owned_roots(values):
        target.delete_tree(root)
    for value in values:
        if not value.owned:
            target.delete_value(value.key, value.name)
    if registry is None:
        _notify_shell()


def is_registered(exe: Path) -> bool:
    """Whether HKCU's ProgID for videos opens this exe."""
    import winreg

    try:
        command = winreg.QueryValue(winreg.HKEY_CURRENT_USER,
                                    r"Software\Classes\Glancer.Video\shell\open\command")
    except OSError:
        return False
    return command == f'"{exe}" "%1"'


def _notify_shell() -> None:
    SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def _inno_quote(text: str) -> str:
    # Inno: " doubled inside a quoted string; a literal { is {{ ({app} stays a constant).
    return '"' + text.replace("{", "{{").replace("{{app}", "{app}").replace('"', '""') + '"'


def inno_registry() -> str:
    """[Registry] lines for installer/associations.iss (HKLM, removed on uninstall)."""
    lines = ["; Generated by: python -m glancer.win.associations --inno", "[Registry]"]
    roots = owned_roots(entries(EXE_NAME))
    for value in entries(r"{app}\Glancer.exe"):
        if value.owned:
            flags = "uninsdeletekey" if value.key in roots else ""
        else:
            flags = "uninsdeletevalue"
        line = (f"Root: HKLM; Subkey: {_inno_quote(value.key)}; ValueType: string; "
                f"ValueName: {_inno_quote(value.name)}; ValueData: {_inno_quote(value.data)}")
        lines.append(line + (f"; Flags: {flags}" if flags else ""))
    return "\n".join(lines) + "\n"


def reg_file(exe: Path, root: str = "HKEY_CURRENT_USER") -> str:
    """A .reg file with the same values, for registering by hand."""
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    lines = ["Windows Registry Editor Version 5.00", ""]
    current = None
    for value in entries(str(exe)):
        if value.key != current:
            lines += ["", f"[{root}\\{value.key}]"]
            current = value.key
        name = "@" if not value.name else f'"{esc(value.name)}"'
        lines.append(f'{name}="{esc(value.data)}"')
    return "\r\n".join(lines) + "\r\n"


if __name__ == "__main__":
    if "--inno" in sys.argv:
        sys.stdout.write(inno_registry())
    elif "--reg" in sys.argv:
        sys.stdout.write(reg_file(Path(sys.argv[-1])))
    else:
        print("usage: python -m glancer.win.associations --inno | --reg <path to Glancer.exe>")
