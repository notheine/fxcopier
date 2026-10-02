"""
Второй слой защиты: проверка сделки по реальным цифрам счёта ПЕРЕД отправкой ордера.
Если сделка выглядит опасной — она не исполняется, копировщик встаёт на паузу и
спрашивает владельца (кнопки в группе).

Пороги откалиброваны по истории канала (06–09.2026, лот по таблице, баланс $1000):
риск на сделку — медиана 4.5% баланса, 90% сигналов ≤ 8%, максимум 22%;
стоп — медиана $17, максимум $73.
"""
import time

DEFAULTS = {
    "enabled": True,
    "max_trade_risk_pct": 10.0,      # потеря при стопе по этой сделке > 10% баланса
    "max_total_risk_pct": 15.0,      # суммарный риск всех открытых + новой > 15%
    "max_sl_distance": {"GOLD": 80.0, "BTC": 3000.0},   # стоп дальше от входа — аномалия
    "max_lot_vs_table": 2.0,         # лот больше табличного в 2+ раза — ошибка настройки
    "max_total_lot": 0.5,            # абсолютный потолок общего лота на сигнал
    "max_margin_pct_free": 50.0,     # маржа сделки > 50% свободных средств
    "max_signals_per_30min": 4,      # шквал сигналов (взлом/сбой канала)
    "max_losses_in_row": 3,          # после 3 убыточных подряд — пауза и вопрос
    "max_drawdown_pct": 25.0,        # эквити ниже пика баланса на 25% — пауза и вопрос
    "approval_ttl_min": 15,          # разрешение на сделку действует 15 мин (дальше цена устарела)
}


def settings(cfg):
    g = dict(DEFAULTS)
    g.update(cfg.get("guard") or {})
    return g


def check_trade(g, *, symbol_key, plan_risk, plan_lot, table_lot, sl_distance, balance, free_margin,
                margin_needed, open_risk, recent_signals):
    """Список причин, почему сделка опасна (пустой — всё в порядке)."""
    if not g.get("enabled", True):
        return []
    r = []
    if balance <= 0:
        return ["баланс счёта не определён"]
    risk_pct = plan_risk / balance * 100
    if risk_pct > g["max_trade_risk_pct"]:
        r.append(f"риск сделки {plan_risk:.2f} = {risk_pct:.1f}% баланса (порог {g['max_trade_risk_pct']:g}%)")
    total_pct = (open_risk + plan_risk) / balance * 100
    if total_pct > g["max_total_risk_pct"] and open_risk > 0:
        r.append(f"суммарный риск с открытыми сделками {total_pct:.1f}% баланса (порог {g['max_total_risk_pct']:g}%)")
    lim = (g.get("max_sl_distance") or {}).get(symbol_key)
    if lim and sl_distance > lim:
        r.append(f"стоп в {sl_distance:.2f} от входа — необычно далеко (порог {lim:g})")
    if table_lot > 0 and plan_lot > table_lot * g["max_lot_vs_table"] + 1e-9:
        r.append(f"лот {plan_lot:g} больше табличного {table_lot:g} в {plan_lot / table_lot:.1f} раза")
    if plan_lot > g["max_total_lot"] + 1e-9:
        r.append(f"лот {plan_lot:g} больше потолка {g['max_total_lot']:g}")
    if margin_needed and free_margin > 0 and margin_needed / free_margin * 100 > g["max_margin_pct_free"]:
        r.append(f"маржа {margin_needed:.2f} = {margin_needed / free_margin * 100:.0f}% свободных средств")
    now = time.time()
    n = sum(1 for t in recent_signals if now - t < 1800)
    if n >= g["max_signals_per_30min"]:
        r.append(f"{n + 1}-й сигнал за 30 минут — необычно часто")
    return r


def check_account(g, *, last_pnls, equity, peak_balance):
    """Причины поставить торговлю на паузу по состоянию счёта."""
    if not g.get("enabled", True):
        return []
    r = []
    k = g["max_losses_in_row"]
    if k and len(last_pnls) >= k and all(p < -0.5 for p in last_pnls[-k:]):
        r.append(f"{k} убыточные сделки подряд")
    if peak_balance > 0 and equity < peak_balance * (1 - g["max_drawdown_pct"] / 100):
        r.append(f"просадка {100 - equity / peak_balance * 100:.0f}% от пика баланса {peak_balance:.2f}")
    return r


def sl_move_increases_risk(side, entry, old_sl, new_sl):
    """Новый стоп дальше от входа, чем старый (риск растёт)."""
    s = 1 if side == "BUY" else -1
    return s * (entry - new_sl) > s * (entry - old_sl) + 1e-9
