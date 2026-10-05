"""Быстрые проверки разбора и логики входа: python -m pytest tests  (или python tests/test_core.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from parser import parse_signal, parse_command  # noqa: E402
from trader import decide_entry, plan_positions, lot_from_table  # noqa: E402
import guard as G  # noqa: E402

SIGNALS = {
    "GOLD BUY\nBUY @ 4506-4498\nTF: 1M\n✅TP1: 4510\n✅TP2: 4513\n✅TP3: 4521\n✅TP4: 4536\n❌SL: 4483": ("BUY", True),
    "Продажа XAUUSD\n📍4340-4343\n✅TP1: 4337\n✅TP2: 4330\n✅TP3: 4305\n🔴SL: 4349": ("SELL", True),
    "GOLD BUY\n4594-4598\n✅TР1: 4604\n✅TР2: 4608\n✅TР3: 46112\n🔴SL: 4592": ("BUY", False),        # опечатка TP3
    "GOLD SELL\n4515-4500\n✅TP1: 4519\n✅TP2: 4530\n✅TP3: 4545\n🔴SL: 4467": ("SELL", False),      # направление перепутано
    "SELL XAUUSD\n4075\nTake profit 1: 4069\nTake profit 2: 4065\nTake profit 3: 4060\nStop loss : 4082": ("SELL", True),  # 13.07
    "BUY XAUUSD\n4029-4030\nTake profit 1: 4035\nTake profit 2: 4039\nTake profit 3: 4045\nStop: 4021": ("BUY", True),   # 15.07
    "RISKY GOLD LIMIT BUY\nlimit buy 4333\n✅TP1: 4355\n✅TP2: 4376\n✅TP3: 4402\n🔴SL: 4309": ("BUY", True),
}
COMMANDS = {
    "Фиксирую 1/3 TP ✅\nСтоп переношу в бу\n+350 points": "be",
    "Закрываем позицию сейчас": "close",
    "Корректировка Стоп Лоса: переставляем на отметку 4333": "sl",
    "Не забудьте про то что третий тейк другой, в сигнале опечатка\nтретий тейк на 3997": "tp",
    "Сделка закрылась по стопу 🔴\n-300 points": "closed_report",
    "Фиксирую 1/3 TP ✅\nСтоп в бу пока не переставляю, ожидайте апдейт по позиции\n+367 points": None,
}


def test_signals():
    for text, (side, valid) in SIGNALS.items():
        s = parse_signal(text)
        assert s and s.side == side and s.valid == valid, (text, s and s.errors)


def test_commands():
    for text, kind in COMMANDS.items():
        kinds = [c.kind for c in parse_command(text)]
        assert (kind in kinds) if kind else not kinds, (text, kinds)


def test_entry():
    s = parse_signal("TRADE GOLD SELL\n4167-4175\nTP1: 4165\nTP2: 4161\nTP3: 4149\nSL: 4187")
    assert decide_entry(s, 4170, 4170.2, 2, 1)[0] == "market"
    assert decide_entry(s, 4184.67, 4184.8, 2, 1)[0] == "skip"     # ушла за зону к стопу
    assert decide_entry(s, 4163, 4163.2, 2, 1)[0] == "skip"        # TP1 уже пройден
    b = parse_signal("GOLD BUY\n4317-4308\nTP1: 4321\nTP2: 4325\nTP3: 4337\nSL: 4300")
    assert decide_entry(b, 4319.8, 4320, 2, 1)[0] == "limit"


def test_lots():
    assert lot_from_table(1000, [[200, .01], [900, .05], [1500, .06]]) == .05
    assert plan_positions(0.05, [1, 2, 3], 0.01, 0.01) == [(1, .01), (2, .01), (3, .01)]
    assert plan_positions(0.025, [1, 2, 3], 0.01, 0.01) == [(1, .01), (3, .01)]


def test_guard():
    g = G.settings({})
    base = dict(symbol_key="GOLD", plan_lot=0.03, table_lot=0.05, sl_distance=17, balance=1000,
                free_margin=1000, margin_needed=126, open_risk=0, recent_signals=[])
    assert G.check_trade(g, plan_risk=51, **base) == []                       # обычная сделка 5%
    assert G.check_trade(g, plan_risk=150, **base)                            # 15% — опасно
    assert G.check_trade(g, plan_risk=51, **{**base, "sl_distance": 120})     # стоп $120
    assert G.check_trade(g, plan_risk=51, **{**base, "plan_lot": 0.6})        # лот выше потолка
    assert G.check_trade(g, plan_risk=51, **{**base, "open_risk": 110})       # суммарно 16%
    assert G.check_account(g, last_pnls=[5, -10, -20, -30], equity=900, peak_balance=1000)
    assert not G.check_account(g, last_pnls=[-10, -20, 5], equity=900, peak_balance=1000)
    assert G.check_account(g, last_pnls=[], equity=700, peak_balance=1000)
    assert G.sl_move_increases_risk("BUY", 4300, 4290, 4280)
    assert not G.sl_move_increases_risk("BUY", 4300, 4290, 4295)
    assert G.sl_move_increases_risk("SELL", 4300, 4310, 4320)


if __name__ == "__main__":
    for f in (test_signals, test_commands, test_entry, test_lots, test_guard):
        f()
    print("OK")
