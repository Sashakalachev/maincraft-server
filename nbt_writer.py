"""
Кодирует NBT-дерево, описанное в JSON-формате prismarine-nbt
({"type": ..., "value": ..., "name": ...}), в настоящий бинарный
NBT (Java Edition, big-endian).

Формат prismarine-nbt JSON в двух словах:
  {"type": "compound", "value": {"key1": {...tag...}, "key2": {...}}}
  {"type": "int", "value": 42}
  {"type": "string", "value": "hello"}
  {"type": "list", "value": {"type": "compound", "value": [ {...}, {...} ]}}
  {"type": "byteArray"/"intArray"/"longArray", "value": [...]}
"""
import struct

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TYPE_TO_TAG = {
    "byte": TAG_BYTE, "short": TAG_SHORT, "int": TAG_INT, "long": TAG_LONG,
    "float": TAG_FLOAT, "double": TAG_DOUBLE, "byteArray": TAG_BYTE_ARRAY,
    "string": TAG_STRING, "list": TAG_LIST, "compound": TAG_COMPOUND,
    "intArray": TAG_INT_ARRAY, "longArray": TAG_LONG_ARRAY,
}


def _write_string(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return struct.pack(">H", len(encoded)) + encoded


def _write_payload(tag_type: int, value, node: dict) -> bytes:
    if tag_type == TAG_BYTE:
        return struct.pack(">b", value)
    if tag_type == TAG_SHORT:
        return struct.pack(">h", value)
    if tag_type == TAG_INT:
        return struct.pack(">i", value)
    if tag_type == TAG_LONG:
        # long иногда представлен как [high32, low32] (из-за JS-ограничений)
        if isinstance(value, (list, tuple)):
            high, low = value
            combined = ((high & 0xFFFFFFFF) << 32) | (low & 0xFFFFFFFF)
            if combined & (1 << 63):
                combined -= 1 << 64
            return struct.pack(">q", combined)
        return struct.pack(">q", value)
    if tag_type == TAG_FLOAT:
        return struct.pack(">f", value)
    if tag_type == TAG_DOUBLE:
        return struct.pack(">d", value)
    if tag_type == TAG_STRING:
        return _write_string(value)
    if tag_type == TAG_BYTE_ARRAY:
        return struct.pack(">i", len(value)) + bytes(
            (b & 0xFF) for b in value
        )
    if tag_type == TAG_INT_ARRAY:
        out = struct.pack(">i", len(value))
        for v in value:
            out += struct.pack(">i", v)
        return out
    if tag_type == TAG_LONG_ARRAY:
        out = struct.pack(">i", len(value))
        for v in value:
            out += struct.pack(">q", v)
        return out
    if tag_type == TAG_COMPOUND:
        out = bytearray()
        for key, child in value.items():
            out += _write_named_tag(key, child)
        out += struct.pack(">b", TAG_END)
        return bytes(out)
    if tag_type == TAG_LIST:
        inner_type_name = value["type"]
        inner_tag = TYPE_TO_TAG[inner_type_name]
        items = value["value"]
        out = struct.pack(">b", inner_tag) + struct.pack(">i", len(items))
        for item in items:
            # элементы списка - "голые" значения, обёрнутые в такую же форму,
            # что и обычный tag, но БЕЗ имени
            fake_node = {"type": inner_type_name, "value": item}
            out += _write_payload(inner_tag, item, fake_node)
        return bytes(out)
    raise ValueError(f"Неизвестный тип NBT-тега: {tag_type}")


def _write_named_tag(name: str, node: dict) -> bytes:
    tag_type = TYPE_TO_TAG[node["type"]]
    out = struct.pack(">b", tag_type) + _write_string(name)
    out += _write_payload(tag_type, node["value"], node)
    return out


def encode_nbt(node: dict, root_name: str = "") -> bytes:
    """Кодирует корневой NBT-тег (обычно compound) в бинарный вид,
    как того требует протокол Minecraft (root с именем, обычно пустым)."""
    tag_type = TYPE_TO_TAG[node["type"]]
    out = struct.pack(">b", tag_type) + _write_string(root_name)
    out += _write_payload(tag_type, node["value"], node)
    return bytes(out)
