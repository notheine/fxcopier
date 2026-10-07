"""
Тренд-робот (по просьбе владельца 08.10.2026): уведомление об «очевидном тренде» с кнопкой,
по кнопке — первая тактика из обсуждения 07.10 («лесенка»).

Когда уведомляет (свечи M30, только что закрытая свеча):
- цена закрылась ниже минимума (выше максимума) за последние сутки (window_bars = 48 свечей);
- ход от вершины (дна) суток длится не меньше 3 часов (min_bars) — не одна новостная свеча;
- самый большой отскок внутри хода не больше 30% хода (max_bounce) — «почти без отскоков»;
- ни одна свеча не дала больше 40% хода (max_candle) — это не новостной обвал;
- ход не меньше min_move_atr × ATR и не меньше min_move_usd.

Что делает по кнопке (или по команде /trend buy|sell):
1. Вход по рынку по ходу тренда.
2. Стоп за последним откатом (крайняя цена последних swing_bars свечей M30 ± stop_buffer_usd),
   не ближе min_stop_usd и не дальше max_stop_atr × ATR. Это расстояние = R.
3. Лот — чтобы при стопе потерять risk_pct % баланса.
4. Цена прошла ещё R в нашу сторону → стопы прошлых позиций минимум в безубыток (+be_offset_usd)
   и новая позиция тем же лотом со стопом R (не больше max_positions).
5. Тейков нет: общий скользящий стоп trail_r × R от лучшей цены (только подтягивается).
6. Новых серий не открываю в окна no_entry (данные США), в пятницу вечером, в выходные,
   после max_losses_day убыточных серий за день. В пятницу friday_close — закрываю всё.

Стопы всех позиций стоят у брокера; доливки и подтяжку стопа делает программа.
Свои позиции — по magic (770078), позиции копировщика (770077) не трогает.
На реальном счёте не работает, пока в config.yaml нет trend.allow_live: true.

На истории (docs/RESEARCH.md, разделы 1 и 3): сама «лесенка» по сигналу «3 свечи подряд» — на уровне
случайных входов; детектор «сильнейший ход за сутки без отскоков» — слабый плюс. Это эксперимент на демо.
"""
import json
import logging
import math
import os
import time

log = logging.getLogger("trend")

DEFAULTS = {
    "enabled": True,
    "allow_live": False,        # на реальном счёте — только если явно разрешено
    "magic": 770078,
    "symbol": "GOLD",
    "notify": True,             # присылать уведомления (/trend off — выключить)
    # детектор «очевидного тренда»
    "window_bars": 48,          # сутки свечей M30
    "min_bars": 6,              # ход не короче 3 часов
    "max_bounce": 0.30,         # отскок внутри хода ≤ 30% хода
    "max_candle": 0.40,         # одна свеча ≤ 40% хода (иначе это новостной обвал, а не тренд)
    "min_move_atr": 5.0,
    "min_move_usd": 15.0,
    "realert_min": 240,         # в ту же сторону — не чаще раза в 4 часа
    "alert_ttl_min": 15,        # кнопка действует 15 минут
    # тактика
    "risk_pct": 1.0,
    "min_stop_usd": 5.0,
    "max_stop_atr": 2.0,
    "swing_bars": 3,
    "stop_buffer_usd": 0.5,
    "add_step_r": 1.0,
    "trail_r": 2.0,
    "max_positions": 4,
    "be_offset_usd": 0.3,
    "max_losses_day": 2,
    "no_entry": ["15:00-16:00"],   # МСК: большинство данных США выходит в 15:30
    "no_entry_friday_after": "21:00",
    "friday_close": "23:30",
}


# ------------------------------------------------------------------ расчёты (без MT5)

def atr_series(bars, n=14):
    out, a, prev = [], None, None
    for t, o, h, lo, c in bars:
        tr = h - lo if prev is None else max(h - lo, abs(h - prev), abs(lo - prev))
        a = tr if a is None else a + (tr - a) / n
        out.append(a)
        prev = c
    return out


