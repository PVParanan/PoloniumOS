use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use colored::Colorize;
use dirs::data_dir;
use flate2::read::GzDecoder;
use indicatif::{ProgressBar, ProgressStyle};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use tar::Archive;

// ── Constants ─────────────────────────────────────────────
const VERSION: &str = env!("CARGO_PKG_VERSION");
const POPM_DIR: &str = "/var/lib/popm";
const DB_FILE: &str = "/var/lib/popm/installed.json";
const CACHE_DIR: &str = "/var/lib/popm/cache";
const LOG_FILE: &str = "/var/lib/popm/popm.log";
const PPKG_EXT: &str = ".ppkg";

// ── CLI ───────────────────────────────────────────────────
#[derive(Parser)]
#[command(
    name = "popm",
    about = "PoloniumOS Package Manager",
    version = VERSION,
    long_about = None
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Install a package (.ppkg file or name from repo)
    Install {
        package: String,
        #[arg(long)]
        force: bool,
    },
    /// Remove an installed package
    Remove { package: String },
    /// Search available packages
    Search { query: String },
    /// Update package lists from repos
    Update,
    /// Upgrade all installed packages
    Upgrade,
    /// List installed packages
    List,
    /// Show package information
    Info { package: String },
    /// Verify a .ppkg file
    Verify { file: String },
    /// Build a .ppkg from a spec directory
    Build { specdir: String },
}

// ── Types ─────────────────────────────────────────────────
#[derive(Debug, Serialize, Deserialize, Clone)]
struct Manifest {
    name: String,
    version: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    author: String,
    #[serde(default)]
    license: String,
    #[serde(default)]
    arch: String,
    #[serde(default)]
    url: String,
    #[serde(default)]
    depends: Vec<String>,
    #[serde(default)]
    conflicts: Vec<String>,
    #[serde(default)]
    sha256: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct InstalledRecord {
    name: String,
    version: String,
    description: String,
    author: String,
    installed: String,
    files: Vec<String>,
}

type Database = HashMap<String, InstalledRecord>;

// ── Logging ───────────────────────────────────────────────
fn log_action(action: &str, package: &str, status: &str) {
    let ts = chrono_now();
    let line = format!("[{ts}] {action:<10} {package:<30} {status}\n");
    let _ = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(LOG_FILE)
        .map(|mut f| io::Write::write_all(&mut f, line.as_bytes()));
}

fn chrono_now() -> String {
    // Simple timestamp without chrono dependency
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| format!("{}", d.as_secs()))
        .unwrap_or_default()
}

// ── Output helpers ────────────────────────────────────────
fn ok(msg: &str)   { println!("{} {}", "[+]".green().bold(),  msg); }
fn info(msg: &str) { println!("{} {}", "[*]".cyan().bold(),   msg); }
fn warn(msg: &str) { println!("{} {}", "[!]".yellow().bold(), msg); }
fn err(msg: &str)  { eprintln!("{} {}", "[ERROR]".red().bold(), msg); }

// ── Database ──────────────────────────────────────────────
fn db_load() -> Result<Database> {
    let path = Path::new(DB_FILE);
    if !path.exists() {
        return Ok(HashMap::new());
    }
    let content = fs::read_to_string(path)?;
    Ok(serde_json::from_str(&content).unwrap_or_default())
}

fn db_save(db: &Database) -> Result<()> {
    fs::create_dir_all(POPM_DIR)?;
    let content = serde_json::to_string_pretty(db)?;
    fs::write(DB_FILE, content)?;
    Ok(())
}

// ── SHA256 ────────────────────────────────────────────────
fn sha256_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 { break; }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

