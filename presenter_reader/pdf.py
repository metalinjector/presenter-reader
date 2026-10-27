"""Все слайды в одном PDF: по странице на скриншот, картинки без пересжатия.

PNG хранит пиксели в сжатом без потерь потоке zlib с фильтрами строк. PDF умеет читать
ровно такой поток (FlateDecode с /Predictor 15), поэтому данные из PNG переносятся в PDF
байт в байт: ничего не распаковывается и не пережимается. Пиксели в PDF те же, что в PNG,
а PDF весит почти как сумма PNG — это просто контейнер для них.
"""

from __future__ import annotations

import os
import struct

import cv2
import numpy as np

from . import __version__

PT_PER_PX = 72 / 96     # страница — картинка при 96 точках на дюйм: при масштабе 100 % пиксель в пиксель

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_pdf(png_paths: list[str], out_path: str, title: str = "") -> None:
    """Собрать PDF: страницы — картинки ``png_paths`` в том же порядке.

    Объекты: 1 — каталог, 2 — список страниц, 3 — сведения о документе, дальше на каждую
    страницу по три: страница, её содержимое и картинка. Пишем во временный файл и
    переименовываем, чтобы при сбое не остался обрезанный PDF.
    """
    n = len(png_paths)
    offsets: list[int] = []
    tmp = out_path + ".part"
    try:
        with open(tmp, "wb") as fh:
            def obj(body: str, stream: bytes | None = None) -> None:
                offsets.append(fh.tell())
                fh.write(b"%d 0 obj\n" % len(offsets) + body.encode("ascii"))
                if stream is not None:
                    fh.write(b"\nstream\n" + stream + b"\nendstream")
                fh.write(b"\nendobj\n")

            fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            kids = " ".join(f"{4 + 3 * i} 0 R" for i in range(n))
            obj("<< /Type /Catalog /Pages 2 0 R >>")
            obj(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>")
            about = f"/Producer {_text('Presenter Reader ' + __version__)}"
            if title:
                about = f"/Title {_text(title)} " + about
            obj(f"<< {about} >>")
            for i, path in enumerate(png_paths):
                with open(path, "rb") as f:
                    w, h, image, data = _png_image(f.read())
                pw, ph = _num(w * PT_PER_PX), _num(h * PT_PER_PX)
                obj(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw} {ph}] "
                    f"/Resources << /XObject << /Im0 {6 + 3 * i} 0 R >> >> /Contents {5 + 3 * i} 0 R >>")
                content = f"q {pw} 0 0 {ph} 0 0 cm /Im0 Do Q".encode("ascii")
                obj(f"<< /Length {len(content)} >>", content)
                obj(f"<< {image} /Length {len(data)} >>", data)
            xref = fh.tell()
            fh.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1))
            fh.write(b"".join(b"%010d 00000 n \n" % o for o in offsets))
            fh.write(b"trailer\n<< /Size %d /Root 1 0 R /Info 3 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                     % (len(offsets) + 1, xref))
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _png_image(png: bytes) -> tuple[int, int, str, bytes]:
    """Ширина, высота, словарь картинки PDF и её поток — сжатые строки из PNG как есть."""
    parsed = _parse_png(png)
    if parsed is None:
        # Прозрачность, чересстрочная развёртка, 16 бит на канал: такое PDF напрямую не
        # возьмёт. Наши скриншоты такими не бывают, но на всякий случай приводим картинку
        # к обычному 8-битному RGB без потерь цвета.
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            parsed = _parse_png(cv2.imencode(".png", img)[1].tobytes())
        if parsed is None:
            raise ValueError("не удалось прочитать картинку PNG")
    w, h, depth, ctype, palette, data = parsed
    if ctype == 2:
        space, colors = "/DeviceRGB", 3
    elif ctype == 0:
        space, colors = "/DeviceGray", 1
    else:
        space, colors = f"[/Indexed /DeviceRGB {len(palette) // 3 - 1} <{palette.hex()}>]", 1
    image = (f"/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {space} "
             f"/BitsPerComponent {depth} /Filter /FlateDecode "
             f"/DecodeParms << /Predictor 15 /Colors {colors} /BitsPerComponent {depth} /Columns {w} >>")
    return w, h, image, data


def _parse_png(png: bytes):
    """(ширина, высота, бит, тип цвета, палитра, сжатые строки), если PNG можно положить
    в PDF как есть, иначе None."""
    if not png.startswith(_PNG_SIGNATURE):
        return None
    pos, header, palette, chunks = 8, None, b"", []
    while pos + 8 <= len(png):
        size, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + size]
        pos += size + 12                    # длина, тип, данные, контрольная сумма
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            return None                     # прозрачность
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
    if header is None or not chunks:
        return None
    w, h, depth, ctype, _, _, interlace = header
    if interlace or depth > 8 or ctype not in (0, 2, 3) or (ctype == 3 and not palette):
        return None
    return w, h, depth, ctype, palette, b"".join(chunks)


def _text(s: str) -> str:
    """Строка PDF в UTF-16 — так в свойствах документа читается и кириллица."""
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def _num(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")
