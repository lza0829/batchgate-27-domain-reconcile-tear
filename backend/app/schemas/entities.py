import re

from pydantic import BaseModel, Field, field_validator

DOMAIN_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class MaxAttemptsBody(BaseModel):
    max_attempts: int = Field(ge=1, le=10)


class DomainBody(BaseModel):
    domain: str = Field(min_length=1, max_length=32, pattern=DOMAIN_PATTERN)

    @field_validator("domain")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not re.match(DOMAIN_PATTERN, value):
            raise ValueError("domain must match ^[a-z0-9][a-z0-9_-]*$")
        return value
