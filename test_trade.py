"""
Проверка связки «сигнал → MT5» без Telegram. Работает ТОЛЬКО на демо-счёте.

  python test_trade.py open     — открыть позиции по последнему сигналу канала
  python test_trade.py status   — показать тестовые позиции
  python test_trade.py close    — закрыть тестовые позиции

Если цены в сигнале уже устарели (рынок ушёл), уровни сдвигаются к текущей цене
с теми же расстояниями до тейков и стопа — чтобы проверить вход по рынку.
"""
import json
import sys
import time

import yaml

from mt5link import MT5Link
from parser import parse_signal
from trader import Trader, decide_entry

# последний сигнал из канала (01.10.2026 16:30)
LAST_SIGNAL = """TRADE GOLD SELL
4167-4175
✅TP1: 4165
✅TP2: 4161
✅TP3: 4149
🔴SL: 4187"""

TEST_ID = 999001
STATE = "test_state.json"


def connect():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    link = MT5Link(cfg["mt5"].get("bridge_host", "127.0.0.1"), int(cfg["mt5"].get("bridge_port", 18812)))
    tr = Trader(cfg, link)
    acc, is_demo = tr.connect()
    print(f"Счёт {acc.login} на {acc.server}: {'ДЕМО' if is_demo else 'РЕАЛЬНЫЙ'}, баланс {acc.balance:.2f} {acc.currency}")
    if not is_demo:
        sys.exit("⛔️ Это реальный счёт — тест запускается только на демо. Остановлено.")
    ti = link.terminal_info()
    print(f"Терминал: подключён={ti.connected}, алготорговля разрешена={ti.trade_allowed}")
    return cfg, link, tr


def rec():
    return {"id": TEST_ID, "orders": json.load(open(STATE)) if _exists() else [], "symbol_key": "GOLD"}


def _exists():
    try:
        open(STATE).close()
        return True
    except OSError:
        return False


def show(tr):
    pos = tr.positions_of(rec())
    if not pos:
        print("Тестовых позиций нет.")
    for p in pos:
        print(f"  #{p.ticket} {p.symbol} {'BUY' if p.type == 0 else 'SELL'} {p.volume:g} @ {p.price_open:g} "
              f"SL {p.sl:g} TP {p.tp:g} | P/L {p.profit:+.2f}")
    return pos


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "open"
    cfg, link, tr = connect()

    if cmd == "status":
        show(tr)
        return
    if cmd == "close":
        for m in tr.close_all(rec()):
            print("  " + m)
        return

    sig = parse_signal(LAST_SIGNAL)
    print(f"\nПоследний сигнал: {sig.describe()}")
    name = tr.symbol(sig.symbol)
    info = link.symbol_info(name)
    tick = link.symbol_info_tick(name)
    print(f"Инструмент у брокера: {name}; сейчас bid {tick.bid} / ask {tick.ask}, спред {tick.ask - tick.bid:.2f}")
    sc = cfg["symbols"][sig.symbol]
    action, price, why = decide_entry(sig, tick.bid, tick.ask, sc["entry_tolerance"], sc.get("min_sl_gap", 1))
    print(f"Что сделал бы копировщик с этим сигналом сейчас: {action} — {why}")

    if action != "market":
        cur = tick.bid if sig.side == "SELL" else tick.ask
        d = round(cur - sig.mid, int(info.digits))
        sig.zone = (sig.zone[0] + d, sig.zone[1] + d)
        sig.tps = {k: round(v + d, int(info.digits)) for k, v in sig.tps.items()}
        sig.sl = round(sig.sl + d, int(info.digits))
        print(f"Цены сигнала устарели → для теста сдвигаю уровни на {d:+g}: {sig.describe()}")

    orders, report = tr.open_signal(TEST_ID, sig)
    print("\n" + report)
    if not orders:
        sys.exit("⚠️ Позиции не открылись — пришлите этот вывод.")
    json.dump(orders, open(STATE, "w"))

    time.sleep(2)
    print("\nПроверяю изменение тейка (TP3 дальше на 1$):")
    k3 = max(o["k"] for o in orders)
    new_tp = sig.tps[k3] + (1.0 if sig.side == "BUY" else -1.0)
    for m in tr.set_tp(rec(), k3, new_tp):
        print("  " + m)

    print("\nОткрытые тестовые позиции (их видно и в приложении MT5 на телефоне):")
    show(tr)
    print("\nЗакрыть: fxctl test-close   (или оставьте — сработают свои SL/TP)")


if __name__ == "__main__":
    main()
