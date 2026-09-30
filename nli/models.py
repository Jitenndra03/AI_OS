"""Immutable, strictly typed proposals. A proposal is data, never shell code."""
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class CommandProposal:
    action: str
    script: str
    target: Optional[str]
    requires_sudo: bool
    confirm_required: bool
    risk_level: str
    explanation: str
    argv: tuple[str, ...] = ()
    cwd: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "CommandProposal":
        required = {"action", "script", "target", "requires_sudo", "confirm_required",
                    "risk_level", "explanation"}
        if not isinstance(data, dict) or not required <= data.keys():
            raise ValueError("Missing proposal fields")
        if data.keys() - (required | {"argv", "cwd"}):
            raise ValueError("Unexpected proposal fields")
        for name in ("action", "script", "risk_level", "explanation"):
            if type(data[name]) is not str:
                raise ValueError(f"{name} must be a string")
        if data["target"] is not None and type(data["target"]) is not str:
            raise ValueError("target must be a string or null")
        for name in ("requires_sudo", "confirm_required"):
            if type(data[name]) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if data["risk_level"] not in {"low", "medium", "high"}:
            raise ValueError("Invalid risk level")
        argv = data.get("argv", ())
        if not isinstance(argv, (list, tuple)) or any(type(x) is not str for x in argv):
            raise ValueError("argv must contain strings")
        if type(data.get("cwd", "")) is not str:
            raise ValueError("cwd must be a string")
        return cls(**{**data, "argv": tuple(argv)})

    def to_dict(self) -> dict:
        return asdict(self)
