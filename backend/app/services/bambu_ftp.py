import asyncio
import logging
import socket
import ssl
import time
from ftplib import FTP, FTP_TLS
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)


class ImplicitFTP_TLS(FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (port 990) with session reuse."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        # DEBUG: Log SSL context settings
        logger.debug(
            f"[FTP-DEBUG] SSL context created: check_hostname={self.ssl_context.check_hostname}, verify_mode={self.ssl_context.verify_mode}"
        )
        logger.debug(
            f"[FTP-DEBUG] SSL minimum_version={self.ssl_context.minimum_version}, maximum_version={self.ssl_context.maximum_version}"
        )

    def connect(self, host="", port=990, timeout=-999, source_address=None):
        """Connect to host, wrapping socket in TLS immediately (implicit FTPS)."""
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address:
            self.source_address = source_address

        logger.debug(f"[FTP-DEBUG] Creating TCP connection to {self.host}:{self.port} timeout={self.timeout}")
        start_time = time.time()

        # Create and wrap socket immediately (implicit TLS)
        self.sock = socket.create_connection((self.host, self.port), self.timeout, source_address=self.source_address)
        tcp_time = time.time() - start_time
        logger.debug(f"[FTP-DEBUG] TCP connected in {tcp_time:.3f}s, socket timeout={self.sock.gettimeout()}")

        # DEBUG: Log socket options before TLS
        try:
            sndbuf = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            rcvbuf = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            logger.debug(f"[FTP-DEBUG] Socket buffers: SO_SNDBUF={sndbuf}, SO_RCVBUF={rcvbuf}")
        except Exception as e:
            logger.debug(f"[FTP-DEBUG] Could not get socket options: {e}")

        logger.debug("[FTP-DEBUG] Starting TLS handshake...")
        tls_start = time.time()
        self.sock = self.ssl_context.wrap_socket(self.sock, server_hostname=self.host)
        tls_time = time.time() - tls_start
        logger.debug(f"[FTP-DEBUG] TLS handshake completed in {tls_time:.3f}s")

        # DEBUG: Log TLS details
        logger.debug(f"[FTP-DEBUG] TLS version: {self.sock.version()}")
        logger.debug(f"[FTP-DEBUG] TLS cipher: {self.sock.cipher()}")
        try:
            cert = self.sock.getpeercert(binary_form=True)
            logger.debug(f"[FTP-DEBUG] Server certificate length: {len(cert) if cert else 0} bytes")
        except Exception as e:
            logger.debug(f"[FTP-DEBUG] Could not get peer cert: {e}")

        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        logger.debug(f"[FTP-DEBUG] FTP welcome: {self.welcome}")
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        """Override to reuse SSL session for data connection (required by vsFTPd)."""
        logger.debug(f"[FTP-DEBUG] ntransfercmd called: cmd={cmd}, rest={rest}")
        start_time = time.time()

        conn, size = FTP.ntransfercmd(self, cmd, rest)
        data_connect_time = time.time() - start_time
        logger.debug(f"[FTP-DEBUG] Data connection established in {data_connect_time:.3f}s, size={size}")

        if self._prot_p:
            logger.debug("[FTP-DEBUG] Wrapping data connection in TLS (session reuse)...")
            tls_start = time.time()
            # Reuse the SSL session from the control connection
            conn = self.ssl_context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,  # Reuse session!
            )
            tls_time = time.time() - tls_start
            logger.debug(
                f"[FTP-DEBUG] Data TLS handshake in {tls_time:.3f}s, version={conn.version()}, cipher={conn.cipher()}"
            )
        return conn, size

    def sendcmd(self, cmd):
        """Override to log all FTP commands."""
        # Don't log password
        log_cmd = cmd if not cmd.upper().startswith("PASS") else "PASS ****"
        logger.debug(f"[FTP-DEBUG] >>> {log_cmd}")
        response = super().sendcmd(cmd)
        logger.debug(f"[FTP-DEBUG] <<< {response}")
        return response

    def getresp(self):
        """Override to log all FTP responses."""
        response = super().getresp()
        logger.debug(f"[FTP-DEBUG] <<< {response}")
        return response


