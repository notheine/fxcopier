"""
Разбор сообщений канала: торговые сигналы и команды по ведению сделок.
Модуль не зависит от MT5 и Telegram — его можно проверять на любом компьютере.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

NUM = r"(\d{2,6}(?:[.,]\d+)?)"
TP_RE = re.compile(r"[tт]\s?[pр]\s*(\d)\s*[:=]?\s*" + NUM, re.I)
SL_RE = re.compile(r"(?<![a-zа-я])[sс]\s?[lл]\s*[:=]?\s*" + NUM, re.I)
DIR_RE = re.compile(r"\b(BUY|SELL)\b|(покупк\w*|продаж\w*)", re.I)
CNUM = r"(\d{3,6}(?:[.,]\d+)?)(?!\d|\s*(?:points|point|pips|pip|пипс|пункт|поинт))"

# Пометки «уменьшить лот»
REDUCE_RE = re.compile(
    r"занижа|уменьша|снижа|в\s*2\s*раза|в\s*два\s*раза|длинн\w*\s+стоп|больш\w*\s+стоп|"
    r"стоп\s*больше|стоплос\w*\s+больше|стоп\s+длинн",
    re.I,
)


def _f(x: str) -> float:
    return float(x.replace(",", "."))


@dataclass
class Signal:
    symbol: str                 # ключ инструмента: GOLD, BTC, EURUSD...
    side: str                   # BUY / SELL
    zone: tuple                 # (low, high) зона входа
    tps: dict                   # {1: price, 2: price, ...}
    sl: float
    is_limit: bool = False
    risky: bool = False
    reduce: bool = False        # в тексте просят занизить лот
    wait_confirm: bool = False  # трейдер пишет «пока не вошёл, ждите апдейт»
    errors: list = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.zone[0] + self.zone[1]) / 2

    @property
    def valid(self) -> bool:
        return not self.errors

    def describe(self) -> str:
        tps = ", ".join(f"TP{k} {v:g}" for k, v in sorted(self.tps.items()))
        z = f"{self.zone[0]:g}" if self.zone[0] == self.zone[1] else f"{self.zone[0]:g}–{self.zone[1]:g}"
        flags = []
        if self.is_limit:
            flags.append("LIMIT")
        if self.risky:
            flags.append("RISKY")
        if self.reduce:
            flags.append("лот ÷2")
        if self.wait_confirm:
            flags.append("ждём подтверждения")
        f = f" [{', '.join(flags)}]" if flags else ""
        return f"{self.symbol} {self.side} {z} | {tps} | SL {self.sl:g}{f}"


def looks_like_signal(text: str) -> bool:
    """Похоже на сигнал (даже если разобрать не удалось)."""
    return bool(DIR_RE.search(text) and TP_RE.search(text))


def _symbol(text: str) -> str:
    up = text.upper()
    if "GOLD" in up or "XAU" in up or "ЗОЛОТ" in up:
        return "GOLD"
    if "BTC" in up or "BITCOIN" in up:
        return "BTC"
    m = re.search(r"\b([A-Z]{6})\b", up)
    return m.group(1) if m else "UNKNOWN"


def parse_signal(text: str, max_zone_pct=0.75, max_tp_pct=3.0, max_sl_pct=2.0) -> Optional[Signal]:
    """Возвращает Signal (возможно с errors) или None, если это не сигнал."""
    if not looks_like_signal(text):
        return None
    d = DIR_RE.search(text)
    w = (d.group(1) or d.group(2)).lower()
    side = "BUY" if w.startswith(("buy", "покуп")) else "SELL"
    tps = {}
    for k, v in TP_RE.findall(text):
        k = int(k)
        if 1 <= k <= 5 and k not in tps:
            tps[k] = _f(v)
    m = SL_RE.search(text)
    sl = _f(m.group(1)) if m else None

    sig = Signal(symbol=_symbol(text), side=side, zone=(0.0, 0.0), tps=tps, sl=sl or 0.0)
    sig.is_limit = bool(re.search(r"\blimit\b|лимит", text, re.I))
    sig.risky = bool(re.search(r"\brisky\b", text, re.I))
    sig.reduce = bool(REDUCE_RE.search(text))
    sig.wait_confirm = bool(re.search(
        r"пока\s+не\s+(вош|вхож|захож)|жди\w*\s+апдейт|после\s+конфирм|рассматриваю\s+позици", text, re.I)) and not sig.is_limit

    if 1 not in tps:
        sig.errors.append("нет TP1")
        return sig
    if sl is None:
        sig.errors.append("нет стоп-лосса")
        return sig

    # Зона входа: первая строка (кроме TP/SL/TF), где есть числа порядка цены
    ref = tps[1]
    zone = None
    for line in text.split("\n"):
        if TP_RE.search(line) or SL_RE.search(line) or re.search(r"\bTF\b", line, re.I):
            continue
        nums = [_f(x) for x in re.findall(r"\d+(?:[.,]\d+)?", line)]
        nums = [x for x in nums if 0.8 * ref <= x <= 1.2 * ref]
        if nums:
            zone = nums[:2]
            break
    if not zone:
        sig.errors.append("не найдена точка входа")
        return sig
    sig.zone = (min(zone), max(zone))

    # --- Проверки здравого смысла ---
    s = 1 if side == "BUY" else -1
    mid = sig.mid
    ks = sorted(tps)
    if any(s * (tps[k] - mid) <= 0 for k in ks):
        sig.errors.append(
            f"направление {side} не сходится с тейками (тейки {'ниже' if s == 1 else 'выше'} входа)")
    if s * (mid - sl) <= 0:
        sig.errors.append(f"стоп {sl:g} не с той стороны от входа для {side}")
    for a, b in zip(ks, ks[1:]):
        if s * (tps[b] - tps[a]) < 0:
            sig.errors.append(f"тейки идут не по порядку (TP{a} {tps[a]:g}, TP{b} {tps[b]:g})")
            break
    width_pct = (sig.zone[1] - sig.zone[0]) / mid * 100
    if width_pct > max_zone_pct:
        sig.errors.append(f"слишком широкая зона входа {sig.zone[0]:g}–{sig.zone[1]:g} (опечатка?)")
    for k in ks:
        if abs(tps[k] - mid) / mid * 100 > max_tp_pct:
            sig.errors.append(f"TP{k} {tps[k]:g} слишком далеко от входа (опечатка?)")
    if abs(sl - mid) / mid * 100 > max_sl_pct:
        sig.errors.append(f"стоп {sl:g} слишком далеко от входа (опечатка?)")
    return sig


# ------------------------------------------------------------------ команды

@dataclass
class Command:
    kind: str                   # be | close | closed_report | sl | tp | cancel | unknown
    price: Optional[float] = None
    tp_index: Optional[int] = None
    note: str = ""

    def describe(self) -> str:
        names = {
            "be": "стоп в безубыток",
            "close": "закрыть по рынку",
            "closed_report": "канал сообщил о закрытии сделки",
            "sl": f"перенести стоп на {self.price:g}" if self.price else "перенести стоп",
            "tp": f"TP{self.tp_index} → {self.price:g}" if self.price else "изменить тейк",
            "cancel": "отменить лимитный ордер",
            "unknown": "непонятная команда",
        }
        return names.get(self.kind, self.kind)


ORD = {"перв": 1, "втор": 2, "трет": 3, "четв": 4}

BE_RE = re.compile(
    r"(стоп\w*|sl|с/л)[\s\-–]*(лос\w*)?\s*(переношу|переносим|переставляем|ставим|передвигаем|двигаем|перевожу|переводим)?\s*"
    r"(в|на)\s*(бу\b|б/у|безубыт|без\s+убыт|точку\s+входа|твх)"
    r"|не\s+забыва\w*\s+ставить\s+(безубыт|бу\b|б/у)"
    r"|(переводим|переносим|ставим)\s+(сделк\w+|позици\w+)?\s*(в|на)\s*(бу\b|б/у|безубыт)",
    re.I,
)
CLOSED_RE = re.compile(
    r"(сделк\w*|позици\w*|ордер\w*)?[^\n]{0,15}(закрыл\w*|выбило|закрывается\s+по\s+стопу)"
    r"|закрываемся\s+по\s+стопу|получаем\s+(\w+\s+)?(стоп|убыток|отмену)|ушел\s+по\s+sl|ушло\s+в\s+(бу|б/у)"
    r"|^\s*sl\s*$|закрылось|фиксируем\s+убыток|фиксирую\s+убыток|ушли\s+в\s+(бу|б/у)|полностью\s+отработал|полная\s+отработка|все\s+цели\s+достигнуты",
    re.I | re.M,
)
CLOSE_RE = re.compile(
    r"закрыва(ем|ю|ет)\s+(данную\s+|эту\s+)?(сделку|позицию)|закрываем\s+по\s+рынку|закрываю\s+[^\n]{0,30}по\s+рынку"
    r"|закрыть\s+позицию|сейчас\s+закрываем|закрываем\s+сейчас|закрываемся\s+по\s+рынку"
    r"|закрываю\s+(в\s+)?(безубыт\w*|без\s+убыт\w*|бу\b|б/у)",
    re.I,
)
SL_MOVE_RE = re.compile(
    r"(стоп[\s\-–]*лос\w*|стоп\w*|sl)[^\d\n]{0,40}?(переставл\w*|передвига\w*|перенос\w*|корректир\w*|ставим|двигаем)"
    r"[^\d\n]{0,25}?" + CNUM
    + r"|(корректировк\w*|переставл\w*|передвига\w*)\s+(стоп[\s\-–]*лос\w*|стоп\w*|sl)[^\d\n]{0,30}?" + CNUM,
    re.I,
)
SL_TO_TP_RE = re.compile(r"(без\s*убыт\w*|бу\b|б/у|стоп\w*)[^\d\n]{0,25}на\s+(перв|втор|трет)\w*\s+тейк[^\d\n]{0,10}" + CNUM, re.I)
TP_MOVE_RES = [
    re.compile(r"(\d)\s*/\s*\d\s*[tт][pр][^\d\n]{0,10}(корректир|перемещ|переставл|перенос)\w*[^\d\n]{0,25}" + CNUM, re.I),
    re.compile(r"корректировк\w*\s+(\d)\s*/\s*\d\s*[tт][pр][^\d\n]{0,30}?" + CNUM, re.I),
    re.compile(r"(перв|втор|трет|четв)\w*\s+тейк[^\d\n]{0,25}?" + CNUM, re.I),
    re.compile(r"[tт][pр]\s*(\d)\s+значени\w*\s*" + CNUM, re.I),
]
CANCEL_RE = re.compile(
    r"(отменя\w*|снима\w*|удаля\w*)\s+(лимит\w*|ордер\w*|сигнал\w*)|(лимит\w*|ордер\w*|сигнал\w*)\s+(отменя\w*|снима\w*|удаля\w*|отменен\w*)"
    r"|сигнал\s+не\s+актуал",
    re.I,
)
# слова, указывающие что сообщение, возможно, про действие, которое мы не распознали
NEGATION_RE = re.compile(r"пока\s+не\s+(переставля|перенош|переношу|двига)|не\s+переставля\w*|не\s+перенос\w*", re.I)
SUMMARY_RE = re.compile(r"^\W*(итог\w*\s+(дня|недели|месяца)|результаты\s+(дня|недели))|сработал\w*\s+лимит", re.I)
MAX_CMD_LEN = 350
ACTION_HINT_RE = re.compile(r"перезаход|пере\s+заход|закрыва|закрыть|частич|половин|лимитк|стоп\s+на|тейк\s+на|переставл|передвига", re.I)
# отчёт о взятом тейке — ничего делать не нужно, тейки стоят в ордерах
TP_REPORT_RE = re.compile(r"фиксиру\w*\s*\d\s*/\s*\d|✅\s*т[пp]\s*\d|^\s*t[pр]\s*\d\s*✅|зафиксировали", re.I | re.M)


def parse_command(text: str) -> list:
    """Список команд из сообщения (может быть пустым)."""
    if not text or looks_like_signal(text):
        return []
    t = text.lower().replace("ё", "е")
    cmds = []
    if SUMMARY_RE.search(t):
        return []
    if len(t) > MAX_CMD_LEN:
        # длинные посты — это аналитика; действия по ним не выполняем
        return []

    m = SL_TO_TP_RE.search(t)
    if m:
        return [Command("sl", price=_f(m.group(3)), note="стоп на уровень тейка")]

    for rx in TP_MOVE_RES:
        for m in rx.finditer(t):
            k, price = m.group(1), m.groups()[-1]
            k = ORD.get(k[:4], None) if not k.isdigit() else int(k)
            if k:
                cmds.append(Command("tp", price=_f(price), tp_index=k))
        if cmds:
            break

    m = SL_MOVE_RE.search(t)
    if m:
        price = m.group(3) or m.group(6)
        if price and len(price.split(".")[0].split(",")[0]) >= 3:
            cmds.append(Command("sl", price=_f(price)))

    if BE_RE.search(t) and not NEGATION_RE.search(t) and not any(c.kind == "sl" for c in cmds):
        cmds.append(Command("be"))

    if CANCEL_RE.search(t):
        cmds.append(Command("cancel"))

    if CLOSE_RE.search(t):
        cmds.append(Command("close"))
    elif CLOSED_RE.search(t) and not re.search(r"(стоп\w*)\s+уже\s+стоит", t):
        cmds.append(Command("closed_report"))

    if not cmds and not TP_REPORT_RE.search(t) and ACTION_HINT_RE.search(t):
        cmds.append(Command("unknown", note=text.strip()[:200]))
    return cmds
