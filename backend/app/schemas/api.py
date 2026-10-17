from typing import Literal

from pydantic import BaseModel, Field


class OrderInput(BaseModel):
    product: str = Field(default="Wireless Headphones", min_length=2, max_length=120)
    quantity: int = Field(default=1, ge=1, le=100)
    customer_ref: str | None = Field(default=None, max_length=40)
    process: bool = True
    fault: str | None = None
    fault_module: Literal["Order Management", "Payment", "Inventory", "Delivery", "Notification"] | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class FaultInput(BaseModel):
    fault: Literal["500", "503", "504", "429", "auth", "malformed", "invalid_address",
                   "payment_rejected", "inventory_unavailable", "queue_backlog",
                   "database_bottleneck", "recovery_fails", "unfamiliar"]
    module: Literal["Order Management", "Payment", "Inventory", "Delivery", "Notification"] | None = None


class AnalyzeInput(BaseModel):
    order_id: str


class ActionDecision(BaseModel):
    approve: bool
    actor: str = Field(default="operator", min_length=2, max_length=64)


class SettingsInput(BaseModel):
    retry_limit: int | None = Field(default=None, ge=1, le=5)
    simulation_speed: int | None = Field(default=None, ge=1, le=10)
    fault_injection_enabled: bool | None = None
    stage_limits_ms: dict[str, int] | None = None


class Explanation(BaseModel):
    what_happened: str = Field(min_length=1, max_length=1000)
    affected_module: str = Field(min_length=1, max_length=200)
    evidence: list[str]
    likely_impact: str = Field(min_length=1, max_length=1000)
    next_step: str = Field(min_length=1, max_length=1000)
    additional_evidence_needed: list[str]
