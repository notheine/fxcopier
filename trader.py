"""
Торговая часть: расчёт лота, решение о входе, ордера, безубыток, закрытия.
Вся работа с MT5 идёт через MT5Link (mt5link.py).
"""
import math
import time
import logging

log = logging.getLogger("trader")

OK_CODES = {10008, 10009, 10010, 10025}   # PLACED, DONE, DONE_PARTIAL, NO_CHANGES (уже так и стоит)
RETRY_CODES = {10004, 10020, 10021}       # REQUOTE, PRICE_CHANGED, PRICE_OFF


# ---------------------------------------------------------------- чистая логика

def lot_from_table(balance: float, table: list) -> float:
    """Таблица куратора: [[мин_баланс, лот], ...] по возрастанию."""
    lot = 0.0
    for min_bal, l in table:
        if balance >= min_bal:
            lot = l
    return lot


def plan_positions(total: float, tp_keys: list, vmin: float, vstep: float):
    """
    Делим общий лот на позиции по тейкам (до трёх).
    Возвращает [(номер_тейка, объём), ...].
    """
    ks = [k for k in (1, 2, 3) if k in tp_keys] or sorted(tp_keys)[:3]
    eps = 1e-9
    per = math.floor(total / len(ks) / vstep + eps) * vstep
    if per >= vmin - eps:
        return [(k, round(per, 8)) for k in ks]
    m = int(total / vmin + eps)               # сколько минимальных позиций влезает
    if m >= 2 and len(ks) >= 2:
        return [(ks[0], vmin), (ks[-1], vmin)]
    if m >= 1:
        return [(ks[min(1, len(ks) - 1)], vmin)]
    return []


def decide_entry(sig, bid: float, ask: float, tol: float, min_sl_gap: float):
    """
    Решение о входе.
    Возвращает (действие, цена, пояснение), действие: market | limit | skip.
    """
    lo, hi = sig.zone
    tp1 = sig.tps[min(sig.tps)]
    buy = sig.side == "BUY"
    price = ask if buy else bid
    s = 1 if buy else -1
    if s * (price - sig.sl) <= 0:
        return "skip", None, f"цена {price} уже за стопом {sig.sl}"
    if s * (price - tp1) >= 0:
        return "skip", None, f"цена {price} уже дошла до TP1 {tp1}"
    if abs(price - sig.sl) <= min_sl_gap:
        return "skip", None, f"цена {price} слишком близко к стопу {sig.sl}"
    in_zone = lo - tol <= price <= hi + tol
    # «хорошая» сторона зоны — ближе к тейкам (для BUY выше зоны, для SELL ниже)
    edge = hi if buy else lo
    beyond_to_tp = s * (price - edge) > 0 and not in_zone
    if sig.is_limit:
        if s * (price - edge) > 0:
            return "limit", edge, "лимитный ордер по сигналу"
        if in_zone:
            return "market", price, "цена уже в зоне лимитки"
        return "skip", None, f"цена {price} ушла за зону {lo:g}–{hi:g} в сторону стопа"
    if in_zone:
        return "market", price, "цена в зоне входа"
    if beyond_to_tp:
        return "limit", edge, f"цена {price} ушла от зоны к тейкам, ждём отката к {edge:g}"
    return "skip", None, f"цена {price} ушла за зону {lo:g}–{hi:g} в сторону стопа — не вхожу"


# ---------------------------------------------------------------- работа с MT5

