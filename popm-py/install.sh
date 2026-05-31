#!/bin/bash
# Install popm and ppkg-build onto PoloniumOS
# Run as root: sudo bash install.sh

set -e

INSTALL_BIN="/usr/local/bin"
INSTALL_LIB="/usr/lib/popm"
ETC_DIR="/etc/popm"
VAR_DIR="/var/lib/popm"

echo "[*] Installing popm — PoloniumOS Package Manager"

# Check Python3
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python3 is required"
    exit 1
fi

# Create directories
mkdir -p "$ETC_DIR"
mkdir -p "$VAR_DIR/cache"
mkdir -p "$VAR_DIR/repos"
mkdir -p "$INSTALL_LIB"

# Install main scripts
cp src/popm.py      "$INSTALL_LIB/popm.py"
cp tools/ppkg-build.py "$INSTALL_LIB/ppkg-build.py"
chmod 644 "$INSTALL_LIB/"*.py

# Create wrappers in /usr/local/bin
cat > "$INSTALL_BIN/popm" << 'EOF'
#!/bin/sh
exec python3 /usr/lib/popm/popm.py "$@"
EOF

cat > "$INSTALL_BIN/ppkg-build" << 'EOF'
#!/bin/sh
exec python3 /usr/lib/popm/ppkg-build.py "$@"
EOF

chmod 755 "$INSTALL_BIN/popm"
chmod 755 "$INSTALL_BIN/ppkg-build"

# Create default repos.list
if [ ! -f "$ETC_DIR/repos.list" ]; then
    cat > "$ETC_DIR/repos.list" << 'REPOEOF'
# PoloniumOS Package Repositories
# Format: name  url
#
# Official PoloniumOS repo (uncomment when live)
# official  https://repo.poloniumos.org/packages
#
# Community repo
# community https://community.poloniumos.org/packages
REPOEOF
fi

# Create empty database
if [ ! -f "$VAR_DIR/installed.json" ]; then
    echo "{}" > "$VAR_DIR/installed.json"
fi

echo "[+] popm installed successfully!"
echo ""
echo "  Commands:"
echo "    popm install <package>     install a .ppkg or package by name"
echo "    popm remove  <package>     remove a package"
echo "    popm search  <query>       search available packages"
echo "    popm update                update package lists"
echo "    popm list                  list installed packages"
echo "    popm build   <specdir>     build a .ppkg"
echo ""
echo "  Package builder:"
echo "    ppkg-build init  myapp     scaffold a new package"
echo "    ppkg-build build myapp/    build myapp.ppkg"
echo "    ppkg-build info  myapp.ppkg show package info"
echo ""
