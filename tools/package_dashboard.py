"""Package only the publisher; static assets are uploaded separately."""

from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / ".aws-sam" / "dashboard-publisher.zip"


def package(destination=ARTIFACT):
    source = (ROOT / "dashboard_publisher.py").read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".zip", delete=False) as file:
            temporary = Path(file.name)
        with ZipFile(temporary, "w") as archive:
            info = ZipInfo("dashboard_publisher.py", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, source)
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


if __name__ == "__main__":
    print(package())
