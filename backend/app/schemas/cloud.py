from pydantic import BaseModel, Field
from typing import Optional


class CloudLoginRequest(BaseModel):
    """Request to initiate cloud login."""
    email: str = Field(..., description="Bambu Lab account email")
    password: str = Field(..., description="Account password")
    region: str = Field(default="global", description="Region: 'global' or 'china'")


class CloudVerifyRequest(BaseModel):
    """Request to verify login with 2FA code."""
    email: str = Field(..., description="Bambu Lab account email")
    code: str = Field(..., description="6-digit verification code from email")


class CloudLoginResponse(BaseModel):
    """Response from login attempt."""
    success: bool
    needs_verification: bool = False
    message: str


class CloudAuthStatus(BaseModel):
    """Current authentication status."""
    is_authenticated: bool
    email: Optional[str] = None


class CloudTokenRequest(BaseModel):
    """Request to set access token directly."""
    access_token: str = Field(..., description="Bambu Lab access token")


class SlicerSetting(BaseModel):
    """A slicer setting/preset."""
    setting_id: str
    name: str
    type: str  # filament, printer, process
    version: Optional[str] = None
    user_id: Optional[str] = None
    updated_time: Optional[str] = None


class SlicerSettingsResponse(BaseModel):
    """Response containing slicer settings."""
    filament: list[SlicerSetting] = []
    printer: list[SlicerSetting] = []
    process: list[SlicerSetting] = []


class CloudDevice(BaseModel):
    """A bound printer device."""
    dev_id: str
    name: str
    dev_model_name: Optional[str] = None
    dev_product_name: Optional[str] = None
    online: bool = False


class SlicerSettingCreate(BaseModel):
    """Request to create a new slicer preset."""
    type: str = Field(..., description="Preset type: 'filament', 'print', or 'printer'")
    name: str = Field(..., description="Display name for the preset")
    base_id: str = Field(..., description="Base preset ID to inherit from")
    version: str = Field(default="2.0.0.0", description="Version string for the preset")
    setting: dict = Field(default_factory=dict, description="Setting key-value pairs (delta from base)")


class SlicerSettingUpdate(BaseModel):
    """Request to update an existing slicer preset."""
    name: Optional[str] = Field(None, description="New display name")
    setting: Optional[dict] = Field(None, description="Setting key-value pairs to update")


class SlicerSettingDetail(BaseModel):
    """Detailed slicer setting/preset response."""
    message: Optional[str] = None
    code: Optional[str] = None
    error: Optional[str] = None
    public: bool = False
    version: Optional[str] = None
    type: str
    name: str
    update_time: Optional[str] = None
    nickname: Optional[str] = None
    base_id: Optional[str] = None
    setting: dict = Field(default_factory=dict)
    filament_id: Optional[str] = None
    setting_id: Optional[str] = None  # For response after create


class SlicerSettingDeleteResponse(BaseModel):
    """Response from deleting a preset."""
    success: bool
    message: str
