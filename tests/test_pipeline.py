"""Сквозная проверка на коротком синтетическом вебинаре (нужен ffmpeg с libx264)."""

import json
import os
import shutil

import pytest

from presenter_reader import Params, process

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")


@pytest.fixture(scope="session")
def mini_video(tmp_path_factory):
    from tests.synth import make_scenario, render
    d = tmp_path_factory.mktemp("video")
    path = str(d / "mini.mp4")
    gt = render(make_scenario("mini"), path, fps=10, gop_seconds=2, crf=28)
    return path, gt


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy / float(aw * ah + bw * bh - ix * iy)


@pytest.mark.parametrize("mode", ["auto", "sequential"])
def test_mini_webinar(mini_video, tmp_path, mode):
    path, gt = mini_video
    res = process(path, str(tmp_path / "out"), Params(mode=mode))
    screens = gt["screens"]
    assert len(res.slides) == len(screens)
    for s, e in zip(res.slides, screens):
        assert abs(s.t_start - e["start"]) <= 1.5
        assert (s.repeat_of is None) == (e["repeat_of"] is None)
    assert _iou(res.layout.rect, gt["slide_rect"]) > 0.97
    # файлы: PNG на каждый слайд, у повтора — пометка о сходстве
    pngs = sorted(f for f in os.listdir(res.out_dir) if f.startswith("slide_"))
    assert len(pngs) == len(screens)
    assert sum("_repeat-of-" in f and "_sim-" in f for f in pngs) == 1
    with open(os.path.join(res.out_dir, "report.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    assert rep["slides_count"] == len(screens)
    assert os.path.exists(os.path.join(res.out_dir, "layout.png"))
    assert not os.path.exists(os.path.join(res.out_dir, ".runs_tmp"))


def test_manual_roi(mini_video, tmp_path):
    path, gt = mini_video
    res = process(path, str(tmp_path / "out"), Params(roi=tuple(gt["slide_rect"])))
    assert res.layout.method == "manual"
    assert len(res.slides) == len(gt["screens"])


def test_skip_duplicates(mini_video, tmp_path):
    path, gt = mini_video
    res = process(path, str(tmp_path / "out"), Params(skip_duplicates=True))
    screens = gt["screens"]
    n_rep = sum(1 for e in screens if e["repeat_of"] is not None)
    assert len(res.slides) == len(screens) - n_rep
    assert res.info["duplicates_skipped"] == n_rep
    first = res.slides[0]
    assert first.appearances == 2 and len(first.shows) >= 2      # показ в начале и возврат
    pngs = [f for f in os.listdir(res.out_dir) if f.startswith("slide_")]
    assert len(pngs) == len(res.slides)
    assert not any("_repeat-of-" in f for f in pngs)
    with open(os.path.join(res.out_dir, "report.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    assert rep["skip_duplicates"] is True and rep["duplicates_skipped"] == n_rep
    assert rep["slides"][0]["appearances"] == 2


def test_pdf(mini_video, tmp_path):
    from tests.test_pdf import pdf_pages, png_idat
    path, gt = mini_video
    out = tmp_path / "out"
    res = process(path, str(out), Params(pdf=True))
    assert res.pdf == str(out / "mini_slides.pdf") and os.path.exists(res.pdf)
    with open(out / "report.json", encoding="utf-8") as fh:
        assert json.load(fh)["pdf"] == "mini_slides.pdf"
    # страницы — скриншоты в порядке показа, байт в байт как в PNG
    streams = [stream for _, _, stream in pdf_pages(res.pdf)]
    assert streams == [png_idat(out / f) for f in res.files]
    assert len(streams) == len(gt["screens"])
    # без PDF в той же папке — PDF прошлой обработки удаляется, чтобы не путать
    res = process(path, str(out), Params())
    assert res.pdf is None and not os.path.exists(out / "mini_slides.pdf")
    with open(out / "report.json", encoding="utf-8") as fh:
        assert json.load(fh)["pdf"] is None
