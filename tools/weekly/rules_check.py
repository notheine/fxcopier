"""Еженедельная проверка правил (по просьбе владельца 14.10.2026): работают ли наши правила за последний месяц и неделю.

Запуск: systemd-таймер fx-weekly.timer (суббота 10:00 МСК) или вручную `fxctl check`. Работает на сервере от fx, nice 19.
1) Данные: сообщения канала — channel_log.jsonl копировщика (+ лог копировщика для пропусков; второе подключение
   к Telegram не делаем), свечи GOLD M1 — через мост MT5. Всё копится в /home/fx/data/.
2) Прогоны истории настоящим кодом (tools/backtest/run.sh → bt3.py) с 27.05 по сегодня, старт $1000:
   наши правила как сейчас, строго по куратору и наши правила без каждого своего правила по очереди (rule_variants).
3) Реальный демо-счёт по magic за неделю и месяц.
4) Короткий отчёт в группу (бот, Bot API) и файлы в /home/fx/data/weekly/ (ГГГГ-ММ-ДД.txt/.json, history.csv).
Ничего не меняет в настройках копировщика — только сообщает. Решение — за владельцем.
Новое наше правило → добавьте строку в rule_variants (как выключить его в прогоне)."""
import csv
import datetime as dt
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request

import yaml

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("FX_DATA", "/home/fx/data")
W = os.path.join(DATA, "weekly")
PY = os.environ.get("PY", os.path.join(APP, "venv", "bin", "python"))
START = dt.date(2026, 5, 27)          # начало истории канала в прогонах
BAL0 = 1000.0
SAME = 0.3                            # п.п. за месяц: разница меньше — «без разницы»
MAGICS = [(770077, "🔵 Наши"), (770079, "🟠 Куратор"), (770078, "🤖 Тренд")]
LOG_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ INFO main: канал #(\d+)(?: \(догнал опросом\))?: ?(.*)$")


# ------------------------------------------------------------------ расчёты (проверяются tests/test_weekly.py)

def metrics(eq, end_balance, today, start=START, bal0=BAL0):
    """eq — [(«ГГГГ-ММ-ДД», баланс на начало дня, эквити)] из прогона. Возвращает % : в среднем за месяц
    (сложный процент с начала истории), за последние 30 и 7 дней (от начала дня today−30 / today−7); и итоговый баланс."""
    months = max((today - start).days, 1) / 30.44
    avg = ((end_balance / bal0) ** (1 / months) - 1) * 100

    def ago(days):
        # баланс на начало дня (today − days): первая запись с этой даты и позже (выходные без сделок пропускаются)
        cut = today - dt.timedelta(days=days)
        b = next((bal for d, bal, _ in eq if dt.date.fromisoformat(d) >= cut), end_balance)
        return (end_balance / b - 1) * 100 if b else 0.0
    return {"end": round(end_balance, 2), "avg_month": round(avg, 1), "month": round(ago(30), 1), "week": round(ago(7), 1)}


def verdict(with_rule, without_rule, same=SAME):
    """Помогает ли правило за последний месяц: ✅ помогает, ⚠️ мешает, ➖ без разницы."""
    d = with_rule - without_rule
    return "✅" if d >= same else ("⚠️" if d <= -same else "➖")


def real_results(deals, balance_now, now_srv, days):
    """Реальный счёт за последние days дней. deals — [(время сервера, тип, magic, сумма с комиссией и свопом)].
    % — к балансу на начало периода (пополнения и снятия учтены)."""
    cut = now_srv - days * 86400
    period = [d for d in deals if d[0] >= cut]
    base = balance_now - sum(d[3] for d in period)
    out = {}
    for magic, _ in MAGICS:
        pnl = sum(d[3] for d in period if d[2] == magic and d[1] in (0, 1))
        out[magic] = (round(pnl, 2), round(pnl / base * 100, 1) if base > 0 else 0.0)
    return out


def pct(x):
    return f"{x:+.1f}%".replace("-", "−")


