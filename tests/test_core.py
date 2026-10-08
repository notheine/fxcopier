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
    "Фиксирую 1/3 TP ✅\nСтоп в бу пока не переставляю, ожидайте апдейт по позиции\n+367 points": "no_be",
    "Большой стоп, соблюдаем риски": "reduce",
    "Отодвигаю первый TP на значение 4190": "tp",          # 06.10 #1333
    "Занижаем риск стоп длинный": "reduce",
    "Соблюдайте риски, большой Стоплос": "reduce",
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
    # SELL, середина диапазона 4171: цена 4172 (лучше точки) — по рынку; 4169 (хуже) — SELL LIMIT 4171
    assert decide_entry(s, 4172, 4172.2, 2, 1)[0] == "market"
    assert decide_entry(s, 4169, 4169.2, 2, 1)[:2] == ("limit", 4171.0)
    assert decide_entry(s, 4184.67, 4184.8, 2, 1)[0] == "skip"     # выше диапазона, к стопу — не входим
    assert decide_entry(s, 4184.67, 4184.8, 2, 1, stop_orders=True)[:2] == ("stop", 4175.0)
    assert decide_entry(s, 4163, 4163.2, 2, 1)[0] == "skip"        # TP1 уже пройден
    # 05.10 #1313: диапазон 4158-4155, середина 4156.5 — по 4159.04 НЕ входим, ставим BUY LIMIT 4156.5
    c = parse_signal("TRADE GOLD BUY\n\n4158-4155\n\n✅TP1: 4161\n✅TP2: 4164\n✅TP3: 4172\n\n🔴SL: 4151")
    assert decide_entry(c, 4158.89, 4159.04, 2, 0.3)[:2] == ("limit", 4156.5)
    assert decide_entry(c, 4157.5, 4157.65, 2, 0.3)[:2] == ("limit", 4156.5)  # внутри, но хуже середины
    assert decide_entry(c, 4155.5, 4155.65, 2, 0.3)[0] == "market"           # нижняя половина — по рынку
    assert decide_entry(c, 4158.89, 4159.04, 2, 0.3, level=0)[:2] == ("limit", 4155.0)
    assert decide_entry(c, 4158.89, 4159.04, 2, 0.3, level=1)[:2] == ("limit", 4158.0)
    assert decide_entry(c, 4153.0, 4153.15, 2, 0.3)[0] == "skip"             # ниже диапазона
    d = parse_signal("SELL XAUUSD\n4075\nTake profit 1: 4069\nTake profit 2: 4065\nTake profit 3: 4060\nStop loss : 4082")
    assert decide_entry(d, 4075.5, 4075.65, 2, 0.3)[0] == "market"            # одна цена: диапазон ±2$, точка 4075
    assert decide_entry(d, 4073.5, 4073.65, 2, 0.3)[:2] == ("limit", 4075.0)


def test_lots():
    assert lot_from_table(1000, [[200, .01], [900, .05], [1500, .06]]) == .05
    assert plan_positions(0.05, [1, 2, 3], 0.01, 0.01) == [(1, .01), (2, .01), (3, .01)]
    assert plan_positions(0.025, [1, 2, 3], 0.01, 0.01) == [(1, .01), (2, .01)]   # 08.10: TP1+TP2


def test_channel_priority():
    """Лот ÷2 по сообщению канала после входа; уход цены от зоны к тейкам — пропуск, а не лимитка."""
    from fake_mt5 import FakeMT5
    from trader import Trader
    cfg = {"magic": 770077, "lot_table": [[200, .01], [900, .05], [1500, .06], [2000, .08]], "risk": {},
           "symbols": {"GOLD": {"enabled": True, "candidates": ["GOLD"], "entry_tolerance": 2.0, "max_spread": 1.0,
                                "max_slippage": 0.5, "be_offset": 0.3, "min_sl_gap": 1.0}}}
    sig = parse_signal("GOLD BUY\n4398-4402\nTP1: 4405\nTP2: 4410\nTP3: 4420\nSL: 4380")
    for bal, before, after in ((1000, [.01, .01, .01], [.01, .01]), (2000, [.02, .02, .02], [.01, .01, .01])):
        f = FakeMT5(bal); f.bid = 4399.5
        t = Trader(cfg, f)
        plan, _ = t.prepare(1, sig)
        orders, _ = t.execute(plan)
        rec = {"id": 1, "orders": orders}
        assert sorted(p.volume for p in f.positions_get()) == before
        t.reduce_half(rec)
        assert sorted(round(p.volume, 2) for p in f.positions_get()) == after, f.positions_get()
        if bal == 1000:   # остались TP1 и дальний тейк
            assert sorted(t.k_of(rec, p.identifier, p.comment) for p in f.positions_get()) == [1, 3]
    f = FakeMT5(1000); f.bid = 4403.0                 # цена выше диапазона 4398–4402 → BUY LIMIT по середине 4400
    t = Trader(cfg, f)
    plan, why = t.prepare(2, sig)
    assert plan["action"] == "limit" and plan["price"] == 4400.0, why
    orders, _ = t.execute(plan)
    assert len(f.orders_get()) == 3 and not f.positions_get()
    f.tick(4399.8)                                     # цена дошла до точки входа — ордера исполнились
    assert len(f.positions_get()) == 3 and all(p.price_open == 4400.0 for p in f.positions_get())
    cfg["risk"]["entry_level"] = 0.0                   # настройка владельца: лучший край
    f = FakeMT5(1000); f.bid = 4399.0
    plan, why = Trader(cfg, f).prepare(3, sig)
    assert plan["action"] == "limit" and plan["price"] == 4398.0, why


