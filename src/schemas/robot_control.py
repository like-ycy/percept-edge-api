from pydantic import BaseModel, Field


class ElectromagnetToggleRequest(BaseModel):
    enabled: bool


class LiftHeightRequest(BaseModel):
    height: int = Field(ge=0, le=2300)


class CommandResultPayload(BaseModel):
    component_id: str
    action: str
    result: dict


class ElectromagnetToggleResponse(BaseModel):
    enabled: bool
    component_id: str
    action: str
    result: dict


class LiftHeightResponse(BaseModel):
    height: int
    component_id: str
    action: str
    result: dict
