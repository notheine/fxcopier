"""
Подключение к MetaTrader 5.

- На Windows используется официальная библиотека MetaTrader5 напрямую.
- На Linux терминал и Windows-Python работают под Wine, а сюда библиотека
  пробрасывается через RPyC (сервер запускается командой
  `wine python -m rpyc.cli.rpyc_classic --host 127.0.0.1 --port 18812`).
"""
import platform


class MT5Link:
    def __init__(self, host="127.0.0.1", port=18812):
        self.remote = platform.system() != "Windows"
        self.host, self.port = host, port
        self.conn = None
        self._connect_lib()

    def _connect_lib(self):
        if self.remote:
            import rpyc
            self.conn = rpyc.classic.connect(self.host, self.port)
            self.conn._config["sync_request_timeout"] = 60
            self.conn.execute("import MetaTrader5 as mt5")
            self.m = self.conn.modules.MetaTrader5
        else:
            import MetaTrader5
            self.m = MetaTrader5

    def reconnect_lib(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self._connect_lib()

    # константы (ORDER_TYPE_BUY и т.п.) — простые числа, читаем как есть
    def __getattr__(self, name):
        if name.isupper():
            return getattr(self.m, name)
        raise AttributeError(name)

    # --- вызовы, которым нужны «настоящие» словари на стороне Windows ---
    def order_send(self, request: dict):
        if self.remote:
            return self.conn.eval("mt5.order_send(%r)" % (request,))
        return self.m.order_send(request)

    def initialize(self, **kw):
        if self.remote:
            args = ", ".join(f"{k}={v!r}" for k, v in kw.items() if v not in (None, ""))
            return self.conn.eval(f"mt5.initialize({args})")
        return self.m.initialize(**{k: v for k, v in kw.items() if v not in (None, "")})

    # --- остальные вызовы: аргументы простые (числа/строки) ---
    def shutdown(self):
        return self.m.shutdown()

    def last_error(self):
        return tuple(self.m.last_error())

    def account_info(self):
        return self.m.account_info()

    def terminal_info(self):
        return self.m.terminal_info()

    def symbol_info(self, s):
        return self.m.symbol_info(s)

    def symbol_info_tick(self, s):
        return self.m.symbol_info_tick(s)

    def symbol_select(self, s, on=True):
        return self.m.symbol_select(s, on)

    def positions_get(self, symbol=None):
        r = self.m.positions_get(symbol=symbol) if symbol else self.m.positions_get()
        return list(r) if r else []

    def orders_get(self, symbol=None):
        r = self.m.orders_get(symbol=symbol) if symbol else self.m.orders_get()
        return list(r) if r else []

    def order_calc_margin(self, order_type, symbol, volume, price):
        return self.m.order_calc_margin(order_type, symbol, volume, price)

    def history_deals_get(self, ts_from: int, ts_to: int):
        r = self.m.history_deals_get(int(ts_from), int(ts_to))
        return list(r) if r else []

    def price_extremes(self, symbol: str, ts_from: float, ts_to: float):
        """Максимум и минимум цены (bid, минутные свечи) за период в UTC-секундах. None — нет данных."""
        import time as _t
        tick = self.m.symbol_info_tick(symbol)
        off = round((int(tick.time) - _t.time()) / 3600) * 3600 if tick else 0   # время сервера брокера
        a, b = int(ts_from + off) - 60, int(ts_to + off) + 60
        if self.remote:
            r = self.conn.eval(f"(lambda r: None if r is None or len(r) == 0 else (float(max(r['high'])), "
                               f"float(min(r['low']))))(mt5.copy_rates_range({symbol!r}, mt5.TIMEFRAME_M1, {a}, {b}))")
            return tuple(r) if r else None
        r = self.m.copy_rates_range(symbol, self.m.TIMEFRAME_M1, a, b)
        if r is None or len(r) == 0:
            return None
        return float(max(r["high"])), float(min(r["low"]))

    def rates(self, symbol: str, tf: str = "M30", count: int = 200, start: int = 1):
        """Закрытые свечи (start=1 — без текущей): список (время сервера, open, high, low, close), старые первыми."""
        if self.remote:   # одной строкой — так в разы быстрее, чем поэлементно через мост
            s = str(self.conn.eval(
                f"(lambda r: '' if r is None else ';'.join('%d,%r,%r,%r,%r' % (x['time'], float(x['open']), "
                f"float(x['high']), float(x['low']), float(x['close'])) for x in r))"
                f"(mt5.copy_rates_from_pos({symbol!r}, mt5.TIMEFRAME_{tf}, {int(start)}, {int(count)}))"))
            out = []
            for row in s.split(";") if s else []:
                t, o, h, lo, c = row.split(",")
                out.append((int(t), float(o), float(h), float(lo), float(c)))
            return out
        r = self.m.copy_rates_from_pos(symbol, getattr(self.m, f"TIMEFRAME_{tf}"), int(start), int(count))
        return [] if r is None else [(int(x["time"]), float(x["open"]), float(x["high"]), float(x["low"]),
                                      float(x["close"])) for x in r]