class BambuFTPClient:
    """FTP client for retrieving files from Bambu Lab printers."""

    FTP_PORT = 990

    def __init__(self, ip_address: str, access_code: str):
        self.ip_address = ip_address
        self.access_code = access_code
        self._ftp: ImplicitFTP_TLS | None = None

    def connect(self) -> bool:
        """Connect to the printer FTP server (implicit FTPS on port 990)."""
        try:
            logger.debug(f"[FTP-DEBUG] BambuFTPClient.connect() to {self.ip_address}:{self.FTP_PORT}")
            self._ftp = ImplicitFTP_TLS()
            self._ftp.set_debuglevel(2)  # Enable ftplib debug output

            logger.debug("[FTP-DEBUG] Calling connect()...")
            self._ftp.connect(self.ip_address, self.FTP_PORT, timeout=10)

            logger.debug("[FTP-DEBUG] Calling login(bblp, ****)...")
            self._ftp.login("bblp", self.access_code)

            logger.debug("[FTP-DEBUG] Calling prot_p() for protected data channel...")
            self._ftp.prot_p()

            logger.debug("[FTP-DEBUG] Calling set_pasv(True) for passive mode...")
            self._ftp.set_pasv(True)

            logger.debug("[FTP-DEBUG] Connection successful!")
            return True
        except Exception as e:
            logger.warning(f"FTP connection failed to {self.ip_address}: {e}")
            logger.debug("[FTP-DEBUG] Connection exception details:", exc_info=True)
            self._ftp = None
            return False

    def disconnect(self):
        """Disconnect from the FTP server."""
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception:
                pass
            self._ftp = None

    def list_files(self, path: str = "/") -> list[dict]:
        """List files in a directory."""
        if not self._ftp:
            return []

        files = []
        try:
            self._ftp.cwd(path)
            items = []
            self._ftp.retrlines("LIST", items.append)

            for item in items:
                parts = item.split()
                if len(parts) >= 9:
                    name = " ".join(parts[8:])
                    is_dir = item.startswith("d")
                    size = int(parts[4]) if not is_dir else 0

                    # Parse modification time from FTP listing
                    # Format: "Nov 30 10:15" or "Nov 30  2024"
                    mtime = None
                    try:
                        from datetime import datetime

                        month = parts[5]
                        day = parts[6]
                        time_or_year = parts[7]

                        # Determine if it's time (HH:MM) or year
                        if ":" in time_or_year:
                            # Recent file: "Nov 30 10:15" - assume current year
                            year = datetime.now().year
                            time_str = f"{month} {day} {year} {time_or_year}"
                            mtime = datetime.strptime(time_str, "%b %d %Y %H:%M")
                            # If parsed date is in the future, use last year
                            if mtime > datetime.now():
                                mtime = mtime.replace(year=year - 1)
                        else:
                            # Older file: "Nov 30 2024" - no time, just date
                            time_str = f"{month} {day} {time_or_year}"
                            mtime = datetime.strptime(time_str, "%b %d %Y")
                    except (ValueError, IndexError):
                        pass

                    file_entry = {
                        "name": name,
                        "is_directory": is_dir,
                        "size": size,
                        "path": f"{path.rstrip('/')}/{name}",
                    }
                    if mtime:
                        file_entry["mtime"] = mtime
                    files.append(file_entry)
        except Exception:
            pass

        return files

    def download_file(self, remote_path: str) -> bytes | None:
        """Download a file from the printer."""
        if not self._ftp:
            return None

        try:
            buffer = BytesIO()
            self._ftp.retrbinary(f"RETR {remote_path}", buffer.write)
            return buffer.getvalue()
        except Exception:
            return None

    def download_to_file(self, remote_path: str, local_path: Path) -> bool:
        """Download a file from the printer to local filesystem."""
        if not self._ftp:
            logger.warning("download_to_file called but FTP not connected")
            return False

        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_path, "wb") as f:
                self._ftp.retrbinary(f"RETR {remote_path}", f.write)
            logger.info(f"Successfully downloaded {remote_path} to {local_path}")
            return True
        except Exception as e:
            logger.debug(f"Failed to download {remote_path}: {e}")
            # Clean up partial file if it exists
            if local_path.exists():
                try:
                    local_path.unlink()
                except Exception:
                    pass
            return False

    def upload_file(self, local_path: Path, remote_path: str) -> bool:
        """Upload a file to the printer."""
        if not self._ftp:
            logger.warning("upload_file: FTP not connected")
            return False

        try:
            file_size = local_path.stat().st_size if local_path.exists() else 0
            logger.info(f"FTP uploading {local_path} ({file_size} bytes) to {remote_path}")
            logger.debug(f"[FTP-DEBUG] Starting upload: file_size={file_size}, remote_path={remote_path}")

            # Track upload progress
            bytes_sent = 0
            last_log_time = time.time()
            start_time = time.time()

            def upload_callback(block):
                nonlocal bytes_sent, last_log_time
                bytes_sent += len(block)
                now = time.time()
                # Log progress every 5 seconds
                if now - last_log_time >= 5:
                    elapsed = now - start_time
                    speed = bytes_sent / elapsed if elapsed > 0 else 0
                    percent = (bytes_sent / file_size * 100) if file_size > 0 else 0
                    logger.debug(
                        f"[FTP-DEBUG] Upload progress: {bytes_sent}/{file_size} bytes ({percent:.1f}%), speed={speed/1024:.1f} KB/s, elapsed={elapsed:.1f}s"
                    )
                    last_log_time = now

            logger.debug(f"[FTP-DEBUG] Calling storbinary(STOR {remote_path})...")
            with open(local_path, "rb") as f:
                # Use custom callback to track progress
                self._ftp.storbinary(f"STOR {remote_path}", f, callback=upload_callback)

            elapsed = time.time() - start_time
            speed = file_size / elapsed if elapsed > 0 else 0
            logger.info(
                f"FTP upload complete: {remote_path} ({file_size} bytes in {elapsed:.1f}s, {speed/1024:.1f} KB/s)"
            )
            logger.debug("[FTP-DEBUG] Upload finished successfully")
            return True
        except Exception as e:
            elapsed = time.time() - start_time if "start_time" in locals() else 0
            logger.error(f"FTP upload failed for {remote_path}: {e} (after {elapsed:.1f}s)")
            logger.debug("[FTP-DEBUG] Upload exception details:", exc_info=True)
            return False

    def upload_bytes(self, data: bytes, remote_path: str) -> bool:
        """Upload bytes to the printer."""
        if not self._ftp:
            return False

        try:
            buffer = BytesIO(data)
            self._ftp.storbinary(f"STOR {remote_path}", buffer)
            return True
        except Exception:
            return False

    def delete_file(self, remote_path: str) -> bool:
        """Delete a file from the printer."""
        if not self._ftp:
            return False

        try:
            self._ftp.delete(remote_path)
            return True
        except Exception as e:
            logger.warning(f"Failed to delete {remote_path}: {e}")
            return False

    def get_file_size(self, remote_path: str) -> int | None:
        """Get the size of a file."""
        if not self._ftp:
            return None

        try:
            return self._ftp.size(remote_path)
        except Exception:
            return None

    def get_storage_info(self) -> dict | None:
        """Get storage information from the printer."""
        if not self._ftp:
            return None

        result = {}

        # Try AVBL command (available space) - some FTP servers support this
        try:
            response = self._ftp.sendcmd("AVBL")
            logger.debug(f"AVBL response: {response}")
            # Response format: "213 <bytes available>"
            if response.startswith("213"):
                parts = response.split()
                if len(parts) >= 2:
                    result["free_bytes"] = int(parts[1])
        except Exception as e:
            logger.debug(f"AVBL command not supported: {e}")
            # Try STAT command as fallback
            try:
                response = self._ftp.sendcmd("STAT")
                logger.debug(f"STAT response: {response}")
            except Exception:
                pass

        # Calculate used space by listing root directories
        try:
            total_used = 0
            dirs_to_scan = ["/cache", "/timelapse", "/model"]

            for dir_path in dirs_to_scan:
                try:
                    self._ftp.cwd(dir_path)
                    items = []
                    self._ftp.retrlines("LIST", items.append)

                    for item in items:
                        parts = item.split()
                        if len(parts) >= 5 and not item.startswith("d"):
                            try:
                                total_used += int(parts[4])
                            except ValueError:
                                pass
                except Exception:
                    pass

            result["used_bytes"] = total_used
        except Exception:
            pass

        return result if result else None


