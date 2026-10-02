# FxCopier — копировщик сигналов Telegram → MetaTrader 5

Читает Telegram-канал с торговыми сигналами (золото) и за ~1 секунду открывает сделки на счёте FxPro MT5:
3 позиции на сигнал (TP1/TP2/TP3), лот по таблице, стоп в безубыток при касании TP1,
выполняет команды канала («закрываем по рынку», «стоп на …», «третий тейк на …»).
Отчёты и каждое действие — от бота в группу Telegram; ежедневные/недельные/месячные итоги.

Работает 24/7 на дешёвом Linux-VPS (MT5 под Wine) — ~1 340 ₽/мес.

## Документация

| | |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | **Развёртывание с нуля** — сервер, установка, счёт, Telegram, бот |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Как устроено: компоненты, службы, логика торговли, отчёты |
| [docs/SIGNALS.md](docs/SIGNALS.md) | Форматы сигналов канала, команды и как они исполняются, проверка на истории |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Эксплуатация, сбои и их решения, обновление, переход на реальный счёт |
| [CLAUDE.md](CLAUDE.md) | Контекст для ИИ-помощника, который продолжит работу над проектом |

## Коротко

```bash
# установка на чистый Ubuntu (с вашего компьютера)
git clone https://github.com/notheine/fxcopier.git ~/FxCopier
cd ~/FxCopier && tar -cf - . | ssh root@<IP> "mkdir -p /root/fxcopier && tar -xf - -C /root/fxcopier && systemd-run --unit=fx-install --collect bash /root/fxcopier/deploy/install.sh"

ssh -t root@<IP> fxctl mt5           # счёт MT5
ssh -t root@<IP> fxctl tg            # вход в Telegram по QR
ssh -t root@<IP> fxctl bot <username>  # бот + группа для отчётов
ssh root@<IP> fxctl mode demo        # торговля на демо
```

Команды в группе: `/status` `/report` `/report week` `/report month` `/pause` `/resume` `/closeall` `/help`

## Безопасность

- В репозитории **нет секретов**: `config.yaml`, сессии Telegram, токен бота, история — только на сервере (`.gitignore`).
- Режим `demo` не запустится на реальном счёте; `live` требует явного подтверждения.
- Сигнал с ошибкой или непонятная команда → не торгует, пишет в группу.
- Стопы и тейки стоят на сервере брокера — сбой сервера копировщика не оставляет позиции без защиты.

## Тесты

```bash
python tests/test_core.py
```