def test_after_tp1():
    """06.10 #1332: цена уже за TP1 — TP2/TP3 по рынку, TP1 лимиткой на точку входа."""
    from fake_mt5 import FakeMT5
    from trader import Trader
    cfg = {"magic": 770077, "lot_table": [[200, .01], [900, .05]], "risk": {"after_tp1": "rest_limit"},
           "symbols": {"GOLD": {"enabled": True, "candidates": ["GOLD"], "entry_tolerance": 2.0, "max_spread": 1.0,
                                "max_slippage": 0.5, "be_offset": 0.3, "min_sl_gap": 1.0}}}
    sig = parse_signal("GOLD BUY\n4398-4402\nTP1: 4405\nTP2: 4410\nTP3: 4420\nSL: 4380")
    f = FakeMT5(1000); f.bid = 4406.0
    t = Trader(cfg, f)
    plan, why = t.prepare(1, sig)
    assert plan and plan["be_k"] == 2, why
    orders, _ = t.execute(plan)
    kinds = sorted((o["k"], o["kind"]) for o in orders)
    assert kinds == [(1, "limit"), (2, "market"), (3, "market")], kinds
    assert [o.price_open for o in f.orders_get()] == [4400.0]
    lot_cfg = {**cfg, "fixed_total_lot": 0.025}        # половинный лот: позиции TP1 (лимитка) и TP2 (08.10), БУ — у TP2
    plan, why = Trader(lot_cfg, f).prepare(4, sig)
    assert [k for k, _ in plan["positions"]] == [1, 2] and plan["be_k"] == 2, (plan["positions"], plan["be_k"])
    cfg["risk"]["after_tp1"] = "skip"
    assert Trader(cfg, f).prepare(2, sig)[0] is None
    f.bid = 4415.0                                     # прошла и TP2 — остаётся только TP3
    cfg["risk"]["after_tp1"] = "rest"
    plan, why = Trader(cfg, f).prepare(3, sig)
    assert [k for k, _ in plan["positions"]] == [3], plan["positions"]


