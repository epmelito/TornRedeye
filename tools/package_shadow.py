"""Package the independent consumer using the existing deterministic packager."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import package_lambda


MODULES = ("aws_shadow_forecast.py", "evidence_backup.py", "tornredeye/__init__.py",
           "tornredeye/normalization.py", "tornredeye/evidence_paths.py", "tornredeye/stock_history.py",
           "tornredeye/availability_forecast.py", "tornredeye/shadow_forecast.py")
ARTIFACT = package_lambda.ROOT / ".aws-sam" / "shadow-consumer.zip"


def package(destination=ARTIFACT):
    return package_lambda.package(destination, modules=MODULES)


if __name__ == "__main__":
    print(package())
