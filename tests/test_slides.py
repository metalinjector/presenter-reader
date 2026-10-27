from presenter_reader.slides import Slide, drop_duplicates


def _slide(t, best=None, sim=None, threshold=80):
    s = Slide(0, None, [(t, t + 10)])
    s.best_ref, s.best_sim = best, sim
    if best is not None and sim >= threshold:
        s.repeat_of, s.similarity = best.number, sim
    return s


def _numbered(slides):
    for i, s in enumerate(slides, 1):
        s.number = i
    return slides


def test_drop_duplicates_chain_and_renumbering():
    a = _slide(0)
    b = _slide(10)
    _numbered([a, b])
    a2 = _slide(20, a, 99)           # дубль a
    c = _slide(30)
    a3 = _slide(40, a2, 100)         # похож на дубль — значит, тоже на a
    b_mod = _slide(50, b, 88)        # заметно изменённый b — остаётся с пометкой
    slides = _numbered([a, b, a2, c, a3, b_mod])
    b_mod.repeat_of = b.number
    kept, skipped = drop_duplicates(slides, 95)
    assert kept == [a, b, c, b_mod] and skipped == 2
    assert [s.number for s in kept] == [1, 2, 3, 4]
    assert a.appearances == 3 and a.shows == [(0, 10), (20, 30), (40, 50)]
    assert b_mod.repeat_of == 2 and b_mod.similarity == 88


def test_drop_duplicates_threshold():
    a = _slide(0)
    _numbered([a])
    a2 = _slide(20, a, 94)
    kept, skipped = drop_duplicates(_numbered([a, a2]), 95)
    assert kept == [a, a2] and skipped == 0
