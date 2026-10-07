"""
Бэктест «трендовой лесенки» на золоте (идея владельца 07.10.2026).

Идея: график уверенно идёт в одну сторону → входим по тренду с коротким стопом,
по мере движения доливаем позиции (только когда старые уже в безубытке),
тейков нет — все позиции закрывает общий скользящий стоп.

Данные: CSV из MT5 (tools/export_history.py): time(сервер, сек),open,high,low,close,spread(пункты).
Цены — bid. Исполнение моделируется на «младших» свечах (M5, для длинной истории M15),
сигнал — по свечам M30, собранным из них же.

Внутри свечи путь цены: O→L→H→C для растущей свечи, O→H→L→C для падающей.
Стоп у покупки срабатывает по bid, у продажи — по ask (bid+спред), как в MT5.
Издержки: спред из данных + запас, комиссия $7 за лот (FxPro, 3.5+3.5), проскальзывание на стопах,
своп за ночь (лонг −69.25 $/лот, шорт +17.15 $/лот, в среду ×3).

Результат каждой серии считается в R (R = риск первой позиции), затем депозит с риском risk% на серию.

Запуск: python tools/trend_bt.py gold_M15.csv   (итоги вариантов по годам + сравнение со случайными входами
        + проверка «продолжает ли цена идти после сильного хода»)
Итоги проверки 07.10.2026 — docs/RESEARCH.md.
"""
import csv
import math
import statistics
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

COMM = 0.07          # $ на унцию (0.01 лота = 1 унция) за круг
SLIP = 0.10          # проскальзывание стопа/доливки, $
SP_ADD = 0.05        # запас к спреду из свечи (там минимальный за свечу), $
SWAP_LONG = -0.6925  # $ на унцию за ночь
SWAP_SHORT = 0.1715
BE_OFF = 0.3         # «безубыток» = вход ± $0.3


@dataclass
class P:
    n: int = 3            # свечей M30 подряд в одну сторону
    move: float = 2.0     # их общий ход ≥ move × ATR(M30)
    mono: bool = True     # каждое закрытие дальше предыдущего (без откатов)
    r: float = 0.6        # стоп первой позиции = r × ATR
    rmin: float = 3.0     # но не меньше, $
    s: float = 1.0        # шаг доливки = s × R
    t: float = 2.0        # скользящий стоп = t × R от лучшей цены
    maxpos: int = 4       # позиций в серии максимум
    spike: float = 0.0    # не входить, если последняя свеча > spike × ATR (0 — выкл)
    hours: tuple = (0, 24)  # часы входа по времени сервера [от, до)
    maxloss_day: int = 2  # убыточных серий в день — дальше стоп до завтра
    maxsp: float = 0.6    # не входить при спреде больше, $
    fade: bool = False    # проверка «наоборот»: входить против движения
    risk: float = 1.0     # % депозита на серию
    timeout: int = 0      # закрыть серию через N свечей M30 без новой доливки (0 — выкл)
    mode: str = "bars"    # bars — n свечей подряд; er — «чистота» хода за n свечей; donch — пробой n свечей; random — случайно (проверка)
    er: float = 0.6       # для mode=er: |итоговый ход| / сумма |ходов свечей| ≥ er
    ema: int = 0          # фильтр старшего тренда: цена по нужную сторону EMA(ema) на M30 (0 — выкл)
    seed: int = 1


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.reader(f):
            if not r or not r[0]:
                continue
            rows.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]) * 0.01))
    rows.sort()
    out, last = [], None
    for x in rows:
        if x[0] != last:
            out.append(x)
            last = x[0]
    return out


def to_m30(bars):
    """Свечи M30 из младших + индекс первой младшей свечи следующего окна."""
    m30, cur = [], None
    for i, (t, o, h, l, c, sp) in enumerate(bars):
        k = t // 1800 * 1800
        if cur is None or cur[0] != k:
            if cur:
                m30.append(cur)
            cur = [k, o, h, l, c, i, i]       # t,o,h,l,c,first_idx,last_idx
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[6] = i
    if cur:
        m30.append(cur)
    return m30


def atr_series(m30, n=14):
    out, a, prev = [], None, None
    for t, o, h, l, c, *_ in m30:
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
        a = tr if a is None else a + (tr - a) / n
        out.append(a)
        prev = c
    return out


def dtime(t):
    return datetime.fromtimestamp(t, timezone.utc)   # время сервера, «как UTC»


@dataclass
class Seq:
    d: int
    t0: int
    R: float
    pos: list = field(default_factory=list)   # [entry, stop, open?, pnl]
    ext: float = 0.0
    next_add: float = 0.0
    adds_ok: bool = True
    last_add_m30: int = 0


