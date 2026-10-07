"""
Выгрузка истории свечей золота из MT5 на сервере (для бэктестов в tools/).

Запуск на сервере (MT5 и мост fx-bridge должны работать):
    /home/fx/copier/venv/bin/python tools/export_history.py [папка]
Пишет gold_M1.csv, gold_M5.csv, gold_M15.csv, gold_M30.csv:
    time (время сервера FxPro, секунды), open, high, low, close (bid), spread (пункты, 0.01$)

В терминале «Макс. баров в окне» = 100 000, поэтому берём 99 000 последних свечей:
M1 ≈ 3.5 мес., M5 ≈ 1.4 года, M15 ≈ 4 года, M30 (60 000) ≈ 5 лет.
"""
import os
import sys

import rpyc

out = sys.argv[1] if len(sys.argv) > 1 else "."
c = rpyc.classic.connect("127.0.0.1", 18812)
c._config["sync_request_timeout"] = 300
c.execute("import MetaTrader5 as mt5")
sym = "GOLD"
c.eval(f"mt5.symbol_select({sym!r}, True)")
for tf, n in (("M1", 99000), ("M5", 99000), ("M15", 99000), ("M30", 60000)):
    s = str(c.eval(
        f"(lambda r: '' if r is None else '\\n'.join('%d,%.2f,%.2f,%.2f,%.2f,%d' % "
        f"(x['time'], x['open'], x['high'], x['low'], x['close'], x['spread']) for x in r))"
        f"(mt5.copy_rates_from_pos({sym!r}, mt5.TIMEFRAME_{tf}, 0, {n}))"))
    path = os.path.join(out, f"gold_{tf}.csv")
    with open(path, "w") as f:
        f.write(s + "\n")
    lines = s.split("\n") if s else []
    print(tf, len(lines), "свечей →", path)
