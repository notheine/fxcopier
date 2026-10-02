#!/usr/bin/env bash
# Установка копировщика на чистый Ubuntu 24.04 (запускать от root).
#   bash install.sh
# Ставит: Wine, MetaTrader 5, Windows-Python с библиотекой MetaTrader5, мост RPyC,
# сам копировщик и службы systemd. Повторный запуск безопасен.
set -euo pipefail

APP_USER=fx
APP_HOME=/home/$APP_USER
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"     # папка с кодом (где лежит main.py)
DST=$APP_HOME/copier
PREFIX=$APP_HOME/.wine
PY_VER=3.11.9
log() { echo -e "\n\033[1;32m==> $*\033[0m"; }

[ "$(id -u)" = 0 ] || { echo "Запустите от root"; exit 1; }
# остатки прошлых попыток (зависшие процессы Wine)
systemctl stop fx-copier fx-bridge fx-mt5 2>/dev/null || true
pkill -9 -u $APP_USER 2>/dev/null || true

log "0/8 Источники пакетов"
export DEBIAN_FRONTEND=noninteractive
# убираем недоделанные следы прошлой попытки
rm -f /etc/apt/sources.list.d/winehq*.sources /etc/apt/keyrings/winehq-archive.key
# репозиторий мониторинга Timeweb (zabbix) недоступен — отключаем
for f in /etc/apt/sources.list.d/*; do
  [ -f "$f" ] && grep -q "zabbix.repo.timeweb.ru" "$f" && mv "$f" "$f.disabled" || true
done
# зеркало Timeweb недоступно из Амстердама — переключаемся на официальное
MIRROR=http://archive.ubuntu.com/ubuntu
wget -q --spider -T 10 "$MIRROR/dists/" || MIRROR=https://mirrors.edge.kernel.org/ubuntu
for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
  [ -f "$f" ] && sed -i -E "s#https?://mirror\.timeweb\.ru/ubuntu/?#$MIRROR/#g" "$f" || true
done
echo "Зеркало: $MIRROR"

log "1/8 Пакеты системы"
dpkg --add-architecture i386
apt-get update -q
apt-get install -y -q wget curl ca-certificates gnupg xvfb x11vnc xdotool imagemagick python3-venv python3-pip unzip cabextract tzdata

log "2/8 Swap 2 ГБ (запас памяти для Wine)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "3/8 Wine 10"
# MT5 не ставится под Wine 11 («A debugger has been found…») — нужен Wine 10 (или 9)
wine_major() { wine --version 2>/dev/null | sed -E 's/^wine-([0-9]+).*/\1/'; }
if command -v wine >/dev/null && [ "$(wine_major)" -ge 11 ]; then
  echo "Установлен $(wine --version) — удаляю, MT5 под ним не работает"
  pkill -9 -u $APP_USER 2>/dev/null || true
  apt-get remove -y -q --purge $(dpkg-query -W -f='${Package}\n' | grep -E '^(winehq|wine)' ) || true
  rm -f /etc/apt/sources.list.d/winehq*.sources
  rm -rf "$PREFIX"            # префикс от Wine 11 не переиспользуем
fi
if ! command -v wine >/dev/null; then
  apt-get update -q
  CAND="$(apt-cache policy wine | awk '/Candidate:/{print $2}')"
  if echo "$CAND" | grep -qE '^([0-9]+:)?(9|10)\.'; then
    echo "Ставлю Wine $CAND из репозитория Ubuntu"
    apt-get install -y -q wine
  else
    echo "В Ubuntu Wine $CAND — ставлю Wine 10 из WineHQ (сборка для Ubuntu 24.04)"
    mkdir -pm755 /etc/apt/keyrings
    wget -qO- https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor --yes -o /etc/apt/keyrings/winehq-archive.gpg
    wget -qO- https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources \
      | sed 's#/etc/apt/keyrings/winehq-archive.key#/etc/apt/keyrings/winehq-archive.gpg#' > /etc/apt/sources.list.d/winehq.sources
    apt-get update -q
    V=10.0.0.0~noble-1
    apt-get install -y -q --install-recommends winehq-stable=$V wine-stable=$V wine-stable-amd64=$V wine-stable-i386=$V
    apt-mark hold winehq-stable wine-stable wine-stable-amd64 wine-stable-i386
  fi
