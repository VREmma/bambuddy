import json
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.models.settings import Settings
from backend.app.models.notification import NotificationProvider
from backend.app.models.smart_plug import SmartPlug
from backend.app.schemas.settings import AppSettings, AppSettingsUpdate


router = APIRouter(prefix="/settings", tags=["settings"])

# Default settings
DEFAULT_SETTINGS = AppSettings()


async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Get a single setting value by key."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Set a single setting value."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = Settings(key=key, value=value)
        db.add(setting)


@router.get("/", response_model=AppSettings)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get all application settings."""
    settings_dict = DEFAULT_SETTINGS.model_dump()

    # Load saved settings from database
    result = await db.execute(select(Settings))
    db_settings = result.scalars().all()

    for setting in db_settings:
        if setting.key in settings_dict:
            # Parse the value based on the expected type
            if setting.key in ["auto_archive", "save_thumbnails", "capture_finish_photo", "spoolman_enabled", "check_updates"]:
                settings_dict[setting.key] = setting.value.lower() == "true"
            elif setting.key in ["default_filament_cost", "energy_cost_per_kwh", "ams_temp_good", "ams_temp_fair"]:
                settings_dict[setting.key] = float(setting.value)
            elif setting.key in ["ams_humidity_good", "ams_humidity_fair"]:
                settings_dict[setting.key] = int(setting.value)
            elif setting.key == "default_printer_id":
                # Handle nullable integer
                settings_dict[setting.key] = int(setting.value) if setting.value and setting.value != "None" else None
            else:
                settings_dict[setting.key] = setting.value

    return AppSettings(**settings_dict)


@router.put("/", response_model=AppSettings)
async def update_settings(
    settings_update: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update application settings."""
    update_data = settings_update.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        # Convert value to string for storage
        if isinstance(value, bool):
            str_value = "true" if value else "false"
        elif value is None:
            str_value = "None"
        else:
            str_value = str(value)
        await set_setting(db, key, str_value)

    await db.commit()

    # Return updated settings
    return await get_settings(db)


@router.post("/reset", response_model=AppSettings)
async def reset_settings(db: AsyncSession = Depends(get_db)):
    """Reset all settings to defaults."""
    # Delete all settings
    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        await db.delete(setting)

    await db.commit()

    return DEFAULT_SETTINGS


@router.get("/check-ffmpeg")
async def check_ffmpeg():
    """Check if ffmpeg is installed and available."""
    from backend.app.services.camera import get_ffmpeg_path

    ffmpeg_path = get_ffmpeg_path()

    return {
        "installed": ffmpeg_path is not None,
        "path": ffmpeg_path,
    }


@router.get("/spoolman")
async def get_spoolman_settings(db: AsyncSession = Depends(get_db)):
    """Get Spoolman integration settings."""
    spoolman_enabled = await get_setting(db, "spoolman_enabled") or "false"
    spoolman_url = await get_setting(db, "spoolman_url") or ""
    spoolman_sync_mode = await get_setting(db, "spoolman_sync_mode") or "auto"

    return {
        "spoolman_enabled": spoolman_enabled,
        "spoolman_url": spoolman_url,
        "spoolman_sync_mode": spoolman_sync_mode,
    }


