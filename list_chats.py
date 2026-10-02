"""Показывает ваши каналы и их ID — чтобы указать нужный в config.yaml."""
import yaml
from telethon.sync import TelegramClient

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
with TelegramClient("session", int(cfg["telegram"]["api_id"]), cfg["telegram"]["api_hash"]) as c:
    for d in c.iter_dialogs():
        if d.is_channel:
            print(f"{d.id:>16}  {d.name}")