fi
WINE_BIN="$(command -v wine)"
# в сборке Ubuntu wineserver не лежит в PATH — делаем ссылку
if ! command -v wineserver >/dev/null; then
  WS="$(find /usr/lib /usr/libexec /opt -type f -name 'wineserver*' -perm -u+x 2>/dev/null | grep -v '\.so' | head -1 || true)"
  [ -n "$WS" ] && ln -sf "$WS" /usr/local/bin/wineserver && echo "wineserver: $WS"
fi
command -v wineserver >/dev/null || { echo "Не найден wineserver"; exit 1; }
"$WINE_BIN" --version
[ "$(wine_major)" -le 10 ] || { echo "Не удалось поставить Wine 10"; exit 1; }

log "4/8 Пользователь $APP_USER и префикс Wine"
id $APP_USER >/dev/null 2>&1 || useradd -m -s /bin/bash $APP_USER
mkdir -p $DST
cp -r "$SRC_DIR"/. $DST/
chown -R $APP_USER:$APP_USER $APP_HOME

# постоянный виртуальный экран на время установки (графические установщики Windows)
pkill -f "Xvfb :98" 2>/dev/null || true
Xvfb :98 -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
XPID=$!
trap 'kill $XPID 2>/dev/null || true' EXIT
sleep 2

as_fx() { sudo -u $APP_USER -H env DISPLAY=:98 WINEPREFIX=$PREFIX WINEARCH=win64 WINEDEBUG=-all \
          WINEDLLOVERRIDES="mscoree,mshtml=" bash -c "$*"; }

if [ ! -f $PREFIX/system.reg ]; then
  as_fx "wineboot -i && wineserver -w"
fi
as_fx "winecfg -v win10 && wineserver -w" || true
# не запускать отладчик Wine при сбоях (иначе процесс «висит»)
as_fx "wine reg add 'HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug' /v Debugger /d '' /f && wineserver -w" >/dev/null 2>&1 || true

log "5/8 Windows-Python $PY_VER + библиотека MetaTrader5"
# Python берём из пакета NuGet (обычный zip) — без графического установщика
WINPY="$PREFIX/drive_c/Python311/python.exe"
if [ ! -f "$WINPY" ]; then
  as_fx "rm -rf /tmp/pynuget && mkdir -p /tmp/pynuget && cd /tmp/pynuget && \
         wget -q https://www.nuget.org/api/v2/package/python/$PY_VER -O py.zip && unzip -q py.zip && \
         mkdir -p '$PREFIX/drive_c/Python311' && cp -r tools/. '$PREFIX/drive_c/Python311/'"
  rm -rf /tmp/pynuget
fi
[ -f "$WINPY" ] || { echo "Windows-Python не установился — пришлите вывод"; exit 1; }
as_fx "wine '$WINPY' -m pip --version" >/dev/null 2>&1 || \
  as_fx "wine '$WINPY' -m ensurepip --upgrade" || \
  as_fx "cd /tmp && wget -q https://bootstrap.pypa.io/get-pip.py -O get-pip.py && wine '$WINPY' get-pip.py"
as_fx "wine '$WINPY' -m pip install -q --disable-pip-version-check --no-warn-script-location MetaTrader5 numpy==1.26.4 rpyc==6.0.1"
as_fx "wine '$WINPY' -c 'import MetaTrader5, rpyc; print(\"MetaTrader5\", MetaTrader5.__version__)'"

