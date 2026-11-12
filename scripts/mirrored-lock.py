"""The image's requirements.lock, pointed at storage's mirrors: for the build's second try.

    python scripts/mirrored-lock.py requirements.lock > mirrored.lock
    pip install --index-url https://storage.sustenaut.eu/pypi/simple --require-hashes -r mirrored.lock

A GitHub release wheel (`name @ https://github.com/.../releases/download/...`) becomes the
copy storage's wheel mirror keeps under its sha256, /wheels/f/<sha256>/<file>; PyPI's
packages come from the mirror's simple index. Every hash stays as the lock pins it, so
a mirror can only serve what the lock already trusts.
"""

from __future__ import annotations

import re
import sys

MIRROR = "https://storage.sustenaut.eu/wheels/f"
URL = re.compile(r"^(?P<name>\S+) @ https://github\.com/\S+/releases/download/\S+/(?P<file>[^/\s]+\.whl)(?P<rest>.*)$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def mirrored(text: str) -> str:
    lines = text.splitlines()
    out = []
    for k, line in enumerate(lines):
        match = URL.match(line)
        if match:
            sha = next(HASH.search(later).group(1) for later in lines[k:] if HASH.search(later))
            line = f"{match['name']} @ {MIRROR}/{sha}/{match['file']}{match['rest']}"
        out.append(line)
    return "\n".join(out) + "\n"


if __name__ == "__main__":  # pragma: no cover - the image build's
    sys.stdout.write(mirrored(open(sys.argv[1], encoding="utf-8").read()))
