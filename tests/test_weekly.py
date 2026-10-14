"""Расчёты еженедельной проверки правил (tools/weekly/rules_check.py): python tests/test_weekly.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "weekly"))
import rules_check as R  # noqa: E402


def test_metrics():
    # 27.05 → 13.10 (139 дней ≈ 4.57 мес): $1276.7 — 5.5% в мес (как в прогоне 14.10)
    today = dt.date(2026, 10, 13)
    eq = [("2026-05-27", 1000, 1000), ("2026-09-13", 1200, 1200), ("2026-10-06", 1250, 1250), ("2026-10-07", 1260, 1260)]
    m = R.metrics(eq, 1276.7, today)
    assert m["avg_month"] == 5.5, m
    assert m["month"] == round((1276.7 / 1200 - 1) * 100, 1), m          # от начала 13.09
    assert m["week"] == round((1276.7 / 1250 - 1) * 100, 1), m           # от начала 06.10
    assert R.metrics(eq, 1376.02, today)["avg_month"] == 7.2
    # 13.09 — воскресенье: месяц считается от начала понедельника 14.09, а не от пятницы 11.09
    eq2 = [("2026-09-11", 1418.96, 0), ("2026-09-14", 1362.68, 0), ("2026-10-12", 1383.7, 0), ("2026-10-13", 1376.02, 0)]
    m = R.metrics(eq2, 1376.02, today)
    assert m["month"] == 1.0 and m["week"] == round((1376.02 / 1383.7 - 1) * 100, 1), m


def test_verdict():
    assert R.verdict(1.0, -0.1) == "✅"
    assert R.verdict(1.0, 1.2) == "➖"
    assert R.verdict(0.4, 2.5) == "⚠️"


def test_real():
    now = 1_000_000_000
    deals = [(now - 20 * 86400, 2, 0, 100.0),            # пополнение 20 дней назад
             (now - 10 * 86400, 0, 770077, 30.0),
             (now - 3 * 86400, 1, 770079, -20.0),
             (now - 2 * 86400, 1, 0, -50.0)]             # ручная сделка: в итог профилей не входит
    r = R.real_results(deals, 1060.0, now, 7)            # баланс неделю назад: 1060 + 20 + 50 = 1130
    assert r[770079] == (-20.0, round(-20 / 1130 * 100, 1)) and r[770077] == (0, 0.0), r
    r = R.real_results(deals, 1060.0, now, 30)           # месяц назад: 1060 − 100 − 30 + 20 + 50 = 1000
    assert r[770077] == (30.0, 3.0), r


def test_rules_and_message():
    cfg = {"management": {"be_near_tp1_usd": 0.5}, "risk": {"entry_mode": "limit"}}
    rules = R.rule_variants(cfg, 0.2, 0.8)
    assert [r[0] for r in rules] == ["benear", "entry", "pair"]
    assert [r[0] for r in R.rule_variants({"management": {}, "risk": {}}, 0.5, 0.5)] == ["pair"]
    runs = {"our": {"avg_month": 7.2, "month": 1.0, "week": 0.3}, "cur": {"avg_month": 1.0, "month": -2.0, "week": 0.1},
            "benear": {"avg_month": 5.5, "month": -0.1, "week": 0.2},
            "entry": {"avg_month": 6.0, "month": 2.5, "week": 0.0}, "pair": {"avg_month": 7.0, "month": 0.9, "week": 0.3}}
    real = {m: {k: (0.0, 0.0) for k, _ in R.MAGICS} for m in ("week", "month")}
    msg = R.build_message(dt.date(2026, 10, 17), runs, rules, real, [])
    assert "✅ Ранний безубыток" in msg and "⚠️ Точка входа" in msg and "➖ Лот на 2 позиции" in msg, msg
    assert "За последний месяц мешает: Точка входа" in msg and "−0.1%" in msg, msg
    ok = R.build_message(dt.date(2026, 10, 17), dict(runs, entry=runs["our"]), rules, None, [])
    assert "✅ Наши правила за последний месяц работают" in ok, ok


if __name__ == "__main__":
    for f in (test_metrics, test_verdict, test_real, test_rules_and_message):
        f()
    print("OK")
