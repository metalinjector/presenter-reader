"""PDF из скриншотов: страницы по порядку, картинки — байт в байт данные из PNG."""

import re
import struct
import zlib

import numpy as np
import pytest

from presenter_reader.imageops import write_png
from presenter_reader.pdf import write_pdf


def png_idat(path) -> bytes:
    """Сжатые строки картинки из PNG (все блоки IDAT подряд)."""
    with open(path, "rb") as fh:
        data = fh.read()
    pos, out = 8, b""
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            out += data[pos + 8:pos + 8 + size]
        pos += size + 12
    return out


def pdf_objects(path) -> dict:
    """Объекты PDF по таблице xref: номер → (словарь, поток или None)."""
    with open(path, "rb") as fh:
        pdf = fh.read()
    assert pdf.startswith(b"%PDF-1.4\n") and pdf.endswith(b"%%EOF\n")
    xref = int(pdf[pdf.rindex(b"startxref") + 9:].split()[0])
    lines = pdf[xref:].split(b"\n")
    assert lines[0] == b"xref"
    count = int(lines[1].split()[1])
    objs = {}
    for num in range(1, count):
        entry = lines[2 + num]
        assert len(entry) == 19 and entry.endswith(b" 00000 n ")    # 20 байт с переводом строки
        off = int(entry[:10])
        head = b"%d 0 obj\n" % num
        assert pdf[off:off + len(head)] == head
        start = off + len(head)
        end = pdf.index(b"\n", start)
        body, stream = pdf[start:end].decode("ascii"), None
        if pdf[end:end + 8] == b"\nstream\n":
            n = int(re.search(r"/Length (\d+)", body).group(1))
            stream = pdf[end + 8:end + 8 + n]
            assert pdf[end + 8 + n:end + 8 + n + 10] == b"\nendstream"
        objs[num] = (body, stream)
    return objs


def pdf_pages(path) -> list[tuple[str, str, bytes]]:
    """Страницы по порядку: (словарь страницы, словарь картинки, поток картинки)."""
    objs = pdf_objects(path)
    kids = re.search(r"/Kids \[([^\]]*)\]", objs[2][0]).group(1)
    pages = []
    for num in map(int, re.findall(r"(\d+) 0 R", kids)):
        page = objs[num][0]
        img = int(re.search(r"/Im0 (\d+) 0 R", page).group(1))
        pages.append((page, objs[img][0], objs[img][1]))
    return pages


def _images(tmp_path):
    rng = np.random.default_rng(0)
    imgs = [rng.integers(0, 256, (36, 64, 3), np.uint8),        # цветной шум — сжатие ничего не скроет
            np.tile(np.arange(120, dtype=np.uint8), (40, 1)),   # оттенки серого
            rng.integers(0, 256, (48, 30, 3), np.uint8)]
    paths = []
    for i, img in enumerate(imgs):
        p = tmp_path / f"slide_{i + 1:03d}.png"
        write_png(str(p), img)
        paths.append(str(p))
    return imgs, paths


def test_pages_hold_png_data_unchanged(tmp_path):
    imgs, paths = _images(tmp_path)
    out = tmp_path / "slides.pdf"
    write_pdf(paths, str(out), title="Налоги: урок 2")
    pages = pdf_pages(out)
    assert len(pages) == len(paths)
    for (page, image, stream), img, path in zip(pages, imgs, paths):
        h, w = img.shape[:2]
        colors = 3 if img.ndim == 3 else 1
        assert stream == png_idat(path)                      # те же байты, без пересжатия
        assert f"/Width {w} /Height {h} " in image
        assert f"/Predictor 15 /Colors {colors} /BitsPerComponent 8 /Columns {w} " in image
        pw, ph = (f"{v * 0.75:.4f}".rstrip("0").rstrip(".") for v in (w, h))
        assert f"/MediaBox [0 0 {pw} {ph}]" in page        # 96 точек на дюйм
    info = pdf_objects(out)[3][0]
    title = re.search(r"/Title <FEFF([0-9A-F]*)>", info).group(1)
    assert bytes.fromhex(title).decode("utf-16-be") == "Налоги: урок 2"
    assert not (tmp_path / "slides.pdf.part").exists()


def test_png_with_alpha_becomes_plain_rgb(tmp_path):
    rgba = np.zeros((10, 20, 4), np.uint8)
    rgba[..., 2], rgba[..., 3] = 200, 128
    p = tmp_path / "alpha.png"
    write_png(str(p), rgba)
    out = tmp_path / "a.pdf"
    write_pdf([str(p)], str(out))
    (_, image, stream), = pdf_pages(out)
    assert "/ColorSpace /DeviceRGB" in image and "/Colors 3 " in image
    raw = zlib.decompress(stream)
    assert len(raw) == 10 * (1 + 20 * 3)                     # строки PNG: байт фильтра + RGB


def test_broken_png_leaves_no_file(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    out = tmp_path / "x.pdf"
    with pytest.raises(ValueError):
        write_pdf([str(bad)], str(out))
    assert not out.exists() and not (tmp_path / "x.pdf.part").exists()
