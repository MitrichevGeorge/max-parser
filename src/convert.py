import struct
from typing import Any, Dict
import lz4.block
import msgpack


class PacketCodec:
    HEADER_FORMAT = ">BBHHB"    # magic(1B) + cmd(1B) + seq(2B) + opcode(2B) + compression(1B) + length(3B) = 10B
    HEADER_SIZE = 10

    def __init__(self) -> None:
        self._seq: int = 0

    @staticmethod
    def _ext_hook(code: int, data: bytes) -> Any:
        if code == 1:
            try:
                return msgpack.unpackb(data, raw=False, strict_map_key=False)
            except Exception:
                padded_data = data[-8:].zfill(8)
                return int.from_bytes(padded_data, "big", signed=False)
        return msgpack.ExtType(code, data)

    def bytes_to_payload(self, packet: bytes) -> Dict[str, Any]:
        if len(packet) < self.HEADER_SIZE:
            raise ValueError("Packet too short")

        magic, cmd, seq, opcode, compression = struct.unpack(self.HEADER_FORMAT, packet[:7])

        payload_len = (packet[7] << 16) | (packet[8] << 8) | packet[9]

        payload = packet[self.HEADER_SIZE : self.HEADER_SIZE + payload_len]

        if compression > 0:
            payload = lz4.block.decompress(
                payload, 
                uncompressed_size=payload_len * compression
            )

        decoded = None
        if payload:
            decoded = msgpack.unpackb(
                payload,
                raw=False,
                strict_map_key=False,
                ext_hook=self._ext_hook
            )

        return {
            "magic": magic,
            "cmd": cmd,
            "seq": seq,
            "opcode": opcode,
            "compression": compression,
            "payload": decoded,
        }

    def payload_to_bytes(self, opcode: int, payload: Any) -> bytes:
        payload_bytes = msgpack.packb(payload, use_bin_type=True)
        if not isinstance(payload_bytes, bytes):
            raise ValueError("Something went wrong: not bytes")
        payload_len = len(payload_bytes)
        if payload_len > 0xFFFFFF:
            raise ValueError("Payload is too large")

        magic = 10
        cmd = 0
        compression = 0

        header = bytearray(struct.pack(self.HEADER_FORMAT, magic, cmd, self._seq, opcode, compression))
        len_bytes = struct.pack(">I", payload_len)[1:]
        self._seq = (self._seq + 1) & 0xFFFF

        return bytes(header) + len_bytes + payload_bytes