def rule_variants(cfg, elb, els):
    """Наши правила, которые проверяем: (ключ, название, переменные прогона без правила, как назвать «без»).
    Новое правило → новая строка здесь (и, если нужно, переменная в tools/backtest/bt3.py)."""
    mg, rk = cfg.get("management") or {}, cfg.get("risk") or {}
    out = []
    near = float(mg.get("be_near_tp1_usd", 0) or 0)
    if near and mg.get("auto_be_after_tp1", True):
        out.append(("benear", f"Ранний безубыток (за ${near:g} до TP1)", {"BENEAR": "0"}, "без него"))
    if rk.get("entry_mode", "limit") == "limit" and (abs(elb - 0.5) > 0.01 or abs(els - 0.5) > 0.01):
        out.append(("entry", f"Точка входа: покупки {elb * 100:.0f}%, продажи {els * 100:.0f}%",
                    {"ELB": "0.5", "ELS": "0.5"}, "середина 50%"))
    out.append(("pair", "Лот на 2 позиции → TP1 и TP2", {"PAIR": "13"}, "TP1 и TP3"))
    return out


def build_message(today, runs, rules, real, errors):
    """Текст отчёта в группу: короткие строки, пустая строка между блоками."""
    L = [f"🗓 Проверка правил — {today:%d.%m}", "(раз в неделю: работают ли правила за последний месяц)"]
    if real:
        L += ["", "💰 Реально на демо-счёте:"]
        for magic, label in MAGICS:
            w, m = real["week"][magic], real["month"][magic]
            L.append(f"{label}: неделя {pct(w[1])} · месяц {pct(m[1])}")
    L += ["", f"🧪 На истории канала (с {START:%d.%m}, $1000):"]
    for key, label in (("our", "🔵 Наши"), ("cur", "🟠 Куратор")):
        r = runs.get(key)
        if r:
            L.append(f"{label}: в среднем {pct(r['avg_month'])} в мес · месяц {pct(r['month'])} · неделя {pct(r['week'])}")
    bad = []
    if rules:
        L += ["", "🔎 Каждое наше правило (в скобках — если его убрать):"]
        our = runs.get("our")
        for key, name, _, alt in rules:
            r = runs.get(key)
            if not (r and our):
                continue
            v = verdict(our["month"], r["month"])
            word = {"✅": "за месяц помогает", "⚠️": "за месяц мешает", "➖": "за месяц без разницы"}[v]
            L.append(f"{v} {name} — {word}")
            L.append(f"   месяц {pct(our['month'])} ({alt} {pct(r['month'])}) · "
                     f"в среднем {pct(our['avg_month'])} ({pct(r['avg_month'])}) в мес")
            if v == "⚠️":
                bad.append(name)
    our, cur = runs.get("our"), runs.get("cur")
    L.append("")
    if bad:
        L.append("⚠️ За последний месяц мешает: " + "; ".join(bad) + ".")
        L.append("Ничего не меняю сам. Напишите Claude: «разбери проверку правил».")
    elif our and our["month"] < 0:
        L.append("⚠️ Наши правила за последний месяц в минусе. Напишите Claude: «разбери проверку правил».")
    elif our:
        L.append("✅ Наши правила за последний месяц работают — ничего не меняю.")
    if our and cur and cur["month"] > our["month"] + SAME:
        L.append(f"Куратор за месяц лучше наших на {cur['month'] - our['month']:.1f}%.")
    if errors:
        L += ["", "Не получилось: " + "; ".join(errors)]
    return "\n".join(L)


# ------------------------------------------------------------------ данные