def ema_series(m30, n):
    out, e, k = [], None, 2 / (n + 1)
    for b in m30:
        e = b[4] if e is None else e + k * (b[4] - e)
        out.append(e)
    return out


_rnd = {}


def signal(m30, atr, j, p: P, ema=None):
    """Сигнал по закрытым свечам m30[j-n+1..j]. +1 / -1 / 0."""
    if j < max(p.n, p.ema) + 15:
        return 0
    seg = m30[j - p.n + 1: j + 1]
    a = atr[j - p.n]           # ATR до начала движения
    if a <= 0:
        return 0
    d = _signal(m30, atr, j, p, seg, a)
    if d and ema is not None and d * (m30[j][4] - ema[j]) <= 0:
        return 0
    return d


def _signal(m30, atr, j, p, seg, a):
    if p.mode == "random":
        import random
        r = _rnd.setdefault(p.seed, random.Random(p.seed))
        x = r.random()
        return 1 if x < 0.02 else (-1 if x < 0.04 else 0)
    if p.mode == "donch":                 # пробой максимума/минимума последних n свечей
        hi = max(b[2] for b in m30[j - p.n: j])
        lo = min(b[3] for b in m30[j - p.n: j])
        c = m30[j][4]
        return 1 if c > hi else (-1 if c < lo else 0)
    if p.mode == "er":
        net = seg[-1][4] - m30[j - p.n][4]
        path = sum(abs(m30[k][4] - m30[k - 1][4]) for k in range(j - p.n + 1, j + 1))
        if path <= 0 or abs(net) / path < p.er or abs(net) < p.move * a:
            return 0
        d = 1 if net > 0 else -1
        if p.spike and (seg[-1][2] - seg[-1][3]) > p.spike * atr[j - 1]:
            return 0
        return d
    for d in (1, -1):
        if all(d * (b[4] - b[1]) > 0 for b in seg):
            if p.mono and not all(d * (seg[k][4] - seg[k - 1][4]) > 0 for k in range(1, len(seg))):
                continue
            if d * (seg[-1][4] - seg[0][1]) >= p.move * a:
                if p.spike and (seg[-1][2] - seg[-1][3]) > p.spike * atr[j - 1]:
                    return 0
                return d
    return 0


def run(bars, p: P, m30=None, atr=None, start_t=0, end_t=1 << 62):
    if m30 is None:
        m30 = to_m30(bars)
        atr = atr_series(m30)
    ema = ema_series(m30, p.ema) if p.ema else None
    _rnd.pop(p.seed, None)
    seqs = []
    bal = 1000.0
    day_losses = {}
    j = 0
    nm = len(m30)
    seq = None
    while j < nm - 1:
        # сигнал на закрытии свечи j, вход на открытии свечи j+1
        nxt = m30[j + 1]
        if seq is None and start_t <= nxt[0] < end_t:
            d = signal(m30, atr, j, p, ema)
            if p.fade:
                d = -d
            dt = dtime(nxt[0])
            ok = d != 0 and p.hours[0] <= dt.hour < p.hours[1]
            ok = ok and not (dt.weekday() == 4 and dt.hour >= 21) and dt.weekday() < 5
            ok = ok and day_losses.get(dt.date(), 0) < p.maxloss_day
            if ok and nxt[0] - m30[j][0] > 3600:      # после перерыва в торговле не входим
                ok = False
            if ok:
                i0 = nxt[5]
                t, o, h, l, c, sp = bars[i0]
                sp += SP_ADD
                if sp <= p.maxsp:
                    R = max(p.rmin, p.r * atr[j])
                    ent = o + sp if d > 0 else o          # покупка по ask, продажа по bid
                    seq = Seq(d=d, t0=t, R=R)
                    stop = ent - R if d > 0 else ent + R  # покупка: по bid; продажа: по ask
                    seq.pos.append([ent, stop, True, 0.0])
                    seq.ext = o
                    seq.next_add = ent + d * p.s * R
                    seq.last_add_m30 = j + 1
                    j_end = sim_seq(bars, m30, seq, p, i0)
                    r_mult = sum(x[3] for x in seq.pos) / R
                    bal *= 1 + p.risk / 100 * r_mult
                    seqs.append((seq.t0, d, r_mult, len(seq.pos), R, bal, m30[j_end][0]))
                    if r_mult < 0:
                        k = dtime(seq.t0).date()
                        day_losses[k] = day_losses.get(k, 0) + 1
                    seq = None
                    j = max(j + 1, j_end)     # новый сигнал — только по свечам после выхода
                    continue
        j += 1
    return seqs, bal


