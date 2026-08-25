from pathlib import Path

CONSTRAINTS = Path("constraints/be5292d-run-32602746777.txt")
DOCKERFILE = Path("Dockerfile")

KNOWN_GOOD_BASE = (
    "python:3.12-slim@sha256:"
    "2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a"
)
EXPECTED_PINS = {
    "google-api-core": "2.34.0",
    "google-auth": "2.56.3",
    "google-cloud-core": "2.6.1",
    "google-genai": "2.19.0",
    "llama-index-core": "0.14.24",
    "llama-index-embeddings-google-genai": "0.5.1",
    "llama-index-llms-google-genai": "0.10.0",
    "pytest": "8.4.2",
}


def _constraint_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pins[name.strip()] = version.strip()
    return pins


def test_constraints_file_pins_the_aug22_resolution() -> None:
    pins = _constraint_pins()
    for name, version in EXPECTED_PINS.items():
        assert pins[name] == version
    assert "comunicamundo-v4" not in pins


def test_dockerfile_pins_the_aug22_slim_digest() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert KNOWN_GOOD_BASE in text
    assert "--constraint" in text
    assert "constraints/be5292d-run-32602746777.txt" in text
