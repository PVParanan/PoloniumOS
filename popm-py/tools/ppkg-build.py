#!/usr/bin/env python3
"""
ppkg-build — PoloniumOS Package Builder
Creates .ppkg packages from a simple directory structure.

Usage:
    ppkg-build init  myapp          # scaffold a new package
    ppkg-build build myapp/         # build myapp.ppkg
    ppkg-build info  myapp.ppkg     # show package info
    ppkg-build test  myapp.ppkg     # test install in /tmp sandbox

.ppkg MANIFEST fields:
    name        (required) package name, lowercase, no spaces
    version     (required) semver e.g. 1.0.0
    description (required) one line description
    author      your name / org
    license     e.g. MIT, GPL-3.0
    depends     list of required packages
    conflicts   list of conflicting packages
    arch        x86_64 / aarch64 / any
    url         project homepage
    sha256      auto-filled by build step
"""

import os
import sys
import json
import shutil
import hashlib
import tarfile
import argparse
from pathlib import Path
from datetime import datetime

PPKG_EXT = ".ppkg"

def scaffold(name):
    """Create a new package directory with template files."""
    d = Path(name)
    if d.exists():
        print(f"Directory '{name}' already exists")
        sys.exit(1)

    # Create structure
    (d / "files" / "usr" / "bin").mkdir(parents=True)
    (d / "files" / "usr" / "share" / name).mkdir(parents=True)
    (d / "scripts").mkdir()

    # MANIFEST
    manifest = {
        "name":        name,
        "version":     "1.0.0",
        "description": f"{name} package for PoloniumOS",
        "author":      "Your Name",
        "license":     "MIT",
        "arch":        "x86_64",
        "url":         f"https://github.com/yourname/{name}",
        "depends":     [],
        "conflicts":   [],
        "sha256":      ""
    }
    with open(d / "MANIFEST", "w") as f:
        json.dump(manifest, f, indent=2)

    # Example binary placeholder
    bin_file = d / "files" / "usr" / "bin" / name
    bin_file.write_text(f"#!/bin/sh\necho 'Hello from {name}!'\n")
    bin_file.chmod(0o755)

    # pre-install script
    (d / "scripts" / "pre-install.sh").write_text(
        "#!/bin/sh\n# Runs before installation\necho 'Pre-install OK'\n"
    )
    (d / "scripts" / "post-install.sh").write_text(
        "#!/bin/sh\n# Runs after installation\necho 'Post-install OK'\n"
    )
    (d / "scripts" / "pre-remove.sh").write_text(
        "#!/bin/sh\n# Runs before removal\necho 'Pre-remove OK'\n"
    )
    (d / "scripts" / "post-remove.sh").write_text(
        "#!/bin/sh\n# Runs after removal\necho 'Post-remove OK'\n"
    )

    # Make scripts executable
    for s in (d / "scripts").glob("*.sh"):
        s.chmod(0o755)

    print(f"[+] Scaffolded package: {name}/")
    print(f"    Edit {name}/MANIFEST to set metadata")
    print(f"    Put your files in {name}/files/")
    print(f"    Then run: ppkg-build build {name}/")


def build(spec_dir):
    """Build a .ppkg from a spec directory."""
    spec = Path(spec_dir)
    manifest_path = spec / "MANIFEST"

    if not manifest_path.exists():
        print(f"[ERROR] No MANIFEST in {spec_dir}")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    name    = manifest["name"]
    version = manifest["version"]
    arch    = manifest.get("arch", "any")
    output  = Path(f"{name}-{version}-{arch}{PPKG_EXT}")

    print(f"[*] Building {name} v{version} ({arch})...")

    with tarfile.open(output, "w:gz") as tar:
        # Add MANIFEST
        tar.add(manifest_path, arcname="MANIFEST")

        # Add files/
        files_dir = spec / "files"
        if files_dir.exists():
            for item in sorted(files_dir.rglob("*")):
                arcname = "files/" + str(item.relative_to(files_dir))
                tar.add(item, arcname=arcname, recursive=False)
                if item.is_file():
                    print(f"    + {arcname}")

        # Add scripts/
        scripts_dir = spec / "scripts"
        if scripts_dir.exists():
            for script in sorted(scripts_dir.glob("*.sh")):
                tar.add(script, arcname=f"scripts/{script.name}")

    # Calculate SHA256
    h = hashlib.sha256()
    with open(output, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    sha = h.hexdigest()

    # Update manifest with sha256
    manifest["sha256"] = sha
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Rebuild with updated manifest
    with tarfile.open(output, "w:gz") as tar:
        tar.add(manifest_path, arcname="MANIFEST")
        files_dir = spec / "files"
        if files_dir.exists():
            for item in sorted(files_dir.rglob("*")):
                arcname = "files/" + str(item.relative_to(files_dir))
                tar.add(item, arcname=arcname, recursive=False)
        scripts_dir = spec / "scripts"
        if scripts_dir.exists():
            for script in sorted(scripts_dir.glob("*.sh")):
                tar.add(script, arcname=f"scripts/{script.name}")

    size = output.stat().st_size
    print(f"[+] Built: {output} ({size/1024:.1f} KB)")
    print(f"[+] SHA256: {sha}")


def show_info(ppkg_path):
    """Display .ppkg metadata."""
    with tarfile.open(ppkg_path, "r:gz") as tar:
        mf = tar.extractfile(tar.getmember("MANIFEST"))
        manifest = json.loads(mf.read().decode())

    print(f"\n  Name        : {manifest['name']}")
    print(f"  Version     : {manifest['version']}")
    print(f"  Description : {manifest.get('description','')}")
    print(f"  Author      : {manifest.get('author','')}")
    print(f"  License     : {manifest.get('license','')}")
    print(f"  Arch        : {manifest.get('arch','any')}")
    print(f"  URL         : {manifest.get('url','')}")
    print(f"  Depends     : {', '.join(manifest.get('depends',[]))}")
    print(f"  SHA256      : {manifest.get('sha256','')[:16]}...")

    with tarfile.open(ppkg_path, "r:gz") as tar:
        files = [m.name for m in tar.getmembers()
                 if m.name.startswith("files/") and not m.isdir()]
    print(f"  Files       : {len(files)}")
    print()


def test_install(ppkg_path):
    """Test-install a .ppkg into a /tmp sandbox."""
    import tempfile
    sandbox = Path(tempfile.mkdtemp(prefix="ppkg-test-"))
    print(f"[*] Test-installing into sandbox: {sandbox}")

    with tarfile.open(ppkg_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("files/"):
                rel = member.name[len("files/"):]
                if not rel:
                    continue
                dest = sandbox / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                src_file = tar.extractfile(member)
                if src_file:
                    dest.write_bytes(src_file.read())
                    print(f"    → {dest}")

    print(f"[+] Test install complete in {sandbox}")
    print(f"    Inspect with: ls -lR {sandbox}")
    print(f"    Cleanup with: rm -rf {sandbox}")


def main():
    parser = argparse.ArgumentParser(
        prog="ppkg-build",
        description="PoloniumOS Package Builder"
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init",  help="Scaffold a new package").add_argument("name")
    sub.add_parser("build", help="Build a .ppkg").add_argument("specdir")
    sub.add_parser("info",  help="Show .ppkg info").add_argument("file")
    sub.add_parser("test",  help="Test install in sandbox").add_argument("file")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == "init":  scaffold(args.name)
    elif args.cmd == "build": build(args.specdir)
    elif args.cmd == "info":  show_info(args.file)
    elif args.cmd == "test":  test_install(args.file)

if __name__ == "__main__":
    main()
