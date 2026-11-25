//! Local, explicitly selected Agent Skills. Imported resources are inert UTF-8 text.
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    env, fs,
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};
use tempfile::NamedTempFile;

const MAX_TEXT: usize = 64 * 1024;
const MAX_TOTAL: usize = 128 * 1024;
const MAX_STORED: usize = MAX_TOTAL * 6 + MAX_TEXT;

#[derive(Debug, Deserialize, Serialize)]
pub struct Manifest {
    pub name: String,
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compatibility: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub license: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Package {
    markdown: String,
    references: BTreeMap<String, String>,
}

pub struct Skill {
    pub manifest: Manifest,
    pub guidance: String,
    pub references: Vec<String>,
}

fn valid_name(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && !s.starts_with('-')
        && !s.ends_with('-')
        && !s.contains("--")
        && s.bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

fn read_text(path: &Path, limit: usize) -> Result<String, String> {
    if !fs::metadata(path).map_err(|e| e.to_string())?.is_file() {
        return Err("expected a regular text file".into());
    }
    let mut text = String::new();
    fs::File::open(path)
        .and_then(|f| f.take((limit + 1) as u64).read_to_string(&mut text))
        .map_err(|e| e.to_string())?;
    if text.len() > limit {
        return Err("skill file exceeds size limit".into());
    }
    Ok(text)
}

fn parse(markdown: &str) -> Result<(Manifest, &str), String> {
    let clean = markdown.strip_prefix('\u{feff}').unwrap_or(markdown);
    let mut lines = clean.split_inclusive('\n');
    let opening = lines.next().unwrap_or("");
    if opening.trim_end_matches(['\r', '\n']) != "---" {
        return Err("SKILL.md must start with YAML frontmatter".into());
    }
    let mut yaml = String::new();
    for line in lines {
        if line.trim_end_matches(['\r', '\n']) == "---" {
            let start = opening.len() + yaml.len() + line.len();
            let body = clean.get(start..).unwrap_or("").trim();
            let manifest: Manifest = serde_saphyr::from_str(&yaml)
                .map_err(|e| format!("invalid SKILL.md metadata: {e}"))?;
            if !valid_name(&manifest.name)
                || manifest.description.trim().is_empty()
                || manifest.description.chars().count() > 1024
                || manifest
                    .compatibility
                    .as_ref()
                    .is_some_and(|s| s.chars().count() > 500)
                || body.is_empty()
            {
                return Err("invalid skill name, description, compatibility or empty body".into());
            }
            return Ok((manifest, body));
        }
        yaml.push_str(line);
    }
    Err("missing end of SKILL.md frontmatter".into())
}

fn reference_name(s: &str) -> bool {
    let p = Path::new(s);
    s.starts_with("references/")
        && s.len() <= 512
        && !s.contains('\\')
        && s.split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
        && p.components().all(|v| matches!(v, Component::Normal(_)))
        && matches!(p.extension().and_then(|x| x.to_str()), Some("md" | "txt"))
}

fn activate(pkg: &Package, name: &str) -> Result<Skill, String> {
    let (manifest, body) = parse(&pkg.markdown)?;
    if manifest.name != name {
        return Err("skill name does not match its directory or registry entry".into());
    }
    if pkg.markdown.len() > MAX_TEXT
        || pkg.references.len() > 16
        || pkg
            .references
            .iter()
            .any(|(k, v)| !reference_name(k) || v.len() > MAX_TEXT)
        || pkg.markdown.len() + pkg.references.values().map(String::len).sum::<usize>() > MAX_TOTAL
    {
        return Err("invalid skill resource or package exceeds size limit".into());
    }
    let mut guidance = body.to_owned();
    for (key, val) in &pkg.references {
        guidance.push_str(&format!("\n\nReference {key}\n{val}"));
    }
    Ok(Skill {
        manifest,
        guidance,
        references: pkg.references.keys().cloned().collect(),
    })
}

pub fn directory() -> Result<PathBuf, String> {
    if let Some(explicit) = env::var_os("AFMCLI_SKILLS_DIR").filter(|p| !p.is_empty()) {
        return Ok(PathBuf::from(explicit));
    }
    let home = env::var_os("HOME")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .ok_or("set AFMCLI_SKILLS_DIR or HOME")?;
    Ok(if cfg!(target_os = "macos") {
        home.join("Library/Application Support/AFMCLI/skills")
    } else {
        env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".local/share"))
            .join("afmcli/skills")
    })
}