@router.put("/spoolman")
async def update_spoolman_settings(
    settings: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update Spoolman integration settings."""
    if "spoolman_enabled" in settings:
        await set_setting(db, "spoolman_enabled", settings["spoolman_enabled"])
    if "spoolman_url" in settings:
        await set_setting(db, "spoolman_url", settings["spoolman_url"])
    if "spoolman_sync_mode" in settings:
        await set_setting(db, "spoolman_sync_mode", settings["spoolman_sync_mode"])

    await db.commit()

    # Return updated settings
    return await get_spoolman_settings(db)


@router.get("/backup")
async def export_backup(db: AsyncSession = Depends(get_db)):
    """Export all settings, notification providers, and smart plugs as JSON backup."""
    # Get all settings
    result = await db.execute(select(Settings))
    db_settings = result.scalars().all()
    settings_data = {s.key: s.value for s in db_settings}

    # Get notification providers
    result = await db.execute(select(NotificationProvider))
    providers = result.scalars().all()
    providers_data = []
    for p in providers:
        providers_data.append({
            "name": p.name,
            "provider_type": p.provider_type,
            "enabled": p.enabled,
            "config": json.loads(p.config) if isinstance(p.config, str) else p.config,
            "on_print_start": p.on_print_start,
            "on_print_complete": p.on_print_complete,
            "on_print_failed": p.on_print_failed,
            "on_print_stopped": p.on_print_stopped,
            "on_print_progress": p.on_print_progress,
            "on_printer_offline": p.on_printer_offline,
            "on_printer_error": p.on_printer_error,
            "on_filament_low": p.on_filament_low,
            "on_maintenance_due": p.on_maintenance_due,
            "quiet_hours_enabled": p.quiet_hours_enabled,
            "quiet_hours_start": p.quiet_hours_start,
            "quiet_hours_end": p.quiet_hours_end,
        })

    # Get smart plugs
    result = await db.execute(select(SmartPlug))
    plugs = result.scalars().all()
    plugs_data = []
    for plug in plugs:
        plugs_data.append({
            "name": plug.name,
            "ip_address": plug.ip_address,
            "enabled": plug.enabled,
            "auto_off_enabled": plug.auto_off_enabled,
            "auto_off_delay_minutes": plug.auto_off_delay_minutes,
        })

    backup = {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat(),
        "settings": settings_data,
        "notification_providers": providers_data,
        "smart_plugs": plugs_data,
    }

    return JSONResponse(
        content=backup,
        headers={
            "Content-Disposition": f"attachment; filename=bambutrack-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        }
    )


@router.post("/restore")
async def import_backup(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Restore settings, notification providers, and smart plugs from JSON backup."""
    try:
        content = await file.read()
        backup = json.loads(content.decode("utf-8"))
    except Exception as e:
        return {"success": False, "message": f"Invalid backup file: {str(e)}"}

    restored = {"settings": 0, "notification_providers": 0, "smart_plugs": 0}

    # Restore settings
    if "settings" in backup:
        for key, value in backup["settings"].items():
            await set_setting(db, key, value)
            restored["settings"] += 1

    # Restore notification providers (skip duplicates by name)
    if "notification_providers" in backup:
        for provider_data in backup["notification_providers"]:
            # Check if provider with same name exists
            result = await db.execute(
                select(NotificationProvider).where(NotificationProvider.name == provider_data["name"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                provider = NotificationProvider(
                    name=provider_data["name"],
                    provider_type=provider_data["provider_type"],
                    enabled=provider_data.get("enabled", True),
                    config=json.dumps(provider_data.get("config", {})),
                    on_print_start=provider_data.get("on_print_start", False),
                    on_print_complete=provider_data.get("on_print_complete", True),
                    on_print_failed=provider_data.get("on_print_failed", True),
                    on_print_stopped=provider_data.get("on_print_stopped", True),
                    on_print_progress=provider_data.get("on_print_progress", False),
                    on_printer_offline=provider_data.get("on_printer_offline", False),
                    on_printer_error=provider_data.get("on_printer_error", False),
                    on_filament_low=provider_data.get("on_filament_low", False),
                    on_maintenance_due=provider_data.get("on_maintenance_due", False),
                    quiet_hours_enabled=provider_data.get("quiet_hours_enabled", False),
                    quiet_hours_start=provider_data.get("quiet_hours_start"),
                    quiet_hours_end=provider_data.get("quiet_hours_end"),
                )
                db.add(provider)
                restored["notification_providers"] += 1

    # Restore smart plugs (skip duplicates by IP)
    if "smart_plugs" in backup:
        for plug_data in backup["smart_plugs"]:
            # Check if plug with same IP exists
            result = await db.execute(
                select(SmartPlug).where(SmartPlug.ip_address == plug_data["ip_address"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                plug = SmartPlug(
                    name=plug_data["name"],
                    ip_address=plug_data["ip_address"],
                    enabled=plug_data.get("enabled", True),
                    auto_off_enabled=plug_data.get("auto_off_enabled", False),
                    auto_off_delay_minutes=plug_data.get("auto_off_delay_minutes", 5),
                )
                db.add(plug)
                restored["smart_plugs"] += 1

    await db.commit()

    return {
        "success": True,
        "message": f"Restored {restored['settings']} settings, {restored['notification_providers']} notification providers, {restored['smart_plugs']} smart plugs",
        "restored": restored,
    }
