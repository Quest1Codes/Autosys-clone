"""
SNMP Notifier — sends SNMP v1 traps for alarm notifications.

Uses stdlib only (no pysnmp dependency).  Constructs a minimal SNMP v1
trap PDU and sends it via UDP to the configured NMS host.

Configuration (env vars)
------------------------
AUTOSYS_SNMP_HOST        NMS host to receive traps (e.g. "nms.example.com")
AUTOSYS_SNMP_PORT        UDP port (default: 162)
AUTOSYS_SNMP_COMMUNITY   SNMP community string (default: "public")
"""
from __future__ import annotations

import socket
import struct
from datetime import datetime
from typing import Optional

from loguru import logger

from autosys.db.schema import AlarmRow


# Enterprise OID prefix for AutoSys clone
_ENTERPRISE_OID = "1.3.6.1.4.1.7483.1.1"


class SnmpNotifier:
    """
    Sends SNMP v1 trap PDUs via UDP.

    Parameters
    ----------
    host:
        NMS host name or IP.
    port:
        UDP port (default 162).
    community:
        SNMP community string (default "public").
    send_fn:
        Injectable for tests — replaces the UDP send.
        Signature: ``(host: str, port: int, data: bytes) -> bool``
    """

    def __init__(
        self,
        host: str,
        port: int = 162,
        community: str = "public",
        send_fn: Optional[callable] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.community = community
        self._send_fn = send_fn or _default_udp_send

    def send(self, alarm: AlarmRow) -> bool:
        """
        Build and send an SNMP v1 trap for *alarm*.

        Returns True if the UDP packet was sent successfully.
        """
        pdu = self._build_trap_pdu(alarm)
        try:
            ok = self._send_fn(self.host, self.port, pdu)
            if ok:
                logger.debug(
                    "SnmpNotifier: trap sent for %r to %s:%d",
                    alarm.alarm_id[:8], self.host, self.port,
                )
            return ok
        except Exception as exc:
            logger.warning("SnmpNotifier: send error for %r: %s", alarm.alarm_id[:8], exc)
            return False

    # ------------------------------------------------------------------
    # SNMP v1 trap PDU construction
    # ------------------------------------------------------------------

    def _build_trap_pdu(self, alarm: AlarmRow) -> bytes:
        """
        Construct a minimal SNMP v1 trap PDU.

        Structure:
          - Version: 0 (SNMPv1)
          - Community: octet string
          - PDU type: 0xA4 (trap)
          - Enterprise OID
          - Agent address
          - Generic trap type: 6 (enterpriseSpecific)
          - Specific trap type: 1
          - Timestamp
          - VarBind list (alarm message)
        """
        community_bytes = self.community.encode("ascii")
        msg_bytes = alarm.message.encode("utf-8")[:255]
        job_bytes = alarm.job_name.encode("utf-8")[:255]

        # VarBind: 1.3.6.1.4.1.7483.1.1.1.0 = job_name (octet string)
        varbind1 = _encode_varbind(
            f"{_ENTERPRISE_OID}.1.0", 0x04, job_bytes,
        )
        # VarBind: 1.3.6.1.4.1.7483.1.1.2.0 = message (octet string)
        varbind2 = _encode_varbind(
            f"{_ENTERPRISE_OID}.2.0", 0x04, msg_bytes,
        )
        varbinds = varbind1 + varbind2
        varbind_seq = _encode_length(len(varbinds)) + varbinds
        varbind_seq = b"\x30" + varbind_seq

        # Enterprise OID
        enterprise_oid = _encode_oid(_ENTERPRISE_OID)
        # Agent address (0.0.0.0 — we don't know our IP)
        agent_addr = struct.pack("!I", 0)
        # Generic trap: 6 = enterpriseSpecific
        generic_trap = struct.pack("!B", 6)
        # Specific trap: 1
        specific_trap = struct.pack("!B", 1)
        # Timestamp: 100ths of seconds since uptime
        ts = struct.pack("!I", 0)

        trap_body = (
            enterprise_oid
            + b"\x40\x04" + agent_addr
            + b"\x02\x01" + generic_trap
            + b"\x02\x01" + specific_trap
            + b"\x43\x04" + ts
            + varbind_seq
        )

        # PDU header
        pdu = b"\xA4" + _encode_length(len(trap_body)) + trap_body

        # Community string
        community_field = b"\x04" + _encode_length(len(community_bytes)) + community_bytes

        # Version: 0 (SNMPv1)
        version_field = b"\x02\x01\x00"

        # Full message
        message_body = version_field + community_field + pdu
        message = b"\x30" + _encode_length(len(message_body)) + message_body

        return message


# ---------------------------------------------------------------------------
# BER encoding helpers
# ---------------------------------------------------------------------------

def _encode_length(length: int) -> bytes:
    """BER encode a length field."""
    if length < 0x80:
        return struct.pack("!B", length)
    elif length < 0x100:
        return b"\x81" + struct.pack("!B", length)
    elif length < 0x10000:
        return b"\x82" + struct.pack("!H", length)
    else:
        return b"\x83" + struct.pack("!I", length)[1:]


def _encode_oid(oid_str: str) -> bytes:
    """BER encode an OID string into bytes."""
    parts = [int(x) for x in oid_str.split(".")]
    if len(parts) < 2:
        return b"\x06\x01\x00"
    first = parts[0] * 40 + parts[1]
    encoded = [first]
    for part in parts[2:]:
        if part < 0x80:
            encoded.append(part)
        else:
            # Multi-byte encoding
            bytes_list = []
            val = part
            bytes_list.append(val & 0x7F)
            val >>= 7
            while val > 0:
                bytes_list.append((val & 0x7F) | 0x80)
                val >>= 7
            encoded.extend(reversed(bytes_list))
    body = bytes(encoded)
    return b"\x06" + _encode_length(len(body)) + body


def _encode_varbind(oid_str: str, value_type: int, value_bytes: bytes) -> bytes:
    """Encode a single VarBind (OID + value)."""
    oid_encoded = _encode_oid(oid_str)
    value_encoded = bytes([value_type]) + _encode_length(len(value_bytes)) + value_bytes
    varbind = oid_encoded + value_encoded
    return b"\x30" + _encode_length(len(varbind)) + varbind


def _default_udp_send(host: str, port: int, data: bytes) -> bool:
    """Send a UDP packet to host:port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (host, port))
        return True
    finally:
        sock.close()