log "6/8 MetaTrader 5"
# фирменный MT5 от FxPro (в нём уже есть серверы брокера; универсальный MT5 их не знает)
find_mt5() { find "$PREFIX/drive_c" -ipath "*fxpro*" -iname terminal64.exe 2>/dev/null | head -1 || true; }
MT5_EXE="$(find_mt5)"
windows_list() { DISPLAY=:98 xdotool search --onlyvisible --name '.' 2>/dev/null | while read w; do DISPLAY=:98 xdotool getwindowname "$w"; done | sort -u | tr '\n' '|'; }
snapshot() { DISPLAY=:98 import -window root /root/fx_screen.png 2>/dev/null && echo "  снимок экрана: /root/fx_screen.png"; }
if [ -z "$MT5_EXE" ]; then
  # WebView2 Runtime — нужен свежим версиям MT5 (так делает и официальный скрипт MetaQuotes для Linux)
  if [ ! -d "$PREFIX/drive_c/Program Files (x86)/Microsoft/EdgeWebView" ]; then
    echo "  ставлю WebView2 Runtime…"
    as_fx "cd /tmp && wget -q 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -O webview2.exe && \
           timeout 600 wine webview2.exe /silent /install; timeout 120 wineserver -w; rm -f webview2.exe" || echo "  (WebView2 не встал — продолжаю без него)"
  fi
  as_fx "cd /tmp && wget -q https://fxpro-cdn.cloud/repo/website/assets/apps/fxpro5setup.exe -O mt5setup.exe"
  ls -la /tmp/mt5setup.exe
  # установщик качает ~100 МБ и может работать в фоне — запускаем и ждём появления терминала (до 10 мин)
  as_fx "cd /tmp && WINEDEBUG=err+all wine mt5setup.exe /auto" > /tmp/mt5setup.log 2>&1 &
  for i in $(seq 1 120); do
    MT5_EXE="$(find_mt5)"
    [ -n "$MT5_EXE" ] && break
    if [ $((i % 6)) = 0 ]; then
      echo "  …жду установку MT5 ($((i * 5)) с); окна: $(windows_list)"
      grep -q NtRaiseHardError /tmp/mt5setup.log && { echo "  установщик показал окно с ошибкой"; break; }
    fi
    sleep 5
  done
  if [ -n "$MT5_EXE" ]; then
    echo "  терминал появился, даю установщику закончить…"
    sleep 60
  else
    snapshot || true
  fi
  as_fx "wineserver -k" || true
  sleep 3
  MT5_EXE="$(find_mt5)"
fi
if [ -z "$MT5_EXE" ]; then
  echo "MetaTrader 5 не установился. Журнал установщика:"
  tail -40 /tmp/mt5setup.log || true
  exit 1
fi
MT5_WIN="C:\\${MT5_EXE#$PREFIX/drive_c/}"; MT5_WIN="${MT5_WIN//\//\\}"
echo "MetaTrader 5 установлен: $MT5_EXE  ($MT5_WIN)"

log "7/8 Копировщик (Linux-часть)"
as_fx "cd $DST && python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt"
install -m 755 $DST/deploy/fxctl /usr/local/bin/fxctl
echo "$MT5_WIN" > $DST/mt5_path.txt; chown $APP_USER:$APP_USER $DST/mt5_path.txt

log "8/8 Службы systemd"
cat > /etc/systemd/system/fx-xvfb.service <<EOF
[Unit]
Description=Virtual display for MT5
[Service]
User=$APP_USER
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/fx-mt5.service <<EOF
[Unit]
Description=MetaTrader 5 terminal (Wine)
After=fx-xvfb.service network-online.target
Requires=fx-xvfb.service
[Service]
User=$APP_USER
Environment=DISPLAY=:99 WINEPREFIX=$PREFIX WINEDEBUG=-all WINEDLLOVERRIDES=mscoree,mshtml=
ExecStart=/bin/bash $DST/deploy/run_mt5.sh "$MT5_EXE"
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/fx-bridge.service <<EOF
[Unit]
Description=RPyC bridge to MetaTrader5 python lib (Wine)
After=fx-mt5.service
Requires=fx-xvfb.service
[Service]
User=$APP_USER
Environment=DISPLAY=:99 WINEPREFIX=$PREFIX WINEDEBUG=-all WINEDLLOVERRIDES=mscoree,mshtml=
WorkingDirectory=$DST
ExecStart=$WINE_BIN $PREFIX/drive_c/Python311/python.exe bridge_server.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/fx-copier.service <<EOF
[Unit]
Description=Telegram -> MT5 signal copier
After=fx-bridge.service network-online.target
Wants=network-online.target
[Service]
User=$APP_USER
WorkingDirectory=$DST
ExecStartPre=/bin/sleep 15
ExecStart=$DST/venv/bin/python main.py
Restart=always
RestartSec=20
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now fx-xvfb.service

echo
echo "Готово. Дальше:  fxctl setup   (ввести данные Telegram и счёта MT5)"
