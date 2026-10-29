from typing import Any, Literal, List, Optional
from enum import Enum
from pydantic import BaseModel, Field


class PlaygroundMode(str, Enum):
    T2I = "t2i"
    I2I = "i2i"
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"
    V2V = "v2v"


class PlaygroundOutput(BaseModel):
    id: str = Field(..., description="Unique identifier (UUID)")
    media_path: str = Field(..., description="Generated file path relative to output/")
    media_type: str = Field(..., description="Output media type: image or video")
    thumbnail_path: Optional[str] = Field(None, description="Thumbnail file path relative to output/")
    saved_to_library: bool = Field(False, description="Whether this output has been saved to the project library")
    attempt_id: Optional[str] = Field(None, description="Batch attempt that produced this output")


class PlaygroundAttempt(BaseModel):
    attempt_id: str = Field(..., description="Stable batch member identifier")
    ordinal: int = Field(..., ge=0, description="Zero-based batch ordinal")
    status: str = Field("pending", description="Batch member status")
    error: Optional[str] = Field(None, description="Batch member failure, if any")
    provider_execution: Optional[dict[str, Any]] = Field(None, description="Neutral Core provider execution record, if used")


class PlaygroundGeneration(BaseModel):
    record_type: Literal["generation"] = "generation"
    schema_version: int = Field(1, description="Playground history schema version")
    id: str = Field(..., description="Unique identifier (UUID)")
    client_request_id: Optional[str] = Field(None, description="Client generated request id")
    request_contract_version: Optional[str] = Field(None, description="Request identity contract")
    request_fingerprint: Optional[str] = Field(None, description="Stable request fingerprint")
    mode: PlaygroundMode = Field(..., description="Generation mode")
    model_id: str = Field(..., description="Model identifier from model catalog")
    prompt: str = Field(..., description="Text prompt for generation")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt to exclude undesired elements")
    input_media: List[str] = Field(default_factory=list, description="Input file paths for image/video-conditioned modes")
    parameters: dict = Field(default_factory=dict, description="Generation parameters (resolution, duration, aspect_ratio, etc.)")
    batch_size: int = Field(1, ge=1, le=4, description="Number of outputs to generate per request (1-4)")
    outputs: List[PlaygroundOutput] = Field(default_factory=list, description="Generated outputs")
    attempts: List[PlaygroundAttempt] = Field(default_factory=list, description="Per-batch admission and execution status")
    status: str = Field("pending", description="Generation status: pending/processing/completed/failed")
    error: Optional[str] = Field(None, description="Error message if generation failed")
    created_at: str = Field(..., description="Creation timestamp in ISO 8601 format")


class PlaygroundRequestTombstone(BaseModel):
    record_type: Literal["tombstone"] = "tombstone"
    schema_version: int = 1
    client_request_id: str
    request_contract_version: Optional[str] = None
    request_fingerprint: str
    generation_id: str
    deleted_at: str
    deleted_status: str


class PlaygroundRequestReceipt(BaseModel):
    schema_version: int = 1
    replayed: bool
    request_status: Literal["accepted", "deleted"]
    generation: Optional[PlaygroundGeneration] = None
    tombstone: Optional[PlaygroundRequestTombstone] = None


class PlaygroundTemplate(BaseModel):
    id: str = Field(..., description="Unique identifier (UUID)")
    name: str = Field(..., description="Template display name")
    category: str = Field("general", description="Template category: image/video/general")
    prompt: str = Field(..., description="Template prompt text")
    negative_prompt: Optional[str] = Field(None, description="Default negative prompt")
    default_mode: Optional[PlaygroundMode] = Field(None, description="Default generation mode for this template")
    default_model_id: Optional[str] = Field(None, description="Default model identifier")
    default_parameters: dict = Field(default_factory=dict, description="Default generation parameters")
    created_at: str = Field(..., description="Creation timestamp in ISO 8601 format")
    updated_at: str = Field(..., description="Last update timestamp in ISO 8601 format")


class GenerateRequest(BaseModel):
    client_request_id: str = Field(..., description="Client generated idempotency key")
    mode: PlaygroundMode = Field(..., description="Generation mode")
    model_id: str = Field(..., description="Model identifier from model catalog")
    prompt: str = Field(..., description="Text prompt for generation")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt to exclude undesired elements")
    input_media: Optional[List[str]] = Field(None, description="Input file paths for image/video-conditioned modes")
    parameters: Optional[dict] = Field(None, description="Generation parameters (resolution, duration, aspect_ratio, etc.)")
    batch_size: Optional[int] = Field(1, ge=1, le=4, description="Number of outputs to generate (1-4)")

    def identity_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_request_id": self.client_request_id,
            "mode": self.mode.value,
            "model_id": self.model_id,
            "prompt": self.prompt,
        }
        if self.negative_prompt is not None:
            payload["negative_prompt"] = self.negative_prompt
        if self.input_media is not None and len(self.input_media) > 0:
            payload["input_media"] = list(self.input_media)
        if self.parameters is not None and len(self.parameters) > 0:
            payload["parameters"] = self.parameters
        if self.batch_size is not None and self.batch_size > 1:
            payload["batch_size"] = self.batch_size
        return payload


class SaveToLibraryRequest(BaseModel):
    category: str = Field("general", description="Library category for the saved output")


class CreateTemplateRequest(BaseModel):
    name: str = Field(..., description="Template display name")
    category: Optional[str] = Field("general", description="Template category: image/video/general")
    prompt: str = Field(..., description="Template prompt text")
    negative_prompt: Optional[str] = Field(None, description="Default negative prompt")
    default_mode: Optional[PlaygroundMode] = Field(None, description="Default generation mode")
    default_model_id: Optional[str] = Field(None, description="Default model identifier")
    default_parameters: Optional[dict] = Field(None, description="Default generation parameters")


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = Field(None, description="Template display name")
    category: Optional[str] = Field(None, description="Template category: image/video/general")
    prompt: Optional[str] = Field(None, description="Template prompt text")
    negative_prompt: Optional[str] = Field(None, description="Default negative prompt")
    default_mode: Optional[PlaygroundMode] = Field(None, description="Default generation mode")
    default_model_id: Optional[str] = Field(None, description="Default model identifier")
    default_parameters: Optional[dict] = Field(None, description="Default generation parameters")
