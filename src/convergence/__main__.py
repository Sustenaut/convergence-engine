"""`python -m convergence services STATE.json`: the services a state file lists, one per line.

`python -m convergence secret-readers STATE.json CHECKOUTS`: k8s/admin/secret-readers.yaml.

The pod runs the pages and the rounds (convergence.main); this is for a person
checking a state file before it lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .readers import generate
from .state import services


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 3 and args[0] == "secret-readers":
        try:
            print(generate(Path(args[1]).read_text(encoding="utf-8"), Path(args[2])), end="")
        except (OSError, ValueError) as e:
            print(f"convergence: {e}", file=sys.stderr)
            return 1
        return 0
    if len(args) != 2 or args[0] != "services":
        print(__doc__.strip().splitlines()[0].replace("`", ""), file=sys.stderr)
        return 2
    try:
        listed = services(Path(args[1]).read_text(encoding="utf-8"), args[1])
    except (OSError, ValueError) as e:
        print(f"convergence: {e}", file=sys.stderr)
        return 1
    print("\n".join(listed) if listed else "convergence: no repository has the role deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover - run as python -m convergence
