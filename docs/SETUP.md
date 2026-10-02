# Развёртывание с нуля

Полная инструкция: от пустого аккаунта у хостера до работающего копировщика.
Время: ~40 минут, из них ~15 — автоматическая установка.

## 0. Что понадобится

| Что | Где взять |
|---|---|
| Демо- (или реальный) счёт FxPro MT5, тип **Hedging** | Приложение FxPro → «Демо-счёт» → Raw+ → **Hedging**. Netting не подходит: 3 позиции на сигнал сольются в одну |
| Номер счёта, пароль, сервер (`FxPro-MT5 Demo` / `FxPro-MT5 Live…`) | Там же |
| Telegram-аккаунт, который **состоит в канале** с сигналами | Сейчас: второй аккаунт владельца |
| `api_id` и `api_hash` этого аккаунта | https://my.telegram.org → API development tools → Create application (Platform: Desktop) |
| Основной Telegram владельца (username) | Сюда и в группу будут приходить отчёты |
| Mac/PC с Терминалом и SSH | — |

## 1. Сервер

Сейчас используется **Timeweb Cloud**, но подойдёт любой VPS.

- ОС: **Ubuntu 24.04 или 26.04** (проверено на 26.04 «resolute»)
- Регион: **Амстердам** (рядом серверы FxPro в Лондоне и Telegram)
- Конфигурация: **1×3.3 ГГц, 2 ГБ RAM, 30 ГБ** — достаточно (MT5 под Wine + мост ≈ 1 ГБ)
- Публичный IPv4: **да**; доступ по паролю — разрешён (потом можно добавить SSH-ключ)
- Стоимость на 10.2026: ~1 340 ₽/мес

> Windows-сервер не нужен: MT5 работает под Wine. Если когда-нибудь Wine начнёт сбоить —
> копировщик работает и на Windows без изменений кода (см. ARCHITECTURE.md → «Windows»).

## 2. Код на сервер и установка

На своём компьютере:

```bash
git clone https://github.com/notheine/fxcopier.git ~/FxCopier
cd ~/FxCopier && tar -cf - . | ssh root@<IP> "rm -rf /root/fxcopier && mkdir /root/fxcopier && tar -xf - -C /root/fxcopier && systemd-run --unit=fx-install --collect bash /root/fxcopier/deploy/install.sh && sleep 2 && journalctl -fu fx-install -o cat --since=-30s"
```

Установка идёт **фоном на сервере** (обрыв SSH её не прервёт). Окончание — строка
`Готово. Дальше: fxctl setup`, после неё Ctrl+C.

Что делает `deploy/install.sh` (повторный запуск безопасен):
1. переключает apt на официальное зеркало Ubuntu (зеркало Timeweb из Амстердама недоступно), отключает репозиторий zabbix Timeweb;
2. ставит пакеты, swap 2 ГБ;
3. ставит **Wine 10** (Wine 11 ломает установщик MT5 — см. RUNBOOK) и закрепляет версию;
4. создаёт пользователя `fx` и Wine-префикс, отключает отладчик Wine;
5. Windows-Python 3.11 (из NuGet-архива, без GUI-установщика) + `MetaTrader5`, `numpy==1.26.4`, `rpyc==6.0.1`;
6. **фирменный MT5 от FxPro** (`fxpro5setup.exe`) + WebView2;
7. Linux-часть копировщика (venv) и команду `fxctl`;
8. службы systemd: `fx-xvfb`, `fx-mt5`, `fx-bridge`, `fx-copier`.

## 3. Счёт MT5

```bash
ssh -t root@<IP> fxctl mt5
```
Ввести номер счёта, пароль, сервер (Enter = `FxPro-MT5 Demo`). Терминал запустится и войдёт в счёт.

Проверка сделкой (только демо; откроет 3 позиции по 0.01 по последнему сигналу из `test_trade.py`):
```bash
ssh -t root@<IP> fxctl test
ssh root@<IP> fxctl test-close
```

## 4. Telegram

```bash
ssh -t root@<IP> fxctl tg
```
Ввести `api_id`, `api_hash`, часть названия канала (Enter = `Win Win`). Появится **QR-код** —
отсканировать: Telegram на телефоне → Настройки → Устройства → Подключить устройство.
Если включён облачный пароль — спросит его.

Если заявка в канал ещё не одобрена — ничего страшного: копировщик ждёт и проверяет раз в минуту.

## 5. Бот и группа для отчётов

```bash
ssh -t root@<IP> fxctl bot <основной_username_без_@>
```
От аккаунта копировщика через @BotFather создаётся бот, создаётся группа «FxCopier — сделки»
(аккаунт копировщика + владелец + бот). Если настройки приватности не дают добавить владельца —
ему в личку придёт ссылка-приглашение. Всё записывается в `config.yaml`.

## 6. Режим

```bash
ssh root@<IP> fxctl mode dry_run   # только разбор, без сделок
ssh root@<IP> fxctl mode demo      # торговля, откажется работать на реальном счёте
ssh -t root@<IP> fxctl mode live   # реальный счёт, спросит подтверждение «ДА»
```

## 7. Проверка

```bash
ssh root@<IP> fxctl status    # все 4 службы active
ssh root@<IP> fxctl logs      # журнал
```
В группе: «🚀 Копировщик запущен», команды `/status`, `/report`.

## Перенос на новый сервер

Секреты на сервере в `/home/fx/copier/`: `config.yaml`, `session.session` (аккаунт Telegram),
`bot_session.session`, а также `state.json`, `journal.jsonl` (история для отчётов).
Проще всего пройти шаги 2–5 заново; чтобы сохранить историю отчётов — скопировать `state.json` и `journal.jsonl`.