def test_entry_modes():
    """Лесенка (своя точка у каждой позиции), откат (лимитка подтягивается), запасной вход по рынку."""
    from fake_mt5 import FakeMT5
    from trader import Trader, pullback_price
    cfg = {"magic": 770077, "lot_table": [[200, .01], [900, .05]], "risk": {"entry_mode": "ladder"},
           "symbols": {"GOLD": {"enabled": True, "candidates": ["GOLD"], "entry_tolerance": 2.0, "max_spread": 1.0,
                                "max_slippage": 0.5, "be_offset": 0.3, "min_sl_gap": 1.0}}}
    sig = parse_signal("GOLD BUY\n4398-4402\nTP1: 4405\nTP2: 4410\nTP3: 4420\nSL: 4380")
    f = FakeMT5(1000); f.bid = 4401.0                  # ask 4401.15: в диапазоне, ниже верхнего края
    t = Trader(cfg, f)
    plan, why = t.prepare(1, sig)
    orders, _ = t.execute(plan)
    kinds = sorted((o["k"], o["kind"], o["price"]) for o in orders)
    assert kinds == [(1, "market", 4401.15), (2, "limit", 4400.0), (3, "limit", 4398.0)], kinds
    f.tick(4399.5)                                     # дошли до середины — исполнилась TP2
    assert len(f.positions_get()) == 2 and len(f.orders_get()) == 1
    assert pullback_price("BUY", 4405.0, 2, 4398, 4402) == 4402.0      # не выше верхнего края
    assert pullback_price("BUY", 4401.0, 2, 4398, 4402) == 4399.0
    assert pullback_price("SELL", 4399.0, 2, 4398, 4402) == 4401.0
    assert pullback_price("BUY", 4399.0, 2, 4398, 4402) == 4398.0      # не ниже нижнего края
    cfg["risk"].update(entry_mode="pullback", pullback_usd=2)
    f = FakeMT5(1000); f.bid = 4400.85                 # ask 4401.0 → BUY LIMIT 4399
    t = Trader(cfg, f)
    plan, why = t.prepare(2, sig)
    assert plan["action"] == "limit" and plan["price"] == 4399.0 and plan["trail"]["ext"] == 4401.0, why
    orders, _ = t.execute(plan)
    rec = {"id": 2, "side": "BUY", "symbol_key": "GOLD", "orders": orders}
    assert t.move_pending(rec, 4400.5) == 3 and all(o.price_open == 4400.5 for o in f.orders_get())
    assert t.move_pending(rec, 4400.0) == 0            # вниз не двигаем
    f.bid = 4401.5
    res, new = t.pending_to_market(rec)                # запасной вход: лимитки → рынок
    assert not f.orders_get() and len(f.positions_get()) == 3 and all(o["kind"] == "market" for o in new), res
    # ответ куратора 06.10 на примере #1313 (BUY 4158–4155, TP1 4161)
    cfg["risk"] = {"entry_mode": "curator", "curator_near_usd": 1.0}
    s2 = parse_signal("TRADE GOLD BUY 4158-4155\nTP1: 4161\nTP2: 4164\nTP3: 4170\nSL: 4140")
    for bid, ks in ((4156.0, [1, 2, 3]), (4158.5, [1, 2, 3]), (4152.0, [1, 2, 3]), (4160.0, [2, 3])):
        f = FakeMT5(1000); f.bid = bid
        plan, why = Trader(cfg, f).prepare(5, s2)
        assert plan["action"] == "market" and [k for k, _ in plan["positions"]] == ks and not plan["be_k"], (bid, why)
    cfg["risk"] = {"entry_level": 0.2, "below_range": "market"}
    cfg["risk"] = {"entry_level": 0.2, "sl_rule": "cap_tp1"}   # стоп не дальше TP1 (по умолчанию выключено)
    f = FakeMT5(1000); f.bid = 4155.5
    plan, why = Trader(cfg, f).prepare(9, s2)
    assert plan["sl_k"] == {1: 4150.2, 2: 4150.2, 3: 4150.2}, plan["sl_k"]   # лимитка 4155.6, TP1 4161
    cfg["risk"]["sl_rule"] = "skip_tp1"
    assert Trader(cfg, f).prepare(10, s2)[0] is None
    cfg["risk"] = {"entry_level": 0.2, "below_range": "market"}
    f = FakeMT5(1000); f.bid = 4152.0                  # ниже диапазона: по рынку вместо пропуска
    assert Trader(cfg, f).prepare(6, s2)[0]["action"] == "market"
    s3 = parse_signal("GOLD SELL\n4155-4158\nTP1: 4150\nTP2: 4145\nTP3: 4140\nSL: 4170")
    f = FakeMT5(1000); f.bid = 4161.0                  # SELL зеркально: цена выше диапазона — по рынку
    plan, why = Trader(cfg, f).prepare(7, s3)
    assert plan["action"] == "market" and len(plan["positions"]) == 3, why
    f.bid = 4169.5                                     # вплотную к стопу — не входим
    assert Trader(cfg, f).prepare(8, s3)[0] is None


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


