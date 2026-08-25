"""
Minimal Minecraft-сервер на чистом Python.

Этап 1: сервер отвечает на пинг (виден в списке серверов, показывает MOTD,
онлайн игроков, версию) и умеет принять Handshake.

Дальше будем добавлять: Login -> Play (спавн игрока, чанки, движение).
"""
import socket
import threading
import traceback

from protocol import (
    PacketBuffer,
    read_packet,
    send_packet,
    write_string,
    write_json,
    write_varint,
    write_long,
)
from play import handle_play

HOST = "0.0.0.0"
PORT = 25565

# Версия протокола, под которую пишем сервер.
# 763 = Minecraft 1.20.1. Если у вас другая версия клиента - скажи, поменяем.
PROTOCOL_VERSION = 763
GAME_VERSION_NAME = "1.20.1"

MOTD = "§aPython Server §7| §fНаписан с нуля"
MAX_PLAYERS = 20


def handle_status(sock: socket.socket):
    """Обрабатывает состояние Status: отдаёт JSON с описанием сервера + отвечает на Ping."""
    while True:
        pkt = read_packet(sock)
        packet_id = pkt.read_varint()

        if packet_id == 0x00:
            # Status Request -> отвечаем Status Response
            response = {
                "version": {"name": GAME_VERSION_NAME, "protocol": PROTOCOL_VERSION},
                "players": {
                    "max": MAX_PLAYERS,
                    "online": 0,
                    "sample": [],
                },
                "description": {"text": MOTD},
            }
            send_packet(sock, 0x00, write_json(response))

        elif packet_id == 0x01:
            # Ping request -> Pong response (просто эхо long-числа)
            payload = pkt.read(8)
            send_packet(sock, 0x01, payload)
            return  # после понга клиент сам закрывает соединение


def handle_login(sock: socket.socket, addr):
    """Обрабатывает состояние Login: читает ник, отправляет Login Success
    (offline-mode - без проверки через Mojang), затем передаёт управление
    в Play-состояние."""
    pkt = read_packet(sock)
    packet_id = pkt.read_varint()

    if packet_id != 0x00:
        return

    username = pkt.read_string()
    print(f"[LOGIN] {addr} входит как '{username}'")

    # Login Success (0x02): UUID (16 байт) + Username + Properties count (0)
    import hashlib
    # offline-mode UUID - детерминированный хэш от "OfflinePlayer:<ник>",
    # так же как это делает vanilla-сервер в offline-mode.
    digest = hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest()
    uuid_bytes = bytearray(digest)
    uuid_bytes[6] = (uuid_bytes[6] & 0x0F) | 0x30  # версия 3
    uuid_bytes[8] = (uuid_bytes[8] & 0x3F) | 0x80  # вариант

    payload = bytes(uuid_bytes) + write_string(username) + write_varint(0)
    send_packet(sock, 0x02, payload)

    handle_play(sock, addr, username, bytes(uuid_bytes))


def handle_client(conn: socket.socket, addr):
    try:
        # --- Handshake ---
        pkt = read_packet(conn)
        packet_id = pkt.read_varint()
        if packet_id != 0x00:
            conn.close()
            return

        protocol_version = pkt.read_varint()
        server_address = pkt.read_string()
        server_port = pkt.read_unsigned_short()
        next_state = pkt.read_varint()

        print(f"[HANDSHAKE] {addr} -> protocol={protocol_version}, "
              f"addr={server_address}:{server_port}, next_state={next_state}")

        if next_state == 1:
            handle_status(conn)
        elif next_state == 2:
            handle_login(conn, addr)

    except (ConnectionError, EOFError):
        pass
    except Exception:
        print(f"[ERROR] Ошибка при обработке {addr}:")
        traceback.print_exc()
    finally:
        conn.close()


def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(128)
    print(f"Сервер слушает на {HOST}:{PORT} (протокол {PROTOCOL_VERSION} / MC {GAME_VERSION_NAME})")

    try:
        while True:
            conn, addr = server_sock.accept()
            thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        print("\nОстанавливаю сервер...")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
