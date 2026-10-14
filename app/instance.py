"""Chỉ cho chạy một Norilan: lần mở thứ hai gửi lệnh sang bản đang chạy rồi thoát.

Giao thức: mỗi lệnh là một dòng JSON. Bản đang chạy đọc xong thì trả lời "ok\\n";
bên gửi chỉ ngắt kết nối sau khi nhận "ok" – vì trên Windows (named pipe), ngắt quá sớm
có thể làm mất phần dữ liệu bên kia chưa kịp đọc.
"""
from __future__ import annotations

import getpass
import json
import logging
import re

from PySide6.QtCore import QDeadlineTimer, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app import APP_NAME

log = logging.getLogger(__name__)


def server_name() -> str:
    """Mỗi tài khoản Windows một tên riêng."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"{APP_NAME}-{re.sub(r'[^A-Za-z0-9_-]', '_', user)}"


ACK = b"ok"


def deliver(message: dict, *, name: str | None = None, timeout_ms: int = 1000, ack_timeout_ms: int = 3000) -> str:
    """Gửi lệnh cho bản đang chạy. Trả về 'acked' (đã nhận), 'no-ack' (có bản chạy nhưng không trả lời)
    hoặc 'no-server' (không có bản nào đang chạy)."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(timeout_ms):
        return "no-server"
    socket.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)

    reply = b""
    deadline = QDeadlineTimer(ack_timeout_ms)
    while b"\n" not in reply and not deadline.hasExpired():
        if socket.bytesAvailable() or socket.waitForReadyRead(max(1, deadline.remainingTime())):
            reply += bytes(socket.readAll())
        elif socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            break
    acked = reply.split(b"\n", 1)[0].strip() == ACK
    if not acked:
        log.warning("Bản Norilan đang chạy không xác nhận lệnh %s", message)

    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(timeout_ms)
    return "acked" if acked else "no-ack"


def send_to_running(message: dict, *, name: str | None = None, timeout_ms: int = 1000) -> bool:
    """True nếu đã có Norilan đang chạy (lệnh đã được chuyển sang) – khi đó bản này nên thoát."""
    return deliver(message, name=name, timeout_ms=timeout_ms) != "no-server"


class InstanceServer(QObject):
    message_received = Signal(dict)

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._buffers: dict[QLocalSocket, bytes] = {}

    def start(self) -> bool:
        QLocalServer.removeServer(self._name)  # dọn tên kẹt lại nếu lần trước app bị tắt đột ngột
        ok = self._server.listen(self._name)
        if not ok:
            log.warning("Không mở được kênh single-instance: %s", self._server.errorString())
        return ok

    def close(self) -> None:
        self._buffers.clear()  # bỏ qua tín hiệu 'disconnected' Qt phát ra khi huỷ các kết nối còn mở
        self._server.close()

    def _on_connection(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._finish(s))
            # Windows (named pipe): bên gửi thường đã ghi xong VÀ ngắt kết nối trước khi ta kịp nối
            # tín hiệu → readyRead/disconnected đã phát mất. Phải tự đọc phần đã có sẵn ngay tại đây.
            self._read(socket)
            if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._finish(socket)

    def _read(self, socket: QLocalSocket) -> None:
        """Đọc phần dữ liệu đang có; mỗi dòng hoàn chỉnh (kết thúc bằng \\n) là một lệnh."""
        if socket not in self._buffers:
            return
        try:
            chunk = bytes(socket.readAll())
        except RuntimeError:  # đối tượng Qt đã bị huỷ
            self._buffers.pop(socket, None)
            return
        data = self._buffers[socket] + chunk
        *lines, rest = data.split(b"\n")
        self._buffers[socket] = rest
        for line in lines:
            self._emit(line)
            self._ack(socket)

    def _finish(self, socket: QLocalSocket) -> None:
        if socket not in self._buffers:
            return  # đã xử lý xong (tránh xử lý hai lần)
        self._read(socket)
        rest = self._buffers.pop(socket, b"")
        if rest.strip():
            self._emit(rest)
        socket.deleteLater()

    @staticmethod
    def _ack(socket: QLocalSocket) -> None:
        """Báo cho bên gửi biết đã đọc xong – bên gửi lúc đó mới ngắt kết nối."""
        try:
            if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
                socket.write(ACK + b"\n")
                socket.flush()
        except RuntimeError:
            pass

    def _emit(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            message = json.loads(line)
        except ValueError:
            log.warning("Bỏ qua lệnh lỗi từ instance khác: %r", line[:100])
            return
        if isinstance(message, dict):
            self.message_received.emit(message)
