"""Тренд-робот (trend.py): детектор и серия на имитации брокера. python tests/test_trend.py"""
import asyncio
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from fake_mt5 import FakeMT5  # noqa: E402
from trader import Trader  # noqa: E402
import trend as TRD  # noqa: E402

P = dict(TRD.DEFAULTS)


def flat_then_fall(n_flat=80, n_fall=20, step=4.0, bounce=1.0, start=4180.0, t0=1791000000):
    """Боковик ±1.5$, затем ровное падение по step за свечу с отскоками bounce."""
    bars, t, px = [], t0, start
    for i in range(n_flat):
        o = px + (1 if i % 2 else -1) * 0.5
        bars.append((t, o, o + 1.5, o - 1.5, px))
        t += 1800
    for i in range(n_fall):
        o = px
        c = px - step
        bars.append((t, o, o + bounce, c - 0.5, c))
        px = c
        t += 1800
    return bars


def test_detect():
    bars = flat_then_fall()
    s = TRD.detect(bars, P)
    assert s and s["d"] == -1 and s["leg"] > 60 and s["bounce"] <= 0.3 * s["leg"], s
    up = [(t, 8000 - o, 8000 - lo, 8000 - h, 8000 - c) for t, o, h, lo, c in bars]   # зеркально — рост
    s = TRD.detect(up, P)
    assert s and s["d"] == 1, s
    # большие отскоки — не «очевидный тренд»
    bars2 = flat_then_fall(bounce=45.0)
    assert TRD.detect(bars2, P) is None
    # одна новостная свеча — не тренд (ход короче 3 часов)
    bars3 = flat_then_fall(n_fall=0)
    t, o, h, lo, c = bars3[-1]
    bars3.append((t + 1800, c, c + 1, c - 60, c - 59))
    assert TRD.detect(bars3, P) is None
    # без нового минимума за сутки — тишина
    assert TRD.detect(bars[:85], P) is None or TRD.detect(bars[:85], P)["d"] == -1
    assert TRD.detect(flat_then_fall(n_fall=0), P) is None


def make(now_h=12, weekday=2, bars=None):
    f = FakeMT5(1000.0)
    f.bid = 4100.0
    f.spread = 0.15
    bars = bars or flat_then_fall(start=4180.0)
    f.rates = lambda sym, tf, n, start=1: bars[-n:]
    cfg = {"mode": "demo", "magic": 770077, "symbols": {"GOLD": {"candidates": ["GOLD"]}}, "trend": {}}
    tr = Trader(cfg, f)
    msgs = []

    async def notify(text, buttons=None, alt=""):
        msgs.append((text, buttons))
    base = dt.datetime(2026, 10, 5 + weekday, now_h, 5)      # 05.10.2026 — понедельник
    d = tempfile.mkdtemp()
    tb = TRD.TrendBot(cfg, tr, notify, lambda: base, make_buttons=lambda aid, dd: [("go", aid, dd)],
                      state_path=os.path.join(d, "s.json"), journal=os.path.join(d, "j.jsonl"))
    return f, tb, msgs


def test_series():
    f, tb, msgs = make()
    # чужая позиция копировщика (magic 770077) — тренд-робот её не трогает
    f.order_send({"action": 1, "symbol": "GOLD", "volume": 0.01, "type": 0, "price": 4100.15, "sl": 4000.0,
                  "tp": 4300.0, "magic": 770077, "comment": "WW1-1"})
    run = asyncio.run
    assert run(tb.start(-1, "тест")) == "Открываю"
    ser = tb.s["series"]
    R = ser["R"]
    assert 5.0 <= R <= 20 and ser["lot"] == 0.01, ser
    mine = tb.positions()
    assert len(mine) == 1 and mine[0].type == 1 and abs(mine[0].sl - (4100.0 + R)) < 0.02, mine
    # цена прошла R вниз — доливка, стоп первой в безубыток
    f.tick(4100.0 - R - 0.01)
    run(tb.manage())
    mine = sorted(tb.positions(), key=lambda p: p.ticket)
    assert len(mine) == 2 and mine[0].sl <= 4100.0 - 0.3 + 1e-6, [(p.price_open, p.sl) for p in mine]
    assert any("доливка 2" in m for m, _ in msgs), msgs
    # дальше вниз — ещё доливки (до 4), стоп тянется на 2R за ценой
    for k in range(2, 8):
        f.tick(4100.0 - k * R - 0.01)
        run(tb.manage())
    mine = tb.positions()
    assert len(mine) == 4 and tb.s["series"]["n"] == 4, len(mine)
    low = 4100.0 - 7 * R - 0.01
    assert all(abs(p.sl - (low + 0.15 + 2 * R)) < 0.05 for p in mine), [(p.sl, low + 0.15 + 2 * R) for p in mine]
    # разворот — все закрываются по стопу у брокера, серия закрыта в плюс
    f.tick(low + 2 * R + 0.2)
    assert not tb.positions()
    tb.s["series"]["opened"] -= 60
    run(tb.manage())
    assert tb.s["series"] is None and any("закрыта" in m for m, _ in msgs), msgs[-1]
    assert "+" in [m for m, _ in msgs if "закрыта" in m][0]
    assert len(f.pos) == 1 and list(f.pos.values())[0].magic == 770077          # позиция копировщика на месте
    assert "серий 1, в плюс 1" in tb.report_line(0, 1e12)


def test_loss_and_limits():
    f, tb, msgs = make()
    run = asyncio.run
    run(tb.start(1, "тест"))
    R = tb.s["series"]["R"]
    f.tick(4100.0 - R - 0.5)                    # стоп первой же позиции
    tb.s["series"]["opened"] -= 60
    run(tb.manage())
    assert tb.s["series"] is None and tb.s["losses"][tb.today()] == 1
    f.tick(4100.0)
    run(tb.start(1, "тест"))
    f.tick(4100.0 - tb.s["series"]["R"] - 0.5)
    tb.s["series"]["opened"] -= 60
    run(tb.manage())
    assert tb.s["losses"][tb.today()] == 2
    assert "убыточные серии" in tb.blocked()               # две убыточные серии — до завтра
    # окно данных США и выходные
    f2, tb2, _ = make(now_h=15)
    assert "данных США" in tb2.blocked()
    f3, tb3, _ = make(weekday=5)
    assert "выходные" in tb3.blocked()
    # реальный счёт — только с allow_live
    f4, tb4, _ = make()
    tb4.cfg["mode"] = "live"
    assert "реальный счёт" in tb4.blocked()


def test_alert_button():
    f, tb, msgs = make()
    run = asyncio.run
    run(tb.scan())
    assert msgs and "уверенно падает" in msgs[0][0] and msgs[0][1] == [("go", 1, -1)], msgs
    aid = tb.s["alert"]["id"]
    run(tb.scan())                                       # та же свеча — повторно не шлём
    assert len(msgs) == 1
    assert run(tb.on_button("go", "999")) == "Уже неактуально"
    assert run(tb.on_button("go", str(aid))) == "Открываю" and tb.s["series"]
    # кнопка «пропустить» и устаревание
    f, tb, msgs = make()
    run(tb.scan())
    tb.s["alert"]["created"] -= 3600
    assert run(tb.on_button("go", str(tb.s["alert"]["id"]))) == "Устарело" and not tb.s["series"]


if __name__ == "__main__":
    for fn in (test_detect, test_series, test_loss_and_limits, test_alert_button):
        fn()
    print("OK")
