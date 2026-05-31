#!/usr/bin/env python3
"""
popm — PoloniumOS Package Manager
Package format: .ppkg (PoloniumOS Package)

Usage:
    popm install <package>
    popm remove  <package>
    popm search  <query>
    popm update
    popm upgrade
    popm list
    popm info    <package>
    popm build   <specfile>
    popm verify  <file.ppkg>

.ppkg format:
    A .ppkg file is a gzip-compressed tar archive containing:
        MANIFEST        — package metadata (JSON)
        files/          — files to install
        scripts/
            pre-install.sh
            post-install.sh
            pre-remove.sh
            post-remove.sh
        signature.sig   — SHA256 signature (future: GPG)
"""

import os
import sys
import json
import shutil
import hashlib
import tarfile
import argparse
import tempfile
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime

# ── Constants ─────────────────────────────────────────────
VERSION        = "1.0.0"
POPM_DIR       = Path("/var/lib/popm")
DB_FILE        = POPM_DIR / "installed.json"
CACHE_DIR      = POPM_DIR / "cache"
REPO_DIR       = POPM_DIR / "repos"
LOG_FILE       = POPM_DIR / "popm.log"
INSTALL_ROOT   = Path("/")
REPO_LIST      = Path("/etc/popm/repos.list")
PPKG_EXT       = ".ppkg"

# Default PoloniumOS official repo
DEFAULT_REPO   = "https://repo.poloniumos.org/packages"

# ── Colors ────────────────────────────────────────────────
class C:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def ok(msg):    print(f"{C.GREEN}[+]{C.RESET} {msg}")
def info(msg):  print(f"{C.CYAN}[*]{C.RESET} {msg}")
def warn(msg):  print(f"{C.YELLOW}[!]{C.RESET} {msg}")
def err(msg):   print(f"{C.RED}[ERROR]{C.RESET} {msg}", file=sys.stderr)
def bold(msg):  print(f"{C.BOLD}{msg}{C.RESET}")

# ── Logging ───────────────────────────────────────────────
def log(action, package, status):
    POPM_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] {action:10s} {package:30s} {status}\n")

# ── Database ──────────────────────────────────────────────
def db_load():
    """Load the installed packages database."""
    if not DB_FILE.exists():
        return {}
    with open(DB_FILE) as f:
        return json.load(f)

def db_save(db):
    """Save the installed packages database."""
    POPM_DIR.mkdir(parents=True, exist_ok=True)
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

def db_add(manifest, installed_files):
    """Record a newly installed package."""
    db = db_load()
    db[manifest["name"]] = {
        "name":        manifest["name"],
        "version":     manifest["version"],
        "description": manifest.get("description", ""),
        "author":      manifest.get("author", ""),
        "installed":   datetime.now().isoformat(),
        "files":       installed_files
    }
    db_save(db)

def db_remove(name):
    """Remove a package record."""
    db = db_load()
    if name in db:
        del db[name]
        db_save(db)

def db_get(name):
    """Get info about an installed package."""
    return db_load().get(name)

# ── .ppkg reader ──────────────────────────────────────────
def read_manifest(ppkg_path):
    """Read MANIFEST from a .ppkg file without fully extracting it."""
    with tarfile.open(ppkg_path, "r:gz") as tar:
        try:
            mf = tar.getmember("MANIFEST")
            f  = tar.extractfile(mf)
            return json.loads(f.read().decode())
        except KeyError:
            err(f"Invalid .ppkg: no MANIFEST found in {ppkg_path}")
            sys.exit(1)

def verify_ppkg(ppkg_path):
    """Verify .ppkg integrity via SHA256 checksum."""
    info(f"Verifying {ppkg_path}...")
    manifest = read_manifest(ppkg_path)

    if "sha256" not in manifest:
        warn("No SHA256 in manifest — skipping verification")
        return True

    h = hashlib.sha256()
    with open(ppkg_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)

    actual = h.hexdigest()
    if actual != manifest["sha256"]:
        err(f"Checksum mismatch!\n  Expected: {manifest['sha256']}\n  Got:      {actual}")
        return False

    ok("Checksum verified")
    return True

# ── Installation ──────────────────────────────────────────
def run_script(tar, script_name, tmpdir):
    """Run a pre/post install/remove script from the package."""
    try:
        member = tar.getmember(f"scripts/{script_name}")
        script_path = Path(tmpdir) / script_name
        tar.extract(member, tmpdir)
        os.chmod(script_path, 0o755)
        result = subprocess.run([str(script_path)], capture_output=True, text=True)
        if result.returncode != 0:
            warn(f"{script_name} exited with code {result.returncode}")
            if result.stderr:
                warn(result.stderr.strip())
        else:
            ok(f"Ran {script_name}")
    except KeyError:
        pass  # Script doesn't exist — that's fine

