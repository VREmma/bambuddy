"""Virtual Printer Manager - coordinates SSDP, MQTT, and FTP services."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import settings as app_settings
from backend.app.services.virtual_printer.certificate import CertificateService
from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer
from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer
from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

logger = logging.getLogger(__name__)


class VirtualPrinterManager:
    """Manages the virtual printer lifecycle and coordinates all services."""

    # Fixed configuration
    PRINTER_NAME = "Bambuddy"
    PRINTER_SERIAL = "00M09A391800001"  # X1C serial format
    PRINTER_MODEL = "3DPrinter-X1-Carbon"  # Full model name for slicer compatibility

    def __init__(self):
        """Initialize the virtual printer manager."""
        self._session_factory: Callable | None = None
        self._enabled = False
        self._access_code = ""
        self._mode = "immediate"

        # Service instances
        self._ssdp: VirtualPrinterSSDPServer | None = None
        self._ftp: VirtualPrinterFTPServer | None = None
        self._mqtt: SimpleMQTTServer | None = None

        # Background tasks
        self._tasks: list[asyncio.Task] = []

        # Directories
        self._base_dir = app_settings.base_dir / "virtual_printer"
        self._upload_dir = self._base_dir / "uploads"
        self._cert_dir = self._base_dir / "certs"

        # Certificate service - pass serial to match CN in certificate
        self._cert_service = CertificateService(self._cert_dir, serial=self.PRINTER_SERIAL)

        # Track pending uploads for MQTT correlation
        self._pending_files: dict[str, Path] = {}

    def set_session_factory(self, session_factory: Callable) -> None:
        """Set the database session factory.

        Args:
            session_factory: Async context manager for database sessions
        """
        self._session_factory = session_factory

    @property
    def is_enabled(self) -> bool:
        """Check if virtual printer is enabled."""
        return self._enabled

    @property
    def is_running(self) -> bool:
        """Check if virtual printer services are running."""
        return len(self._tasks) > 0 and all(not t.done() for t in self._tasks)

    async def configure(
        self,
        enabled: bool,
        access_code: str = "",
        mode: str = "immediate",
    ) -> None:
        """Configure and start/stop virtual printer.

        Args:
            enabled: Whether to enable the virtual printer
            access_code: Authentication password for slicer connections
            mode: Archive mode - 'immediate' or 'queue'
        """
        if enabled and not access_code:
            raise ValueError("Access code is required when enabling virtual printer")

        self._access_code = access_code
        self._mode = mode

        if enabled and not self._enabled:
            await self._start()
        elif not enabled and self._enabled:
            await self._stop()

        self._enabled = enabled

    async def _start(self) -> None:
        """Start all virtual printer services."""
        logger.info("Starting virtual printer services...")

        # Ensure certificates exist
        cert_path, key_path = self._cert_service.ensure_certificates()

        # Create directories
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        (self._upload_dir / "cache").mkdir(exist_ok=True)

        # Initialize services
        self._ssdp = VirtualPrinterSSDPServer(
            name=self.PRINTER_NAME,
            serial=self.PRINTER_SERIAL,
            model=self.PRINTER_MODEL,
        )

        self._ftp = VirtualPrinterFTPServer(
            upload_dir=self._upload_dir,
            access_code=self._access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_file_received=self._on_file_received,
        )

        self._mqtt = SimpleMQTTServer(
            serial=self.PRINTER_SERIAL,
            access_code=self._access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_print_command=self._on_print_command,
        )

        # Start services as background tasks
        # Wrap each in error handler so one failure doesn't stop others
        async def run_with_logging(coro, name):
            try:
                await coro
            except Exception as e:
                logger.error(f"Virtual printer {name} failed: {e}")

        self._tasks = [
            asyncio.create_task(run_with_logging(self._ssdp.start(), "SSDP"), name="virtual_printer_ssdp"),
            asyncio.create_task(run_with_logging(self._ftp.start(), "FTP"), name="virtual_printer_ftp"),
            asyncio.create_task(run_with_logging(self._mqtt.start(), "MQTT"), name="virtual_printer_mqtt"),
        ]

        logger.info(f"Virtual printer '{self.PRINTER_NAME}' started (serial: {self.PRINTER_SERIAL})")

    async def _stop(self) -> None:
        """Stop all virtual printer services."""
        logger.info("Stopping virtual printer services...")

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks = []

        # Stop services
        if self._ssdp:
            await self._ssdp.stop()
            self._ssdp = None

        if self._ftp:
            await self._ftp.stop()
            self._ftp = None

        if self._mqtt:
            await self._mqtt.stop()
            self._mqtt = None

        logger.info("Virtual printer stopped")

    async def _on_file_received(self, file_path: Path, source_ip: str) -> None:
        """Handle file upload completion from FTP.

        Args:
            file_path: Path to uploaded file
            source_ip: IP address of the uploading slicer
        """
        logger.info(f"Virtual printer received file: {file_path.name} from {source_ip}")

        # Store file reference for MQTT correlation
        self._pending_files[file_path.name] = file_path

        # In immediate mode, archive right away
        # In queue mode, create pending upload record
        if self._mode == "immediate":
            await self._archive_file(file_path, source_ip)
        else:
            await self._queue_file(file_path, source_ip)

    async def _on_print_command(self, filename: str, data: dict) -> None:
        """Handle print command from MQTT.

        In a real printer, this would start the print. For virtual printer,
        we just log it since archiving is handled by file upload.

        Args:
            filename: Name of the file to print
            data: Print command data (contains settings like timelapse, bed_leveling, etc.)
        """
        logger.info(f"Virtual printer received print command for: {filename}")
        logger.debug(f"Print command data: {data}")

        # The file should already be archived from FTP upload
        # This command just confirms the slicer's intent to "print"

    async def _archive_file(self, file_path: Path, source_ip: str) -> None:
        """Archive file immediately.

        Args:
            file_path: Path to the 3MF file
            source_ip: IP address of uploader
        """
        if not self._session_factory:
            logger.error("Cannot archive: no database session factory configured")
            return

        # Only archive 3MF files
        if file_path.suffix.lower() != ".3mf":
            logger.debug(f"Skipping non-3MF file: {file_path.name}")
            # Remove from pending and clean up
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink()
            except Exception:
                pass
            return

        try:
            from backend.app.services.archive import ArchiveService

            async with self._session_factory() as db:
                service = ArchiveService(db)

                # Archive the print
                archive = await service.archive_print(
                    printer_id=None,  # No physical printer
                    source_file=file_path,
                    print_data={
                        "status": "archived",
                        "source": "virtual_printer",
                        "source_ip": source_ip,
                    },
                )

                if archive:
                    logger.info(f"Archived virtual printer upload: {archive.id} - {archive.print_name}")

                    # Clean up uploaded file (it's now copied to archive)
                    try:
                        file_path.unlink()
                    except Exception:
                        pass

                    # Remove from pending
                    self._pending_files.pop(file_path.name, None)
                else:
                    logger.error(f"Failed to archive file: {file_path.name}")

        except Exception as e:
            logger.error(f"Error archiving file: {e}")

    async def _queue_file(self, file_path: Path, source_ip: str) -> None:
        """Queue file for user review.

        Args:
            file_path: Path to the 3MF file
            source_ip: IP address of uploader
        """
        if not self._session_factory:
            logger.error("Cannot queue: no database session factory configured")
            return

        # Only queue 3MF files
        if file_path.suffix.lower() != ".3mf":
            logger.warning(f"Skipping non-3MF file: {file_path.name}")
            return

        try:
            from backend.app.models.pending_upload import PendingUpload

            async with self._session_factory() as db:
                pending = PendingUpload(
                    filename=file_path.name,
                    file_path=str(file_path),
                    file_size=file_path.stat().st_size,
                    source_ip=source_ip,
                    status="pending",
                    uploaded_at=datetime.now(UTC),
                )
                db.add(pending)
                await db.commit()

                logger.info(f"Queued virtual printer upload: {pending.id} - {file_path.name}")

                # Remove from pending files dict
                self._pending_files.pop(file_path.name, None)

        except Exception as e:
            logger.error(f"Error queueing file: {e}")

    def get_status(self) -> dict:
        """Get virtual printer status.

        Returns:
            Status dictionary with enabled, running, mode, etc.
        """
        return {
            "enabled": self._enabled,
            "running": self.is_running,
            "mode": self._mode,
            "name": self.PRINTER_NAME,
            "serial": self.PRINTER_SERIAL,
            "model": self.PRINTER_MODEL,
            "pending_files": len(self._pending_files),
        }


# Global instance
virtual_printer_manager = VirtualPrinterManager()
