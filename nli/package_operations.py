"""Small, deterministic APT plans; this module never runs privileged commands."""
from dataclasses import dataclass
import re
import shlex

APT_GET = "/usr/bin/apt-get"
SUDO = "/usr/bin/sudo"
PACKAGE_ACTIONS = frozenset({"install_packages", "remove_packages", "update_packages"})
# Names, not versions, repository selectors, paths, options, or APT patterns.
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]{1,127}(?::[a-z0-9][a-z0-9-]{0,31})?\Z", re.ASCII)


def validate_packages(target: str) -> tuple[str, ...]:
    if type(target) is not str or not target.strip() or len(target) > 3200:
        raise ValueError("Specify between 1 and 20 exact Debian/Ubuntu package names.")
    packages = tuple(target.split())
    if not 1 <= len(packages) <= 20:
        raise ValueError("Specify between 1 and 20 package names.")
    for package in packages:
        if not _PACKAGE.fullmatch(package) or package.split(":", 1)[0].endswith("-"):
            raise ValueError(f"Invalid package name: {package!r}. Options, paths and shell syntax are forbidden.")
    return tuple(dict.fromkeys(packages))


@dataclass(frozen=True)
class PackageStep:
    argv: tuple[str, ...]
    explanation: str

    @property
    def display(self) -> str:
        return shlex.join(("sudo", *self.argv))


def package_plan(action: str, packages: tuple[str, ...] = ()) -> tuple[PackageStep, ...]:
    if action not in PACKAGE_ACTIONS:
        raise ValueError("Unsupported package action.")
    if type(packages) is not tuple or any(type(p) is not str for p in packages):
        raise ValueError("Packages must be a tuple of exact names.")
    if action == "update_packages":
        if packages:
            raise ValueError("Updating package indexes takes no package names.")
    elif not packages or validate_packages(" ".join(packages)) != packages:
        raise ValueError("Invalid or duplicate package names.")
    update = PackageStep((APT_GET, "-o", "APT::Update::Error-Mode=any", "update"),
                         "Refresh the configured repositories' package indexes. This does not upgrade installed applications.")
    if action == "update_packages":
        return (update,)
    config = ("-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold")
    if action == "install_packages":
        install = PackageStep((APT_GET, *config, "--assume-yes", "--no-remove", "install", "--", *packages),
                              "Install the named packages and required dependencies; existing named packages may be upgraded. Refuse any removal and preserve modified configuration files.")
        return (update, install, update)
    return (PackageStep((APT_GET, *config, "--assume-yes", "remove", "--", *packages),
                        "Remove the named packages and any dependent packages APT lists in the simulation. Keep configuration files; do not purge or autoremove."),)


def format_package_plan(action: str, packages: tuple[str, ...] = ()) -> str:
    return "\n".join(f"{number}. {step.display}\n   {step.explanation}"
                     for number, step in enumerate(package_plan(action, packages), 1))