def install_ppkg(ppkg_path, force=False):
    """Install a .ppkg file onto the system."""
    ppkg_path = Path(ppkg_path)

    if not ppkg_path.exists():
        err(f"File not found: {ppkg_path}")
        sys.exit(1)

    if not ppkg_path.suffix == PPKG_EXT:
        err(f"Not a .ppkg file: {ppkg_path}")
        sys.exit(1)

    manifest = read_manifest(ppkg_path)
    name     = manifest["name"]
    version  = manifest["version"]

    info(f"Installing {C.BOLD}{name}{C.RESET} v{version}")
    info(manifest.get("description", ""))

    # Check if already installed
    existing = db_get(name)
    if existing and not force:
        warn(f"{name} v{existing['version']} is already installed")
        ans = input("  Reinstall? [y/N] ").strip().lower()
        if ans != "y":
            info("Cancelled")
            return

    installed_files = []

    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(ppkg_path, "r:gz") as tar:

            # Run pre-install script
            run_script(tar, "pre-install.sh", tmpdir)

            # Extract files/ tree to system root
            for member in tar.getmembers():
                if not member.name.startswith("files/"):
                    continue

                # Strip "files/" prefix to get real path
                rel_path = member.name[len("files/"):]
                if not rel_path:
                    continue

                dest = INSTALL_ROOT / rel_path

                # Extract to temp first
                tar.extract(member, tmpdir)
                src = Path(tmpdir) / member.name

                # Create parent dirs
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Copy to destination
                if src.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    shutil.copy2(src, dest)
                    installed_files.append(str(dest))

                info(f"  → {dest}")

            # Run post-install script
            run_script(tar, "post-install.sh", tmpdir)

    # Record in database
    db_add(manifest, installed_files)
    log("install", name, f"v{version} OK")
    ok(f"Installed {name} v{version} successfully!")

# ── Removal ───────────────────────────────────────────────
def remove_ppkg(name):
    """Remove an installed package."""
    record = db_get(name)
    if not record:
        err(f"Package '{name}' is not installed")
        sys.exit(1)

    info(f"Removing {C.BOLD}{name}{C.RESET} v{record['version']}...")

    # Remove all installed files
    removed = 0
    for fpath in record.get("files", []):
        p = Path(fpath)
        if p.exists():
            p.unlink()
            removed += 1
        # Remove empty parent dirs
        try:
            p.parent.rmdir()
        except OSError:
            pass

    db_remove(name)
    log("remove", name, f"v{record['version']} OK")
    ok(f"Removed {name} ({removed} files deleted)")

# ── Search ────────────────────────────────────────────────
def search_packages(query):
    """Search available packages in repo cache."""
    repos = load_repos()
    found = False

    for repo_name, repo_url in repos.items():
        cache = CACHE_DIR / repo_name / "index.json"
        if not cache.exists():
            warn(f"No cache for {repo_name} — run: popm update")
            continue

        with open(cache) as f:
            index = json.load(f)

        for pkg in index.get("packages", []):
            if (query.lower() in pkg["name"].lower() or
                query.lower() in pkg.get("description", "").lower()):
                installed = db_get(pkg["name"])
                status = f"{C.GREEN}[installed]{C.RESET}" if installed else ""
                print(f"  {C.BOLD}{pkg['name']}{C.RESET} "
                      f"v{pkg['version']} — {pkg.get('description','')} {status}")
                found = True

    if not found:
        warn(f"No packages found matching '{query}'")

# ── Repo management ───────────────────────────────────────
def load_repos():
    """Load repo list from /etc/popm/repos.list"""
    repos = {"official": DEFAULT_REPO}  # Always include official

    if REPO_LIST.exists():
        with open(REPO_LIST) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    repos[parts[0]] = parts[1]

    return repos

def update_repos():
    """Fetch package index from all repos."""
    repos = load_repos()
    info("Updating package lists...")

    for repo_name, repo_url in repos.items():
        info(f"  Fetching {repo_name} ({repo_url})...")
        cache_dir = CACHE_DIR / repo_name
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            index_url = f"{repo_url}/index.json"
            urllib.request.urlretrieve(index_url, cache_dir / "index.json")
            ok(f"  {repo_name} updated")
        except Exception as e:
            warn(f"  Failed to fetch {repo_name}: {e}")

# ── Download and install from repo ────────────────────────
def install_from_repo(name):
    """Find and install a package by name from repos."""
    repos = load_repos()

    for repo_name, repo_url in repos.items():
        cache = CACHE_DIR / repo_name / "index.json"
        if not cache.exists():
            continue

        with open(cache) as f:
            index = json.load(f)

        for pkg in index.get("packages", []):
            if pkg["name"] == name:
                # Found it — download
                ppkg_url  = f"{repo_url}/{pkg['filename']}"
                ppkg_path = CACHE_DIR / repo_name / pkg["filename"]

                info(f"Downloading {name} v{pkg['version']} from {repo_name}...")

                def progress(count, block_size, total):
                    pct = min(int(count * block_size * 100 / total), 100)
                    bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
                    print(f"\r  [{bar}] {pct}%", end="", flush=True)

                urllib.request.urlretrieve(ppkg_url, ppkg_path, progress)
                print()

                install_ppkg(ppkg_path)
                return

    err(f"Package '{name}' not found in any repo")
    err("Try: popm update    to refresh package lists")
    sys.exit(1)

