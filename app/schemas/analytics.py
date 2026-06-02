"""Pydantic v2 response models for the analytics endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NoShowsByQuarterRow(BaseModel):
    """One (neighbourhood, gender) row with the four quarterly no-show counts.

    JSON keys are emitted as ``Q1``..``Q4`` to match the brief's example output.
    """

    model_config = ConfigDict(populate_by_name=True)

    neighbourhood: str
    gender: str
    q1: int = Field(serialization_alias="Q1")
    q2: int = Field(serialization_alias="Q2")
    q3: int = Field(serialization_alias="Q3")
    q4: int = Field(serialization_alias="Q4")


class NoShowsByQuarterResponse(BaseModel):
    year: int
    rows: list[NoShowsByQuarterRow]


class AboveAverageNeighbourhoodRow(BaseModel):
    id: int
    neighbourhood: str
    no_shows: int


class AboveAverageNeighbourhoodsResponse(BaseModel):
    year: int
    mean_no_shows: float
    rows: list[AboveAverageNeighbourhoodRow]
