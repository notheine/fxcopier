"""Мост RPyC к библиотеке MetaTrader5. Запускается Windows-Python'ом под Wine (только на Linux-сервере)."""
from rpyc.core.service import ClassicService
from rpyc.utils.server import ThreadedServer

if __name__ == "__main__":
    print("MT5 bridge on 127.0.0.1:18812", flush=True)
    ThreadedServer(ClassicService, hostname="127.0.0.1", port=18812, reuse_addr=True,
                   protocol_config={"allow_public_attrs": True, "allow_all_attrs": True,
                                    "sync_request_timeout": 120}).start()
