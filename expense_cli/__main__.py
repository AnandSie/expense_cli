from pathlib import Path
import sys

try:
    from expense_cli.cli import main
except ModuleNotFoundError:
    # Support "Run Python File" from inside the package directory in IDEs.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from expense_cli.cli import main


if __name__ == "__main__":
    main()