def detect(bars, p):
    """Очевидный тренд на только что закрытой свече. bars — закрытые свечи (t, o, h, l, c), старые первыми.
    Возвращает словарь (d = +1 рост / −1 падение, ход, отскок, …) или None."""
    W = int(p["window_bars"])
    if len(bars) < W + 20:
        return None
    a = atr_series(bars)
    j = len(bars) - 1
    prev = bars[j - W: j]
    c = bars[j][4]
    if c < min(b[3] for b in prev):
        d = -1
        s = max(range(j - W, j + 1), key=lambda k: bars[k][2])
        start = bars[s][2]
    elif c > max(b[2] for b in prev):
        d = 1
        s = min(range(j - W, j + 1), key=lambda k: bars[k][3])
        start = bars[s][3]
    else:
        return None
    leg = abs(c - start)
    if j - s < p["min_bars"] or leg < max(p["min_move_atr"] * a[max(s - 1, 0)], p["min_move_usd"]):
        return None
    mb, ext, big = 0.0, start, 0.0
    for k in range(s + 1, j + 1):
        b = bars[k]
        mb = max(mb, (b[2] - ext) if d < 0 else (ext - b[3]))
        ext = min(ext, b[3]) if d < 0 else max(ext, b[2])
        big = max(big, d * (b[4] - b[1]))
    if mb > p["max_bounce"] * leg or big > p["max_candle"] * leg:
        return None
    return {"d": d, "leg": round(leg, 2), "bounce": round(mb, 2), "start_px": start, "start_t": bars[s][0],
            "price": c, "hours": (bars[j][0] + 1800 - bars[s][0]) / 3600, "atr": a[j], "t": bars[j][0]}


def stop_distance(bars, d, entry, atr_v, p):
    """R: до крайней цены последних swing_bars свечей (+ запас), в пределах [min_stop_usd, max_stop_atr × ATR]."""
    sw = bars[-int(p["swing_bars"]):]
    if d < 0:
        dist = max(b[2] for b in sw) + p["stop_buffer_usd"] - entry
    else:
        dist = entry - (min(b[3] for b in sw) - p["stop_buffer_usd"])
    hi = max(p["min_stop_usd"], p["max_stop_atr"] * atr_v)
    return round(min(max(dist, p["min_stop_usd"]), hi), 2)


def in_windows(now, windows):
    hm = now.hour * 60 + now.minute
    for w in windows or []:
        a, b = w.split("-")
        ha, ma = map(int, a.split(":"))
        hb, mb = map(int, b.split(":"))
        if ha * 60 + ma <= hm < hb * 60 + mb:
            return w
    return None


def _hm(s):
    h, m = map(int, s.split(":"))
    return h, m


# ------------------------------------------------------------------ робот