// ── Manifest reader ───────────────────────────────────────
fn read_manifest(ppkg_path: &Path) -> Result<Manifest> {
    let file = fs::File::open(ppkg_path)
        .with_context(|| format!("Cannot open {}", ppkg_path.display()))?;
    let gz   = GzDecoder::new(file);
    let mut archive = Archive::new(gz);

    for entry in archive.entries()? {
        let mut entry = entry?;
        let path = entry.path()?.to_path_buf();
        if path.to_str() == Some("MANIFEST") {
            let mut content = String::new();
            entry.read_to_string(&mut content)?;
            let manifest: Manifest = serde_json::from_str(&content)
                .context("Invalid MANIFEST JSON")?;
            return Ok(manifest);
        }
    }
    anyhow::bail!("No MANIFEST found in {}", ppkg_path.display())
}

// ── Verify ────────────────────────────────────────────────
fn verify_ppkg(ppkg_path: &Path) -> Result<bool> {
    info(&format!("Verifying {}...", ppkg_path.display()));
    let manifest = read_manifest(ppkg_path)?;

    if manifest.sha256.is_empty() {
        warn("No SHA256 in manifest — skipping verification");
        return Ok(true);
    }

    let actual = sha256_file(ppkg_path)?;
    if actual != manifest.sha256 {
        err(&format!(
            "Checksum mismatch!\n  Expected: {}\n  Got:      {}",
            manifest.sha256, actual
        ));
        return Ok(false);
    }

    ok("Checksum verified");
    Ok(true)
}

// ── Install ───────────────────────────────────────────────
fn install_ppkg(ppkg_path: &Path, force: bool) -> Result<()> {
    if !ppkg_path.exists() {
        anyhow::bail!("File not found: {}", ppkg_path.display());
    }

    let manifest = read_manifest(ppkg_path)?;
    let name     = &manifest.name;
    let version  = &manifest.version;

    info(&format!(
        "Installing {} v{}",
        name.bold(),
        version
    ));

    if !manifest.description.is_empty() {
        info(&manifest.description);
    }

    // Check already installed
    let mut db = db_load()?;
    if let Some(existing) = db.get(name) {
        if !force {
            warn(&format!(
                "{} v{} is already installed",
                name, existing.version
            ));
            print!("  Reinstall? [y/N] ");
            io::Write::flush(&mut io::stdout())?;
            let mut ans = String::new();
            io::stdin().read_line(&mut ans)?;
            if ans.trim().to_lowercase() != "y" {
                info("Cancelled");
                return Ok(());
            }
        }
    }

    let tmp_dir = tempfile::tempdir()?;
    let mut installed_files: Vec<String> = Vec::new();

    // Run pre-install script
    run_script_from_ppkg(ppkg_path, "scripts/pre-install.sh", tmp_dir.path())?;

    // Extract files/
    {
        let file    = fs::File::open(ppkg_path)?;
        let gz      = GzDecoder::new(file);
        let mut archive = Archive::new(gz);

        let pb = ProgressBar::new_spinner();
        pb.set_style(
            ProgressStyle::default_spinner()
                .template("  {spinner:.cyan} {msg}")
                .unwrap()
        );

        for entry in archive.entries()? {
            let mut entry = entry?;
            let raw_path  = entry.path()?.to_path_buf();
            let raw_str   = raw_path.to_str().unwrap_or("");

            if !raw_str.starts_with("files/") {
                continue;
            }

            let rel = &raw_str["files/".len()..];
            if rel.is_empty() { continue; }

            let dest = Path::new("/").join(rel);
            pb.set_message(format!("→ {}", dest.display()));

            if entry.header().entry_type().is_dir() {
                fs::create_dir_all(&dest)?;
            } else {
                if let Some(parent) = dest.parent() {
                    fs::create_dir_all(parent)?;
                }
                entry.unpack(&dest)?;
                installed_files.push(dest.to_string_lossy().to_string());
            }
        }
        pb.finish_and_clear();
    }

    // Run post-install script
    run_script_from_ppkg(ppkg_path, "scripts/post-install.sh", tmp_dir.path())?;

    // Record in database
    let record = InstalledRecord {
        name:        name.clone(),
        version:     version.clone(),
        description: manifest.description.clone(),
        author:      manifest.author.clone(),
        installed:   chrono_now(),
        files:       installed_files,
    };
    db.insert(name.clone(), record);
    db_save(&db)?;

    log_action("install", name, &format!("v{version} OK"));
    ok(&format!("Installed {} v{} successfully!", name, version));
    Ok(())
}

