"""Assert the installed environment matches the Aug 22 known-good freeze.

Source: GitHub Actions run 32602746777 on be5292d.
No model call. No Cloud Run. No deploy.
"""

from __future__ import annotations

import argparse
import sys
from importlib import metadata
from pathlib import Path

KNOWN_GOOD_RUN = "32602746777"
KNOWN_GOOD_SHA = "be5292d6af449481503543b24d82f061b52c2c47"
KNOWN_GOOD_BASE = (
    "python:3.12-slim@sha256:"
    "2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a"
)
KNOWN_GOOD_WORKER = (
    "us-central1-docker.pkg.dev/comunicamundo-v4/comunicamundo-v4/"
    "hefesto-v4@sha256:8e7f0eaede43d84408d7494f59118af80e83393081701759d2715635bd22cae9"
)

RUNTIME_PINS = {
    "google-api-core": "2.34.0",
    "google-auth": "2.56.3",
    "google-cloud-core": "2.6.1",
    "google-cloud-firestore": "2.28.1",
    "google-cloud-storage": "3.13.1",
    "google-genai": "2.19.0",
    "llama-index-core": "0.14.24",
    "llama-index-embeddings-google-genai": "0.5.1",
    "llama-index-llms-google-genai": "0.10.0",
}

DEV_PINS = {
    "pytest": "8.4.2",
    "pytest-asyncio": "1.1.0",
}

CONSTRAINTS_RELATIVE = Path("constraints/be5292d-run-32602746777.txt")


def _constraints_path() -> Path:
    return Path(__file__).resolve().parents[1] / CONSTRAINTS_RELATIVE


def constraint_pins(path: Path | None = None) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in (path or _constraints_path()).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pins[name.strip()] = version.strip()
    return pins


def installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def assert_pins(pins: dict[str, str], *, required: bool) -> list[str]:
    errors: list[str] = []
    for name, expected in pins.items():
        got = installed_version(name)
        if got is None:
            if required:
                errors.append(f"{name}: missing (expected {expected})")
            continue
        if got != expected:
            errors.append(f"{name}: expected {expected}, got {got}")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="skip pytest pins (worker image, no [dev] extra)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"ENVIRONMENT_PROOF python={sys.version.replace(chr(10), ' ')}")
    print(f"ENVIRONMENT_PROOF known_good_run={KNOWN_GOOD_RUN}")
    print(f"ENVIRONMENT_PROOF known_good_sha={KNOWN_GOOD_SHA}")
    print(f"ENVIRONMENT_PROOF known_good_base={KNOWN_GOOD_BASE}")
    print(f"ENVIRONMENT_PROOF known_good_worker={KNOWN_GOOD_WORKER}")

    if sys.version_info[:2] != (3, 12):
        print("ENVIRONMENT_PROOF: FAIL python is not 3.12")
        return 1

    errors = assert_pins(RUNTIME_PINS, required=True)
    errors.extend(assert_pins(DEV_PINS, required=not args.runtime_only))
    constraints_file = _constraints_path()
    if constraints_file.is_file():
        freeze_pins = constraint_pins(constraints_file)
        for name, expected in {**RUNTIME_PINS, **DEV_PINS}.items():
            if freeze_pins.get(name) != expected:
                errors.append(f"constraints file missing {name}=={expected}")
    elif not args.runtime_only:
        errors.append(f"constraints file missing: {constraints_file}")

    if errors:
        print("ENVIRONMENT_PROOF: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("ENVIRONMENT_PROOF: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
