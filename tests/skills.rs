use serde_json::Value;
use std::{
    fs,
    path::Path,
    process::{Command, Output},
};
use tempfile::tempdir;

fn cli(registry: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_afmcli"))
        .env("AFMCLI_SKILLS_DIR", registry)
        .args(args)
        .output()
        .unwrap()
}

fn text(body: &str) -> String {
    format!(
        "\u{feff}---\r\nname: test-skill\r\ndescription: >\r\n  Look up a\r\n  short definition.\r\nmetadata:\r\n  author: example\r\n---\r\n{body}\r\n"
    )
}

#[test]
fn explicit_import_round_trip_and_replacement_through_cli() {
    let tmp = tempdir().unwrap();
    let reg = tmp.path().join("registry");
    let skill = tmp.path().join("test-skill");
    fs::create_dir_all(skill.join("references")).unwrap();
    fs::create_dir_all(skill.join("scripts")).unwrap();
    fs::write(skill.join("SKILL.md"), text("Read the glossary.")).unwrap();
    fs::write(skill.join("references/glossary.txt"), "A short example.").unwrap();
    fs::write(skill.join("references/unused.txt"), "Ignored note.").unwrap();
    fs::write(skill.join("scripts/example"), "unused helper").unwrap();
    let file = skill.join("SKILL.md");
    let path = file.to_str().unwrap();
    let args = [
        "skills",
        "import",
        path,
        "--reference",
        "references/glossary.txt",
    ];
    assert!(cli(&reg, &["skills", "list"]).status.success());
    assert!(!reg.exists());
    let (a, b) = std::thread::scope(|s| {
        let first = s.spawn(|| cli(&reg, &args));
        let second = s.spawn(|| cli(&reg, &args));
        (first.join().unwrap(), second.join().unwrap())
    });
    assert!(a.status.success() && b.status.success());
    let store = reg.join("test-skill.json");
    let package: Value = serde_json::from_slice(&fs::read(&store).unwrap()).unwrap();
    assert_eq!(
        package["references"],
        serde_json::json!({"references/glossary.txt":"A short example."})
    );
    let list = cli(&reg, &["skills", "list"]);
    assert!(
        String::from_utf8(list.stdout)
            .unwrap()
            .contains("Look up a short definition.")
    );
    fs::write(&file, text("New procedure.")).unwrap();
    assert!(!cli(&reg, &args).status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&fs::read(&store).unwrap()).unwrap(),
        package
    );
    assert!(
        cli(
            &reg,
            &["skills", "import", skill.to_str().unwrap(), "--replace"]
        )
        .status
        .success()
    );
    let updated = fs::read_to_string(store).unwrap();
    assert!(updated.contains("New procedure.") && !updated.contains("glossary.txt"));
}

#[test]
fn invalid_manifests_and_references_do_not_create_entries() {
    let tmp = tempdir().unwrap();
    let reg = tmp.path().join("registry");
    let skill = tmp.path().join("test-skill");
    fs::create_dir_all(skill.join("references")).unwrap();
    let path = skill.to_str().unwrap();
    for body in [
        "missing frontmatter",
        "---\nname: other\ndescription: test\n---\nRead.",
        "---\nname: test-skill\ndescription: ''\n---\nRead.",
    ] {
        fs::write(skill.join("SKILL.md"), body).unwrap();
        assert!(!cli(&reg, &["skills", "import", path]).status.success());
    }
    fs::write(skill.join("SKILL.md"), text("Short note.")).unwrap();
    fs::write(skill.join("references/large.md"), "z".repeat(65_537)).unwrap();
    for reference in [
        "../outside.txt",
        "references/large.md",
        "references/absent.md",
        "scripts/helper.sh",
    ] {
        assert!(
            !cli(&reg, &["skills", "import", path, "--reference", reference])
                .status
                .success()
        );
    }
    #[cfg(unix)]
    {
        fs::write(tmp.path().join("other.txt"), "Sibling note").unwrap();
        std::os::unix::fs::symlink(
            tmp.path().join("other.txt"),
            skill.join("references/linked.txt"),
        )
        .unwrap();
        assert!(
            !cli(
                &reg,
                &[
                    "skills",
                    "import",
                    path,
                    "--reference",
                    "references/linked.txt"
                ]
            )
            .status
            .success()
        );
    }
    assert!(!reg.exists());
}
