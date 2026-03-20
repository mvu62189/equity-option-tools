from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BSInput:
    spot: float
    strike: float
    rate: float
    dividend: float
    tau: float
    vol: float
    is_call: bool


@dataclass(slots=True)
class BSResult:
    price: float


@dataclass(slots=True)
class SSVIConstraints:
    rho_min: float = -0.999
    rho_max: float = 0.999
    b_min: float = 1e-6
    sigma_min: float = 1e-6


@dataclass(slots=True)
class SSVIResult:
    a: float
    b: float
    rho: float
    m: float
    sigma: float
    objective: float
    success: bool
    iterations: int
    durrleman_pass: bool = True


@dataclass(slots=True)
class AmericanContract:
    spot: float
    strike: float
    rate: float
    dividend: float
    tau: float
    is_call: bool


@dataclass(slots=True)
class AmericanIVDiagnostics:
    implied_vol: float
    american_price: float
    european_price: float
    eep: float
    success: bool