class TrendBot:
    def __init__(self, cfg, trader, notify, now, make_buttons=None,
                 state_path="trend_state.json", journal="trend_journal.jsonl"):
        self.cfg = cfg
        self.p = dict(DEFAULTS)
        self.p.update(cfg.get("trend") or {})
        self.tr = trader
        self.mt5 = trader.mt5
        self.notify = notify
        self.now = now
        self.make_buttons = make_buttons or (lambda aid, d: None)
        self.state_path, self.journal = state_path, journal
        self.s = {"series": None, "alert": None, "last_bar": 0, "last_alert": {}, "losses": {},
                  "muted": not self.p["notify"], "seq": 0}
        if os.path.exists(state_path):
            try:
                self.s.update(json.load(open(state_path, encoding="utf-8")))
            except ValueError:
                log.warning("trend_state.json испорчен — начинаю с чистого")

    # ---- служебное
    def save(self):
        tmp = self.state_path + ".tmp"
        json.dump(self.s, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_path)

    @property
    def magic(self):
        return int(self.p["magic"])

    def sym(self):
        return self.tr.symbol(self.p["symbol"])

    def mode_problem(self):
        """Почему робот не может открывать сделки (None — может)."""
        if not self.p["enabled"]:
            return "тренд-робот выключен в config.yaml"
        mode = self.cfg.get("mode", "dry_run")
        if mode == "dry_run":
            return "режим dry_run — сделки не открываю"
        if mode == "live" and not self.p["allow_live"]:
            return "реальный счёт — тренд-робот работает только на демо (trend.allow_live)"
        return None

    def today(self):
        return self.now().strftime("%Y-%m-%d")

    def blocked(self):
        """Причина, по которой новую серию открыть нельзя, или None."""
        why = self.mode_problem()
        if why:
            return why
        if self.s["series"]:
            return "уже идёт тренд-серия"
        n = self.now()
        if n.weekday() >= 5:
            return "выходные, рынок закрыт"
        if n.weekday() == 4 and (n.hour, n.minute) >= _hm(self.p["no_entry_friday_after"]):
            return "пятница вечер — новые серии не открываю"
        w = in_windows(n, self.p["no_entry"])
        if w:
            return f"время данных США ({w} МСК) — не вхожу"
        if self.s["losses"].get(self.today(), 0) >= self.p["max_losses_day"]:
            return f"уже {self.p['max_losses_day']} убыточные серии сегодня — до завтра не вхожу"
        return None

    def bars(self, n=200):
        return self.mt5.rates(self.sym(), "M30", n, 1)

    # ---- план серии
    def plan(self, d):
        sym = self.sym()
        info = self.mt5.symbol_info(sym)
        tick = self.mt5.symbol_info_tick(sym)
        bars = self.bars(60)
        a = atr_series(bars)[-1] if bars else 5.0
        entry = tick.ask if d > 0 else tick.bid
        R = stop_distance(bars, d, entry, a, self.p) if bars else self.p["min_stop_usd"]
        bal = self.tr.account().balance
        risk = bal * self.p["risk_pct"] / 100
        per_lot = self.tr.money(info, 1.0, R)
        step, vmin = float(info.volume_step), float(info.volume_min)
        lot = math.floor(risk / per_lot / step + 1e-9) * step if per_lot > 0 else 0
        note = ""
        if lot < vmin:
            if self.tr.money(info, vmin, R) <= 2 * risk:
                lot, note = vmin, " (минимальный лот, риск чуть больше)"
            else:
                return None, f"даже минимальный лот {vmin:g} даёт риск больше {2 * self.p['risk_pct']:g}% — не вхожу"
        lot = round(lot, 2)
        return {"d": d, "entry": entry, "R": R, "lot": lot, "risk": round(self.tr.money(info, lot, R), 2),
                "sl": round(entry - d * R, 2), "note": note, "bal": bal}, ""

    # ---- уведомления
    async def scan(self):
        last = self.mt5.rates(self.sym(), "M30", 1, 1)       # дёшево: только время последней закрытой свечи
        if not last or last[-1][0] == self.s["last_bar"]:
            return
        bars = self.bars()
        if not bars:
            return
        self.s["last_bar"] = bars[-1][0]
        self.save()
        a = self.s.get("alert")
        if a and time.time() - a["created"] > self.p["alert_ttl_min"] * 60:
            self.s["alert"] = None
            self.save()
        if self.s["muted"] or self.s["series"]:
            return
        sig = detect(bars, self.p)
        if not sig:
            return
        la = self.s["last_alert"].get(str(sig["d"]))
        if la and time.time() - la < self.p["realert_min"] * 60:
            return
        why = self.blocked()
        if why and not why.startswith("режим dry_run"):
            log.info("тренд %s, но уведомление не шлю: %s", sig["d"], why)
            return
        plan, err = self.plan(sig["d"])
        self.s["seq"] += 1
        aid = self.s["seq"]
        self.s["last_alert"][str(sig["d"])] = time.time()
        self.s["alert"] = {"id": aid, "d": sig["d"], "created": time.time(), "price": plan["entry"] if plan else sig["price"],
                           "R": plan["R"] if plan else 0}
        self.save()
        down = sig["d"] < 0
        head = "📉 Золото уверенно падает" if down else "📈 Золото уверенно растёт"
        txt = (f"{head}\n\n"
               f"С {sig['start_px']:.0f} до {sig['price']:.0f} за {sig['hours']:.0f} ч, "
               f"отскоки не больше ${sig['bounce']:.0f}\n\n")
        if plan:
            txt += (f"Предлагаю {'продажу' if down else 'покупку'} по тактике «лесенка»:\n"
                    f"вход по рынку ~{plan['entry']:.2f}\n"
                    f"стоп {plan['sl']:.2f} (${plan['R']:g}), лот {plan['lot']:g} — риск ${plan['risk']:.0f}{plan['note']}\n"
                    f"доливка каждые ${plan['R'] * self.p['add_step_r']:g}, стопы тянутся за ценой\n\n"
                    f"Кнопка действует {self.p['alert_ttl_min']} мин")
            if why:
                txt += f"\n⚠️ {why}"
            await self.notify(txt, buttons=self.make_buttons(aid, sig["d"]))
        else:
            await self.notify(txt + f"Не предлагаю: {err}")

    async def on_button(self, action, aid):
        a = self.s.get("alert")
        if not a or str(a["id"]) != str(aid):
            return "Уже неактуально"
        self.s["alert"] = None
        self.save()
        if action == "no":
            await self.notify("Тренд: пропустил.")
            return "Пропущено"
        if time.time() - a["created"] > self.p["alert_ttl_min"] * 60:
            await self.notify(f"⌛️ Прошло больше {self.p['alert_ttl_min']} мин — уведомление устарело, не вхожу.")
            return "Устарело"
        tick = self.mt5.symbol_info_tick(self.sym())
        cur = tick.ask if a["d"] > 0 else tick.bid
        moved = a["d"] * (cur - a["price"])
        if a["R"] and abs(moved) > a["R"]:
            await self.notify(f"Цена ушла на ${moved:+.1f} от уведомления (больше стопа ${a['R']:g}) — не вхожу.")
            return "Цена ушла"
        return await self.start(a["d"], "по кнопке")

    # ---- серия
    def _req(self, d, lot, px, sl, k, sid):
        sym = self.sym()
        info = self.mt5.symbol_info(sym)
        return {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(lot),
                "type": self.mt5.ORDER_TYPE_BUY if d > 0 else self.mt5.ORDER_TYPE_SELL,
                "price": px, "sl": round(sl, int(info.digits)), "tp": 0.0,
                "deviation": int(round(1.0 / info.point)), "magic": self.magic,
                "comment": f"TR{sid}-{k}", "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.tr._filling(info)}

    def _open(self, d, lot, R, k, sid):
        tick = self.mt5.symbol_info_tick(self.sym())
        px = tick.ask if d > 0 else tick.bid
        ok, res, err = self.tr._send(self._req(d, lot, px, px - d * R, k, sid))
        fill = float(getattr(res, "price", 0) or px) if ok else px
        return ok, (int(res.order) if ok else 0), err, fill

    async def start(self, d, src):
        why = self.blocked()
        if why:
            await self.notify(f"Тренд: не вхожу — {why}.")
            return why
        plan, err = self.plan(d)
        if not plan:
            await self.notify(f"Тренд: не вхожу — {err}.")
            return "Не вхожу"
        self.s["seq"] += 1
        sid = self.s["seq"]
        ok, ticket, err, fill = self._open(d, plan["lot"], plan["R"], 1, sid)
        if not ok:
            await self.notify(f"⚠️ Тренд: не удалось открыть сделку: {err}")
            return "Ошибка"
        tick = self.mt5.symbol_info_tick(self.sym())
        self.s["series"] = {"id": sid, "d": d, "R": plan["R"], "lot": plan["lot"], "opened": time.time(),
                            "ext": tick.bid, "next_add": round(fill + d * self.p["add_step_r"] * plan["R"], 2),
                            "adds_ok": True, "n": 1, "n_open": 1, "tickets": [ticket], "src": src}
        self.save()
        word = "Продал" if d < 0 else "Купил"
        await self.notify(f"{'📉' if d < 0 else '📈'} Тренд-серия #{sid} ({src})\n\n"
                          f"{word} {plan['lot']:g} по {fill:.2f}\n"
                          f"Стоп {fill - d * plan['R']:.2f} (${plan['R']:g}), риск ${plan['risk']:.0f}{plan['note']}\n\n"
                          f"Доливка при {self.s['series']['next_add']:.2f}, всего до {self.p['max_positions']} позиций\n"
                          f"Тейков нет, стоп тянется за ценой на ${self.p['trail_r'] * plan['R']:g}")
        return "Открываю"

    def positions(self):
        ser = self.s["series"]
        if not ser:
            return []
        pref = f"TR{ser['id']}-"
        tickets = set(ser["tickets"])
        return [p for p in self.mt5.positions_get()
                if p.magic == self.magic and (str(p.comment).startswith(pref) or int(p.identifier) in tickets)]

    def _set_sl(self, p, level, d, bid, ask):
        """Только подтягиваем стоп; не ближе $0.2 к цене; шаг не меньше $0.1."""
        cur = float(p.sl or 0)
        if d > 0:
            new = round(min(level, bid - 0.2), 2)
            if cur and new < cur + 0.1:
                return False
        else:
            new = round(max(level, ask + 0.2), 2)
            if cur and new > cur - 0.1:
                return False
        ok, _, err = self.tr.modify_position(p, sl=new)
        if not ok:
            log.warning("тренд: стоп #%s → %s не принят: %s", p.ticket, new, err)
        return ok

    def _pnl(self, ser):
        try:
            deals = self.mt5.history_deals_get(ser["opened"] - 86400, time.time() + 86400)
            ids = set(ser["tickets"])
            return round(sum(x.profit + x.commission + x.swap for x in deals if int(x.position_id) in ids), 2)
        except Exception as e:
            log.warning("тренд: итог серии не посчитан: %s", e)
            return 0.0

    async def _finish(self, ser, why):
        pnl = self._pnl(ser)
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": ser["id"], "side": "BUY" if ser["d"] > 0 else "SELL", "opened": ser["opened"],
                                "closed": time.time(), "pnl": pnl, "positions": ser["n"], "src": ser.get("src")},
                               ensure_ascii=False) + "\n")
        if pnl < 0:
            day = self.today()
            self.s["losses"] = {day: self.s["losses"].get(day, 0) + 1}
        self.s["series"] = None
        self.save()
        await self.notify(f"🏁 Тренд-серия #{ser['id']} закрыта ({why})\n\n"
                          f"Позиций было: {ser['n']}\nИтог: {pnl:+.2f} $")

    async def manage(self):
        ser = self.s["series"]
        if not ser:
            return
        pos = self.positions()
        if not pos:
            if time.time() - ser["opened"] > 10:
                await self._finish(ser, "стоп")
            return
        n = self.now()
        if n.weekday() == 4 and (n.hour, n.minute) >= _hm(self.p["friday_close"]):
            self.close_all()
            await self._finish(ser, "пятница, закрываю на выходные")
            return
        d, R = ser["d"], ser["R"]
        tick = self.mt5.symbol_info_tick(self.sym())
        bid, ask = tick.bid, tick.ask
        changed = False
        if len(pos) < ser["n_open"]:
            ser["adds_ok"] = False            # одна из позиций закрылась по стопу — больше не доливаем
        ser["n_open"] = len(pos)
        if d * (bid - ser["ext"]) > 0:
            ser["ext"] = bid
            changed = True
        hit = (ask >= ser["next_add"]) if d > 0 else (bid <= ser["next_add"])
        if ser["adds_ok"] and ser["n"] < self.p["max_positions"] and hit:
            for p in pos:
                self._set_sl(p, p.price_open + d * self.p["be_offset_usd"], d, bid, ask)
            k = ser["n"] + 1
            ok, ticket, err, fill = self._open(d, ser["lot"], R, k, ser["id"])
            if ok:
                ser["n"] = k
                ser["n_open"] += 1
                ser["tickets"].append(ticket)
                ser["next_add"] = round(ser["next_add"] + d * self.p["add_step_r"] * R, 2)
                more = (f"следующая при {ser['next_add']:.2f}" if k < self.p["max_positions"]
                        else "это последняя доливка")
                await self.notify(f"➕ Тренд-серия #{ser['id']}: доливка {k} — {ser['lot']:g} по {fill:.2f}, "
                                  f"стоп {fill - d * R:.2f}\n\nСтопы прошлых позиций — в безубыток, {more}")
            else:
                ser["adds_ok"] = False
                await self.notify(f"⚠️ Тренд-серия #{ser['id']}: доливка не прошла ({err}) — дальше без доливок")
            changed = True
            pos = self.positions()
        trail = ser["ext"] - self.p["trail_r"] * R if d > 0 else ser["ext"] + (ask - bid) + self.p["trail_r"] * R
        for p in pos:
            self._set_sl(p, trail, d, bid, ask)
        if changed:
            self.save()

    def close_all(self):
        lines = []
        for p in self.positions():
            ok, _, err = self.tr.close_position(p)
            lines.append(f"тренд #{p.ticket} {'закрыта' if ok else 'ошибка ' + err}")
        return lines

    async def tick(self):
        if not self.p["enabled"]:
            return
        await self.manage()
        await self.scan()

    # ---- команды и отчёты
    def status_text(self):
        ser = self.s["series"]
        L = [f"🤖 Тренд-робот: уведомления {'выключены (/trend on)' if self.s['muted'] else 'включены'}"]
        why = self.mode_problem()
        if why:
            L.append(f"⚠️ {why}")
        if ser:
            pos = self.positions()
            L.append(f"Серия #{ser['id']} {'продажа' if ser['d'] < 0 else 'покупка'}: позиций {len(pos)}, "
                     f"P/L {sum(p.profit for p in pos):+.2f}, стоп ${ser['R']:g}")
        else:
            L.append("Активной серии нет")
        lost = self.s["losses"].get(self.today(), 0)
        if lost:
            L.append(f"Убыточных серий сегодня: {lost} из {self.p['max_losses_day']}")
        return "\n".join(L)

    def rules_line(self):
        if not self.p["enabled"]:
            return ""
        return (f"• Тренд-робот: очевидный тренд за сутки → уведомление с кнопкой; по кнопке — «лесенка», "
                f"риск {self.p['risk_pct']:g}%, до {self.p['max_positions']} позиций (/trend)")

    def report_line(self, t0, t1):
        rows = []
        if os.path.exists(self.journal):
            for line in open(self.journal, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if t0 <= r["closed"] < t1:
                    rows.append(r)
        if not rows:
            return ""
        pnl = sum(r["pnl"] for r in rows)
        return f"🤖 Тренд-робот: серий {len(rows)}, в плюс {sum(r['pnl'] > 0 for r in rows)}, итог {pnl:+.2f}"

    async def command(self, arg):
        a = (arg or "").strip().lower()
        if a in ("sell", "продать", "продажа", "вниз"):
            await self.start(-1, "вручную")
        elif a in ("buy", "купить", "покупка", "вверх"):
            await self.start(1, "вручную")
        elif a in ("stop", "close", "закрыть", "стоп"):
            ser = self.s["series"]
            lines = self.close_all()
            if ser:
                await self._finish(ser, "закрыл по команде")
            await self.notify("Тренд: " + ("\n".join(lines) if lines else "открытых позиций нет"))
        elif a in ("off", "выкл"):
            self.s["muted"] = True
            self.save()
            await self.notify("🔕 Тренд: уведомления выключены. Включить: /trend on")
        elif a in ("on", "вкл"):
            self.s["muted"] = False
            self.save()
            await self.notify("🔔 Тренд: уведомления включены")
        else:
            await self.notify(self.status_text() + "\n\n"
                              "/trend sell, /trend buy — начать серию сейчас\n"
                              "/trend stop — закрыть серию\n"
                              "/trend off, /trend on — уведомления")