// ── Script runner ─────────────────────────────────────────
fn run_script_from_ppkg(
    ppkg_path: &Path,
    script_name: &str,
    tmp_dir: &Path,
) -> Result<()> {
    let file = fs::File::open(ppkg_path)?;
    let gz   = GzDecoder::new(file);
    let mut archive = Archive::new(gz);

    for entry in archive.entries()? {
        let mut entry = entry?;
        let path = entry.path()?.to_path_buf();
        if path.to_str() == Some(script_name) {
            let script_path = tmp_dir.join(
                path.file_name().unwrap_or_default()
            );
            entry.unpack(&script_path)?;

            // Make executable
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(
                    &script_path,
                    fs::Permissions::from_mode(0o755),
                )?;
            }

            let output = Command::new(&script_path).output()?;
            if !output.status.success() {
                warn(&format!(
                    "{} exited with code {}",
                    script_name,
                    output.status.code().unwrap_or(-1)
                ));
            } else {
                ok(&format!("Ran {script_name}"));
            }
            return Ok(());
        }
    }
    Ok(()) // Script not found is fine
}

// ── Remove ────────────────────────────────────────────────
fn remove_package(name: &str) -> Result<()> {
    let mut db = db_load()?;

    let record = db.get(name)
        .with_context(|| format!("Package '{name}' is not installed"))?
        .clone();

    info(&format!("Removing {} v{}...", name.bold(), record.version));

    let mut removed = 0usize;
    for fpath in &record.files {
        let p = Path::new(fpath);
        if p.exists() {
            fs::remove_file(p)?;
            removed += 1;
        }
        // Try removing empty parent dirs
        if let Some(parent) = p.parent() {
            let _ = fs::remove_dir(parent);
        }
    }

    db.remove(name);
    db_save(&db)?;

    log_action("remove", name, &format!("v{} OK", record.version));
    ok(&format!("Removed {name} ({removed} files deleted)"));
    Ok(())
}

// ── List ──────────────────────────────────────────────────
fn list_installed() -> Result<()> {
    let db = db_load()?;
    if db.is_empty() {
        info("No packages installed yet");
        return Ok(());
    }

    println!(
        "\n  {:<25} {:<12} {:<20}",
        "Package".bold(),
        "Version".bold(),
        "Installed".bold()
    );
    println!("  {}", "─".repeat(57));

    let mut packages: Vec<_> = db.values().collect();
    packages.sort_by(|a, b| a.name.cmp(&b.name));

    for record in packages {
        println!(
            "  {:<25} {:<12} {:<20}",
            record.name.bold(),
            record.version,
            &record.installed[..record.installed.len().min(10)]
        );
    }
    println!("\n  {} package(s) installed\n", db.len());
    Ok(())
}

// ── Info ──────────────────────────────────────────────────
fn show_info(name: &str) -> Result<()> {
    let db = db_load()?;
    match db.get(name) {
        Some(r) => {
            println!("\n  {} v{}", r.name.bold(), r.version);
            println!("  Description : {}", r.description);
            println!("  Author      : {}", r.author);
            println!("  Installed   : {}", r.installed);
            println!("  Files       : {}", r.files.len());
            println!();
        }
        None => warn(&format!("'{name}' is not installed")),
    }
    Ok(())
}

