"""
Картинка графика для уведомлений тренд-робота: свечи M30, выделенный ход, уровни входа/стопа, сделки серии.
render(...) → PNG (bytes). Время свечей — время сервера FxPro (= МСК).
"""
import datetime as dt
import io

UP, DOWN = "#26a69a", "#ef5350"


def _fmt(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d.%m %H:%M")


def render(bars, title="", leg=None, levels=None, deals=None, price=None, last_n=110):
    """
    bars   — свечи (t, o, h, l, c), старые первыми (можно с текущей незакрытой в конце);
    leg    — (t_start, price_start, t_end, price_end): выделить ход;
    levels — [(цена, подпись, цвет, стиль линии)];
    deals  — [(t, цена, 'buy'|'sell'|'exit')]: стрелки сделок;
    price  — текущая цена (пунктир).
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    bars = list(bars)[-last_n:]
    n = len(bars)
    times = [b[0] for b in bars]

    def x_of(t):
        """Индекс свечи, в которую попадает время t (без дыр на выходные)."""
        k = 0
        for i, bt in enumerate(times):
            if bt <= t:
                k = i
        return k

    fig = Figure(figsize=(10, 5.6), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.04, right=0.88, top=0.9, bottom=0.12)
    ax.set_facecolor("white")
    ax.grid(True, color="#eeeeee", linewidth=0.8)
    ax.set_axisbelow(True)

    if leg:
        x0, x1 = x_of(leg[0]), x_of(leg[2])
        ax.axvspan(x0 - 0.5, x1 + 0.5, color="#fff3c4", alpha=0.7, zorder=0)
        ax.plot([x0, x1], [leg[1], leg[3]], color="#b8860b", linewidth=2, linestyle="--", zorder=3)

    w = 0.6
    for i, (t, o, h, lo, c) in enumerate(bars):
        col = UP if c >= o else DOWN
        ax.vlines(i, lo, h, color=col, linewidth=1, zorder=2)
        ax.add_patch(_rect(i - w / 2, min(o, c), w, max(abs(c - o), 0.05), col))

    lo_all = min(b[3] for b in bars)
    hi_all = max(b[2] for b in bars)
    for pr, label, color, style in levels or []:
        ax.axhline(pr, color=color, linestyle=style, linewidth=1.3, zorder=4)
        ax.annotate(f"{label} {pr:.2f}", xy=(0.99, pr), xycoords=("axes fraction", "data"), xytext=(0, 3),
                    textcoords="offset points", ha="right", va="bottom", fontsize=8, color=color,
                    bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none", alpha=0.85), zorder=6)
        lo_all, hi_all = min(lo_all, pr), max(hi_all, pr)
    if price is not None:
        ax.axhline(price, color="#555555", linestyle=":", linewidth=1, zorder=4)
        ax.annotate(f"{price:.2f}", xy=(1.0, price), xycoords=("axes fraction", "data"), xytext=(4, 0),
                    textcoords="offset points", va="center", fontsize=8, color="white",
                    bbox=dict(boxstyle="square,pad=0.2", fc="#555555", ec="none"))
    for t, pr, kind in deals or []:
        x = x_of(t)
        if kind == "exit":
            ax.plot([x], [pr], marker="x", color="black", markersize=8, zorder=5)
        else:
            up = kind == "buy"
            ax.plot([x], [pr], marker="^" if up else "v", color="#1e88e5" if up else "#e53935",
                    markersize=9, markeredgecolor="white", zorder=5)

    pad = (hi_all - lo_all) * 0.06 or 1
    ax.set_ylim(lo_all - pad, hi_all + pad)
    ax.set_xlim(-1, n + 1)
    ax.yaxis.tick_right()
    step = max(1, n // 7)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([_fmt(times[i]) for i in ticks], fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.set_title(title, fontsize=11, loc="left")
    ax.text(0.0, -0.11, "GOLD, свечи 30 мин, время МСК", transform=ax.transAxes, fontsize=7, color="#888888")
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def _rect(x, y, w, h, col):
    from matplotlib.patches import Rectangle
    return Rectangle((x, y), w, h, facecolor=col, edgecolor=col, linewidth=0.5, zorder=2)