def sim_seq(bars, m30, seq: Seq, p: P, i0):
    """Проводит серию по младшим свечам с i0. Возвращает индекс свечи M30, на которой серия закрылась."""
    d = seq.d
    nb = len(bars)
    i = i0
    last_day = dtime(bars[i0][0]).date()
    while i < nb:
        t, o, h, l, c, sp = bars[i]
        sp += SP_ADD
        dt = dtime(t)
        # ночь: своп
        if dt.date() != last_day:
            nights = 3 if last_day.weekday() == 2 else 1
            last_day = dt.date()
            for x in seq.pos:
                if x[2]:
                    x[3] += (SWAP_LONG if d > 0 else SWAP_SHORT) * nights
        # пятница вечером — закрыть всё
        if dt.weekday() == 4 and (dt.hour > 23 or (dt.hour == 23 and dt.minute >= 30)) or dt.weekday() >= 5:
            close_all(seq, o, sp)
            return m30_index(m30, t)
        # выход по времени
        if p.timeout:
            jm = m30_index(m30, t)
            if jm - seq.last_add_m30 >= p.timeout and t % 1800 == 0:
                close_all(seq, o, sp)
                return jm
        path = (o, l, h, c) if c >= o else (o, h, l, c)
        a = path[0]
        # гэп на открытии: стопы за ценой открытия
        if hit_stops(seq, a, a, sp, gap=True):
            return m30_index(m30, t)
        for b in path[1:]:
            if d * (b - a) > 0:       # в нашу сторону
                favorable(seq, p, a, b, sp)
            elif b != a:
                if hit_stops(seq, a, b, sp):
                    return m30_index(m30, t)
            a = b
        i += 1
    close_all(seq, bars[-1][4], bars[-1][5])
    return len(m30) - 1


def m30_index(m30, t):
    # бинарный поиск свечи M30, содержащей t
    lo, hi = 0, len(m30) - 1
    k = t // 1800 * 1800
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if m30[mid][0] <= k:
            lo = mid
        else:
            hi = mid - 1
    return lo


def favorable(seq, p, a, b, sp):
    """Цена (bid) идёт от a к b в нашу сторону: доливки, затем подтягиваем стопы."""
    d, R = seq.d, seq.R
    # доливки по пути
    while seq.adds_ok and len(seq.pos) < p.maxpos:
        lvl = seq.next_add                        # покупка — уровень ask, продажа — bid
        trig = lvl - sp if d > 0 else lvl         # в терминах bid
        if d * (b - trig) < 0:
            break
        gap = d * (a - trig) > 0                   # открылись уже за уровнем
        px = (a if gap else trig)
        upd_ext(seq, p, px, sp)
        for x in seq.pos:                          # старые — минимум в безубыток (+$0.3 на издержки)
            if x[2]:
                x[1] = tighter(d, x[1], x[0] + d * BE_OFF)
        fill = (px + sp if d > 0 else px) + d * SLIP
        stop = fill - d * R
        seq.pos.append([fill, stop, True, 0.0])
        seq.next_add = lvl + d * p.s * R
    upd_ext(seq, p, b, sp)


def tighter(d, s1, s2):
    return max(s1, s2) if d > 0 else min(s1, s2)


def upd_ext(seq, p, price, sp):
    d = seq.d
    if d * (price - seq.ext) > 0:
        seq.ext = price
    trail = seq.ext - p.t * seq.R if d > 0 else seq.ext + sp + p.t * seq.R
    for x in seq.pos:
        if x[2]:
            x[1] = tighter(d, x[1], trail)


def hit_stops(seq, a, b, sp, gap=False):
    """Цена (bid) идёт от a к b против нас. Закрывает задетые позиции. True — серия закончилась."""
    d = seq.d
    for x in seq.pos:
        if not x[2]:
            continue
        if d > 0:
            if b <= x[1]:
                px = (min(a, x[1]) if gap else x[1]) - SLIP
                close(x, d, px)
        else:
            if b + sp >= x[1]:
                px = (max(a + sp, x[1]) if gap else x[1]) + SLIP
                close(x, d, px)
    if any(not x[2] for x in seq.pos):
        seq.adds_ok = False               # после первого стопа больше не доливаем
    return all(not x[2] for x in seq.pos)


def close(x, d, px):
    x[2] = False
    x[3] += d * (px - x[0]) - COMM


def close_all(seq, bid, sp):
    for x in seq.pos:
        if x[2]:
            close(x, seq.d, bid if seq.d > 0 else bid + sp)


# ---------------------------------------------------------------- отчёты