async def download_file_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    local_path: Path,
) -> bool:
    """Async wrapper for downloading a file."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.download_to_file(remote_path, local_path)
            finally:
                client.disconnect()
        return False

    return await loop.run_in_executor(None, _download)


async def download_file_try_paths_async(
    ip_address: str,
    access_code: str,
    remote_paths: list[str],
    local_path: Path,
) -> bool:
    """Try downloading a file from multiple paths using a single connection."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
        if not client.connect():
            return False

        try:
            return any(client.download_to_file(remote_path, local_path) for remote_path in remote_paths)
        finally:
            client.disconnect()

    return await loop.run_in_executor(None, _download)


async def upload_file_async(
    ip_address: str,
    access_code: str,
    local_path: Path,
    remote_path: str,
) -> bool:
    """Async wrapper for uploading a file."""
    loop = asyncio.get_event_loop()

    def _upload():
        logger.info(f"FTP connecting to {ip_address} for upload...")
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            logger.info(f"FTP connected to {ip_address}")
            try:
                return client.upload_file(local_path, remote_path)
            finally:
                client.disconnect()
        logger.warning(f"FTP connection failed to {ip_address}")
        return False

    return await loop.run_in_executor(None, _upload)


async def list_files_async(
    ip_address: str,
    access_code: str,
    path: str = "/",
) -> list[dict]:
    """Async wrapper for listing files."""
    loop = asyncio.get_event_loop()

    def _list():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.list_files(path)
            finally:
                client.disconnect()
        return []

    return await loop.run_in_executor(None, _list)


async def delete_file_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
) -> bool:
    """Async wrapper for deleting a file."""
    loop = asyncio.get_event_loop()

    def _delete():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.delete_file(remote_path)
            finally:
                client.disconnect()
        return False

    return await loop.run_in_executor(None, _delete)


async def download_file_bytes_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
) -> bytes | None:
    """Async wrapper for downloading file as bytes."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.download_file(remote_path)
            finally:
                client.disconnect()
        return None

    return await loop.run_in_executor(None, _download)


async def get_storage_info_async(
    ip_address: str,
    access_code: str,
) -> dict | None:
    """Async wrapper for getting storage info."""
    loop = asyncio.get_event_loop()

    def _get_storage():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.get_storage_info()
            finally:
                client.disconnect()
        return None

    return await loop.run_in_executor(None, _get_storage)
