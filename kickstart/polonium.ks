# PoloniumOS Kickstart - Fedora 44 based
url --mirrorlist="https://mirrors.fedoraproject.org/mirrorlist?repo=fedora-44&arch=x86_64"
repo --name="updates" --mirrorlist="https://mirrors.fedoraproject.org/mirrorlist?repo=updates-released-f44&arch=x86_64"

lang en_US.UTF-8
keyboard us
timezone UTC --utc
selinux --enforcing
firewall --enabled --service=ssh

bootloader --location=mbr
zerombr
clearpart --all --initlabel
part /boot/efi --fstype=efi  --size=512
part /boot     --fstype=ext4 --size=1024
part /         --fstype=ext4 --size=8192 --grow

rootpw --lock
user --name=polonium --groups=wheel --plaintext --password=polonium

%packages
@core
@standard
@hardware-support
@gnome-desktop
dracut-live
flatpak
grub2
grub2-efi-x64
-fedora-logos
-fedora-release-notes
-syslinux
-shim
-shim-x64
-shim-ia32
%end

%post
flatpak remote-add --if-not-exists flathub \
    https://flathub.org/repo/flathub.flatpakrepo
# Fix OS branding
tee /etc/os-release << 'OSEOF'
NAME="PoloniumOS"
VERSION="0.1.0"
RELEASE_TYPE=stable
ID=poloniumos
ID_LIKE=fedora
VERSION_ID=0.1.0
PRETTY_NAME="PoloniumOS 0.1.0"
ANSI_COLOR="0;96"
LOGO=poloniumos-logo-icon
DEFAULT_HOSTNAME="poloniumos"
HOME_URL="https://github.com/PVParanan/PoloniumOS"
SUPPORT_URL="https://github.com/PVParanan/PoloniumOS/issues"
BUG_REPORT_URL="https://github.com/PVParanan/PoloniumOS/issues"
VARIANT="Desktop"
VARIANT_ID=desktop
POLONIUMOS_VERSION="0.1.0"
OSEOF

# Fix hostname
echo "poloniumos" > /etc/hostname

# Fix GRUB
sed -i 's/Fedora Linux/PoloniumOS/g' /etc/default/grub

# Install fastfetch
dnf install -y fastfetch chafa

%end
