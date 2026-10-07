"""
Копировщик сигналов: Telegram-канал → MetaTrader 5.

Запуск:  python main.py
Первый запуск спросит номер телефона и код из Telegram (вводится на сервере).
"""
import asyncio
import datetime as dt
import json
import logging
import os
import sys
from types import SimpleNamespace
import time
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import yaml
from telethon import Button, TelegramClient, events

import guard as G
from parser import parse_signal, parse_command, looks_like_signal
from mt5link import MT5Link
from trader import Trader, pullback_price

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
MSK = ZoneInfo(CFG.get("timezone", "Europe/Moscow"))
MODE = CFG.get("mode", "dry_run")
DRY = MODE == "dry_run"

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[RotatingFileHandler("logs/copier.log", maxBytes=5_000_000, backupCount=10, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


# ------------------------------------------------------------------ состояние

class State:
    def __init__(self, path="state.json"):
        self.path = path
        self.d = {"signals": {}, "msg2sig": {}, "paused": False, "day": "", "day_equity": 0.0,
                  "day_paused": False, "weekend_done": ""}
        if os.path.exists(path):
            self.d.update(json.load(open(path, encoding="utf-8")))

    def save(self):
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    @property
    def signals(self):
        return self.d["signals"]

    def active(self):
        return sorted((r for r in self.signals.values() if r["status"] == "active"),
                      key=lambda r: r["created"], reverse=True)


state = State()


def entry_level(side="BUY"):
    """Точка входа в диапазоне сигнала для BUY/SELL: 0 — лучший край, 1 — худший. Меняется командой /entry."""
    rk = CFG.setdefault("risk", {})
    key = f"entry_level_{side.lower()}"
    lvl = state.d.get(key, state.d.get("entry_level"))
    if lvl is None:
        lvl = rk.get(key, rk.get("entry_level", 0.5))
    rk[key] = float(lvl)
    return float(lvl)


def entry_level_text():
    b, s_ = entry_level("BUY"), entry_level("SELL")
    return (f"Точка входа: покупки {b * 100:.0f}%, продажи {s_ * 100:.0f}%\n"
            "(0% — лучший край диапазона, 100% — худший)")


def rules_text():
    """Включённые сейчас правила — по одному на строку (для сообщения при запуске и /status)."""
    rk = CFG.get("risk", {})
    mg = CFG.get("management", {})
    gold = CFG.get("symbols", {}).get("GOLD", {})
    L = ["📋 Включённые правила:"]
    mode = rk.get("entry_mode", "limit")
    if mode == "market":
        L.append("• Вход сразу по рынку, как пришёл сигнал")
    else:
        b, s_ = entry_level("BUY"), entry_level("SELL")
        L.append(f"• Точка входа: покупки {b * 100:.0f}%, продажи {s_ * 100:.0f}%")
        L.append("   (0% — лучший край диапазона, 100% — худший)")
        if mode != "limit":
            L.append(f"• Режим входа: {mode}")
        L.append(f"• Цена хуже точки входа → лимитка на точку, ждёт до {rk.get('pending_expiry_min', 240) // 60:g} ч")
        if float(rk.get("entry_fallback_min", 0) or 0):
            L.append(f"• Лимитка не исполнилась за {rk['entry_fallback_min']:g} мин → вход по рынку")
    below = rk.get("below_range") or ("stop" if rk.get("out_of_range_stop") else "skip")
    L.append({"market": "• Цена лучше диапазона → все три позиции по рынку (куратор)",
              "stop": "• Цена лучше диапазона → стоп-ордер на краю диапазона",
              "skip": "• Цена лучше диапазона → не вхожу"}.get(below, f"• below_range: {below}"))
    if rk.get("above_range") == "rest":
        L.append("• Цена хуже диапазона → позиции TP2 и TP3 по рынку, без TP1")
    after = rk.get("after_tp1", "skip")
    L.append({"skip": "• Цена уже прошла TP1 → не вхожу",
              "rest": "• Цена уже прошла TP1 → TP2 и TP3 по рынку (куратор)",
              "rest_limit": "• Цена уже прошла TP1 → TP2 и TP3 по рынку, TP1 лимиткой"}.get(after, f"• after_tp1: {after}"))
    if mg.get("auto_be_after_tp1", True):
        L.append(f"• Цена дошла до TP1 → стоп остальных позиций в безубыток (+${gold.get('be_offset', 0):g})")
    sl_rule = rk.get("sl_rule", "none") or "none"
    if sl_rule != "none":                                  # стоп/тейки и лот по таблице — само собой, не пишем
        L.append(f"• Стоп относительно тейков: {sl_rule}")
    if CFG.get("fixed_total_lot"):
        L.append(f"• Лот фиксированный {CFG['fixed_total_lot']:g}")
    elif not CFG.get("halve_on_risky", True):
        L.append("• «Занижаем риск» / RISKY — лот НЕ уменьшаю")
    btc = CFG.get("symbols", {}).get("BTC", {})
    if btc.get("enabled"):
        L.append(f"• Биткоин: лот — риск {btc.get('risk_pct', 0):g}% депозита до стопа; тейки, безубыток и лимитки "
                 "ближе $200 к цене (брокер их не принимает) держу сам")
    L.append(f"• Не больше {rk.get('max_active_signals', 2)} сигналов одновременно (куратор)")
    L.append(f"• Сигнал пришёл позже {rk.get('max_signal_age_sec', 120) // 60:g} мин → спрашиваю в чате "
             "(если TP1 или стоп уже задеты — не вхожу)")
    if rk.get("ask_risky_buy", True):
        L.append("• Покупка с длинным стопом («занижаем риск») → спрашиваю в чате")
    if float(rk.get("max_daily_loss_pct", 0) or 0) > 0:
        L.append(f"• Убыток за день больше {rk['max_daily_loss_pct']:g}% → новых входов до завтра нет")
    if CFG.get("weekend", {}).get("enabled"):
        L.append("• Пятница вечером → закрываю всё на выходные")
    if CFG.get("guard", {}).get("enabled", True):
        L.append("• Защита: опасная сделка → пауза и кнопки в группе")
    if state.d.get("paused"):
        L.append("⏸ Сейчас пауза: новые сигналы пропускаю (/resume)")
    return "\n".join(L)


link = None
trader = None
client = TelegramClient(os.path.join(BASE, "session"), int(CFG["telegram"]["api_id"]),
                        CFG["telegram"]["api_hash"], catch_up=True)
NOTIFY = CFG["telegram"].get("notify_chat", "me")
notify_peer = None   # сущность получателя отчётов (резолвится при старте)
BOT_TOKEN = CFG["telegram"].get("bot_token")
REPORT_CHAT = CFG["telegram"].get("report_chat")
OWNER_IDS = set(CFG["telegram"].get("owner_ids", []))
bot = TelegramClient(os.path.join(BASE, "bot_session"), int(CFG["telegram"]["api_id"]),
                     CFG["telegram"]["api_hash"]) if BOT_TOKEN and REPORT_CHAT else None
channel_entity = None
mt5_ok = False


async def notify(text: str, buttons=None, alt=""):
    """Отчёт в группу (от бота). buttons — кнопки; alt — текстовая подсказка, если бота нет."""
    log.info("NOTIFY: %s", text.replace("\n", " | "))
    try:
        if bot:
            await bot.send_message(REPORT_CHAT, text[:4000], buttons=buttons)
        elif buttons and alt:
            await client.send_message(notify_peer or NOTIFY, (text + "\n\n" + alt)[:4000])
        else:
            await client.send_message(notify_peer or NOTIFY, text[:4000])
    except Exception as e:
        log.error("не удалось отправить уведомление: %s", e)


def now_msk():
    return dt.datetime.now(MSK)


def entries_blocked(symbol_key=None):
    """Причина, по которой новые входы запрещены, или None. symbol_key — для выходных (биткоин торгуется и в них)."""
    h = state.d.get("hold")
    if h and h.get("type") in ("trade", "account") and not h.get("ask"):
        return "защита ждёт вашего решения (кнопки в группе)"
    if state.d["paused"]:
        return "копировщик на паузе (/resume чтобы продолжить)"
    if state.d["day_paused"]:
        return "достигнут дневной лимит убытка — новые входы до завтра остановлены"
    if not mt5_ok:
        return "нет связи с MT5"
    wk = CFG.get("weekend", {})
    n = now_msk()
    if wk.get("enabled", False) and n.weekday() == 4:
        hh, mm = map(int, wk.get("no_new_entries_after", "22:00").split(":"))
        if (n.hour, n.minute) >= (hh, mm):
            return "пятница вечер — новые сделки не открываю (на выходные не оставляем)"
    weekend_ok = CFG.get("symbols", {}).get(symbol_key or "", {}).get("weekend_trading", False)
    if n.weekday() >= 5 and not weekend_ok:
        return "выходные, рынок закрыт"
    mx = CFG["risk"].get("max_active_signals", 2)
    if len(state.active()) >= mx:
        return f"уже {mx} активных сигнала (лимит max_active_signals)"
    return None


# ------------------------------------------------------------------ сигналы

async def forward_signal(msg):
    """Пересылает оригинальный сигнал из канала получателю отчётов."""
    if not CFG["telegram"].get("forward_signals", True):
        return
    if bot:
        when = msg.date.astimezone(MSK).strftime("%H:%M:%S")
        title = getattr(channel_entity, "title", "канал")
        await notify(f"📨 Сигнал из канала «{title}» ({when} МСК):\n\n{(msg.message or '')[:3800]}")
        return
    try:
        await client.forward_messages(notify_peer or NOTIFY, msg)
    except Exception as e:
        log.info("переслать не вышло (%s) — отправляю копией", e)
        try:
            await client.send_message(notify_peer or NOTIFY, "📨 Сигнал из канала:\n" + (msg.message or "")[:3900])
        except Exception as e2:
            log.error("не удалось отправить копию сигнала: %s", e2)


async def handle_signal(msg, text, sig, late=False):
    sid = str(msg.id)
    rec = {"id": msg.id, "text": text, "created": time.time(), "symbol_key": sig.symbol, "side": sig.side,
           "zone": list(sig.zone), "tps": {str(k): v for k, v in sig.tps.items()}, "sl": sig.sl,
           "status": "skipped", "orders": [], "be_done": False, "limit_signal": sig.is_limit}
    head = f"📩 Сигнал #{msg.id}: {sig.describe()}"

    if not sig.valid:
        rec["status"] = "invalid"
        rec["reason"] = "ошибка в сигнале"
        state.signals[sid] = rec
        state.save()
        await notify(head + "\n⛔️ НЕ ВХОЖУ — в сигнале ошибка:\n• " + "\n• ".join(sig.errors)
                     + "\nЕсли канал исправит сообщение, я перепроверю.")
        return
    if sig.wait_confirm:
        rec["status"] = "waiting"
        rec["reason"] = "трейдер ещё не вошёл"
        state.signals[sid] = rec
        state.save()
        await notify(head + "\n⏸ Трейдер пишет, что ещё не вошёл — жду отдельного сигнала/подтверждения.")
        return
    age = time.time() - msg.date.timestamp()
    ask = []                       # причины спросить владельца кнопками (решение владельца 07.10)
    if age > CFG["risk"].get("max_signal_age_sec", 120) and (late or not sig.is_limit):
        # опоздание (сбой связи, перезапуск): если с публикации цена касалась TP1 или стопа — не входим
        # (условие куратора), иначе спрашиваем владельца
        why = late_signal_problem(sig, msg, age)
        if why:
            rec["reason"] = "опоздание сигнала"
            state.signals[sid] = rec
            state.save()
            await notify(head + f"\n\n⏭ Сигнал получен с опозданием {age / 60:.0f} мин: {why} — не вхожу.")
            return
        ask.append(f"сигнал получен с опозданием {age / 60:.0f} мин (TP1 и стоп с тех пор не задеты)")
    if CFG["risk"].get("ask_risky_buy", True) and sig.side == "BUY" and (sig.reduce or sig.risky):
        ask.append("покупка с длинным стопом («занижаем риск» / RISKY). На истории такие покупки: "
                   "14 раз, 7 в плюс и 7 в минус, итог −$106 (продажи с длинным стопом — 28 из 35 в плюс)")
    times = [t for t in state.d.get("sig_times", []) if time.time() - t < 3600]
    state.d["sig_times"] = times + [time.time()]
    why = entries_blocked(sig.symbol)
    if why:
        rec["reason"] = why.split(" —")[0].split(" (")[0]
        state.signals[sid] = rec
        state.save()
        await notify(head + f"\n⏭ Пропускаю: {why}")
        return

    entry_level(sig.side)          # настройка владельца (/entry) → CFG для trader.prepare
    try:
        plan, text_plan = trader.prepare(msg.id, sig)
    except Exception as e:
        log.exception("ошибка подготовки")
        plan, text_plan = None, f"⚠️ Ошибка: {e}"
    rec["reduced"] = bool(plan and plan.get("reduced"))
    if plan and plan.get("be_k"):
        rec["be_k"] = plan["be_k"]     # вошли после TP1: безубыток — когда цена дойдёт до нашего первого тейка
    if plan and plan.get("trail"):
        rec["trail"] = plan["trail"]   # режим pullback: лимитка подтягивается за ценой
    reasons = guard_trade(plan, times) if plan else []
    if plan and DRY:
        rec["reason"] = "тестовый режим"
        state.signals[sid] = rec
        state.save()
        extra = ("\n🛡 Защита остановила бы: " + "; ".join(reasons)) if reasons else ""
        await notify(head + "\nℹ️ [тест, без ордеров] " + text_plan + extra)
        return
    if plan and (reasons or ask) and state.d.get("hold"):
        rec["reason"] = "жду ответа по другому сигналу"
        state.signals[sid] = rec
        state.save()
        await notify(head + f"\n\n⏭ Нужно ваше решение ({'; '.join(reasons + ask)}), "
                            f"но я уже жду ответа по сигналу #{state.d['hold'].get('sid')} — этот пропускаю.")
        return
    if plan and reasons:
        rec["status"] = "held"
        rec["reason"] = "остановлено защитой"
        state.signals[sid] = rec
        state.d["hold"] = {"type": "trade", "sid": sid, "reasons": reasons + ask, "created": time.time()}
        state.save()
        await ask_trade(rec, plan, reasons + ask, head)
        return
    if plan and ask:
        rec["status"] = "held"
        rec["reason"] = "вопрос владельцу"
        state.signals[sid] = rec
        state.d["hold"] = {"type": "trade", "ask": True, "sid": sid, "reasons": ask, "created": time.time()}
        state.save()
        ttl = G.settings(CFG)["approval_ttl_min"]
        await notify(f"{head}\n\n❓ НУЖНО ВАШЕ РЕШЕНИЕ:\n• " + "\n• ".join(ask)
                     + f"\n\nЕсли входить: {plan['head']}"
                     + f"\n\nЖду ответа {ttl} мин. Нет ответа — пропускаю. Другие сигналы обрабатываю как обычно.",
                     buttons=[[Button.inline("✅ Входить", f"g:ok:{sid}".encode()),
                               Button.inline("❌ Пропустить", f"g:no:{sid}".encode())]],
                     alt="Ответьте: /approve — входить, /reject — пропустить")
        return
    if plan:
        orders, rep = trader.execute(plan)
    else:
        orders, rep = [], text_plan
    await finish_open(rec, sig, orders, rep, head)


def late_signal_problem(sig, msg, age):
    """Можно ли входить в пропущенный сигнал: не старше late_signal_max_min, TP1 и стоп с тех пор не задеты."""
    lim = CFG["risk"].get("late_signal_max_min", 240)
    if age > lim * 60:
        return f"старше {lim} мин"
    name = trader.symbol(sig.symbol)
    if not name:
        return "инструмент отключён"
    try:
        ext = trader.mt5.price_extremes(name, msg.date.timestamp(), time.time())
    except Exception as e:
        return f"не удалось проверить историю цен ({e})"
    if not ext:
        return "нет истории цен"
    hi, lo = ext
    tp1 = sig.tps[min(sig.tps)]
    if sig.side == "BUY":
        if hi >= tp1:
            return f"цена уже доходила до TP1 {tp1:g}"
        if lo <= sig.sl:
            return f"цена уже доходила до стопа {sig.sl:g}"
    else:
        if lo <= tp1:
            return f"цена уже доходила до TP1 {tp1:g}"
        if hi >= sig.sl:
            return f"цена уже доходила до стопа {sig.sl:g}"
    return None


async def finish_open(rec, sig, orders, rep, head):
    sid = str(rec["id"])
    if orders:
        rec["status"] = "active"
        rec["orders"] = orders
        rec["symbol"] = trader.symbol(sig.symbol)
        if any(o["kind"] in ("limit", "stop", "vlimit", "vstop") for o in orders):
            rec["pending_since"] = time.time()
            rec["pending_expiry_min"] = (CFG["risk"].get("limit_signal_expiry_min", 10080) if sig.is_limit
                                         else CFG["risk"].get("pending_expiry_min", 240))
    else:
        rec["reason"] = rep.replace("не вхожу: ", "").split(" — ")[0][:80]
    state.signals[sid] = rec
    state.save()
    await notify(head + "\n" + ("✅ " if orders else "ℹ️ ") + rep)


# ------------------------------------------------------------------ защита (второй слой)

def guard_trade(plan, recent_times):
    acc = trader.account()
    return G.check_trade(
        G.settings(CFG), symbol_key=plan["sig"].symbol, plan_risk=plan["risk"], plan_lot=plan["lot"],
        table_lot=plan["table_lot"], sl_distance=plan["sl_distance"], balance=acc.balance,
        free_margin=getattr(acc, "margin_free", 0.0), margin_needed=plan["margin"],
        open_risk=trader.open_risk(), recent_signals=recent_times)


def guard_buttons(kind, sid):
    if kind == "trade":
        return [[Button.inline("✅ Открыть как есть", f"g:ok:{sid}".encode()),
                 Button.inline("⚖️ С безопасным лотом", f"g:safe:{sid}".encode())],
                [Button.inline("❌ Пропустить", f"g:no:{sid}".encode())]]
    if kind == "account":
        return [[Button.inline("▶️ Продолжить торговлю", b"g:resume:0"),
                 Button.inline("⏸ Оставить на паузе", b"g:keep:0")]]
    if kind == "sl":
        return [[Button.inline("✅ Перенести стоп", f"g:slok:{sid}".encode()),
                 Button.inline("❌ Оставить прежний", f"g:slno:{sid}".encode())]]


async def ask_trade(rec, plan, reasons, head):
    acc = trader.account()
    ttl = G.settings(CFG)["approval_ttl_min"]
    await notify(
        f"{head}\n🛡 СДЕЛКА ОСТАНОВЛЕНА ЗАЩИТОЙ — выглядит опасной:\n• " + "\n• ".join(reasons)
        + f"\n\nПлан: {plan['head']}\nРиск при стопе: {plan['risk']:.2f} {acc.currency} из баланса {acc.balance:.2f}"
        + f"\n⏸ Новые сигналы на паузе до вашего решения. Разрешение действует {ttl} мин "
          f"(потом цена устареет — сделку не открою, просто продолжу работу).",
        buttons=guard_buttons("trade", rec["id"]),
        alt="Ответьте: /approve — открыть, /approve_safe — с безопасным лотом, /reject — пропустить")


async def guard_account_check():
    """Пауза по состоянию счёта: серия убытков, большая просадка. Плюс истечение вопросов по сделкам."""
    h = state.d.get("hold")
    if h and h.get("ask") and time.time() - h["created"] > G.settings(CFG)["approval_ttl_min"] * 60:
        rec = state.signals.get(str(h["sid"]))       # вопрос владельцу без ответа — просто пропускаем сигнал
        if rec:
            rec["status"], rec["reason"] = "skipped", "нет ответа на вопрос"
        state.d["hold"] = None
        state.save()
        await notify(f"⌛️ Ответа по сигналу #{h['sid']} не было {G.settings(CFG)['approval_ttl_min']} мин — пропускаю.")
        return
    if h and h["type"] == "trade" and time.time() - h["created"] > G.settings(CFG)["approval_ttl_min"] * 60:
        rec = state.signals.get(str(h["sid"]))
        if rec:
            rec["status"], rec["reason"] = "skipped", "нет ответа на вопрос защиты"
        state.d["hold"] = {"type": "account", "sid": 0, "created": time.time(),
                           "reasons": [f"не было ответа по сигналу #{h['sid']}"]}
        state.save()
        await notify(f"⌛️ Ответа по сигналу #{h['sid']} не было {G.settings(CFG)['approval_ttl_min']} мин — "
                     "сделку не открываю (цена устарела). Торговля на паузе до вашего решения.",
                     buttons=guard_buttons("account", 0), alt="Ответьте /resume — продолжить торговлю")
        return
    if h or not trader:
        return
    acc = trader.account()
    peak = max(state.d.get("peak_balance") or 0, acc.balance)
    if peak != state.d.get("peak_balance"):
        state.d["peak_balance"] = peak
        state.save()
    rows = journal_rows(0, 1e12)[state.d.get("losses_ack", 0):]
    reasons = G.check_account(G.settings(CFG), last_pnls=[r["pnl"] for r in rows], equity=acc.equity,
                              peak_balance=peak)
    if reasons:
        state.d["hold"] = {"type": "account", "sid": 0, "reasons": reasons, "created": time.time()}
        state.save()
        await notify("🛡 ЗАЩИТА: торговля на паузе\n• " + "\n• ".join(reasons)
                     + f"\nБаланс {acc.balance:.2f}, эквити {acc.equity:.2f}. Открытые сделки веду дальше "
                       "(стопы/тейки на месте). Новые сигналы — только после вашего решения.",
                     buttons=guard_buttons("account", 0),
                     alt="Ответьте /resume — продолжить торговлю")


async def resolve_hold(action, sid=""):
    """Решение владельца по кнопке или команде. Возвращает короткий ответ для всплывашки."""
    h = state.d.get("hold")
    if not h:
        return "Уже неактуально"
    g = G.settings(CFG)
    if h["type"] == "trade" and action in ("ok", "safe", "no"):
        if sid and str(sid) != str(h["sid"]):
            return "Это решение уже неактуально"
        rec = state.signals.get(str(h["sid"]))
        state.d["hold"] = None
        if action == "no" or rec is None:
            if rec:
                rec["status"], rec["reason"] = "skipped", "отклонено вами (защита)"
            state.save()
            await notify(f"❌ Сигнал #{h['sid']} пропущен по вашему решению. Продолжаю работу.")
            return "Пропущено"
        if time.time() - h["created"] > g["approval_ttl_min"] * 60:
            rec["status"], rec["reason"] = "skipped", "разрешение пришло поздно"
            state.save()
            await notify(f"⌛️ Прошло больше {g['approval_ttl_min']} мин — сигнал #{h['sid']} устарел, не открываю. "
                         "Продолжаю работу.")
            return "Устарело"
        sig = parse_signal(rec["text"], **CFG.get("sanity", {}))
        plan, text_plan = trader.prepare(int(h["sid"]), sig)
        if plan and action == "safe":
            limit = g["max_trade_risk_pct"] / 100 * trader.account().balance
            if plan["risk"] > limit:
                max_lot = plan["lot"] * limit / plan["risk"]
                plan, text_plan = trader.prepare(int(h["sid"]), sig, max_lot=max_lot)
                if plan is None:
                    text_plan = (f"даже минимальный лот даёт риск больше {g['max_trade_risk_pct']:g}% "
                                 f"баланса — не открываю")
        head = f"✅ Сигнал #{h['sid']} — открываю по вашему решению"
        if plan:
            orders, rep = trader.execute(plan)
        else:
            orders, rep = [], text_plan
        rec["status"] = "skipped"
        await finish_open(rec, sig, orders, rep, head)
        return "Выполняю"
    if h["type"] == "account" and action in ("resume", "keep"):
        if action == "keep":
            state.d["paused"] = True
        state.d["hold"] = None
        state.d["losses_ack"] = len(journal_rows(0, 1e12))
        state.d["peak_balance"] = trader.account().balance
        state.save()
        await notify("▶️ Продолжаю торговлю." if action == "resume" else
                     "⏸ Оставил на паузе. Продолжить: /resume")
        return "Принято"
    if h["type"] == "sl" and action in ("slok", "slno"):
        rec = state.signals.get(str(h["sid"]))
        state.d["hold"] = None
        state.save()
        if action == "slok" and rec:
            lines = trader.set_sl(rec, h["price"])
            await notify(f"✅ Сигнал #{h['sid']}: стоп перенесён на {h['price']:g}\n" + "\n".join("• " + l for l in lines))
        else:
            await notify(f"Сигнал #{h['sid']}: стоп оставил прежним.")
        return "Принято"
    return "Не подходит к текущему вопросу"


def resolve_target(msg):
    """К какому сигналу относится команда."""
    rid = msg.reply_to.reply_to_msg_id if msg.reply_to else None
    if rid:
        rid = str(rid)
        if rid in state.signals:
            return state.signals[rid], "ответ на сигнал"
        if rid in state.d["msg2sig"] and state.d["msg2sig"][rid] in state.signals:
            return state.signals[state.d["msg2sig"][rid]], "ответ в ветке сигнала"
    act = state.active()
    if not act:
        return None, "нет активных сигналов"
    note = "последний активный сигнал" + (f" (активных {len(act)}!)" if len(act) > 1 else "")
    return act[0], note


async def retry_skipped_on_tp_change(msg, cmds):
    """
    Канал сдвинул тейк у сигнала, который мы пропустили из-за «цена уже прошла TP1»
    (05.10 #1332 → «Отодвигаю первый TP на 4190» через 24 с). Пересматриваем вход с новыми тейками.
    """
    tp_cmds = [c for c in cmds if c.kind == "tp" and c.price]
    if not tp_cmds:
        return False
    rid = str(msg.reply_to.reply_to_msg_id) if msg.reply_to else None
    cands = [r for r in state.signals.values()
             if r["status"] == "skipped" and "TP1" in (r.get("reason") or "")
             and time.time() - r["created"] < 600 and (rid is None or str(r["id"]) == rid)]
    act = state.active()
    if not cands or (rid is None and act and act[0]["created"] > max(r["created"] for r in cands)):
        return False
    rec = max(cands, key=lambda r: r["created"])
    sig = parse_signal(rec["text"], **CFG.get("sanity", {}))
    if not sig or not sig.valid:
        return False
    for c in tp_cmds:
        if c.tp_index in sig.tps and _plausible(rec, c.price):
            sig.tps[c.tp_index] = c.price
    await notify(f"📣 Канал изменил тейк у пропущенного сигнала #{rec['id']} ("
                 + ", ".join(c.describe() for c in tp_cmds) + ") — пересматриваю вход")
    fake = SimpleNamespace(id=rec["id"], message=rec["text"], reply_to=None,
                           date=dt.datetime.fromtimestamp(time.time(), dt.timezone.utc))
    await handle_signal(fake, rec["text"], sig)
    return True


async def handle_command(msg, text, cmds):
    if await retry_skipped_on_tp_change(msg, cmds):
        return
    rec, how = resolve_target(msg)
    if rec is None:
        if any(c.kind == "unknown" for c in cmds):
            return
        log.info("команда без активных сигналов: %s", text[:100])
        return
    state.d["msg2sig"][str(msg.id)] = str(rec["id"])
    if rec["status"] != "active":
        return
    lines = []
    for c in cmds:
        try:
            if DRY:
                lines.append(f"[тест] {c.describe()}")
                continue
            if c.kind == "be":
                if CFG["management"].get("follow_be", True):
                    res = trader.move_to_be(rec)
                    rec["be_done"] = True
                    lines += res or ["стоп уже в безубытке"]
            elif c.kind in ("close", "closed_report"):
                if c.kind == "close" and not CFG["management"].get("follow_close", True):
                    continue
                if c.kind == "closed_report" and not CFG["management"].get("follow_closed_reports", True):
                    continue
                res = trader.close_all(rec)
                if res:
                    lines += res
            elif c.kind == "sl":
                pos = trader.positions_of(rec)
                worse = pos and any(G.sl_move_increases_risk(rec["side"], p.price_open, p.sl or rec["sl"], c.price)
                                    for p in pos)
                if _plausible(rec, c.price) and worse and G.settings(CFG).get("enabled", True):
                    state.d["hold"] = {"type": "sl", "sid": rec["id"], "price": c.price, "created": time.time(),
                                       "reasons": ["новый стоп дальше от входа — риск растёт"]}
                    state.save()
                    await notify(f"🛡 Канал просит перенести стоп сигнала #{rec['id']} на {c.price:g} — это ДАЛЬШЕ "
                                 f"от входа, риск по сделке вырастет. Переносить?",
                                 buttons=guard_buttons("sl", rec["id"]),
                                 alt="Ответьте /slok — перенести, /slno — оставить")
                    lines.append("жду вашего решения по переносу стопа")
                elif _plausible(rec, c.price):
                    rec["sl_changed"] = True
                    lines += trader.set_sl(rec, c.price) or ["нет открытых позиций"]
                else:
                    lines.append(f"стоп {c.price:g} выглядит неправдоподобно — не меняю")
            elif c.kind == "tp":
                if _plausible(rec, c.price):
                    lines += trader.set_tp(rec, c.tp_index, c.price)
                else:
                    lines.append(f"тейк {c.price:g} выглядит неправдоподобно — не меняю")
            elif c.kind == "cancel":
                lines += trader.cancel_pending(rec) or ["лимиток нет"]
            elif c.kind == "no_be":
                # канал важнее автоматики: трейдер держит исходный стоп — возвращаем его
                rec["no_auto_be"] = True
                if rec.get("be_done") and not rec.get("sl_changed"):
                    res = trader.set_sl(rec, rec["sl"])
                    rec["be_done"] = False
                    lines.append(f"канал держит исходный стоп — возвращаю {rec['sl']:g}")
                    lines += res
                else:
                    lines.append("автоперенос в безубыток для этой сделки выключен")
            elif c.kind == "reduce":
                age = time.time() - rec["created"]
                if rec.get("reduced"):
                    lines.append("лот уже уменьшен при входе")
                elif age > CFG["risk"].get("reduce_window_min", 15) * 60:
                    lines.append(f"сигнал открыт {age / 60:.0f} мин назад — лот не трогаю")
                else:
                    res = trader.reduce_half(rec)
                    rec["reduced"] = True
                    lines += res or ["уменьшать нечего (одна минимальная позиция)"]
            elif c.kind == "unknown":
                lines.append("❓ не понял, что делать — проверьте сами, при необходимости действуйте вручную "
                             "или командой /closeall")
        except Exception as e:
            log.exception("ошибка команды")
            lines.append(f"⚠️ ошибка: {e}")
    state.save()
    noop = ("стоп уже в безубытке", "нет открытых позиций", "лимиток нет")
    lines = [l for l in lines if l not in noop]
    if lines:
        await notify(f"📣 Канал: «{text.strip()[:300]}»\n→ сигнал #{rec['id']} ({how}): "
                     + ", ".join(c.describe() for c in cmds) + "\n" + "\n".join("• " + l for l in lines))


def _plausible(rec, price):
    ref = rec["zone"][0]
    return abs(price - ref) / ref < 0.03


async def handle_edit(msg):
    sid = str(msg.id)
    text = msg.message or ""
    rec = state.signals.get(sid)
    sig = parse_signal(text, **CFG.get("sanity", {}))
    if rec is None:
        # сообщение стало сигналом после правки
        if sig and time.time() - msg.date.timestamp() < CFG["risk"].get("max_signal_age_sec", 120):
            await handle_signal(msg, text, sig)
        return
    if not sig:
        return
    old = parse_signal(rec["text"], **CFG.get("sanity", {}))
    if old and old.describe() == sig.describe() and old.errors == sig.errors:
        return
    rec["text"] = text
    head = f"✏️ Канал исправил сигнал #{msg.id}: {sig.describe()}"
    if rec["status"] in ("invalid", "waiting", "skipped") and not rec["orders"]:
        state.signals.pop(sid)
        if sig.valid and not sig.wait_confirm:
            await notify(head + "\nПерепроверяю и обрабатываю заново.")
        await handle_signal(msg, text, sig)
        return
    if rec["status"] == "active" and sig.valid and not DRY:
        lines = []
        if sig.sl != rec["sl"]:
            lines += trader.set_sl(rec, sig.sl)
        for k, v in sig.tps.items():
            if rec["tps"].get(str(k)) != v:
                lines += trader.set_tp(rec, k, v)
        rec["sl"] = sig.sl
        rec["tps"] = {str(k): v for k, v in sig.tps.items()}
        state.save()
        await notify(head + ("\n" + "\n".join("• " + l for l in lines) if lines else ""))
    elif not sig.valid:
        await notify(head + "\n⚠️ После правки в сигнале ошибка: " + "; ".join(sig.errors) + " — ничего не меняю")


# ------------------------------------------------------------------ фоновый контроль

async def monitor():
    global mt5_ok
    last_reconnect = 0
    alerted = False
    while True:
        await asyncio.sleep(CFG.get("monitor_interval_sec", 0.5))
        try:
            if not trader.healthy():
                raise ConnectionError("terminal not connected")
            if not mt5_ok:
                mt5_ok = True
                if alerted:
                    await notify("✅ Связь с MT5 восстановлена.")
                alerted = False
        except Exception as e:
            mt5_ok = False
            if not alerted:
                alerted = True
                await notify(f"🚨 Нет связи с MT5 ({e}). Новые сигналы и перенос в безубыток не работают! "
                             "Стопы и тейки на сервере брокера продолжают действовать. Пытаюсь переподключиться…")
            if time.time() - last_reconnect > 15:
                last_reconnect = time.time()
                try:
                    link.reconnect_lib()
                    trader.connect()
                except Exception as e2:
                    log.warning("переподключение не удалось: %s", e2)
            continue

        try:
            await daily_checks()
            for rec in list(state.active()):
                await check_signal(rec)
        except Exception:
            log.exception("ошибка в мониторе")


async def daily_checks():
    await scheduled_reports()
    await guard_account_check()
    n = now_msk()
    today = n.strftime("%Y-%m-%d")
    acc = trader.account()
    if state.d["day"] != today:
        state.d.update(day=today, day_equity=acc.equity, day_paused=False)
        state.save()
    lim = CFG["risk"].get("max_daily_loss_pct") or 0      # 0 / пусто — лимита нет (у куратора его нет)
    base = state.d["day_equity"] or acc.equity
    if lim > 0 and not state.d["day_paused"] and acc.equity <= base * (1 - lim / 100):
        state.d["day_paused"] = True
        state.save()
        await notify(f"🛑 Дневной лимит убытка {lim}% достигнут (эквити {acc.equity:.2f} при старте дня {base:.2f}). "
                     "Новые сигналы пропускаю до завтра. Открытые сделки веду дальше.")
    wk = CFG.get("weekend", {})
    if wk.get("enabled", False) and n.weekday() == 4:
        hh, mm = map(int, wk.get("close_all_at", "23:30").split(":"))
        if (n.hour, n.minute) >= (hh, mm) and state.d["weekend_done"] != today:
            state.d["weekend_done"] = today
            lines = []
            for rec in state.active():
                if not DRY:
                    lines += trader.close_all(rec)
            state.save()
            if lines:
                await notify("📅 Пятница: закрываю всё перед выходными\n" + "\n".join("• " + l for l in lines))


async def check_signal(rec):
    vmsgs = trader.virtual_tick(rec)          # уровни ближе дистанции брокера (биткоин), которые держим сами
    if vmsgs:
        state.save()
        await notify(f"⚙️ Сигнал #{rec['id']}:\n" + "\n".join("• " + m for m in vmsgs))
    pos = trader.positions_of(rec)
    pend = trader.pending_of(rec)
    if not pos and not pend:
        rec["status"] = "closed"
        filled = any(o["kind"] == "market" for o in rec["orders"]) or rec.get("filled")
        pnl, n = deal_stats(rec)
        rec["pnl"], rec["closed"] = pnl, time.time()
        state.save()
        if filled or n:
            journal_add(rec, pnl)
            cur = trader.account().currency
            await notify(f"🏁 Сигнал #{rec['id']} — все позиции закрыты. Итог: {pnl:+.2f} {cur}")
        return
    if pos and not rec.get("filled"):
        rec["filled"] = True
        state.save()
        if not any(o.get("via") for o in rec["orders"]) and all(o["kind"] in ("limit", "stop") for o in rec["orders"]):
            await notify(f"🎯 Сигнал #{rec['id']}: отложенный ордер исполнился — мы в позиции "
                         f"({len(pos)} шт. @ {pos[0].price_open:g})")
    bid, ask = trader.price(rec["symbol"])
    tp1 = min(rec["tps"].items(), key=lambda kv: int(kv[0]))[1]
    buy = rec["side"] == "BUY"
    reached = (bid >= tp1) if buy else (ask <= tp1)
    if rec.get("be_k"):                       # вошли, когда TP1 уже был пройден — безубыток от нашего первого тейка
        tp_be = rec["tps"].get(str(rec["be_k"]), tp1)
        reached_be = (bid >= tp_be) if buy else (ask <= tp_be)
    else:
        tp_be, reached_be = tp1, reached
        mg = CFG.get("management", {})
        near, frac = float(mg.get("be_near_tp1_usd", 0) or 0), float(mg.get("be_at_frac", 0) or 0)
        if pos and not reached_be and (near or frac):
            # ранний безубыток: цена почти дошла до TP1 (за near $ или прошла долю frac пути от входа)
            e = sum(p.price_open for p in pos) / len(pos)
            cur = bid if buy else ask
            lvl = (tp1 - near if buy else tp1 + near) if near else e + frac * (tp1 - e)
            reached_be = (cur >= lvl and lvl > e) if buy else (cur <= lvl and lvl < e)

    if (pos and not rec["be_done"] and reached_be and not rec.get("no_auto_be")
            and CFG["management"].get("auto_be_after_tp1", True)):
        rec["be_done"] = True
        res = trader.move_to_be(rec)
        state.save()
        if res:
            await notify(f"🔒 Сигнал #{rec['id']}: цена дошла до {'TP' + str(rec['be_k']) if rec.get('be_k') else 'TP1'} {tp_be:g} → стоп в безубыток\n"
                         + "\n".join("• " + l for l in res))

    if pend and pos and reached and not rec.get("be_k"):
        # часть позиций вошла (лесенка/split), цена дошла до TP1 — остальные ордера больше не нужны
        res = trader.cancel_pending(rec)
        state.save()
        await notify(f"⌛️ Сигнал #{rec['id']}: цена дошла до TP1 {tp1:g} — снимаю неисполненные ордера\n"
                     + "\n".join("• " + l for l in res))

    if pend and not pos:
        exp = rec.get("pending_expiry_min", 20) * 60
        waited = time.time() - rec.get("pending_since", rec["created"])
        why = None
        if reached:
            why = f"цена дошла до TP1 {tp1:g} без нас"
        elif (bid <= rec["sl"]) if buy else (ask >= rec["sl"]):
            why = f"цена дошла до стопа {rec['sl']:g} без нас"
        elif waited > exp:
            why = f"лимитка не исполнилась за {exp // 60:.0f} мин"
        rk = CFG.get("risk", {})
        fb = float(rk.get("entry_fallback_min", 0) or 0)
        if (not why and fb and not rec.get("limit_signal") and not rec.get("fallback_done") and not rec.get("filled")
                and waited >= fb * 60 and (not rk.get("entry_fallback_in_range") or in_zone(rec, bid, ask))):
            # запасной вход: лимитка не исполнилась за N мин, а цена ещё не дошла до TP1 — входим по рынку
            rec["fallback_done"] = True
            res, new = trader.pending_to_market(rec)
            rec["orders"] += new
            state.save()
            await notify(f"⏩ Сигнал #{rec['id']}: лимитка не исполнилась за {fb:g} мин, TP1 не достигнут — "
                         f"вхожу по рынку\n" + "\n".join("• " + l for l in res))
            return
        if not why and rec.get("trail") and not rec.get("filled"):
            # режим pullback: цена ушла дальше в нашу сторону — подтягиваем лимитку (откат на $X от лучшей цены)
            tr = rec["trail"]
            cur = ask if buy else bid
            if (cur > tr["ext"]) if buy else (cur < tr["ext"]):
                tr["ext"] = cur
                if trader.move_pending(rec, pullback_price(rec["side"], cur, tr["x"], tr["lo"], tr["hi"])):
                    state.save()
        if why:
            res = trader.cancel_pending(rec)
            if rec.get("filled"):
                why = why.replace(" без нас", "")   # часть позиций уже отработала — итог посчитает следующая проверка
            else:
                rec["status"] = "closed"
            state.save()
            await notify(f"⌛️ Сигнал #{rec['id']}: {why} — снимаю ордера\n" + "\n".join("• " + l for l in res))


def in_zone(rec, bid, ask):
    """Цена внутри диапазона открытия сигнала (одна цена в сигнале — ± entry_tolerance)."""
    lo, hi = rec["zone"]
    if hi - lo < 0.005:
        tol = CFG["symbols"].get(rec["symbol_key"], {}).get("entry_tolerance", 0)
        lo, hi = lo - tol, hi + tol
    cur = ask if rec["side"] == "BUY" else bid
    return lo <= cur <= hi


def deal_stats(rec):
    """Итог по сигналу из истории сделок MT5: (прибыль с комиссиями, число сделок)."""
    try:
        ids = {o["ticket"] for o in rec["orders"]}
        deals = trader.mt5.history_deals_get(rec["created"] - 86400, time.time() + 86400)
        mine = [d for d in deals if int(d.position_id) in ids]
        return round(sum(d.profit + d.commission + d.swap for d in mine), 2), len(mine)
    except Exception as e:
        log.warning("не удалось посчитать итог: %s", e)
        return 0.0, 0


# ------------------------------------------------------------------ журнал и отчёты

JOURNAL = "journal.jsonl"


def journal_add(rec, pnl):
    row = {"id": rec["id"], "opened": rec["created"], "closed": time.time(), "pnl": pnl,
           "side": rec["side"], "symbol": rec.get("symbol"), "be": rec.get("be_done", False)}
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def journal_rows(t0, t1):
    rows = []
    if os.path.exists(JOURNAL):
        for line in open(JOURNAL, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if t0 <= r["closed"] < t1:
                rows.append(r)
    return rows


def period_bounds(kind, n=None):
    """Начало и конец периода (timestamp) в МСК: day/week/month; prev_month — прошлый месяц."""
    n = n or now_msk()
    d0 = n.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "day":
        start = d0
    elif kind == "week":
        start = d0 - dt.timedelta(days=(d0.weekday() + 2) % 7)   # с субботы: выходные (биткоин) входят в неделю
    elif kind == "month":
        start = d0.replace(day=1)
    elif kind == "prev_month":
        end = d0.replace(day=1)
        start = (end - dt.timedelta(days=1)).replace(day=1)
        return start.timestamp(), end.timestamp(), start
    return start.timestamp(), n.timestamp(), start


def build_report(kind):
    t0, t1, start = period_bounds(kind)
    names = {"day": f"📊 Итоги дня {start:%d.%m}", "week": f"📊 Итоги недели с {start:%d.%m}",
             "month": f"📊 Итоги месяца {start:%m.%Y}", "prev_month": f"📊 Итоги месяца {start:%m.%Y}"}
    sigs = [r for r in state.signals.values() if t0 <= r["created"] < t1]
    entered = [r for r in sigs if r["orders"]]
    skipped = [r for r in sigs if not r["orders"]]
    reasons = {}
    for r in skipped:
        k = r.get("reason") or r["status"]
        reasons[k] = reasons.get(k, 0) + 1
    rows = journal_rows(t0, t1)
    pnl = sum(r["pnl"] for r in rows)
    wins = [r for r in rows if r["pnl"] > 0.5]
    losses = [r for r in rows if r["pnl"] < -0.5]
    flat = len(rows) - len(wins) - len(losses)
    acc = trader.account() if trader else None
    cur = acc.currency if acc else ""
    lines = [names[kind], ""]
    lines.append(f"Сигналов: {len(sigs)} | вошёл: {len(entered)} | пропустил: {len(skipped)}")
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:6]:
        lines.append(f"   • {k}: {v}")
    lines.append(f"Закрыто сделок: {len(rows)} | в плюс: {len(wins)} | в минус: {len(losses)} | в ноль: {flat}")
    if rows:
        wr = len(wins) / max(1, len(wins) + len(losses)) * 100
        lines.append(f"Результат: {pnl:+.2f} {cur} | винрейт {wr:.0f}% (без нулевых)")
        best = max(rows, key=lambda r: r["pnl"])
        worst = min(rows, key=lambda r: r["pnl"])
        lines.append(f"Лучшая: #{best['id']} {best['pnl']:+.2f} | худшая: #{worst['id']} {worst['pnl']:+.2f}")
    if acc:
        base = acc.balance - pnl
        pct = f" ({pnl / base * 100:+.1f}% к началу периода)" if base > 0 and rows else ""
        lines.append(f"Баланс: {acc.balance:.2f} {cur}{pct} | эквити {acc.equity:.2f}")
        pos, orders = trader.all_own()
        if pos or orders:
            lines.append(f"Открыто сейчас: позиций {len(pos)}, лимиток {len(orders)}, "
                         f"плавающий P/L {sum(p.profit for p in pos):+.2f}")
    return "\n".join(lines)


async def scheduled_reports():
    n = now_msk()
    hm = (n.hour, n.minute)
    today = n.strftime("%Y-%m-%d")
    sch = CFG.get("reports", {})
    if not sch.get("enabled", True):
        return
    def busy_today():                          # в выходные отчёт за день — только если были сигналы/сделки (биткоин)
        t0, t1, _ = period_bounds("day", n)
        return any(t0 <= r["created"] < t1 for r in state.signals.values()) or bool(journal_rows(t0, t1))
    if (n.weekday() < 5 or busy_today()) and hm >= tuple(map(int, sch.get("daily_at", "23:50").split(":"))) \
            and state.d.get("rep_day") != today:
        state.d["rep_day"] = today
        state.save()
        await notify(build_report("day"))
    if n.weekday() == 4 and hm >= tuple(map(int, sch.get("weekly_at", "23:52").split(":"))) \
            and state.d.get("rep_week") != today:
        state.d["rep_week"] = today
        state.save()
        await notify(build_report("week"))
    if n.day == 1 and hm >= tuple(map(int, sch.get("monthly_at", "10:00").split(":"))) \
            and state.d.get("rep_month") != today:
        state.d["rep_month"] = today
        state.save()
        await notify(build_report("prev_month"))


# ------------------------------------------------------------------ команды в «Избранном»

HELP = ("Команды (пишите сюда):\n/status — счёт и открытые сделки\n"
        "/entry — точка входа в диапазоне сигнала (/entry buy 20, /entry sell 80, /entry 50 — обе)\n/report — отчёт за день (/report week, /report month)\n/pause — не входить в новые сигналы\n"
        "/resume — снова входить\n/closeall — закрыть ВСЕ сделки копировщика и снять лимитки\n"
        "/approve, /approve_safe, /reject — решение по сделке, остановленной защитой (если нет кнопок)\n/help — эта справка")


async def handle_user_command(cmd, arg=""):
    cmd = cmd.lower()
    alias = {"approve": "ok", "approve_safe": "safe", "reject": "no", "slok": "slok", "slno": "slno"}
    if cmd in alias:
        await notify(await resolve_hold(alias[cmd]))
        return
    if cmd == "resume" and state.d.get("hold"):
        h = state.d["hold"]
        if h["type"] == "account":
            await resolve_hold("resume")
        elif h["type"] == "trade":
            await resolve_hold("no")
    if cmd == "report":
        kind = {"week": "week", "неделя": "week", "month": "month", "месяц": "month"}.get(arg.strip().lower(), "day")
        await notify(build_report(kind))
        return
    if cmd == "entry":
        parts = arg.lower().replace("%", " ").replace(",", ".").split()
        sides = ["BUY", "SELL"]
        if parts and parts[0] in ("buy", "sell", "покупка", "покупки", "продажа", "продажи"):
            sides = ["BUY"] if parts[0].startswith(("buy", "покуп")) else ["SELL"]
            parts = parts[1:]
        if parts:
            try:
                v = float(parts[0])
                v = v / 100 if v > 1 else v
                if not 0 <= v <= 1:
                    raise ValueError
            except ValueError:
                await notify("Укажите число от 0 до 100, например /entry buy 20 или /entry 50")
                return
            state.d.pop("entry_level", None)
            for sd in sides:
                state.d[f"entry_level_{sd.lower()}"] = v
            state.save()
            await notify("✅ Сохранено.\n\n" + entry_level_text() + "\n\nИзменить: /entry buy 20, /entry sell 80, /entry 50 (обе)")
        else:
            await notify(entry_level_text() + "\n\nИзменить: /entry buy 20, /entry sell 80, /entry 50 (обе)")
        return
    if cmd == "status":
        acc = trader.account()
        pos, orders = trader.all_own()
        lines = [f"Режим: {MODE}",
                 f"Счёт {acc.login} ({'демо' if acc.trade_mode == link.ACCOUNT_TRADE_MODE_DEMO else 'РЕАЛЬНЫЙ'})",
                 f"Баланс {acc.balance:.2f} {acc.currency}, эквити {acc.equity:.2f}",
                 f"MT5: {'на связи' if mt5_ok else 'НЕТ СВЯЗИ'}",
                 f"Пауза: {'да' if state.d['paused'] else 'нет'}",
                 "",
                 rules_text(),
                 "",
                 "Сделки:"]
        for p in pos:
            lines.append(f"• {p.symbol} {'BUY' if p.type == 0 else 'SELL'} {p.volume:g} @ {p.price_open:g} "
                         f"SL {p.sl:g} TP {p.tp:g} → {p.profit:+.2f}")
        for o in orders:
            lines.append(f"• отложенный ордер {o.symbol} {o.volume_current:g} @ {o.price_open:g}")
        if not pos and not orders:
            lines.append("открытых сделок нет")
        await notify("\n".join(lines))
    elif cmd == "pause":
        state.d["paused"] = True
        state.save()
        await notify("⏸ Пауза: новые сигналы пропускаю. Открытые сделки веду дальше. /resume — продолжить.")
    elif cmd == "resume":
        state.d["paused"] = False
        state.d["day_paused"] = False
        state.save()
        await notify("▶️ Продолжаю входить в сигналы.")
    elif cmd == "closeall":
        lines = []
        for rec in state.active():
            lines += trader.close_all(rec)
            rec["status"] = "closed"
        pos, orders = trader.all_own()
        for p in pos:
            ok, _, err = trader.close_position(p)
            lines.append(f"#{p.ticket} {'закрыта' if ok else 'ошибка ' + err}")
        for o in orders:
            ok, _, err = trader.cancel_order(o)
            lines.append(f"лимитка #{o.ticket} {'снята' if ok else 'ошибка ' + err}")
        state.save()
        await notify("🧹 Закрыл всё:\n" + ("\n".join("• " + l for l in lines) if lines else "нечего закрывать"))
    else:
        await notify(HELP)


# ------------------------------------------------------------------ запуск

def _seen(mid):
    return mid in state.d.setdefault("seen_ids", [])


def _mark_seen(mid):
    ids = state.d.setdefault("seen_ids", [])
    ids.append(mid)
    del ids[:-300]
    state.d["last_msg_id"] = max(state.d.get("last_msg_id") or 0, mid)
    state.save()


async def process_channel_message(msg, late=False):
    """Сообщение канала (из события или догнанное опросом). Каждое обрабатывается один раз."""
    if _seen(msg.id):
        return
    _mark_seen(msg.id)
    text = msg.message or ""
    log.info("канал #%s%s: %s", msg.id, " (догнал опросом)" if late else "", text.replace("\n", " | ")[:300])
    try:
        sig = parse_signal(text, **CFG.get("sanity", {}))
        if sig:
            await forward_signal(msg)
            await handle_signal(msg, text, sig, late=late)
            return
        cmds = parse_command(text)
        if cmds:
            await handle_command(msg, text, cmds)
        elif looks_like_signal(text):
            await notify(f"❓ Похоже на сигнал, но разобрать не смог:\n{text[:500]}")
    except Exception as e:
        log.exception("ошибка обработки")
        await notify(f"⚠️ Ошибка при обработке сообщения #{msg.id}: {e}")


async def poll_channel():
    """
    Страховка от пропусков: Telegram не всегда досылает сообщения, пришедшие во время перезапуска.
    Раз в 20 с (и сразу при старте) читаем новые сообщения канала и обрабатываем те, что не видели.
    """
    if not state.d.get("last_msg_id"):
        known = [int(k) for k in list(state.signals) + list(state.d.get("msg2sig", {})) if str(k).isdigit()]
        state.d["last_msg_id"] = max(known) if known else 0
        if not state.d["last_msg_id"]:
            last = await client.get_messages(channel_entity, limit=1)
            state.d["last_msg_id"] = last[0].id if last else 0
        state.save()
    while True:
        try:
            new = await client.get_messages(channel_entity, min_id=state.d["last_msg_id"], limit=50)
            for m in sorted(new, key=lambda x: x.id):
                if not _seen(m.id):
                    await process_channel_message(m, late=True)
        except Exception as e:
            log.warning("опрос канала: %s", e)
        await asyncio.sleep(CFG.get("poll_interval_sec", 20))


async def find_channel():
    """Ищет канал. Сначала по запомненному ID, затем по названию. None — если доступа ещё нет."""
    want = str(CFG["telegram"]["channel"]).strip()
    pinned = state.d.get("channel_id")
    async for d in client.iter_dialogs():
        if not d.is_channel:
            continue
        if pinned and d.id == pinned:
            return d.entity
        if not pinned and ((want.lstrip("-").isdigit() and str(d.id) == want) or want.lower() in (d.name or "").lower()):
            state.d["channel_id"] = d.id
            state.save()
            return d.entity
    return None


async def wait_for_channel():
    """Ждёт, пока заявку в канал одобрят (проверка раз в минуту)."""
    ch = await find_channel()
    if ch:
        return ch
    want = CFG["telegram"]["channel"]
    await notify(f"⏳ Канал «{want}» пока недоступен — жду одобрения заявки. Проверяю раз в минуту, "
                 "напишу, как только доступ появится.")
    while True:
        await asyncio.sleep(60)
        try:
            ch = await find_channel()
        except Exception as e:
            log.warning("ошибка поиска канала: %s", e)
            continue
        if ch:
            await notify(f"✅ Доступ к каналу «{ch.title}» получен! Начинаю следить за сигналами.")
            return ch


async def main():
    global link, trader, channel_entity, mt5_ok
    global notify_peer
    await client.start()

    async def on_cmd(event):
        if trader is None:
            await notify("Копировщик ещё ждёт доступа к каналу. Команды заработают после этого.")
            return
        log.info("команда владельца: %s", event.raw_text[:100])
        await handle_user_command(event.pattern_match.group(1), event.raw_text.split(maxsplit=1)[1] if len(event.raw_text.split()) > 1 else "")

    if bot:
        await bot.start(bot_token=BOT_TOKEN)

        @bot.on(events.NewMessage(chats=REPORT_CHAT, pattern=r"^/(\w+)"))
        async def on_bot_cmd(event):
            if OWNER_IDS and event.sender_id not in OWNER_IDS:
                return
            await on_cmd(event)

        @bot.on(events.CallbackQuery(pattern=rb"^g:"))
        async def on_button(event):
            if OWNER_IDS and event.sender_id not in OWNER_IDS:
                await event.answer("Кнопки только для владельца", alert=True)
                return
            if trader is None:
                await event.answer("Копировщик ещё запускается")
                return
            _, action, sid = event.data.decode().split(":", 2)
            try:
                ans = await resolve_hold(action, sid if sid != "0" else "")
            except Exception as e:
                log.exception("ошибка кнопки")
                ans = f"Ошибка: {e}"
            await event.answer(ans[:190])
            try:
                await event.edit(buttons=None)
            except Exception:
                pass
    else:
        try:
            notify_peer = await client.get_input_entity(NOTIFY)
        except Exception as e:
            log.error("получатель отчётов %s не найден (%s) — шлю в «Избранное»", NOTIFY, e)
            notify_peer = "me"
        cmd_chats = ["me"] if notify_peer == "me" else ["me", notify_peer]
        client.add_event_handler(on_cmd, events.NewMessage(chats=cmd_chats, pattern=r"^/(\w+)"))

    channel_entity = await wait_for_channel()
    log.info("канал: %s (%s)", getattr(channel_entity, "title", "?"), channel_entity.id)

    link = MT5Link(CFG["mt5"].get("bridge_host", "127.0.0.1"), int(CFG["mt5"].get("bridge_port", 18812)))
    trader = Trader(CFG, link)
    acc, is_demo = trader.connect()
    mt5_ok = True
    warn = "" if trader.trade_allowed() else "\n⚠️ В терминале выключена алготорговля (Algo Trading) — ордера не пройдут!"
    await notify(f"🚀 Копировщик запущен\n\n"
                 f"Режим: {MODE}{' (только разбор, без ордеров)' if DRY else ''}\n"
                 f"Канал: {channel_entity.title}\n"
                 f"Счёт {acc.login} ({'демо' if is_demo else 'РЕАЛЬНЫЙ'})\n"
                 f"Баланс {acc.balance:.2f} {acc.currency}{warn}\n\n"
                 f"{rules_text()}\n\n"
                 f"Точка входа: /entry buy 20, /entry sell 80\n/help — все команды")

    @client.on(events.NewMessage(chats=channel_entity))
    async def on_new(event):
        await process_channel_message(event.message)

    @client.on(events.MessageEdited(chats=channel_entity))
    async def on_edit(event):
        try:
            await handle_edit(event.message)
        except Exception as e:
            log.exception("ошибка правки")
            await notify(f"⚠️ Ошибка при обработке правки #{event.message.id}: {e}")

    asyncio.create_task(monitor())
    asyncio.create_task(poll_channel())
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
