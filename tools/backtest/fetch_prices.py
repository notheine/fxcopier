"""Выгрузка свечей из MT5 через мост (запускать на сервере от root или fx):
  /home/fx/copier/venv/bin/python fetch_prices.py GOLD 2026-05-20-0-0 2026-10-08-0-0 /home/fx/data/gold_m1.csv M1
Время в файле — время сервера FxPro (= МСК), записано как «UTC»-эпоха. Колонки: time,open,high,low,close,spread(пункты).
M1 терминал хранит ~3.5 месяца, для более ранних дат берите M5."""
import rpyc, datetime as dt, sys, csv
c = rpyc.classic.connect('127.0.0.1', 18812)
c.execute("import MetaTrader5 as M; M.initialize()")
sym = sys.argv[1]
a = dt.datetime(*map(int, sys.argv[2].split('-')), tzinfo=dt.timezone.utc)
b = dt.datetime(*map(int, sys.argv[3].split('-')), tzinfo=dt.timezone.utc)
w = csv.writer(open(sys.argv[4], 'w')); n = 0
t = a
while t < b:
    e = min(t + dt.timedelta(days=5), b)
    c.execute(f"r=M.copy_rates_range('{sym}',M.TIMEFRAME_{sys.argv[5]},{int(t.timestamp())},{int(e.timestamp())})\n"
              "S='' if r is None else '\\n'.join(f\"{int(x['time'])},{x['open']},{x['high']},{x['low']},{x['close']},{int(x['spread'])}\" for x in r)")
    s = str(c.eval("S"))
    if s:
        for line in s.split('\n'):
            w.writerow(line.split(',')); n += 1
    t = e
print('bars', n, c.eval("M.last_error()"))
