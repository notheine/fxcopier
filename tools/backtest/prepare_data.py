"""Готовит папку для прогонов из выгрузок сервера:
  python prepare_data.py <channel.json> <gold_m5.csv> <gold_m1.csv> [<gold_m1_старый.csv>] <папка>
- ch.json — сообщения канала (дата МСК строкой «дд.мм.гггг чч:мм:сс», как ждёт bt3.py);
- gold_hybrid.csv — M5 до начала M1, дальше M1 (7-я колонка — длина свечи в секундах).
config.yaml (настройки как на сервере, без секретов: telegram/mt5 — заглушки) кладётся в папку вручную."""
import csv, json, sys, os, datetime as dt
ch, m5, *m1s, out = sys.argv[1:]
os.makedirs(out, exist_ok=True)
H = json.load(open(ch))
json.dump([{"id": m["id"], "text": m["text"], "reply": m["reply"],
            "date": dt.datetime.fromtimestamp(m["ts"] + 3 * 3600, dt.timezone.utc).strftime("%d.%m.%Y %H:%M:%S")} for m in H],
          open(os.path.join(out, "ch.json"), "w"), ensure_ascii=False)
rows = {}
for r in csv.reader(open(m5)):
    rows[int(r[0])] = r[:6] + ["300"]
m1_start = None
for fn in m1s:                                  # несколько выгрузок M1 — склеиваем, поздние важнее
    for r in csv.reader(open(fn)):
        t = int(r[0]); rows[t] = r[:6] + ["60"]
        m1_start = t if m1_start is None else min(m1_start, t)
bars = [rows[t] for t in sorted(rows) if not (m1_start and t >= m1_start and rows[t][6] == "300")]
csv.writer(open(os.path.join(out, "gold_hybrid.csv"), "w")).writerows(bars)
print("messages", len(H), "bars", len(bars))
