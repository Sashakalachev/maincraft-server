import socket
from protocol import (
    write_varint, write_string, write_unsigned_short,
    send_packet, read_packet,
)

HOST = "127.0.0.1"
PORT = 25569

sock = socket.create_connection((HOST, PORT), timeout=10)

# Handshake -> next_state=2 (login)
payload = (
    write_varint(763)
    + write_string(HOST)
    + write_unsigned_short(PORT)
    + write_varint(2)
)
send_packet(sock, 0x00, payload)

# Login Start
send_packet(sock, 0x00, write_string("TestClient"))

# Login Success
pkt = read_packet(sock)
pid = pkt.read_varint()
print("Login Success? packet_id =", hex(pid))
assert pid == 0x02

# Join Game
pkt = read_packet(sock)
pid = pkt.read_varint()
print("Join Game? packet_id =", hex(pid), "размер данных:", len(pkt.remaining()))
assert pid == 0x28

# Дальше пойдут Chunk Data пакеты - прочитаем первые несколько и Player Position
chunk_count = 0
got_position = False
for _ in range(200):
    pkt = read_packet(sock)
    pid = pkt.read_varint()
    if pid == 0x24:
        chunk_count += 1
    elif pid == 0x3C:
        got_position = True
        x = pkt.read_double()
        y = pkt.read_double()
        z = pkt.read_double()
        print(f"Player Position: x={x}, y={y}, z={z}")
        break

print(f"Получено чанков до позиции: {chunk_count}")
print("Position получена:", got_position)
sock.close()
print("ТЕСТ ПРОЙДЕН" if got_position else "ТЕСТ НЕ ПРОЙДЕН")
