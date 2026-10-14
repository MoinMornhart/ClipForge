"""Dunkles Design für ClipForge (Qt-Stylesheet)."""

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0f1218"
PANEL = "#171b23"
PANEL_2 = "#1e2430"
LINE = "#2a3140"
FG = "#e6eaf0"
MUTED = "#94a0b2"
ACCENT = "#f2a93b"
ACCENT_2 = "#ff6a3d"
TWITCH = "#9146ff"
GOOD = "#4cc98a"
BAD = "#ff5d5d"

STYLE = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; color: {FG}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QDialog {{ background: {BG}; }}
QLabel#h1 {{ font-size: 20pt; font-weight: 800; }}
QLabel#h2 {{ font-size: 13pt; font-weight: 700; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#badge {{ background: {PANEL_2}; border-radius: 9px; padding: 2px 8px; color: {MUTED}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#drop {{ background: {PANEL}; border: 2px dashed {LINE}; border-radius: 16px; }}
QFrame#drop[hover="true"] {{ border-color: {ACCENT}; background: #1f1a12; }}
QListWidget#nav {{ background: {PANEL}; border: none; border-right: 1px solid {LINE}; padding: 10px 6px; }}
QListWidget#nav::item {{ padding: 10px 14px; border-radius: 8px; margin: 2px 4px; color: {MUTED}; }}
QListWidget#nav::item:selected {{ background: {PANEL_2}; color: {FG}; border-left: 3px solid {ACCENT}; }}
QListWidget#nav::item:hover {{ background: {PANEL_2}; }}
QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; outline: none; }}
QListWidget::item {{ border-bottom: 1px solid {LINE}; }}
QListWidget::item:selected {{ background: #262d3b; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {{
  background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 8px;
  selection-background-color: {ACCENT}; selection-color: #111;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
  border-color: {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {PANEL_2}; border: 1px solid {LINE}; selection-background-color: #333c4d; }}
QPushButton {{ background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #262d3b; }}
QPushButton:disabled {{ color: #5d6878; border-color: {PANEL_2}; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
  color: #15100a; font-weight: 700; border: none; padding: 9px 18px; }}
QPushButton#primary:disabled {{ background: #3a3f4a; color: #7d8696; }}
QPushButton#good {{ background: #173426; border-color: #24553c; color: {GOOD}; font-weight: 600; }}
QPushButton#bad {{ background: #3a1a1d; border-color: #5a2a2f; color: {BAD}; font-weight: 600; }}
QProgressBar {{ background: {PANEL_2}; border: none; border-radius: 6px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 6px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 10px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {FG}; border-bottom: 2px solid {ACCENT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4a5468; background: {PANEL_2}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(CHECK_SVG); }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 4px; min-width: 30px; }}
QToolTip {{ background: {PANEL_2}; color: {FG}; border: 1px solid {LINE}; padding: 6px; }}
QTableWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px; gridline-color: {LINE}; }}
QHeaderView::section {{ background: {PANEL_2}; border: none; padding: 6px; color: {MUTED}; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL_2}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; border-top: 1px solid {LINE}; }}
QMessageBox {{ background: {BG}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in [
        (QPalette.Window, BG), (QPalette.WindowText, FG), (QPalette.Base, PANEL_2), (QPalette.AlternateBase, PANEL),
        (QPalette.Text, FG), (QPalette.Button, PANEL_2), (QPalette.ButtonText, FG), (QPalette.Highlight, ACCENT),
        (QPalette.HighlightedText, "#111111"), (QPalette.ToolTipBase, PANEL_2), (QPalette.ToolTipText, FG),
        (QPalette.PlaceholderText, "#6b7586"),
    ]:
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    check = (Path(__file__).parent / "assets" / "check.svg").as_posix()
    app.setStyleSheet(STYLE.replace("CHECK_SVG", check))