pub fn load(dir: &Path, name: &str) -> Result<Skill, String> {
    if !valid_name(name) {
        return Err("expected an imported skill name".into());
    }
    let package = serde_json::from_str(&read_text(&dir.join(format!("{name}.json")), MAX_STORED)?)
        .map_err(|e| format!("invalid stored skill: {e}"))?;
    activate(&package, name)
}

pub fn import(
    dir: &Path,
    src: &Path,
    references: &[String],
    replace: bool,
) -> Result<Skill, String> {
    let src = fs::canonicalize(src).map_err(|e| e.to_string())?;
    let root = if src.is_dir() {
        &src
    } else if src.file_name().and_then(|x| x.to_str()) == Some("SKILL.md") {
        src.parent()
            .ok_or("SKILL.md needs a named parent directory")?
    } else {
        return Err("expected a skill directory or its SKILL.md".into());
    };
    let name = root
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("invalid skill directory name")?;
    if !valid_name(name) {
        return Err("invalid skill directory name".into());
    }
    let inside = |p: &Path| -> Result<PathBuf, String> {
        let f = fs::canonicalize(root.join(p)).map_err(|e| e.to_string())?;
        if !f.starts_with(root) {
            return Err("skill resource points outside the selected directory".into());
        }
        Ok(f)
    };
    let mut package = Package {
        markdown: read_text(&inside(Path::new("SKILL.md"))?, MAX_TEXT)?,
        references: BTreeMap::new(),
    };
    if references.len() > 16 {
        return Err("select at most 16 text references".into());
    }
    let mut remaining = MAX_TOTAL - package.markdown.len();
    for key in references {
        if !reference_name(key) || package.references.contains_key(key) {
            return Err("expected distinct references/*.md or .txt paths".into());
        }
        let text = read_text(&inside(Path::new(key))?, MAX_TEXT.min(remaining))?;
        remaining -= text.len();
        package.references.insert(key.clone(), text);
    }
    let skill = activate(&package, name)?;
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let dest = dir.join(format!("{name}.json"));
    let data = serde_json::to_vec(&package).map_err(|e| e.to_string())?;
    let mut tmp = NamedTempFile::new_in(dir).map_err(|e| e.to_string())?;
    tmp.write_all(&data)
        .and_then(|_| tmp.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    if replace {
        tmp.persist(&dest).map_err(|e| e.to_string())?;
    } else if let Err(e) = tmp.persist_noclobber(&dest) {
        if e.error.kind() != std::io::ErrorKind::AlreadyExists {
            return Err(e.to_string());
        }
        let existing: Package = serde_json::from_str(&read_text(&dest, MAX_STORED)?)
            .map_err(|e| format!("invalid existing skill: {e}"))?;
        if existing != package {
            return Err(
                "a different skill with this name is installed; pass --replace to update it".into(),
            );
        }
    }
    Ok(skill)
}

pub fn list(dir: &Path) -> Result<Vec<Manifest>, String> {
    let entries = match fs::read_dir(dir) {
        Ok(v) => v,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
        Err(e) => return Err(e.to_string()),
    };
    let mut names = Vec::new();
    for entry in entries {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.extension().and_then(|x| x.to_str()) == Some("json") {
            let name = path
                .file_stem()
                .and_then(|x| x.to_str())
                .ok_or("invalid registry entry")?;
            names.push(load(dir, name)?.manifest);
        }
    }
    names.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(names)
}
