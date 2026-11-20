import re
from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


class SupplierInfo(BaseModel):
    name: Optional[str] = None
    gstin: Optional[str] = None
    city: Optional[str] = None
    active: Optional[bool] = None

    @field_validator("gstin")
    @classmethod
    def validate_gstin(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if len(v) != 15 or not GSTIN_REGEX.match(v):
            raise ValueError("GSTIN must be a valid 15-character Indian GSTIN format")
        return v


class LineItem(BaseModel):
    description: str
    quantity: float = Field(..., gt=0, description="Quantity must be > 0")
    unit_price: float = Field(..., gt=0, description="Unit price must be > 0")
    amount: Optional[float] = None


class InvoiceData(BaseModel):
    invoice_number: str
    invoice_date: date
    supplier: SupplierInfo
    line_items: List[LineItem] = Field(..., min_length=1)
    subtotal: float = Field(..., ge=0)
    tax: float = Field(..., ge=0)
    total: float = Field(..., gt=0)

    @field_validator("invoice_date")
    @classmethod
    def validate_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("invoice_date cannot be in the future")
        return v

    @model_validator(mode="after")
    def validate_totals(self):
        if abs((self.subtotal + self.tax) - self.total) > 1.0:
            raise ValueError("total does not match subtotal + tax (tolerance ±1 rupee)")
        return self


class ErrorResponse(BaseModel):
    error: str
    detail: str
    invoice_id: Optional[str] = None