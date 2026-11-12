"""The reaction record: one shape used by manual entry, PDF extraction, the calculators,
the diagnosis, the suggestions, the reports and the feedback store.

A reaction is a list of role-tagged components plus conditions. Roles are never dropped on the
way to the calculators, so the share of each role (for example the solvent share of PMI) can
always be computed.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

ROLES = ("reactant", "reagent", "catalyst", "base", "additive", "reaction_solvent",
         "workup", "purification", "water", "product")

ROLE_LABELS = {
    "reactant": "Reactant", "reagent": "Reagent (stoichiometric)", "catalyst": "Catalyst / ligand",
    "base": "Base", "additive": "Additive", "reaction_solvent": "Reaction solvent",
    "workup": "Workup material", "purification": "Purification material", "water": "Water / aqueous",
    "product": "Product",
}

# Roles in the balanced equation: they count as "reactants" in AE, RME, CE and ERF.
STOICHIOMETRIC_ROLES = ("reactant", "reagent")
# Stage of the process each role belongs to.
STAGE = {"reactant": "reaction", "reagent": "reaction", "catalyst": "reaction", "base": "reaction",
         "additive": "reaction", "reaction_solvent": "reaction", "workup": "workup",
         "purification": "purification", "water": "workup", "product": "reaction"}


@dataclass
class Component:
    name: str
    role: str
    mass_g: Optional[float] = None
    moles: Optional[float] = None
    mw: Optional[float] = None
    smiles: Optional[str] = None
    carbons: Optional[int] = None
    equiv: Optional[float] = None          # equivalents relative to the limiting reactant
    volume_ml: Optional[float] = None
    coefficient: float = 1.0               # stoichiometric coefficient (reactants, reagents, product)
    limiting: bool = False
    benign: bool = False                   # Hudlicky's "benign" (EMY only)
    stage: Optional[str] = None            # "reaction" | "workup" | "purification"; default from the role
    estimated: bool = False                # the mass is an assumption, not a value from the procedure
    note: str = ""                         # where the mass comes from, e.g. "mass = volume x density"

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"unknown role '{self.role}' for '{self.name}'")
        if self.stage is None:
            self.stage = STAGE[self.role]

    @property
    def mass(self) -> float:
        return float(self.mass_g) if self.mass_g else 0.0


@dataclass
class Reaction:
    name: str = ""
    components: list[Component] = field(default_factory=list)
    reported_yield_percent: Optional[float] = None    # as printed in the source, for cross-checking
    temperature_c: Optional[float] = None
    time_h: Optional[float] = None
    reflux: bool = False
    atmosphere: Optional[str] = None                  # "air" | "nitrogen" | "argon" | ...
    activation: Optional[str] = None                  # "thermal" | "microwave" | "blue LED" | ...
    workup_steps: list[str] = field(default_factory=list)      # EcoScale workup categories, one per step
    extra_setups: list[str] = field(default_factory=list)      # EcoScale set-up categories not implied by the conditions
    price_category: Optional[str] = None              # EcoScale price category; None = not assessed
    reaction_class: Optional[str] = None              # "amide_coupling" | "suzuki_miyaura" | "reductive_amination" | None
    source: dict = field(default_factory=dict)        # {"entry": "manual" | "pdf" | "example", "doi": ..., "pages": ...}

    # -- access ---------------------------------------------------------------------------
    def by_role(self, *roles: str) -> list[Component]:
        return [c for c in self.components if c.role in roles]

    @property
    def product(self) -> Optional[Component]:
        return next((c for c in self.components if c.role == "product"), None)

    @property
    def inputs(self) -> list[Component]:
        """Everything that goes in (all components except the product)."""
        return [c for c in self.components if c.role != "product"]

    def find(self, name: str) -> Optional[Component]:
        return next((c for c in self.components if c.name == name), None)

    # -- copies and serialization ---------------------------------------------------------
    def copy(self) -> "Reaction":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Reaction":
        known = {f.name for f in fields(cls)}
        comp_known = {f.name for f in fields(Component)}
        comps = [Component(**{k: v for k, v in c.items() if k in comp_known}) for c in d.get("components", [])]
        rest = {k: v for k, v in d.items() if k in known and k != "components"}
        return cls(components=comps, **rest)