// ── Build ─────────────────────────────────────────────────
fn build_ppkg(spec_dir: &str) -> Result<()> {
    let spec      = Path::new(spec_dir);
    let manifest_path = spec.join("MANIFEST");

    if !manifest_path.exists() {
        anyhow::bail!("No MANIFEST in {spec_dir}");
    }

    let content  = fs::read_to_string(&manifest_path)?;
    let manifest: Manifest = serde_json::from_str(&content)
        .context("Invalid MANIFEST JSON")?;

    let name    = &manifest.name;
    let version = &manifest.version;
    let arch    = if manifest.arch.is_empty() { "any" } else { &manifest.arch };
    let output  = PathBuf::from(format!("{name}-{version}-{arch}{PPKG_EXT}"));

    info(&format!("Building {name} v{version} ({arch})..."));

    {
        let outfile  = fs::File::create(&output)?;
        let gz       = flate2::write::GzEncoder::new(
            outfile,
            flate2::Compression::best()
        );
        let mut archive = tar::Builder::new(gz);

        // Add MANIFEST
        archive.append_path_with_name(&manifest_path, "MANIFEST")?;

        // Add files/
        let files_dir = spec.join("files");
        if files_dir.exists() {
            for entry in walkdir(&files_dir)? {
                let rel = entry.strip_prefix(&files_dir)?;
                let arcname = format!("files/{}", rel.display());
                if entry.is_dir() {
                    let mut header = tar::Header::new_gnu();
                    header.set_mode(0o755);
                    header.set_size(0);
                    header.set_entry_type(tar::EntryType::Directory);
                    header.set_cksum();
                    archive.append_data(
                        &mut header,
                        &arcname,
                        io::empty()
                    )?;
                } else {
                    println!("    + {arcname}");
                    archive.append_path_with_name(&entry, &arcname)?;
                }
            }
        }

        // Add scripts/
        let scripts_dir = spec.join("scripts");
        if scripts_dir.exists() {
            for entry in fs::read_dir(&scripts_dir)? {
                let entry = entry?;
                let fname = entry.file_name();
                let arcname = format!("scripts/{}", fname.to_string_lossy());
                archive.append_path_with_name(entry.path(), &arcname)?;
            }
        }

        archive.finish()?;
    }

    // Calculate SHA256
    let sha = sha256_file(&output)?;
    ok(&format!("Built: {}", output.display()));
    ok(&format!("SHA256: {sha}"));

    Ok(())
}

// ── Walkdir helper ────────────────────────────────────────
fn walkdir(dir: &Path) -> Result<Vec<PathBuf>> {
    let mut result = Vec::new();
    fn recurse(dir: &Path, result: &mut Vec<PathBuf>) -> Result<()> {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let path  = entry.path();
            result.push(path.clone());
            if path.is_dir() {
                recurse(&path, result)?;
            }
        }
        Ok(())
    }
    recurse(dir, &mut result)?;
    result.sort();
    Ok(result)
}

// ── Main ──────────────────────────────────────────────────
fn main() {
    let cli = Cli::parse();

    // Commands that need root
    let needs_root = matches!(
        &cli.command,
        Commands::Install { .. } | Commands::Remove { .. } | Commands::Update
    );

    if needs_root {
        #[cfg(unix)]
        if unsafe { libc::geteuid() } != 0 {
            err("This command requires root. Use: sudo popm <command>");
            std::process::exit(1);
        }
    }

    let result = match &cli.command {
        Commands::Install { package, force } => {
            let path = Path::new(package);
            if package.ends_with(PPKG_EXT) {
                install_ppkg(path, *force)
            } else {
                err("Repo installs coming in v0.2.0 — use a .ppkg file for now");
                Ok(())
            }
        }
        Commands::Remove  { package } => remove_package(package),
        Commands::List                => list_installed(),
        Commands::Info    { package } => show_info(package),
        Commands::Verify  { file    } => {
            verify_ppkg(Path::new(file)).map(|_| ())
        }
        Commands::Build   { specdir } => build_ppkg(specdir),
        Commands::Search  { query   } => {
            warn(&format!("Search for '{query}' — repo support coming in v0.2.0"));
            Ok(())
        }
        Commands::Update  => {
            warn("Repo update coming in v0.2.0");
            Ok(())
        }
        Commands::Upgrade => {
            warn("Upgrade coming in v0.2.0");
            Ok(())
        }
    };

    if let Err(e) = result {
        err(&format!("{e:#}"));
        std::process::exit(1);
    }
}
