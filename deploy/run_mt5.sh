#!/bin/bash
# Запуск терминала MT5 под Wine и ожидание, пока он работает (для systemd).
# Терминал может перезапускать сам себя (обновления) — поэтому следим за процессом, а не за командой wine.
EXE="$1"
wine "$EXE" /portable /config:'C:\fx\start.ini' &
sleep 20
while pgrep -u "$(id -u)" -f terminal64.exe >/dev/null; do sleep 5; done
echo "terminal64.exe завершился"
exit 1