def stats(seqs, bal0=1000.0):
    n = len(seqs)
    if not n:
        return dict(n=0)
    rs = [s[2] for s in seqs]
    wins = [r for r in rs if r > 0]
    loss = [r for r in rs if r <= 0]
    peak, mdd, eq = bal0, 0.0, bal0
    for s in seqs:
        eq = s[5]
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    # самая длинная серия убытков
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    return dict(n=n, win=len(wins) / n, sumR=sum(rs), avgW=(sum(wins) / len(wins)) if wins else 0,
                avgL=(sum(loss) / len(loss)) if loss else 0,
                pf=(sum(wins) / -sum(loss)) if loss and sum(loss) < 0 else float("inf"),
                bal=seqs[-1][5], mdd=mdd, streak=best, maxR=max(rs),
                adds=sum(s[3] for s in seqs) / n)


def fmt(st):
    if not st.get("n"):
        return "нет сделок"
    return (f"серий {st['n']:4d}  плюс {st['win']*100:4.0f}%  сумма {st['sumR']:+7.1f}R  "
            f"PF {st['pf']:.2f}  ср.+ {st['avgW']:.2f}R ср.- {st['avgL']:.2f}R  макс {st['maxR']:.1f}R  "
            f"поз/серию {st['adds']:.1f}  $1000→{st['bal']:.0f}  просадка {st['mdd']*100:.0f}%  "
            f"убытков подряд {st['streak']}")


VARIANTS = {
    "предложенный (3 свечи, стоп 0.6 ATR, трейл 2R, до 4 поз.)":
        dict(mode="bars", n=3, move=1.5, r=0.6, s=1.0, t=2.0, maxpos=4),
    "лучший на 2025 (2 свечи, стоп 1 ATR, трейл 4R, до 5 поз., EMA100)":
        dict(mode="bars", n=2, move=1.0, r=1.0, s=1.0, t=4.0, maxpos=5, ema=100),
    "«чистый ход» er (4 свечи, трейл 4R, EMA100)":
        dict(mode="er", n=4, er=0.5, move=1.5, r=0.7, s=1.0, t=4.0, maxpos=3, ema=100),
    "без доливок (1 позиция, трейл 2R)":
        dict(mode="bars", n=3, move=1.5, r=0.6, t=2.0, maxpos=1),
}


def by_year(seqs):
    g = {}
    for s in seqs:
        g.setdefault(dtime(s[0]).year, []).append(s[2])
    return {y: (len(v), round(sum(v), 1)) for y, v in sorted(g.items())}


def continuation(m30, atr, p: P, hs=(1, 2, 4, 8, 16)):
    """Средний ход цены после сигнала за h свечей M30 (в ATR, за вычетом общего дрейфа) и t-статистика.
    > 0 — цена продолжает идти по сигналу, < 0 — откатывает."""
    c = [b[4] for b in m30]
    n = len(m30)
    base = {h: statistics.mean((c[j + h] - c[j]) / atr[j] for j in range(30, n - h)) for h in hs}
    out = {h: [] for h in hs}
    for j in range(30, n - max(hs)):
        d = signal(m30, atr, j, p)
        if d:
            for h in hs:
                out[h].append(d * (c[j + h] - c[j]) / atr[j] - d * base[h])
    res = {}
    for h, v in out.items():
        if len(v) > 2:
            m = statistics.mean(v)
            res[h] = (round(m, 3), round(m / (statistics.pstdev(v) / math.sqrt(len(v))), 1))
    return len(out[hs[0]]), res


if __name__ == "__main__":
    bars = load(sys.argv[1])
    m30 = to_m30(bars)
    atr = atr_series(m30)
    print("данные", dtime(bars[0][0]).date(), "→", dtime(bars[-1][0]).date())
    for name, kw in VARIANTS.items():
        p = replace(P(), **kw)
        seqs, _ = run(bars, p, m30, atr)
        st = stats(seqs)
        print(name)
        print("   ", fmt(st))
        print("    по годам (серий, сумма R):", by_year(seqs))
        rnd = []
        for seed in range(1, 13):
            x = stats(run(bars, replace(p, mode="random", seed=seed), m30, atr)[0])
            rnd.append(x["sumR"] / x["n"])
        rnd.sort()
        print(f"    случайные входы с тем же управлением: R/серию {rnd[0]:+.3f} … {rnd[-1]:+.3f} "
              f"(медиана {statistics.median(rnd):+.3f}); у варианта {st['sumR'] / st['n']:+.3f}")
    print("Продолжает ли цена идти после сигнала (ход за h свечей M30 в ATR, t-стат.):")
    for n, mv in ((2, 1.0), (3, 1.5), (3, 2.0), (3, 3.0), (4, 2.5)):
        k, r = continuation(m30, atr, replace(P(), mode="bars", n=n, move=mv))
        print(f"    {n} свечи подряд, ход ≥{mv} ATR: сигналов {k}; ", r)
