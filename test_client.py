import socket
from protocol import write_varint, write_string, write_unsigned_short, send_packet, read_packet

HOST = "127.0.0.1"
PORT = 25566

sock = socket.create_connection((HOST, PORT), timeout=5)

# --- Handshake (next_state=1 -> status) ---
payload = (
    write_varint(763)
    + write_string(HOST)
    + write_unsigned_short(PORT)
    + write_varint(1)
)
send_packet(sock, 0x00, payload)

# --- Status Request ---
send_packet(sock, 0x00, b"")

# читаем Status Response
pkt = read_packet(sock)
packet_id = pkt.read_varint()
json_str = pkt.read_string()
print("Packet ID:", packet_id)
print("Status JSON:", json_str)

sock.close()
print("OK: статус получен успешно")
