"""アプリアイコンをプログラムで描画する (外部ファイルに依存しない)。

タスクバーやウィンドウのタイトルバーで Python のアイコンにならないよう、
main.py から QApplication と MainWindow の両方に設定する。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

_BG_TOP = QColor("#2f7fd0")
_BG_BOTTOM = QColor("#1b4f8a")
_TAB_COLOR = QColor("#9fd0ff")
_BODY_COLOR = QColor("#ffd97a")
_BODY_EDGE = QColor("#d9a83f")


def _draw_icon(size: int) -> QPixmap:
    """フォルダをモチーフにしたアイコンを size x size で描く。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 背景 (角丸のグラデーション)
    margin = size * 0.04
    rect = pixmap.rect().adjusted(margin, margin, -margin, -margin)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, _BG_TOP)
    gradient.setColorAt(1, _BG_BOTTOM)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.18, size * 0.18)
    painter.fillPath(path, gradient)

    # フォルダ本体
    fx = size * 0.16
    fw = size * 0.68
    fy = size * 0.34
    fh = size * 0.44
    painter.setPen(Qt.NoPen)
    painter.setBrush(_TAB_COLOR)  # フォルダのツメ
    tab = QPainterPath()
    tab.addRoundedRect(fx, fy - size * 0.09, fw * 0.42, size * 0.16, size * 0.04, size * 0.04)
    painter.fillPath(tab, _TAB_COLOR)
    painter.setBrush(_BODY_COLOR)
    body = QPainterPath()
    body.addRoundedRect(fx, fy, fw, fh, size * 0.05, size * 0.05)
    painter.fillPath(body, _BODY_COLOR)
    painter.setPen(_BODY_EDGE)
    painter.drawPath(body)
    painter.end()
    return pixmap


def create_app_icon() -> QIcon:
    """複数サイズを持った QIcon を返す (タスクバーの拡大表示でも粗くならない)。"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw_icon(size))
    return icon
