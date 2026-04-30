"""Atlas CLI entry point."""

import sys
from atlas import __version__


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--version":
        print(f"atlas {__version__}")
        return 0
    print("atlas (Head of Operations agent) -- v0.1 in development")
    print(f"version: {__version__}")
    print("see: tasks/atlas_v0_1.md in santigrey/control-plane-lab")
    return 0


if __name__ == "__main__":
    sys.exit(main())
