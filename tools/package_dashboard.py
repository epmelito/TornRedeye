"""Package only the publisher; static assets are uploaded separately."""

from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / ".aws-sam" / "dashboard-publisher.zip"


def package(destination=ARTIFACT):
    modules = ("dashboard_publisher.py", "stock_publisher.py", "tornredeye/__init__.py", "tornredeye/evidence_paths.py")
    sources = {name: (ROOT / name).read_bytes() for name in modules}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".zip", delete=False) as file:
            temporary = Path(file.name)
        with ZipFile(temporary, "w") as archive:
            for name, source in sources.items():
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
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
