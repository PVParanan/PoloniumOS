# PoloniumOS

A Fedora 44 based Linux distribution with custom components.

## Current Status — v0.1.0 Released!

PoloniumOS 0.1.0 is installable and running on real hardware.

## Components
- **popm** — Package manager written in Rust with .ppkg format
- **PoloniumInit** — Custom init system (in development)
- **PoloniumSplash** — Custom boot splash (in development)
- **polonium.ks** — Fedora 44 based kickstart config

## Roadmap
- [x] Bootable ISO
- [x] Real hardware installation
- [x] Custom OS branding
- [x] popm package manager in Rust
- [x] .ppkg package format
- [ ] Install popm on system
- [ ] PoloniumInit
- [ ] PoloniumSplash
- [ ] .papp app bundles
- [ ] Custom GNOME theme
- [ ] PoloniumBoot (final release)

## Install
Download the ISO from releases and boot from USB.

## Build from source
See kickstart/polonium.ks for the build configuration.

## License
MIT
