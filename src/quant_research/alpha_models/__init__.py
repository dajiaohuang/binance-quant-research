"""Synthetic-only alpha-model contracts.

No object in this package authorizes empirical data, eligibility, IC, P&L or
backtesting.  Model-specific modules are imported explicitly so the pure-Python
contract layer remains usable without importing a tensor runtime.
"""

from .contracts import (
    DDGLConfig,
    DDGLContractError,
    DDGLInputBatch,
    DDGLLabelBatch,
    DDGLTrainingExample,
    load_ddgl_config,
    make_synthetic_examples,
)

__all__ = [
    "DDGLConfig",
    "DDGLContractError",
    "DDGLInputBatch",
    "DDGLLabelBatch",
    "DDGLTrainingExample",
    "load_ddgl_config",
    "make_synthetic_examples",
]

