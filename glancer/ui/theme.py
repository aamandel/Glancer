"""Dark theme, Windows accent colour and icon glyphs.

The app stays on Qt's native Windows 11 style with the dark colour scheme
forced, so menus and dialogs look native. A stylesheet then styles only
Glancer's own overlay widgets. Highlights use the Windows accent colour, and
the stylesheet is rebuilt if the user changes that colour while Glancer runs.

Icons are glyphs from Segoe Fluent Icons (Windows 11), falling back to Segoe
MDL2 Assets (Windows 10), which uses the same code points.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

TEXT = "#F0F0F0"
DIM_TEXT = "#B4B4B4"
BAR_BACKGROUND = QColor(20, 20, 20, 191)  # rgba(20,20,20,0.75)
UI_FONT = "Segoe UI Variable"
FALLBACK_ACCENT = QColor("#4CC2FF")


class Glyph:
    PLAY = ""
    PAUSE = ""
    FRAME_BACK = ""
    FRAME_FORWARD = ""
    REWIND = ""
    FAST_FORWARD = ""
    VOLUME = ""
    MUTED = ""
    LOOP = ""
    MENU = ""
    FULLSCREEN = ""
    EXIT_FULLSCREEN = ""
    ROTATE = ""
    PREVIOUS = ""
    NEXT = ""
    MUSIC = ""


_icon_family: str | None = None


def icon_font(point_size: float = 12) -> QFont:
    global _icon_family
    if _icon_family is None:
        families = set(QFontDatabase.families())
        _icon_family = next(
            (f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families),
            "Segoe UI Symbol",
        )
    font = QFont(_icon_family)
    font.setPointSizeF(point_size)
    return font


def accent_color() -> QColor:
    color = QGuiApplication.palette().color(QPalette.ColorRole.Accent)
    return color if color.isValid() and color.alpha() > 0 else FALLBACK_ACCENT


def stylesheet(accent: QColor) -> str:
    a = accent.name()
    on_accent = "#000000" if accent.lightnessF() > 0.55 else "#FFFFFF"
    return f"""
#ControlBar QLabel, #AudioInfo QLabel {{ color: {TEXT}; background: transparent; }}
#ControlBar QLabel#TimeLabel {{ color: {DIM_TEXT}; }}
#ControlBar QToolButton {{
    color: {TEXT}; background: transparent; border: none; border-radius: 6px;
    min-width: 32px; min-height: 32px; padding: 0px 4px;
}}
#ControlBar QToolButton:hover {{ background: rgba(255, 255, 255, 0.12); }}
#ControlBar QToolButton:pressed {{ background: rgba(255, 255, 255, 0.06); }}
#ControlBar QToolButton:checked {{ color: {a}; }}
#ControlBar QToolButton:disabled {{ color: rgba(240, 240, 240, 0.3); }}
#ControlBar QToolButton::menu-indicator {{ image: none; width: 0px; }}
#ControlBar QSlider {{ background: transparent; min-height: 18px; }}
#ControlBar QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px; background: rgba(255, 255, 255, 0.28);
}}
#ControlBar QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {a}; }}
#ControlBar QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -4px 0px; border-radius: 6px; background: {a};
}}
#ControlBar QSlider::handle:horizontal:hover {{ background: {TEXT}; }}
#ControlBar QSlider:disabled::sub-page:horizontal {{ background: rgba(255, 255, 255, 0.28); }}
#ControlBar QSlider:disabled::handle:horizontal {{ background: transparent; }}
#EmptyState QPushButton {{
    color: {on_accent}; background: {a}; border: none; border-radius: 8px;
    padding: 10px 28px; font-size: 11pt;
}}
#EmptyState QPushButton:hover {{ background: {accent.lighter(112).name()}; }}
#EmptyState QLabel {{ color: {DIM_TEXT}; background: transparent; }}
#Toast {{
    color: {TEXT}; background: rgba(20, 20, 20, 0.88); border-radius: 8px;
    padding: 10px 16px;
}}
"""


class ThemeWatcher(QObject):
    """Rebuilds the stylesheet when the Windows accent colour changes."""

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._accent = QColor()
        self.apply()
        app.installEventFilter(self)

    def apply(self) -> None:
        accent = accent_color()
        if accent != self._accent:
            self._accent = accent
            self._app.setStyleSheet(stylesheet(accent))

    @property
    def accent(self) -> QColor:
        return QColor(self._accent)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._app and event.type() == QEvent.Type.ApplicationPaletteChange:
            self.apply()
        return False


def apply_theme(app: QApplication) -> ThemeWatcher:
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    font = QFont(UI_FONT)
    font.setPointSizeF(10)
    app.setFont(font)
    return ThemeWatcher(app)
