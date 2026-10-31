"""Assemble the built stock page and existing operations assets; never include data."""

from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ("index.html", "diagnostics.html", "usage.html", "styles.css", "app.js")


def package(destination=ROOT / ".aws-sam" / "stock-site"):
    build = ROOT / "frontend" / "dist"
    entry = build / "stock.html"
    if not entry.is_file():
        raise ValueError("build the stock frontend first: npm --prefix frontend run build")
    files = {"stock.html": entry}
    for source in sorted((build / "stock-assets").rglob("*")):
        if source.is_symlink():
            raise ValueError("stock build must not contain symlinks")
        if source.is_file():
            if source.suffix not in (".js", ".css", ".svg", ".ttf", ".woff2"):
                raise ValueError("unexpected stock asset type")
            files[source.relative_to(build).as_posix()] = source
    if len(files) < 3:
        raise ValueError("stock build assets are missing")
    files.update({name: ROOT / "dashboard" / name for name in OPERATIONS})
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    license_path = destination / "stock-assets" / "FONT-LICENSE.txt"
    shutil.copyfile(ROOT / "frontend" / "src" / "fonts" / "OFL.txt", license_path)
    files["stock-assets/FONT-LICENSE.txt"] = license_path
    (destination / "stock-site-files.json").write_text(json.dumps(sorted(files), indent=2) + "\n")
    # explicit origin metadata prevents the managed policy's one-day fallback
    metadata = {}
    content_types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                     ".ttf": "font/ttf", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
    for name in sorted(files):
        cache = "public,max-age=300"
        if name == "stock.html":
            cache = "public,max-age=0,must-revalidate"
        elif name.startswith("stock-assets/") and name != "stock-assets/FONT-LICENSE.txt":
            cache = "public,max-age=31536000,immutable"
        metadata[name] = {"CacheControl": cache, "ContentType": content_types[Path(name).suffix]}
    (destination / "stock-site-upload-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return destination


if __name__ == "__main__":
    print(package())