def update_channel():
    """Все сообщения канала → DATA/channel_all.json (формат channel.json: id, ts UTC, text, reply)."""
    base = next(p for p in (os.path.join(DATA, f) for f in ("channel_all.json", "channel_upd.json", "channel.json"))
                if os.path.exists(p))
    msgs = {m["id"]: m for m in json.load(open(base, encoding="utf-8"))}
    # лог копировщика: только пропущенные сообщения (тексты до 300 знаков, без reply)
    for fn in sorted(glob.glob(os.path.join(APP, "logs", "copier.log*"))):
        for line in open(fn, encoding="utf-8", errors="replace"):
            m = LOG_RE.match(line.rstrip("\n"))
            if m and int(m.group(2)) not in msgs:
                ts = int(time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")))   # время сервера (UTC)
                msgs[int(m.group(2))] = {"id": int(m.group(2)), "ts": ts, "text": m.group(3).replace(" | ", "\n"),
                                         "reply": None, "src": "log"}
    # channel_log.jsonl копировщика: полные тексты и ответы — главнее
    p = os.path.join(APP, "channel_log.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("edit") and r["id"] in msgs:
                msgs[r["id"]]["text"] = r["text"]
            else:
                old = msgs.get(r["id"])
                msgs[r["id"]] = {k: r[k] for k in ("id", "ts", "text", "reply")}
                if old and old.get("src") != "log":
                    msgs[r["id"]]["ts"] = old["ts"]
    out = [msgs[k] for k in sorted(msgs)]
    tmp = os.path.join(DATA, "channel_all.json.tmp")
    json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, os.path.join(DATA, "channel_all.json"))
    return len(out), out[-1]["id"]


def bridge():
    import rpyc
    c = rpyc.classic.connect("127.0.0.1", 18812)
    c._config["sync_request_timeout"] = 120
    c.execute("import MetaTrader5 as M, time as T\nM.terminal_info() or M.initialize()")
    return c


def update_prices(c, sym="GOLD"):
    """Свечи GOLD M1 копятся в DATA/gold_m1_all.csv (терминал хранит M1 ~3.5 месяца)."""
    rows = {}
    for f in ("gold_m1.csv", "gold_m1_new.csv", "gold_m1_all.csv"):
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            for r in csv.reader(open(p)):
                if r:
                    rows[int(r[0])] = r
    t = max(rows) - 2 * 86400 if rows else int(time.time()) - 100 * 86400
    end = int(time.time()) + 2 * 86400
    while t < end:
        e = min(t + 5 * 86400, end)
        c.execute(f"r=M.copy_rates_range('{sym}',M.TIMEFRAME_M1,{t},{e})\n"
                  "S='' if r is None else '\\n'.join(f\"{int(x['time'])},{x['open']},{x['high']},{x['low']},"
                  "{x['close']},{int(x['spread'])}\" for x in r)")
        s = str(c.eval("S"))
        for line in s.split("\n") if s else []:
            r = line.split(",")
            rows[int(r[0])] = r
        t = e
    tmp = os.path.join(DATA, "gold_m1_all.csv.tmp")
    csv.writer(open(tmp, "w")).writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, os.path.join(DATA, "gold_m1_all.csv"))
    return len(rows), max(rows)


def account(c):
    """Сделки за 40 дней и баланс: ([(время сервера, тип, magic, сумма)], баланс, время сервера сейчас)."""
    c.execute("tk=M.symbol_info_tick('GOLD')\n"
              "OFF=round(((tk.time if tk else T.time())-T.time())/3600)*3600\n"
              "D=M.history_deals_get(int(T.time()+OFF-45*86400),int(T.time()+OFF+2*86400)) or ()\n"
              "S=';'.join('%d,%d,%d,%r' % (d.time,d.type,d.magic,float(d.profit+d.commission+d.swap+d.fee)) for d in D)\n"
              "A=M.account_info()\nAB=float(A.balance)")
    off = int(c.eval("OFF"))
    s = str(c.eval("S"))
    deals = [(int(a), int(b), int(m), float(v)) for a, b, m, v in (x.split(",") for x in s.split(";") if x)]
    return deals, float(c.eval("AB")), time.time() + off


