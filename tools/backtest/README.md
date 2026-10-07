# Прогоны истории канала (бэктест)

Прогон = **настоящий** `main.py` и `trader.py` + брокер-имитатор `tests/fake_mt5.py` + все сообщения канала
(сигналы и команды) + свечи GOLD FxPro. Упрощённые модели не использовать: 07.10 такая модель расходилась с настоящей.

## Данные
- Сервер: `/home/fx/data/` — `channel.json`, `gold_m5.csv` (с 25.05), `gold_m1.csv` (последние ~3.5 мес), `bt/` (готовая папка для прогонов).
- Копия у владельца на Mac: `~/FxCopier/_diag/`. Итоги и таблица сигналов — в документах проекта Claude «Fx»
  (`README_данные_и_прогоны.md`, `signals.csv`, `channel_messages.jsonl`, отчёты прогонов).
- Обновить: `fetch_channel.py`, `fetch_prices.py` (см. шапки файлов), затем `prepare_data.py`.

## Запуск
```
python3 -m venv venv && venv/bin/pip install telethon pyyaml tzdata
DATA=/путь/bt PY=venv/bin/python tools/backtest/run.sh BASE
DATA=/путь/bt PY=venv/bin/python tools/backtest/run.sh S2 CHASE_SELL=2
```
Переменные: `SYM` (GOLD | BTC; для BTC — `btc_hybrid.csv`, стопы брокера не ближе $200, `BTCRISK` % риска), `CH` (файл сообщений), `START`, `END` (2026-5-27), `BAL` (1000), `COMM` ($ за лот за круг, 8), `ELB`/`ELS` (точка входа 0..1),
`LOTX`, `SPLIT` (fill1/fill2/fill3 — полный лот по таблице), `BENEAR` (ранний БУ), `BEOFF`, `AUTOBE=0`, `BEK2=1` (БУ на TP2), `HALVE=0`, `BUYX`/`SELLX`, `MAXACT`, `EXP` (мин), `CHASE_BUY`/`CHASE_SELL` (правило куратора №4 — работают только с trader.py из коммита 85c7323, в основном коде правила нет). Другие настройки — правкой `config.yaml` в папке данных.
Один прогон ≈ 4 мин на 1 ядре; варианты можно запускать параллельно (каждый в своей `r_TAG`).
Защита (guard) и вопросы кнопками выключены — считается, что владелец жмёт «входить».

## Проверка стенда
#1338 (07.10): без правила — лимитка 4114.2, снята при TP1; с `CHASE_SELL=2` — вход по рынку 4113.05, итог +$27.91.
