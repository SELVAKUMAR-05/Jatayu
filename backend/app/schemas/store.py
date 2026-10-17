from typing import Literal

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    sku: str = Field(min_length=2, max_length=40)
    quantity: int = Field(ge=1, le=10)


class CheckoutInput(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=10)
    shipping_address: str = Field(min_length=1, max_length=200)
    payment_method: Literal["demo_card", "demo_declined"] = "demo_card"
    demo_fault: Literal["none", "500", "503", "504", "429", "invalid_address", "payment_rejected",
                        "inventory_unavailable", "queue_backlog", "database_bottleneck"] = "none"
    idempotency_key: str = Field(min_length=8, max_length=128)


class RestockInput(BaseModel):
    quantity: int = Field(ge=1, le=10000)
    reason: str = Field(min_length=3, max_length=160)
