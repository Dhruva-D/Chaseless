import json
from pathlib import Path

from apps.api.app.main import app


def main() -> None:
    output = Path("packages/contracts/openapi.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
