import numpy as np
import pytest

from presenter_reader.imageops import (Comparator, block_sum, downscale, max_sum_rectangle,
                                       median_image)


def test_block_sum_counts_ones():
    a = np.zeros((16, 24), bool)
    a[0:3, 0:2] = True          # 6 единиц в блоке (0, 0)
    a[9, 17] = True             # 1 единица в блоке (1, 2)
    s = block_sum(a, 8)
    assert s.shape == (2, 3)
    assert s[0, 0] == 6 and s[1, 2] == 1 and s.sum() == 7


def test_downscale_shape_and_mean():
    img = np.full((1080, 1920), 100, np.uint8)
    out = downscale(img, (480, 270))
    assert out.shape == (270, 480)
    assert abs(int(out.mean()) - 100) <= 1


def test_max_sum_rectangle_finds_block():
    score = -np.ones((10, 12))
    score[2:6, 3:9] = 1.0
    y0, y1, x0, x1, v = max_sum_rectangle(score)
    assert (y0, y1, x0, x1) == (2, 6, 3, 9)
    assert v == pytest.approx(24)


def _slide():
    img = np.full((360, 640), 240, np.uint8)
    for i in range(5):
        img[60 + 40 * i: 72 + 40 * i, 60:420] = 30     # «строки текста»
    return img


def test_comparator_ignores_cursor_but_sees_new_line():
    cmp = Comparator()
    a = _slide()
    b = a.copy()
    b[200:214, 500:509] = 0                              # курсор
    assert cmp.is_same(a, b, None)
    c = a.copy()
    c[300:312, 60:420] = 30                              # новая строка
    assert not cmp.is_same(a, c, None)
    assert cmp.contains(c, a, None)                      # «c» — шаг анимации после «a»
    assert not cmp.contains(a, c, None)


def test_similarity_identical_and_different():
    cmp = Comparator()
    a = _slide()
    assert cmp.similarity(a, a, None) == pytest.approx(100.0)
    other = np.full((360, 640), 240, np.uint8)
    other[100:300:30, 300:620] = 20
    assert cmp.similarity(a, other, None) < 50


def test_median_removes_moving_object():
    base = np.full((20, 20, 3), 200, np.uint8)
    frames = []
    for x in (2, 8, 14):
        f = base.copy()
        f[5:8, x:x + 3] = 0
        frames.append(f)
    assert (median_image(frames) == 200).all()
