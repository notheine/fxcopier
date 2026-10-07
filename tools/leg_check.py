"""
Проверка правила «самый сильный ход за двое суток, почти без отскоков» (идея владельца 07.10.2026).

Правило (mode=leg в tools/trend_bt.py), на свечах M30:
- закрытие свечи — новый минимум (максимум) за W свечей (48 = сутки, 96 = двое суток);
- ход от максимума (минимума) окна длится ≥ mindur свечей (не одна новостная свеча);
- самый большой отскок внутри хода ≤ bounce × ход («почти без отскоков»);
- вход по рынку; стоп = bm × самый большой отскок (но ≥ ATR); доливка через каждый стоп (R),
  все позиции закрывает общий скользящий стоп t × R от лучшей цены; до maxpos позиций.

Запуск: python tools/leg_check.py gold_M15.csv [gold_M30.csv]   (≈3 мин на 2 ядрах)
1) сетка 432 варианта — итоги по годам; 2) соседние с лучшей настройки; 3) случайные входы с таким же стопом;
4) если дан gold_M30.csv — проверка на 09.2021–07.2022 (этих лет нет в M15). Итоги — docs/RESEARCH.md.
"""
import itertools
import math
import os
import statistics as st
import sys
from dataclasses import replace
from datetime import datetime, timezone
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(__file__))
import trend_bt as T  # noqa: E402

BEST = dict(mode="leg", W=48, bounce=0.3, mindur=6, spikecap=0.0, bm=1.5, r=1.0, t=2.0, maxpos=3, s=1.0)
GRID = [dict(mode="leg", W=W, bounce=b, mindur=md, spikecap=sc, bm=bm, r=1.0, t=t, maxpos=mp, s=1.0)
        for W, b, md, sc, bm, t, mp in itertools.product((48, 96), (0.2, 0.3, 0.4), (6, 12), (0.0, 0.35),
                                                          (1.0, 1.5), (1.0, 1.5, 2.0), (1, 3, 5))]
NEAR = [{**BEST, "W": W, "bounce": b, "bm": bm, "t": t}
        for W, b, bm, t in itertools.product((36, 48, 60, 72), (0.25, 0.3, 0.35), (1.25, 1.5, 1.75), (1.75, 2.0, 2.5))]

bars = T.load(sys.argv[1])
m30 = T.to_m30(bars)
atr = T.atr_series(m30)


def work(kw):
    s, _ = T.run(bars, replace(T.P(), **kw), m30, atr)
    y = {}
    for x in s:
        y[T.dtime(x[0]).year] = y.get(T.dtime(x[0]).year, 0) + x[2]
    return kw, y, T.stats(s)


def rnd(seed):
    s, _ = T.run(bars, replace(T.P(), mode="random", seed=seed, r=3.0, t=2.0, maxpos=3, s=1.0), m30, atr)
    return [x[2] for x in s]


if __name__ == "__main__":
    with Pool(2) as pool:
        grid = pool.map(work, GRID, chunksize=8)
        near = pool.map(work, NEAR, chunksize=8)
        rr = pool.map(rnd, range(1, 21))
    tot = [g[2]["sumR"] for g in grid]
    print(f"сетка {len(grid)}: итог медиана {st.median(tot):+.1f}R, в плюсе {100 * sum(t > 0 for t in tot) / len(tot):.0f}%")
    years = sorted({y for g in grid for y in g[1]})
    for y in years:
        v = [g[1].get(y, 0) for g in grid]
        print(f"   {y}: медиана {st.median(v):+.1f}R, в плюсе {100 * sum(x > 0 for x in v) / len(v):.0f}%")
    b = [g for g in grid if g[0] == BEST][0]
    print("лучшая:", T.fmt(b[2]), "| по годам", {y: round(v, 1) for y, v in sorted(b[1].items())})
    v = [g[2]["sumR"] for g in near]
    print(f"соседние {len(v)}: медиана {st.median(v):+.1f}R, в плюсе {100 * sum(x > 0 for x in v) / len(v):.0f}%")
    allr = [x for r in rr for x in r]
    m, sd = st.mean(allr), st.pstdev(allr)
    n = b[2]["n"]
    print(f"случайные входы (стоп 3 ATR, трейл 2R, до 3 поз.): {m:+.3f}R на серию; у лучшей "
          f"{b[2]['sumR'] / n:+.3f}, z = {(b[2]['sumR'] / n - m) / (sd / math.sqrt(n)):.1f}")
    if len(sys.argv) > 2:
        b30 = T.load(sys.argv[2])
        mm = T.to_m30(b30)
        aa = T.atr_series(mm)
        end = int(datetime(2022, 7, 29, tzinfo=timezone.utc).timestamp())
        s, _ = T.run(b30, replace(T.P(), **BEST), mm, aa, end_t=end)
        print("09.2021–07.2022 (новые данные), лучшая:", T.fmt(T.stats(s)))
        v = [T.stats(T.run(b30, replace(T.P(), **kw), mm, aa, end_t=end)[0])["sumR"] for kw in NEAR]
        print(f"   соседние: медиана {st.median(v):+.1f}R, в плюсе {100 * sum(x > 0 for x in v) / len(v):.0f}%")
