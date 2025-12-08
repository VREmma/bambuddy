from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.app.core.config import settings


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    # Import models to register them with SQLAlchemy
    from backend.app.models import printer, archive, filament, settings, smart_plug, print_queue, notification, maintenance, kprofile_note, notification_template  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Run migrations for new columns (SQLite doesn't auto-add columns)
        await run_migrations(conn)

    # Seed default notification templates
    await seed_notification_templates()


async def run_migrations(conn):
    """Add new columns to existing tables if they don't exist."""
    from sqlalchemy import text

    # Migration: Add is_favorite column to print_archives
    try:
        await conn.execute(text(
            "ALTER TABLE print_archives ADD COLUMN is_favorite BOOLEAN DEFAULT 0"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add content_hash column to print_archives for duplicate detection
    try:
        await conn.execute(text(
            "ALTER TABLE print_archives ADD COLUMN content_hash VARCHAR(64)"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add auto_off_executed column to smart_plugs
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN auto_off_executed BOOLEAN DEFAULT 0"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add on_print_stopped column to notification_providers
    try:
        await conn.execute(text(
            "ALTER TABLE notification_providers ADD COLUMN on_print_stopped BOOLEAN DEFAULT 1"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add source_3mf_path column to print_archives
    try:
        await conn.execute(text(
            "ALTER TABLE print_archives ADD COLUMN source_3mf_path VARCHAR(500)"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add on_maintenance_due column to notification_providers
    try:
        await conn.execute(text(
            "ALTER TABLE notification_providers ADD COLUMN on_maintenance_due BOOLEAN DEFAULT 0"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add location column to printers for grouping
    try:
        await conn.execute(text(
            "ALTER TABLE printers ADD COLUMN location VARCHAR(100)"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add interval_type column to maintenance_types
    try:
        await conn.execute(text(
            "ALTER TABLE maintenance_types ADD COLUMN interval_type VARCHAR(20) DEFAULT 'hours'"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add custom_interval_type column to printer_maintenance
    try:
        await conn.execute(text(
            "ALTER TABLE printer_maintenance ADD COLUMN custom_interval_type VARCHAR(20)"
        ))
    except Exception:
        # Column already exists
        pass

    # Migration: Add power alert columns to smart_plugs
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN power_alert_enabled BOOLEAN DEFAULT 0"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN power_alert_high REAL"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN power_alert_low REAL"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN power_alert_last_triggered DATETIME"
        ))
    except Exception:
        pass

    # Migration: Add schedule columns to smart_plugs
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN schedule_enabled BOOLEAN DEFAULT 0"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN schedule_on_time VARCHAR(5)"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "ALTER TABLE smart_plugs ADD COLUMN schedule_off_time VARCHAR(5)"
        ))
    except Exception:
        pass

    # Migration: Add daily digest columns to notification_providers
    try:
        await conn.execute(text(
            "ALTER TABLE notification_providers ADD COLUMN daily_digest_enabled BOOLEAN DEFAULT 0"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "ALTER TABLE notification_providers ADD COLUMN daily_digest_time VARCHAR(5)"
        ))
    except Exception:
        pass


async def seed_notification_templates():
    """Seed default notification templates if they don't exist."""
    from sqlalchemy import select
    from backend.app.models.notification_template import NotificationTemplate, DEFAULT_TEMPLATES

    async with async_session() as session:
        # Check if templates already exist
        result = await session.execute(select(NotificationTemplate).limit(1))
        if result.scalar_one_or_none() is not None:
            # Templates already seeded
            return

        # Insert default templates
        for template_data in DEFAULT_TEMPLATES:
            template = NotificationTemplate(
                event_type=template_data["event_type"],
                name=template_data["name"],
                title_template=template_data["title_template"],
                body_template=template_data["body_template"],
                is_default=True,
            )
            session.add(template)

        await session.commit()
