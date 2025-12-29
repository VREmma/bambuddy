"""SSDP discovery responder for virtual printer.

Responds to M-SEARCH requests from slicers and sends periodic NOTIFY
announcements so the virtual printer appears as a discoverable Bambu printer.
"""

import asyncio
import logging
import socket
import struct
from datetime import datetime

logger = logging.getLogger(__name__)

# SSDP multicast address - Bambu uses port 2021
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 2021

# Bambu service target
BAMBU_SEARCH_TARGET = "urn:bambulab-com:device:3dprinter:1"


class VirtualPrinterSSDPServer:
    """SSDP server that responds to discovery requests as a virtual Bambu printer."""

    def __init__(
        self,
        name: str = "Bambuddy",
        serial: str = "00M09A391800001",  # X1C serial format for compatibility
        model: str = "BL-P001",  # X1C model code for best compatibility
    ):
        """Initialize the SSDP server.

        Args:
            name: Display name shown in slicer discovery
            serial: Unique serial number for this virtual printer (must match cert CN)
            model: Model code (BL-P001=X1C, C11=P1S, O1D=H2D)
        """
        self.name = name
        self.serial = serial
        self.model = model
        self._running = False
        self._socket: socket.socket | None = None
        self._local_ip: str | None = None

    def _get_local_ip(self) -> str:
        """Get the local IP address to advertise."""
        if self._local_ip:
            return self._local_ip

        # Try to determine local IP by connecting to a public address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            self._local_ip = ip
            return ip
        except Exception:
            return "127.0.0.1"

    def _build_notify_message(self) -> bytes:
        """Build SSDP NOTIFY message for periodic announcements."""
        ip = self._get_local_ip()
        # Based on: https://gist.github.com/Alex-Schaefer/72a9e2491a42da2ef99fb87601955cc3
        # Key: DevBind.bambu.com: free - tells slicer printer is NOT cloud-bound
        message = (
            "NOTIFY * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
            "Server: Buildroot/2018.02-rc3 UPnP/1.0 ssdpd/1.8\r\n"
            "Cache-Control: max-age=1800\r\n"
            f"Location: {ip}\r\n"
            f"NT: {BAMBU_SEARCH_TARGET}\r\n"
            "NTS: ssdp:alive\r\n"
            "EXT:\r\n"
            f"USN: {self.serial}\r\n"
            f"DevModel.bambu.com: {self.model}\r\n"
            f"DevName.bambu.com: {self.name}\r\n"
            "DevSignal.bambu.com: -44\r\n"
            "DevConnect.bambu.com: lan\r\n"
            "DevBind.bambu.com: free\r\n"
            "Devseclink.bambu.com: secure\r\n"
            "DevVersion.bambu.com: 01.07.00.00\r\n"
            "\r\n"
        )
        return message.encode()

    def _build_response_message(self) -> bytes:
        """Build SSDP response message for M-SEARCH requests."""
        ip = self._get_local_ip()
        # Based on: https://gist.github.com/Alex-Schaefer/72a9e2491a42da2ef99fb87601955cc3
        # Key: DevBind.bambu.com: free - tells slicer printer is NOT cloud-bound
        # Added: Devseclink, DevVersion, DevCap for better compatibility
        message = (
            "HTTP/1.1 200 OK\r\n"
            "Server: Buildroot/2018.02-rc3 UPnP/1.0 ssdpd/1.8\r\n"
            f"Date: {datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}\r\n"
            f"Location: {ip}\r\n"
            f"ST: {BAMBU_SEARCH_TARGET}\r\n"
            "EXT:\r\n"
            f"USN: {self.serial}\r\n"
            "Cache-Control: max-age=1800\r\n"
            f"DevModel.bambu.com: {self.model}\r\n"
            f"DevName.bambu.com: {self.name}\r\n"
            "DevSignal.bambu.com: -44\r\n"
            "DevConnect.bambu.com: lan\r\n"
            "DevBind.bambu.com: free\r\n"
            "Devseclink.bambu.com: secure\r\n"
            "DevVersion.bambu.com: 01.07.00.00\r\n"
            "\r\n"
        )
        return message.encode()

    async def start(self) -> None:
        """Start the SSDP server."""
        if self._running:
            return

        logger.info(f"Starting virtual printer SSDP server: {self.name} ({self.serial})")
        self._running = True

        try:
            # Create UDP socket
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Try to set SO_REUSEPORT if available
            try:
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass

            # Set non-blocking mode
            self._socket.setblocking(False)

            # Bind to SSDP port
            self._socket.bind(("", SSDP_PORT))

            # Join multicast group
            mreq = struct.pack("4sl", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            # Enable broadcast
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Set multicast TTL
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

            local_ip = self._get_local_ip()
            logger.info(f"SSDP server listening on port {SSDP_PORT}, advertising IP: {local_ip}")
            logger.info(f"Virtual printer: {self.name} ({self.serial}) model={self.model}")

            # Send initial NOTIFY
            await self._send_notify()
            logger.info("Sent initial SSDP NOTIFY announcement")

            # Run receive and announce loops
            last_notify = asyncio.get_event_loop().time()
            notify_interval = 30.0  # Send NOTIFY every 30 seconds

            while self._running:
                # Try to receive M-SEARCH requests
                try:
                    data, addr = self._socket.recvfrom(4096)
                    message = data.decode("utf-8", errors="ignore")
                    await self._handle_message(message, addr)
                except BlockingIOError:
                    pass
                except Exception as e:
                    if self._running:
                        logger.debug(f"SSDP receive error: {e}")

                # Send periodic NOTIFY
                now = asyncio.get_event_loop().time()
                if now - last_notify >= notify_interval:
                    await self._send_notify()
                    last_notify = now

                await asyncio.sleep(0.1)

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.warning(f"SSDP port {SSDP_PORT} in use - real printers may be running")
            else:
                logger.error(f"SSDP server error: {e}")
        except asyncio.CancelledError:
            logger.debug("SSDP server cancelled")
        except Exception as e:
            logger.error(f"SSDP server error: {e}")
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the SSDP server."""
        logger.info("Stopping SSDP server")
        self._running = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._socket:
            try:
                # Send byebye message
                await self._send_byebye()
            except Exception:
                pass

            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    async def _send_notify(self) -> None:
        """Send SSDP NOTIFY message."""
        if not self._socket:
            return

        try:
            msg = self._build_notify_message()
            self._socket.sendto(msg, (SSDP_ADDR, SSDP_PORT))
            logger.debug(f"Sent SSDP NOTIFY for {self.name}")
        except Exception as e:
            logger.debug(f"Failed to send NOTIFY: {e}")

    async def _send_byebye(self) -> None:
        """Send SSDP byebye message when shutting down."""
        if not self._socket:
            return

        message = (
            "NOTIFY * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
            f"NT: {BAMBU_SEARCH_TARGET}\r\n"
            "NTS: ssdp:byebye\r\n"
            f"USN: {self.serial}\r\n"
            "\r\n"
        )

        try:
            self._socket.sendto(message.encode(), (SSDP_ADDR, SSDP_PORT))
            logger.debug("Sent SSDP byebye")
        except Exception:
            pass

    async def _handle_message(self, message: str, addr: tuple[str, int]) -> None:
        """Handle incoming SSDP message.

        Args:
            message: The SSDP message content
            addr: Tuple of (ip_address, port) of sender
        """
        # Check if this is an M-SEARCH request for Bambu printers
        if "M-SEARCH" not in message:
            return

        # Check search target
        if BAMBU_SEARCH_TARGET not in message and "ssdp:all" not in message.lower():
            return

        logger.debug(f"Received M-SEARCH from {addr[0]}")

        # Send response
        if self._socket:
            try:
                response = self._build_response_message()
                self._socket.sendto(response, addr)
                logger.info(f"Sent SSDP response to {addr[0]} for virtual printer '{self.name}'")
            except Exception as e:
                logger.debug(f"Failed to send SSDP response: {e}")
