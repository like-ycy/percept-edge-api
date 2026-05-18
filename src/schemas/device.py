"""设备激活相关 Schema"""

from pydantic import BaseModel, Field


class DeviceActivationRequest(BaseModel):
    """设备激活请求（本地 API）"""

    activation_code: str = Field(min_length=1, description="激活码")
    desc: str = Field(default="", description="设备描述（可选）")


class CloudDeviceType(BaseModel):
    """云端激活请求中的设备类型信息"""

    name: str
    embodied: str
    end_type: str
    camera: str


class CloudDeviceActivateRequest(BaseModel):
    """云端设备激活请求"""

    device_type: CloudDeviceType
    mac: str
    activation_code: str
    address: str
    desc: str


class DeviceActivationResult(BaseModel):
    """设备激活结果"""

    id: int
    uid: str


class DeviceActivationStatusResult(BaseModel):
    """设备激活状态结果"""

    state: bool
    uid: str | None = None