def test_btc_virtual():
    """Биткоин FxPro: брокер не принимает стоп/тейк/лимитку ближе $200 (trade_stops_level 20000) — держим сами."""
    from fake_mt5 import FakeMT5
    from trader import Trader
    cfg = {"magic": 770077, "lot_table": [[200, .01], [900, .05]], "risk": {"entry_level_buy": 0.5},
           "symbols": {"BTC": {"enabled": True, "candidates": ["BITCOIN"], "entry_tolerance": 50, "max_spread": 60,
                               "max_slippage": 30, "be_offset": 20, "min_sl_gap": 50, "risk_pct": 4}}}
    sig = parse_signal("BTC BUY\n\n📍 83280 - 83100\n\n✅TP1: 83380\n✅TP2: 83490\n✅TP3: 83750\n\n❗️ SL: 82700")   # #1345
    assert sig and sig.symbol == "BTC" and sig.zone == (83100, 83280) and sig.sl == 82700, sig

    def fake(bid):
        f = FakeMT5(1000); f.syms["BITCOIN"] = {"contract": 1.0, "stops": 20000}; f.spread = 0.0; f.bid = bid
        return f
    # 1) точка 50% = 83190, цена 83185 — по рынку; TP1 83380 всего в $195 — брокеру не отдаём, держим сами
    f = fake(83185.0); t = Trader(cfg, f)
    plan, why = t.prepare(1345, sig)
    assert plan["action"] == "market" and [v for _, v in plan["positions"]] == [0.02, 0.02, 0.02], why   # 4% от $1000 / $490
    orders, rep = t.execute(plan)
    assert len(f.pos) == 3 and not f.fails, (f.fails, rep)
    tp = {o["k"]: o.get("vtp") for o in orders}
    assert tp == {1: 83380, 2: None, 3: None}, tp
    assert sorted(p.tp for p in f.pos.values()) == [0.0, 83490.0, 83750.0]
    rec = {"id": 1345, "side": "BUY", "symbol_key": "BTC", "symbol": "BITCOIN", "orders": orders}
    f.tick(83381.0)
    msgs = t.virtual_tick(rec)
    assert len(f.pos) == 2 and "TP1" in msgs[0], msgs                        # TP1 закрыли сами
    res = t.move_to_be(rec)                                                  # БУ 83205 — в $176 от цены: держим сами
    assert len(f.pos) == 2 and all("держу сам" in r for r in res), res
    assert all(p.sl == 82700 for p in f.pos.values())                        # у брокера остаётся исходный стоп
    f.tick(83300.0); assert not t.virtual_tick(rec)
    f.tick(83204.0)
    msgs = t.virtual_tick(rec)
    assert not f.pos and len(msgs) == 2 and all("стоп 83205" in m for m in msgs), msgs
    assert f.balance > 1000
    # 2) точка 20% = 83136, цена 83200 — лимитка в $64 от цены брокер не примет: ждём уровень сами
    cfg["risk"]["entry_level_buy"] = 0.2
    f = fake(83200.0); t = Trader(cfg, f)
    plan, why = t.prepare(1346, sig)
    assert plan["action"] == "limit" and plan["price"] == 83136, why
    orders, rep = t.execute(plan)
    assert not f.ord and not f.pos and all(o["kind"] == "vlimit" for o in orders) and "держу сам" in rep, rep
    rec = {"id": 1346, "side": "BUY", "symbol_key": "BTC", "symbol": "BITCOIN", "orders": orders}
    assert len(t.pending_of(rec)) == 3
    f.tick(83150.0); assert not t.virtual_tick(rec) and not f.pos
    f.tick(83130.0)
    msgs = t.virtual_tick(rec)
    assert len(f.pos) == 3 and not t.pending_of(rec) and len(msgs) == 3, msgs
    assert sorted(p.tp for p in f.pos.values()) == [83380.0, 83490.0, 83750.0]   # от 83130 все тейки дальше $200
    # 3) снятие виртуальных лимиток (цена дошла до TP1 без нас)
    f = fake(83200.0); t = Trader(cfg, f)
    orders, _ = t.execute(t.prepare(1347, sig)[0])
    rec = {"id": 1347, "side": "BUY", "symbol_key": "BTC", "symbol": "BITCOIN", "orders": orders}
    assert len(t.cancel_pending(rec)) == 3 and not t.pending_of(rec)
    f.tick(83100.0); assert not t.virtual_tick(rec) and not f.pos
    # 4) безубыток можно поставить, а TP2 у брокера уже ближе $200 к цене — брокер отклонит всё изменение:
    #    тейк снимаем у брокера и держим сами, стоп ставим
    s2 = parse_signal("BTC BUY\n82950-83050\nTP1: 83300\nTP2: 83420\nTP3: 83700\nSL: 82500")
    f = fake(82960.0); t = Trader(cfg, f)                                   # точка 20% = 82970 — по рынку
    orders, rep = t.execute(t.prepare(1348, s2)[0])
    assert len(f.pos) == 3 and not any(o.get("vtp") for o in orders), rep    # от 82960 все тейки дальше $200
    rec = {"id": 1348, "side": "BUY", "symbol_key": "BTC", "symbol": "BITCOIN", "orders": orders}
    f.tick(83301.0)                                                          # TP1 у брокера
    res = t.move_to_be(rec)
    assert len(f.pos) == 2 and not f.fails, (res, f.fails)
    assert all(p.sl == 82980 for p in f.pos.values()), [(p.sl, p.tp) for p in f.pos.values()]
    assert sorted(p.tp for p in f.pos.values()) == [0.0, 83700.0] and t._odict(rec, [p for p in f.pos.values() if not p.tp][0])["vtp"] == 83420
    f.tick(83421.0); msgs = t.virtual_tick(rec)
    assert len(f.pos) == 1 and "TP2" in msgs[0], msgs
    # 5) золото не меняется: дистанции нет — всё у брокера
    g = FakeMT5(1000); g.bid = 4400.0
    assert Trader({"symbols": {"GOLD": {"candidates": ["GOLD"]}}}, g)._fit(g.symbol_info("GOLD"), True, 4400, 4399.5, 4400.4) == (4399.5, 4400.4, None, None)


if __name__ == "__main__":
    for f in (test_signals, test_commands, test_entry, test_lots, test_channel_priority, test_after_tp1, test_entry_modes, test_guard,
              test_btc_virtual):
        f()
    print("OK")