def bt_config():
    """config.yaml для прогона: настройки как на сервере, без секретов."""
    cfg = yaml.safe_load(open(os.path.join(APP, "config.yaml"), encoding="utf-8"))
    t = cfg.get("telegram") or {}
    cfg["telegram"] = {"api_id": 1, "api_hash": "x", "channel": t.get("channel", "Win Win"), "forward_signals": False}
    cfg["mt5"] = {"login": 1, "password": "x", "server": "demo"}
    cfg["mode"], cfg["i_understand_live_trading"] = "demo", False
    os.makedirs(os.path.join(W, "bt"), exist_ok=True)
    yaml.safe_dump(cfg, open(os.path.join(W, "bt", "config.yaml"), "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
    return cfg


def run_bt(tag, env, today):
    end = today + dt.timedelta(days=1)
    args = ["nice", "-n", "19", "bash", os.path.join(APP, "tools", "backtest", "run.sh"), tag,
            f"START={START.year}-{START.month}-{START.day}", f"END={end.year}-{end.month}-{end.day}"]
    args += [f"{k}={v}" for k, v in env.items()]
    e = dict(os.environ, DATA=os.path.join(W, "bt"), OUT=os.path.join(W, "runs"), PY=PY)
    subprocess.run(args, env=e, check=True, timeout=3 * 3600, stdout=subprocess.DEVNULL)
    r = json.load(open(os.path.join(W, "runs", f"r_{tag}", f"bt_{tag}.json"), encoding="utf-8"))
    m = metrics(r["eq"], r["res"]["balance"], today)
    m.update(dd=r["res"]["max_dd_pct"], trades=r["res"]["trades"], errors=r["res"]["errors"])
    return m


def send(text):
    t = yaml.safe_load(open(os.path.join(APP, "config.yaml"), encoding="utf-8"))["telegram"]
    if not (t.get("bot_token") and t.get("report_chat")):
        print(text)
        return
    data = urllib.parse.urlencode({"chat_id": t["report_chat"], "text": text[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{t['bot_token']}/sendMessage", data, timeout=30).read()
    except Exception as e:                       # без адреса запроса — в нём токен бота
        raise RuntimeError(f"Bot API: {getattr(e, 'code', '')} {getattr(e, 'reason', type(e).__name__)}") from None


# ------------------------------------------------------------------ main

def main():
    os.makedirs(W, exist_ok=True)
    lock = open(os.path.join(W, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("проверка уже идёт")
        return
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
    errors, real = [], None
    t0 = time.time()
    n, last = update_channel()
    print(f"канал: {n} сообщений, последнее #{last}", flush=True)
    try:
        c = bridge()
        print("свечи:", update_prices(c), flush=True)
        deals, bal, now_srv = account(c)
        real = {"week": real_results(deals, bal, now_srv, 7), "month": real_results(deals, bal, now_srv, 30), "balance": bal}
    except Exception as e:
        errors.append(f"MT5: {e}")
        traceback.print_exc()
    subprocess.run([PY, os.path.join(APP, "tools", "backtest", "prepare_data.py"), os.path.join(DATA, "channel_all.json"),
                    os.path.join(DATA, "gold_m5.csv"), os.path.join(DATA, "gold_m1_all.csv"), os.path.join(W, "bt")], check=True)
    cfg = bt_config()
    st = json.load(open(os.path.join(APP, "state.json"), encoding="utf-8"))
    elb, els = float(st.get("entry_level_buy", 0.2)), float(st.get("entry_level_sell", 0.8))
    base = {"ELB": str(elb), "ELS": str(els)}
    rules = rule_variants(cfg, elb, els)
    plan = [("our", base), ("cur", dict(base, PROFILE="cur"))] + [(k, dict(base, **env)) for k, _, env, _ in rules]
    runs = {}
    for tag, env in plan:
        try:
            t1 = time.time()
            runs[tag] = run_bt(tag, env, today)
            print(tag, runs[tag], f"{(time.time() - t1) / 60:.0f} мин", flush=True)
        except Exception as e:
            errors.append(f"прогон {tag}: {type(e).__name__}")
            traceback.print_exc()
    msg = build_message(today, runs, rules, real, errors)
    stamp = f"{today:%Y-%m-%d}"
    open(os.path.join(W, f"{stamp}.txt"), "w", encoding="utf-8").write(msg + "\n")
    json.dump({"date": stamp, "runs": runs, "rules": [r[:2] + (r[3],) for r in rules], "real": real,
               "errors": errors, "minutes": round((time.time() - t0) / 60)},
              open(os.path.join(W, f"{stamp}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    hp = os.path.join(W, "history.csv")
    new = not os.path.exists(hp)
    with open(hp, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "run", "avg_month_pct", "month_pct", "week_pct", "end_balance", "max_dd_pct"])
        for tag, r in runs.items():
            w.writerow([stamp, tag, r["avg_month"], r["month"], r["week"], r["end"], r["dd"]])
    print(msg)
    if os.environ.get("NO_SEND") != "1":
        send(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        try:
            send(f"⚠️ Еженедельная проверка правил не удалась: {type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass
        sys.exit(1)