# ── List installed ────────────────────────────────────────
def list_installed():
    """List all installed packages."""
    db = db_load()
    if not db:
        info("No packages installed yet")
        return

    bold(f"\n  {'Package':<25} {'Version':<12} {'Installed':<20}")
    print(f"  {'─'*25} {'─'*12} {'─'*20}")
    for name, record in sorted(db.items()):
        installed = record['installed'][:10]
        print(f"  {C.BOLD}{name:<25}{C.RESET} "
              f"{record['version']:<12} {installed}")
    print(f"\n  {len(db)} package(s) installed\n")

# ── Package info ──────────────────────────────────────────
def show_info(name):
    """Show detailed info about a package."""
    record = db_get(name)
    if record:
        bold(f"\n  {record['name']} v{record['version']}")
        print(f"  Description : {record.get('description','')}")
        print(f"  Author      : {record.get('author','')}")
        print(f"  Installed   : {record['installed'][:19]}")
        print(f"  Files       : {len(record.get('files',[]))}")
    else:
        warn(f"'{name}' is not installed")

# ── .ppkg builder ─────────────────────────────────────────
def build_ppkg(spec_file):
    """
    Build a .ppkg from a spec directory.

    Spec directory structure:
        mypackage/
        ├── MANIFEST        ← JSON metadata
        ├── files/          ← files to install
        └── scripts/        ← optional hooks
    """
    spec_dir = Path(spec_file)
    if not spec_dir.is_dir():
        err(f"Spec directory not found: {spec_dir}")
        sys.exit(1)

    manifest_path = spec_dir / "MANIFEST"
    if not manifest_path.exists():
        err("No MANIFEST file in spec directory")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    name    = manifest["name"]
    version = manifest["version"]
    output  = Path(f"{name}-{version}{PPKG_EXT}")

    info(f"Building {name} v{version}...")

    with tarfile.open(output, "w:gz") as tar:
        tar.add(manifest_path, arcname="MANIFEST")

        files_dir = spec_dir / "files"
        if files_dir.exists():
            tar.add(files_dir, arcname="files")

        scripts_dir = spec_dir / "scripts"
        if scripts_dir.exists():
            tar.add(scripts_dir, arcname="scripts")

    # Calculate SHA256 and embed in output filename
    h = hashlib.sha256()
    with open(output, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)

    ok(f"Built: {output}")
    ok(f"SHA256: {h.hexdigest()}")
    info("Add the SHA256 to your MANIFEST before publishing")
    return output

# ── CLI ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="popm",
        description=f"PoloniumOS Package Manager v{VERSION}"
    )
    sub = parser.add_subparsers(dest="command")

    # install
    p_install = sub.add_parser("install", help="Install a package")
    p_install.add_argument("package", help="Package name or .ppkg file path")
    p_install.add_argument("--force", action="store_true", help="Force reinstall")

    # remove
    p_remove = sub.add_parser("remove", help="Remove a package")
    p_remove.add_argument("package", help="Package name")

    # search
    p_search = sub.add_parser("search", help="Search packages")
    p_search.add_argument("query", help="Search query")

    # update
    sub.add_parser("update", help="Update package lists")

    # list
    sub.add_parser("list", help="List installed packages")

    # info
    p_info = sub.add_parser("info", help="Show package info")
    p_info.add_argument("package", help="Package name")

    # build
    p_build = sub.add_parser("build", help="Build a .ppkg from spec dir")
    p_build.add_argument("specdir", help="Spec directory path")

    # verify
    p_verify = sub.add_parser("verify", help="Verify a .ppkg file")
    p_verify.add_argument("file", help=".ppkg file path")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # Commands that need root
    root_cmds = {"install", "remove", "update"}
    if args.command in root_cmds and os.geteuid() != 0:
        err(f"popm {args.command} requires root. Use: sudo popm {args.command}")
        sys.exit(1)

    if args.command == "install":
        if args.package.endswith(PPKG_EXT):
            install_ppkg(args.package, force=args.force)
        else:
            install_from_repo(args.package)

    elif args.command == "remove":
        remove_ppkg(args.package)

    elif args.command == "search":
        search_packages(args.query)

    elif args.command == "update":
        update_repos()

    elif args.command == "list":
        list_installed()

    elif args.command == "info":
        show_info(args.package)

    elif args.command == "build":
        build_ppkg(args.specdir)

    elif args.command == "verify":
        ok("Valid .ppkg") if verify_ppkg(args.file) else err("Invalid .ppkg")

if __name__ == "__main__":
    main()
