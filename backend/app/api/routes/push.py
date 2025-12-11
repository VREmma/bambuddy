"""API routes for Web Push notifications."""

import base64
import json
import logging
from datetime import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.notification import PushSubscription
from backend.app.models.settings import Settings
from backend.app.schemas.notification import (
    PushSubscriptionCreate,
    PushSubscriptionResponse,
    PushSubscriptionUpdate,
    VapidPublicKeyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/push", tags=["push"])

# Settings keys for VAPID
VAPID_PRIVATE_KEY = "vapid_private_key"
VAPID_PUBLIC_KEY = "vapid_public_key"
VAPID_CLAIMS_EMAIL = "vapid_claims_email"


def _generate_vapid_keys() -> tuple[str, str]:
    """Generate VAPID key pair using cryptography library."""
    # Generate private key
    private_key = ec.generate_private_key(ec.SECP256R1())

    # Get private key in PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    # Get public key in uncompressed point format (X9.62)
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )

    # Convert to URL-safe base64 (no padding)
    public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b'=').decode('ascii')

    return private_pem, public_b64


async def get_or_create_vapid_keys(db: AsyncSession) -> tuple[str, str]:
    """Get existing VAPID keys or generate new ones."""
    # Try to get existing keys
    result = await db.execute(
        select(Settings).where(Settings.key.in_([VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY]))
    )
    settings = {s.key: s.value for s in result.scalars().all()}

    if VAPID_PRIVATE_KEY in settings and VAPID_PUBLIC_KEY in settings:
        return settings[VAPID_PRIVATE_KEY], settings[VAPID_PUBLIC_KEY]

    # Generate new keys
    logger.info("Generating new VAPID keys for Web Push")
    private_key, public_key = _generate_vapid_keys()

    # Store keys in database
    for key, value in [(VAPID_PRIVATE_KEY, private_key), (VAPID_PUBLIC_KEY, public_key)]:
        existing = await db.execute(select(Settings).where(Settings.key == key))
        setting = existing.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))

    await db.commit()
    logger.info("VAPID keys generated and stored")

    return private_key, public_key


async def get_vapid_claims_email(db: AsyncSession) -> str:
    """Get the email for VAPID claims (defaults to a placeholder)."""
    result = await db.execute(select(Settings).where(Settings.key == VAPID_CLAIMS_EMAIL))
    setting = result.scalar_one_or_none()
    return setting.value if setting else "mailto:bambuddy@localhost"


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def get_vapid_public_key(db: AsyncSession = Depends(get_db)):
    """Get the VAPID public key for push subscription."""
    _, public_key = await get_or_create_vapid_keys(db)
    return VapidPublicKeyResponse(public_key=public_key)


@router.get("/subscriptions", response_model=list[PushSubscriptionResponse])
async def list_subscriptions(db: AsyncSession = Depends(get_db)):
    """List all push subscriptions."""
    result = await db.execute(
        select(PushSubscription).order_by(PushSubscription.created_at.desc())
    )
    return result.scalars().all()


@router.post("/subscribe", response_model=PushSubscriptionResponse)
async def subscribe(
    subscription: PushSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Subscribe a browser to push notifications."""
    # Check if subscription already exists (by endpoint)
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == subscription.endpoint)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing subscription
        existing.p256dh_key = subscription.p256dh_key
        existing.auth_key = subscription.auth_key
        existing.user_agent = subscription.user_agent
        if subscription.name:
            existing.name = subscription.name
        existing.enabled = True
        existing.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(existing)
        logger.info(f"Updated push subscription: {existing.name or existing.id}")
        return existing

    # Create new subscription
    # Generate a default name from user agent if not provided
    name = subscription.name
    if not name and subscription.user_agent:
        # Extract browser name from user agent
        ua = subscription.user_agent.lower()
        if "chrome" in ua and "edg" not in ua:
            name = "Chrome"
        elif "firefox" in ua:
            name = "Firefox"
        elif "safari" in ua and "chrome" not in ua:
            name = "Safari"
        elif "edg" in ua:
            name = "Edge"
        else:
            name = "Browser"

        # Add device hint
        if "mobile" in ua or "android" in ua or "iphone" in ua:
            name += " (Mobile)"
        else:
            name += " (Desktop)"

    new_subscription = PushSubscription(
        endpoint=subscription.endpoint,
        p256dh_key=subscription.p256dh_key,
        auth_key=subscription.auth_key,
        name=name,
        user_agent=subscription.user_agent,
        enabled=True,
    )
    db.add(new_subscription)
    await db.commit()
    await db.refresh(new_subscription)

    logger.info(f"New push subscription created: {new_subscription.name or new_subscription.id}")
    return new_subscription


@router.patch("/subscriptions/{subscription_id}", response_model=PushSubscriptionResponse)
async def update_subscription(
    subscription_id: int,
    update: PushSubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a push subscription."""
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if update.name is not None:
        subscription.name = update.name
    if update.enabled is not None:
        subscription.enabled = update.enabled

    subscription.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(subscription)

    return subscription


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(
    subscription_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a push subscription."""
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    await db.delete(subscription)
    await db.commit()

    return {"message": "Subscription deleted"}


@router.post("/unsubscribe")
async def unsubscribe_by_endpoint(
    endpoint: str,
    db: AsyncSession = Depends(get_db),
):
    """Unsubscribe by endpoint URL (called when browser unsubscribes)."""
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )
    subscription = result.scalar_one_or_none()

    if subscription:
        await db.delete(subscription)
        await db.commit()
        logger.info(f"Push subscription removed by endpoint: {subscription.name or subscription.id}")

    return {"message": "Unsubscribed"}


@router.post("/test")
async def test_push_notification(db: AsyncSession = Depends(get_db)):
    """Send a test push notification to all subscribed browsers."""
    from pywebpush import webpush, WebPushException

    private_key, public_key = await get_or_create_vapid_keys(db)
    claims_email = await get_vapid_claims_email(db)

    # Get all enabled subscriptions
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.enabled == True)
    )
    subscriptions = result.scalars().all()

    if not subscriptions:
        raise HTTPException(status_code=400, detail="No push subscriptions found")

    success_count = 0
    error_count = 0
    errors = []

    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh_key,
                "auth": sub.auth_key,
            },
        }

        payload = json.dumps({
            "title": "BamBuddy Test",
            "body": "Push notifications are working!",
            "url": "/",
        })

        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": claims_email},
            )
            sub.last_success = datetime.utcnow()
            success_count += 1
            logger.info(f"Test push sent to: {sub.name or sub.id}")
        except WebPushException as e:
            error_count += 1
            sub.last_error = str(e)
            sub.last_error_at = datetime.utcnow()
            errors.append(f"{sub.name or sub.id}: {str(e)}")
            logger.error(f"Push error for {sub.name or sub.id}: {e}")

            # If subscription is gone (410), remove it
            if e.response and e.response.status_code == 410:
                await db.delete(sub)
                logger.info(f"Removed expired subscription: {sub.name or sub.id}")

    await db.commit()

    return {
        "success": success_count > 0,
        "message": f"Sent to {success_count} device(s), {error_count} error(s)",
        "errors": errors if errors else None,
    }
