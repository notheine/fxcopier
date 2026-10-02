# Эксплуатация и известные проблемы

## Ежедневное

Всё приходит в группу «FxCopier — сделки» от бота. Команды там же:
`/status`, `/report`, `/report week`, `/report month`, `/pause`, `/resume`, `/closeall`, `/help`.

На сервере: `fxctl status`, `fxctl logs`, `fxctl restart`, `fxctl mode …`.
Логи: `/home/fx/copier/logs/copier.log`, `journalctl -u fx-mt5 -u fx-bridge -u fx-copier`.
Журнал терминала: `/home/fx/.wine/drive_c/Program Files/FxPro Markets MT5/logs/<дата>.log` (UTF-16: `iconv -c -f UTF-16LE -t UTF-8`).

## Если в группе «🚨 Нет связи с MT5»

Стопы/тейки стоят на сервере брокера и работают. Не работают новые входы и перенос в БУ.
```bash
fxctl status
systemctl restart fx-mt5        # перезапустит и мост (PartOf)
fxctl logs
```
Посмотреть экран терминала: `fxctl vnc` + `ssh -L 5900:localhost:5900 root@<IP>` + `vnc://localhost:5900`.
Снимок экрана без VNC: `DISPLAY=:99 import -window root /tmp/s.png` (ImageMagick стоит).

## Грабли, на которые уже наступили (и решения в коде)

| Симптом | Причина | Решение |
|---|---|---|
| `apt` не может скачать с mirror.timeweb.ru | Зеркало Timeweb недоступно из Амстердама | install.sh переключает на archive.ubuntu.com |
| `NO_PUBKEY` / «unsupported filetype» у WineHQ | apt 3 (Ubuntu 26.04) не принимает .key | ключ через `gpg --dearmor` → `.gpg` |
| Установщик MT5: «A debugger has been found running in your system» | **Wine 11** | ставим и `apt-mark hold` **Wine 10** |
| `wineserver: command not found` | в сборке Ubuntu он вне PATH | симлинк в /usr/local/bin |
| Установщик Python падает (X BadWindow) | GUI-установщик под Xvfb | Python из NuGet-zip |
| `ucrtbase.dll.crealf unimplemented` при `import MetaTrader5` | numpy 2.x под Wine 10 | `numpy==1.26.4` |
| Установщик MT5 «завершился», терминала нет | веб-установщик работает в фоне | ждать появления terminal64.exe до 10 мин |
| `IPC timeout (-10005)` | универсальный MT5 не знает серверов FxPro → не логинится | **фирменный** `fxpro5setup.exe` |
| `python -m rpyc.cli.rpyc_classic` сразу выходит | в rpyc 6 нет `__main__` | свой `bridge_server.py` |
| Служба fx-mt5 «падает», терминал жив | wine отцепляется, терминал перезапускает сам себя | `run_mt5.sh` следит за процессом |
| fx-mt5 долго «deactivating» | процессы Wine не умирают по SIGTERM | `ExecStop=wineserver -k`, `TimeoutStopSec=20` |
| Окно «Login» висит после запуска без /config | терминал запущен не через службу | запускать только службой (там /config с логином) |
| SSH-сессия рвётся на долгих шагах | — | установка через `systemd-run`, просмотр `journalctl -f` |
| Тестовая сделка вошла выше зоны SELL у самого стопа | ошибка в `decide_entry` | исправлено: вход только в зоне ±$2 |

## Обновление кода на сервере

```bash
cd ~/FxCopier && git pull
tar --exclude .git -cf - . | ssh root@<IP> "tar -xf - -C /home/fx/copier && chown -R fx:fx /home/fx/copier && install -m 755 /home/fx/copier/deploy/fxctl /usr/local/bin/fxctl && systemctl restart fx-copier"
```
`config.yaml`, сессии и история на сервере при этом не затрагиваются (их нет в репозитории).
Новые параметры конфига смотреть в `config.example.yaml` (у отсутствующих в `config.yaml` есть значения по умолчанию в коде).

## Перед переходом на реальный счёт

1. 1–2 недели демо, смотреть недельные отчёты и пропуски.
2. Реальный счёт тоже **Hedging**. `fxctl mt5` с его данными (сервер `FxPro-MT5 Live…`).
3. Проверить `lot_table` / `fixed_total_lot`: по таблице куратора один стоп ≈ 4–7% депозита.
4. `fxctl mode live`.
