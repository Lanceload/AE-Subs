"""Рисует иконку программы в assets/icon.ico."""
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    k = size / 256
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2d6cdf"))
    p.drawRoundedRect(QRectF(8 * k, 8 * k, 240 * k, 240 * k), 48 * k, 48 * k)

    font = QFont("Arial", 1, QFont.Weight.Black)
    font.setPixelSize(int(110 * k))
    path = QPainterPath()
    path.addText(0, 0, font, "Ae")
    r = path.boundingRect()
    path.translate(128 * k - r.center().x(), 110 * k - r.center().y())
    p.setPen(QPen(QColor("#0b1f44"), 14 * k))
    p.drawPath(path)
    p.fillPath(path, QColor("white"))

    # «субтитр» — плашка с двумя строками
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawRoundedRect(QRectF(40 * k, 170 * k, 176 * k, 52 * k), 12 * k, 12 * k)
    p.setBrush(QColor("#FFE14D"))
    p.drawRoundedRect(QRectF(58 * k, 182 * k, 140 * k, 10 * k), 5 * k, 5 * k)
    p.drawRoundedRect(QRectF(80 * k, 200 * k, 96 * k, 10 * k), 5 * k, 5 * k)
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    draw(256).save(str(out / "icon.ico"))
    draw(256).save(str(out / "icon.png"))
    print("icon saved")