class Trader:
    def __init__(self, cfg: dict, link):
        self.cfg = cfg
        self.mt5 = link
        self.magic = int(cfg.get("magic", 770077))
        self._symbols = {}

    # ---- подключение
    def connect(self):
        c = self.cfg["mt5"]
        ok = self.mt5.initialize(path=c.get("terminal_path"), login=int(c["login"]) if c.get("login") else None,
                                 password=c.get("password"), server=c.get("server"), timeout=60000,
                                 portable=bool(c.get("portable", False)))
        if not ok:
            raise RuntimeError(f"MT5 initialize не удался: {self.mt5.last_error()}")
        acc = self.mt5.account_info()
        if acc is None:
            raise RuntimeError(f"нет данных счёта: {self.mt5.last_error()}")
        is_demo = acc.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO
        mode = self.cfg.get("mode", "dry_run")
        if mode == "demo" and not is_demo:
            raise RuntimeError("В config.yaml режим demo, но подключён РЕАЛЬНЫЙ счёт. Остановлено.")
        if mode == "live" and not self.cfg.get("i_understand_live_trading"):
            raise RuntimeError("Режим live требует i_understand_live_trading: true в config.yaml")
        return acc, is_demo

    def healthy(self) -> bool:
        try:
            ti = self.mt5.terminal_info()
            return bool(ti and ti.connected)
        except Exception:
            return False

    def trade_allowed(self) -> bool:
        ti = self.mt5.terminal_info()
        return bool(ti and ti.trade_allowed)

    # ---- инструменты
    def symbol(self, key: str):
        """Имя инструмента у брокера по ключу из сигнала (GOLD → XAUUSD и т.п.)."""
        if key in self._symbols:
            return self._symbols[key]
        sc = self.cfg["symbols"].get(key)
        if not sc or not sc.get("enabled", True):
            return None
        for name in sc.get("candidates", [key]):
            info = self.mt5.symbol_info(name)
            if info is not None:
                self.mt5.symbol_select(name, True)
                self._symbols[key] = name
                return name
        raise RuntimeError(f"инструмент {key} не найден у брокера (пробовали {sc.get('candidates')})")

    def scfg(self, key):
        return self.cfg["symbols"][key]

    def _norm(self, info, price):
        return round(float(price), int(info.digits))

    def _filling(self, info):
        fm = int(info.filling_mode)
        if fm & 1:
            return self.mt5.ORDER_FILLING_FOK
        if fm & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, req: dict, retries=2):
        res = None
        for i in range(retries + 1):
            res = self.mt5.order_send(req)
            code = res.retcode if res is not None else None
            if code in OK_CODES:
                return True, res, ""
            if code in RETRY_CODES and req.get("action") == self.mt5.TRADE_ACTION_DEAL and i < retries:
                tick = self.mt5.symbol_info_tick(req["symbol"])
                req["price"] = tick.ask if req["type"] == self.mt5.ORDER_TYPE_BUY else tick.bid
                continue
            break
        err = f"код {res.retcode} {res.comment}" if res is not None else f"нет ответа {self.mt5.last_error()}"
        return False, res, err

    # ---- счёт
    def account(self):
        return self.mt5.account_info()

    def lot_total(self, sig):
        acc = self.mt5.account_info()
        fixed = self.cfg.get("fixed_total_lot")
        total = float(fixed) if fixed else lot_from_table(acc.balance, self.cfg["lot_table"])
        reduce = sig.reduce or (sig.risky and self.cfg.get("halve_on_risky", True))
        if reduce:
            total *= 0.5
        return total, reduce

    # ---- открытие
    def prepare(self, sid: int, sig, max_lot: float = None):
        """
        План сделки без отправки ордеров: (plan | None, текст).
        plan содержит всё для проверки риска и исполнения.
        """
        key = sig.symbol
        name = self.symbol(key)
        if name is None:
            return None, f"инструмент {key} отключён в настройках — пропускаю"
        sc = self.scfg(key)
        info = self.mt5.symbol_info(name)
        tick = self.mt5.symbol_info_tick(name)
        if tick is None or tick.bid <= 0:
            return None, f"нет котировок {name} (рынок закрыт?) — пропускаю"
        spread = tick.ask - tick.bid
        if spread > sc.get("max_spread", 1e9):
            return None, f"спред {spread:.2f} больше допустимого {sc['max_spread']} — пропускаю"

        stops_gap = max(int(info.trade_stops_level), 0) * info.point
        min_gap = max(stops_gap, spread * 2, sc.get("min_sl_gap", 0))
        action, price, why = decide_entry(sig, tick.bid, tick.ask, sc.get("entry_tolerance", 0), min_gap)
        if action == "skip":
            return None, f"не вхожу: {why}"

        total, reduced = self.lot_total(sig)
        table_lot = total
        if max_lot is not None:
            total = min(total, max_lot)
        positions = plan_positions(total, list(sig.tps), info.volume_min, info.volume_step)
        if not positions:
            return None, f"лот {total:.3f} меньше минимального {info.volume_min} — пропускаю"
        lot = round(sum(v for _, v in positions), 8)
        sl_dist = abs(price - sig.sl)
        risk = self.money(info, lot, sl_dist)
        margin = None
        try:
            mtype = self.mt5.ORDER_TYPE_BUY if sig.side == "BUY" else self.mt5.ORDER_TYPE_SELL
            margin = self.mt5.order_calc_margin(mtype, name, float(lot), float(price))
        except Exception:
            pass
        lots = " + ".join(f"{v:g}" for _, v in positions)
        head = (f"{'ВХОД ПО РЫНКУ' if action == 'market' else 'ЛИМИТНЫЙ ОРДЕР'} {name} {sig.side} "
                f"@ {price:g} ({why}); лот {lots}" + (" (уменьшен вдвое)" if reduced else "")
                + (" (уменьшен защитой по вашему решению)" if max_lot is not None else ""))
        plan = {"sid": sid, "sig": sig, "name": name, "info": info, "sc": sc, "action": action, "price": price,
                "positions": positions, "lot": lot, "table_lot": table_lot, "sl_distance": sl_dist,
                "risk": risk, "margin": margin, "head": head}
        return plan, head

    def execute(self, plan):
        """Отправляет ордера по плану. Возвращает (список {ticket,k,kind,...}, отчёт)."""
        sig, name, info, sc, action, price = (plan[k] for k in ("sig", "name", "info", "sc", "action", "price"))
        sid = plan["sid"]
        buy = sig.side == "BUY"
        out, errs = [], []
        for k, vol in plan["positions"]:
            req = {
                "symbol": name,
                "volume": float(vol),
                "sl": self._norm(info, sig.sl),
                "tp": self._norm(info, sig.tps[k]),
                "magic": self.magic,
                "comment": f"WW{sid}-{k}",
                "type_time": self.mt5.ORDER_TIME_GTC,
            }
            if action == "market":
                t = self.mt5.symbol_info_tick(name)
                req.update({
                    "action": self.mt5.TRADE_ACTION_DEAL,
                    "type": self.mt5.ORDER_TYPE_BUY if buy else self.mt5.ORDER_TYPE_SELL,
                    "price": t.ask if buy else t.bid,
                    "deviation": int(round(sc.get("max_slippage", 0.5) / info.point)),
                    "type_filling": self._filling(info),
                })
            else:
                req.update({
                    "action": self.mt5.TRADE_ACTION_PENDING,
                    "type": self.mt5.ORDER_TYPE_BUY_LIMIT if buy else self.mt5.ORDER_TYPE_SELL_LIMIT,
                    "price": self._norm(info, price),
                    "type_filling": self.mt5.ORDER_FILLING_RETURN,
                })
            ok, res, err = self._send(req)
            if ok:
                out.append({"ticket": int(res.order), "k": k, "kind": action, "volume": vol,
                            "price": float(res.price or req["price"])})
            else:
                errs.append(f"TP{k}: {err}")
        rep = plan["head"]
        if out and action == "market":
            rep += "\nИсполнено: " + ", ".join(f"TP{o['k']} {o['volume']:g} @ {o['price']:g}" for o in out)
        if errs:
            rep += "\n⚠️ Ошибки: " + "; ".join(errs)
        return out, rep

    def open_signal(self, sid: int, sig, dry_run=False):
        """Подготовить и сразу исполнить (без проверки риска). Возвращает (ордера, отчёт)."""
        plan, text = self.prepare(sid, sig)
        if plan is None:
            return [], text
        if dry_run:
            return [], "[тест, без ордеров] " + text
        return self.execute(plan)

    # ---- деньги и риск
    def money(self, info, volume, distance):
        """Сколько денег = движение цены на distance при объёме volume."""
        ts, tv = float(getattr(info, "trade_tick_size", 0) or 0), float(getattr(info, "trade_tick_value", 0) or 0)
        if ts > 0 and tv > 0:
            return volume * distance / ts * tv
        return volume * distance * float(getattr(info, "trade_contract_size", 100) or 100)

    def open_risk(self):
        """Сколько потеряем, если все открытые позиции копировщика закроются по стопу (БУ = 0)."""
        total = 0.0
        for p in self.mt5.positions_get():
            if p.magic != self.magic or not p.sl:
                continue
            s = 1 if p.type == self.mt5.POSITION_TYPE_BUY else -1
            dist = s * (p.price_open - p.sl)
            if dist > 0:
                total += self.money(self.mt5.symbol_info(p.symbol), p.volume, dist)
        return total

    # ---- поиск своих позиций/ордеров
    def positions_of(self, rec):
        tickets = {o["ticket"] for o in rec.get("orders", [])}
        pref = f"WW{rec['id']}-"
        res = []
        for p in self.mt5.positions_get():
            if p.magic == self.magic and (int(p.identifier) in tickets or str(p.comment).startswith(pref)):
                res.append(p)
        return res

    def pending_of(self, rec):
        tickets = {o["ticket"] for o in rec.get("orders", [])}
        pref = f"WW{rec['id']}-"
        return [o for o in self.mt5.orders_get()
                if o.magic == self.magic and (int(o.ticket) in tickets or str(o.comment).startswith(pref))]

    def k_of(self, rec, ticket_or_identifier, comment=""):
        for o in rec.get("orders", []):
            if o["ticket"] == int(ticket_or_identifier):
                return o["k"]
        try:
            return int(str(comment).split("-")[-1])
        except ValueError:
            return None

    # ---- действия по позициям
    def close_position(self, p):
        info = self.mt5.symbol_info(p.symbol)
        t = self.mt5.symbol_info_tick(p.symbol)
        is_buy = p.type == self.mt5.POSITION_TYPE_BUY
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": float(p.volume),
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "position": int(p.ticket), "price": t.bid if is_buy else t.ask,
            "deviation": int(round(1.0 / info.point)), "magic": self.magic,
            "comment": "close", "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info),
        }
        return self._send(req)

    def modify_position(self, p, sl=None, tp=None):
        info = self.mt5.symbol_info(p.symbol)
        req = {"action": self.mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": int(p.ticket),
               "sl": self._norm(info, sl if sl is not None else p.sl),
               "tp": self._norm(info, tp if tp is not None else p.tp), "magic": self.magic}
        return self._send(req)

    def modify_order(self, o, price=None, sl=None, tp=None):
        info = self.mt5.symbol_info(o.symbol)
        req = {"action": self.mt5.TRADE_ACTION_MODIFY, "order": int(o.ticket), "symbol": o.symbol,
               "price": self._norm(info, price if price is not None else o.price_open),
               "sl": self._norm(info, sl if sl is not None else o.sl),
               "tp": self._norm(info, tp if tp is not None else o.tp),
               "type_time": self.mt5.ORDER_TIME_GTC}
        return self._send(req)

    def cancel_order(self, o):
        return self._send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": int(o.ticket)})

    def close_all(self, rec):
        msgs = []
        for o in self.pending_of(rec):
            ok, _, err = self.cancel_order(o)
            msgs.append(f"лимитка #{o.ticket} {'снята' if ok else 'НЕ снята: ' + err}")
        for p in self.positions_of(rec):
            ok, _, err = self.close_position(p)
            msgs.append(f"позиция #{p.ticket} {'закрыта' if ok else 'НЕ закрыта: ' + err} (P/L {p.profit:+.2f})")
        return msgs

    def cancel_pending(self, rec):
        msgs = []
        for o in self.pending_of(rec):
            ok, _, err = self.cancel_order(o)
            msgs.append(f"лимитка #{o.ticket} {'снята' if ok else 'НЕ снята: ' + err}")
        return msgs

    def _valid_sl(self, p, sl):
        """Можно ли поставить такой стоп при текущей цене."""
        info = self.mt5.symbol_info(p.symbol)
        t = self.mt5.symbol_info_tick(p.symbol)
        gap = max(int(info.trade_stops_level), 0) * info.point
        if p.type == self.mt5.POSITION_TYPE_BUY:
            return sl < t.bid - gap
        return sl > t.ask + gap

    def set_sl(self, rec, price, close_if_invalid=False):
        msgs = []
        for p in self.positions_of(rec):
            if self._valid_sl(p, price):
                ok, _, err = self.modify_position(p, sl=price)
                msgs.append(f"#{p.ticket}: стоп → {price:g}" + ("" if ok else f" НЕ удалось: {err}"))
            elif close_if_invalid:
                ok, _, err = self.close_position(p)
                msgs.append(f"#{p.ticket}: цена уже за уровнем {price:g} — закрыта по рынку"
                            + ("" if ok else f" НЕ удалось: {err}"))
            else:
                msgs.append(f"#{p.ticket}: стоп {price:g} поставить нельзя (цена уже за ним)")
        for o in self.pending_of(rec):
            ok, _, err = self.modify_order(o, sl=price)
            msgs.append(f"лимитка #{o.ticket}: стоп → {price:g}" + ("" if ok else f" НЕ удалось: {err}"))
        return msgs

    def move_to_be(self, rec):
        """Стоп в безубыток (+ небольшой запас) для всех оставшихся позиций сигнала."""
        msgs = []
        off = self.scfg(rec["symbol_key"]).get("be_offset", 0.0)
        for p in self.positions_of(rec):
            is_buy = p.type == self.mt5.POSITION_TYPE_BUY
            info = self.mt5.symbol_info(p.symbol)
            be = round(p.price_open + (off if is_buy else -off), int(info.digits))
            eps = info.point / 2
            if (is_buy and p.sl >= be - eps) or (not is_buy and 0 < p.sl <= be + eps):
                continue  # уже в безубытке или лучше
            if self._valid_sl(p, be):
                ok, _, err = self.modify_position(p, sl=be)
                msgs.append(f"#{p.ticket}: стоп в безубыток {be:g}" + ("" if ok else f" НЕ удалось: {err}"))
            else:
                ok, _, err = self.close_position(p)
                msgs.append(f"#{p.ticket}: цена уже у точки входа — закрыта по рынку ({p.profit:+.2f})"
                            + ("" if ok else f" НЕ удалось: {err}"))
        return msgs

    def set_tp(self, rec, k, price):
        msgs = []
        for p in self.positions_of(rec):
            if self.k_of(rec, p.identifier, p.comment) == k:
                ok, _, err = self.modify_position(p, tp=price)
                msgs.append(f"#{p.ticket}: TP{k} → {price:g}" + ("" if ok else f" НЕ удалось: {err}"))
        for o in self.pending_of(rec):
            if self.k_of(rec, o.ticket, o.comment) == k:
                ok, _, err = self.modify_order(o, tp=price)
                msgs.append(f"лимитка #{o.ticket}: TP{k} → {price:g}" + ("" if ok else f" НЕ удалось: {err}"))
        if not msgs:
            msgs.append(f"позиции под TP{k} нет — ничего не меняю")
        return msgs

    def price(self, symbol_name):
        t = self.mt5.symbol_info_tick(symbol_name)
        return t.bid, t.ask

    def floating(self):
        return sum(p.profit for p in self.mt5.positions_get() if p.magic == self.magic)

    def all_own(self):
        pos = [p for p in self.mt5.positions_get() if p.magic == self.magic]
        orders = [o for o in self.mt5.orders_get() if o.magic == self.magic]
        return pos, orders
