"""Pure, versioned geography identity resolution for SOOI.

This foundation package is deliberately disconnected from Django models and
product runtime flows.  Consumers must pass candidate evidence independently
from requested search geography.
"""

from .loader import GeographyRegistry, RegistryValidationError, load_default_registry, load_registry, load_v2_registry
from .models import (
    AliasRecord,
    GeographyIdentity,
    GeographyResolution,
    GeographyType,
    MembershipRecord,
    ProvenanceRecord,
    ResolutionProvenance,
    ResolutionStatus,
)
from .resolver import normalize_geography_input, resolve

__all__ = (
    "AliasRecord",
    "GeographyIdentity",
    "GeographyRegistry",
    "GeographyResolution",
    "GeographyType",
    "MembershipRecord",
    "ProvenanceRecord",
    "RegistryValidationError",
    "ResolutionProvenance",
    "ResolutionStatus",
    "load_default_registry",
    "load_registry",
    "load_v2_registry",
    "normalize_geography_input",
    "resolve",
)
