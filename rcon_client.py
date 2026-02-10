"""异步 RCON 客户端（纯 asyncio，无第三方 rcon 库）"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Optional, Tuple


RCON_TYPE_RESPONSE_VALUE = 0
RCON_TYPE_EXECCOMMAND = 2
RCON_TYPE_AUTH = 3
RCON_TYPE_AUTH_RESPONSE = 2  # 与 EXECCOMMAND 同数值但语义不同

MAX_RCON_PACKET_SIZE = 1024 * 1024  # 1MB 防御异常 length


class RconError(Exception):
    pass


class RconAuthError(RconError):
    pass


class RconProtocolError(RconError):
    pass


@dataclass(frozen=True)
class RconConfig:
    host: str
    port: int
    password: str
    timeout: float = 5.0


class AsyncRconClient:
    def __init__(self, cfg: RconConfig):
        self.cfg = cfg
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 10
        self._connected = False
        self._authed = False

    @property
    def connected(self) -> bool:
        return (
            self._connected
            and self._writer is not None
            and not self._writer.is_closing()
        )

    @property
    def authed(self) -> bool:
        return self._authed

    def _next_id(self) -> int:
        self._req_id += 1
        if self._req_id > 2_000_000_000:
            self._req_id = 10
        return self._req_id

    @staticmethod
    def _pack(req_id: int, ptype: int, payload: str) -> bytes:
        body = struct.pack("<ii", req_id, ptype) + payload.encode("utf-8") + b"\x00\x00"
        return struct.pack("<i", len(body)) + body

    async def connect(self) -> None:
        if self.connected:
            return
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.cfg.host, self.cfg.port),
            timeout=self.cfg.timeout,
        )
        self._connected = True

    async def close(self) -> None:
        self._authed = False
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _send_packet(self, req_id: int, ptype: int, payload: str) -> None:
        if not self.connected or not self._writer:
            raise RconError("RCON not connected")
        self._writer.write(self._pack(req_id, ptype, payload))
        await asyncio.wait_for(self._writer.drain(), timeout=self.cfg.timeout)

    async def _read_exactly(self, n: int) -> bytes:
        if not self.connected or not self._reader:
            raise RconError("RCON not connected")
        try:
            return await asyncio.wait_for(self._reader.readexactly(n), timeout=self.cfg.timeout)
        except asyncio.IncompleteReadError as e:
            raise RconProtocolError("RCON connection closed unexpectedly") from e
        except asyncio.TimeoutError as e:
            raise RconError("RCON read timeout") from e

    async def _read_packet(self) -> Tuple[int, int, str]:
        raw_len = await self._read_exactly(4)
        (length,) = struct.unpack("<i", raw_len)

        if length < 10:
            raise RconProtocolError(f"Invalid RCON packet length: {length}")
        if length > MAX_RCON_PACKET_SIZE:
            raise RconProtocolError(f"RCON packet too large: {length}")

        body = await self._read_exactly(length)
        req_id, ptype = struct.unpack("<ii", body[:8])
        payload_raw = body[8:]

        if len(payload_raw) < 2 or payload_raw[-2:] != b"\x00\x00":
            raise RconProtocolError("Invalid RCON payload terminator")

        payload = payload_raw[:-2].decode("utf-8", errors="replace")
        return req_id, ptype, payload

    async def auth(self) -> None:
        await self.connect()

        auth_id = self._next_id()
        await self._send_packet(auth_id, RCON_TYPE_AUTH, self.cfg.password)

        # 必须等到真正的 AUTH_RESPONSE（避免误判）
        deadline = asyncio.get_running_loop().time() + self.cfg.timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RconError("RCON auth timeout")

            req_id, ptype, _ = await asyncio.wait_for(self._read_packet(), timeout=remaining)

            if req_id == -1:
                raise RconAuthError("RCON auth failed (bad password?)")

            if req_id == auth_id and ptype == RCON_TYPE_AUTH_RESPONSE:
                self._authed = True
                return

    async def ensure_ready(self) -> None:
        if not self.connected:
            await self.connect()
        if not self.authed:
            await self.auth()

    async def exec(self, command: str) -> str:
        await self.ensure_ready()

        cmd_id = self._next_id()
        end_id = self._next_id()

        await self._send_packet(cmd_id, RCON_TYPE_EXECCOMMAND, command)
        # 终止包：空命令，用于收齐多包响应
        await self._send_packet(end_id, RCON_TYPE_EXECCOMMAND, "")

        chunks: list[str] = []
        deadline = asyncio.get_running_loop().time() + self.cfg.timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RconError("RCON command response timeout")

            req_id, _ptype, payload = await asyncio.wait_for(self._read_packet(), timeout=remaining)

            if req_id == end_id:
                break

            if req_id == cmd_id and payload:
                chunks.append(payload)

        return "".join(chunks).strip("\n")
