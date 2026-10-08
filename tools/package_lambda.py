"""Build the SAM CodeUri ZIP from the three required application modules only."""

from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
MODULES = ("lambda_function.py", "s3_persistence.py", "yata_collector.py")
ARTIFACT = ROOT / ".aws-sam" / "collector.zip"


def package(destination: Path = ARTIFACT) -> Path:
    """Replace a generated ZIP atomically; never traverse the repository."""
    sources = {name: (ROOT / name).read_bytes() for name in MODULES}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".zip", delete=False
        ) as file:
            temporary = Path(file.name)
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in sources.items():
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, content)
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


if __name__ == "__main__":
    print(package())
