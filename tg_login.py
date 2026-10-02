"""
Вход в Telegram по QR-коду (без SMS-кода) и проверка, что канал виден.
Запуск: fxctl tg
"""
import asyncio
import getpass

import qrcode
import yaml
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
tg = cfg["telegram"]


def show_qr(url):
    q = qrcode.QRCode(border=1)
    q.add_data(url)
    q.make(fit=True)
    print("\n" * 2)
    q.print_ascii(invert=True)
    print("Телефон: Telegram → Настройки → Устройства → «Подключить устройство» → наведите камеру на QR выше.")
    print("(QR обновляется каждые ~30 секунд)\n")


async def main():
    client = TelegramClient("session", int(tg["api_id"]), tg["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        qr = await client.qr_login()
        while True:
            show_qr(qr.url)
            try:
                await qr.wait(timeout=30)
                break
            except asyncio.TimeoutError:
                await qr.recreate()
            except SessionPasswordNeededError:
                pw = getpass.getpass("У аккаунта включён облачный пароль Telegram. Введите его (не отображается): ")
                await client.sign_in(password=pw)
                break
    me = await client.get_me()
    print(f"\n✅ Вход выполнен: {me.first_name or ''} (@{me.username or '—'})")
    want = str(tg.get("channel", "")).lower()
    found = None
    print("\nВаши каналы:")
    async for d in client.iter_dialogs():
        if d.is_channel:
            mark = ""
            if want and want in (d.name or "").lower() and not found:
                found, mark = d, "   ← этот канал будет читать копировщик"
            print(f"  {d.name}{mark}")
    if found:
        msgs = await client.get_messages(found.entity, limit=1)
        last = (msgs[0].message or "").replace("\n", " | ")[:120] if msgs else "—"
        print(f"\n✅ Канал найден: «{found.name}». Последнее сообщение: {last}")
    else:
        print(f"\n⚠️ Канал с «{tg.get('channel')}» в названии не найден — пришлите этот список Claude.")
    await client.send_message("me", "✅ Копировщик сигналов подключён к вашему Telegram.")
    await client.disconnect()


asyncio.run(main())
