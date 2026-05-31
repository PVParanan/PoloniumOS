# PoloniumOS

A Fedora 44 based Linux distribution with custom components.

## Components
- **popm** — Package manager written in Rust with .ppkg format (v0.1.0)
- **PoloniumInit** — Lightweight PID 1 init system replacing systemd (C) 
- **PoloniumSplash** — Framebuffer boot splash replacing Plymouth (C)
- **polonium.ks** — Fedora 44 based kickstart config

## Bootloader
Currently using GRUB. Custom bootloader (PoloniumBoot) planned for final release.

## Roadmap
- [x] Custom package manager (popm) in Rust
- [x] .ppkg package format
- [ ] ISO build
- [ ] PoloniumInit (custom init system)
- [ ] PoloniumSplash (custom boot splash)
- [ ] .papp app bundle format
- [ ] .ppkg double-click installer
- [ ] Custom bootloader (final release)

## Status
Under active development — v0.1.0

## License
MIT
