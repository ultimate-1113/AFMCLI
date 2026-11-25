//! Adapter for the public on-device macOS CLI. Mixed image/text requests require --text.
use crate::json;
use crate::skills::Skill;
use regex::Regex;
use serde_json::Value;
use std::borrow::Cow;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::LazyLock;
use std::thread;
use std::time::Duration;
use wait_timeout::ChildExt;

pub const ADVANCED: &str = "AFM 3 Core Advanced";
const FM: &str = "/usr/bin/fm";
static ANSI: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;]*[A-Za-z]").unwrap());

#[derive(Default, Debug, Clone, PartialEq)]
pub struct Request {
    pub instructions: String,
    pub prompt: String,
    pub schema: Option<Value>,
}
pub trait Model {
    fn ready(&mut self) -> Result<(), String>;
    fn count(&mut self, r: &Request) -> Result<usize, String>;
    fn generate(&mut self, r: &Request) -> Result<String, String>;
}

pub struct Apple {
    pub timeout: Duration,
    pub image: Option<PathBuf>,
    ready: bool,
    skill: Option<Skill>,
}
impl Apple {
    pub fn new(seconds: u64, image: Option<PathBuf>) -> Self {
        Self {
            timeout: Duration::from_secs(seconds),
            image,
            ready: false,
            skill: None,
        }
    }
    pub fn use_skill(&mut self, skill: Skill) {
        self.skill = Some(skill);
    }
    fn prompt<'a>(&self, original: &'a str) -> Cow<'a, str> {
        match &self.skill {
            None => Cow::Borrowed(original),
            Some(skill) => Cow::Owned(format!(
                "Use the selected procedure when applicable. Follow the task and its output contract.\n{}\n\n{}",
                serde_json::json!({"procedure":skill.manifest.name,"guidance":skill.guidance}),
                original
            )),
        }
    }
    fn call(&self, args: &[&str], input: &str) -> Result<Vec<u8>, String> {
        self.call_at(Path::new(FM), args, input)
    }
    fn call_at(&self, executable: &Path, args: &[&str], input: &str) -> Result<Vec<u8>, String> {
        let mut child = Command::new(executable)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| {
                format!("cannot start Apple's fm ({e}); run afmcli doctor on a supported Mac")
            })?;
        let mut writer = child.stdin.take().unwrap();
        let bytes = input.as_bytes();
        let mut out = child.stdout.take().unwrap();
        let mut err = child.stderr.take().unwrap();
        thread::scope(|s| {
            let w = s.spawn(move || writer.write_all(bytes));
            let out_task = s.spawn(move || {
                let mut b = Vec::new();
                out.read_to_end(&mut b).map(|_| b)
            });
            let err_task = s.spawn(move || {
                let mut b = Vec::new();
                err.read_to_end(&mut b).map(|_| b)
            });
            let status = match child.wait_timeout(self.timeout) {
                Ok(Some(status)) => status,
                result => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(if result.is_err() {
                        "fm wait failed"
                    } else {
                        "fm timed out"
                    }
                    .into());
                }
            };
            let bytes = out_task
                .join()
                .map_err(|_| "stdout thread failed")?
                .map_err(|e| e.to_string())?;
            let stderr = err_task
                .join()
                .map_err(|_| "stderr thread failed")?
                .map_err(|e| e.to_string())?;
            let written = w.join().map_err(|_| "stdin thread failed")?;
            if args == ["models"] {
                // fm can exit 1 for an unavailable PCC even when system is available.
                let mut listing = bytes.clone();
                listing.push(b'\n');
                listing.extend_from_slice(&stderr);
                if has_advanced(&String::from_utf8_lossy(&listing)) {
                    return Ok(listing);
                }
            }
            if !status.success() {
                let error = String::from_utf8_lossy(&stderr).to_lowercase()
                    + &String::from_utf8_lossy(&bytes).to_lowercase();
                let kind = if error.contains("license") || error.contains("legal notice") {
                    "fm requires the local user's license agreement"
                } else if error.contains("guardrail") || error.contains("refus") {
                    "fm refused the request"
                } else if error.contains("context window")
                    || error.contains("context size exceeded")
                {
                    "fm could not fit the request in its context window"
                } else {
                    "fm returned an error; inspect the installed fm help and system model availability"
                };
                return Err(format!("{kind} (exit {status})"));
            }
            written.map_err(|e| e.to_string())?;
            Ok(bytes)
        })
    }
    fn image_arg(&self) -> Result<Option<&str>, String> {
        self.image
            .as_ref()
            .map(|p| {
                p.to_str()
                    .ok_or_else(|| "image path must be valid UTF-8".to_string())
            })
            .transpose()
    }
}

pub fn has_advanced(list: &str) -> bool {
    ANSI.replace_all(list, "").lines().any(|line| {
        let l = line.trim();
        l.strip_prefix('✓')
            .or_else(|| l.strip_prefix('✔'))
            .is_some_and(|v| {
                v.split_whitespace()
                    .eq(["system", "(AFM", "3", "Core", "Advanced)"])
            })
    })
}

impl Model for Apple {
    fn ready(&mut self) -> Result<(), String> {
        if self.ready {
            return Ok(());
        }
        let list = self.call(&["models"], "")?;
        if !has_advanced(&String::from_utf8_lossy(&list)) {
            return Err("fm models must list an available system (AFM 3 Core Advanced)".into());
        }
        self.ready = true;
        Ok(())
    }
    fn count(&mut self, r: &Request) -> Result<usize, String> {
        self.ready()?;
        let mut a = vec!["count-tokens", "--quiet", "--instructions", &r.instructions];
        let prompt = self.prompt(&r.prompt);
        let text = match &r.schema {
            Some(s) => format!("{prompt}\n{s}"),
            None => prompt.into_owned(),
        };
        let input = if let Some(img) = self.image_arg()? {
            a.extend(["--image", img, "--text", &text]);
            ""
        } else {
            &text
        };
        let output = self.call(&a, input)?;
        String::from_utf8_lossy(&output)
            .trim()
            .parse()
            .map_err(|_| "fm returned an unreadable token count".into())
    }
    fn generate(&mut self, r: &Request) -> Result<String, String> {
        self.ready()?;
        let dir = private_dir()?;
        let transcript = dir.path().join("response.json");
        let mut a = vec![
            "respond",
            "--model",
            "system",
            "--greedy",
            "--no-stream",
            "--instructions",
            &r.instructions,
            "--save-transcript",
            as_path(&transcript)?,
        ];
        let schema_path = dir.path().join("schema.json");
        if let Some(s) = &r.schema {
            std::fs::write(&schema_path, s.to_string()).map_err(|e| e.to_string())?;
            a.extend(["--schema", as_path(&schema_path)?]);
        }
        let prompt = self.prompt(&r.prompt);
        let input = if let Some(img) = self.image_arg()? {
            a.extend(["--image", img, "--text", &prompt]);
            ""
        } else {
            &prompt
        };
        self.call(&a, input)?;
        let t = json::parse(
            &std::fs::read_to_string(transcript).map_err(|_| "fm did not record a transcript")?,
        )?;
        confirm_input(&t, &prompt, self.image.is_some())?;
        decode(&t)
    }
}
fn confirm_input(t: &Value, prompt: &str, image: bool) -> Result<(), String> {
    let user = t
        .pointer("/transcript/transcript/entries")
        .and_then(Value::as_array)
        .and_then(|a| a.iter().rev().find(|e| e["role"] == "user"))
        .and_then(|e| e["contents"].as_array())
        .ok_or("missing user input in transcript")?;
    if !user
        .iter()
        .any(|part| part["type"] == "text" && part["text"].as_str() == Some(prompt))
        || (image && !user.iter().any(|part| part["type"] == "attachment"))
    {
        return Err("fm transcript does not contain the expected text and attachments".into());
    }
    Ok(())
}
fn as_path(p: &Path) -> Result<&str, String> {
    p.to_str()
        .ok_or_else(|| "temporary path must be UTF-8".into())
}
fn private_dir() -> Result<tempfile::TempDir, String> {
    let mut builder = tempfile::Builder::new();
    builder.prefix("afmcli-");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        builder.permissions(std::fs::Permissions::from_mode(0o700));
    }
    builder.tempdir().map_err(|e| e.to_string())
}
pub fn decode(t: &Value) -> Result<String, String> {
    if t["modelName"] != "system" {
        return Err("transcript does not confirm the on-device route".into());
    }
    let last = t
        .pointer("/transcript/transcript/entries")
        .and_then(Value::as_array)
        .and_then(|a| a.last())
        .ok_or("transcript has no response")?;
    if !matches!(last["role"].as_str(), Some("response" | "assistant"))
        || last["finishReason"]
            .as_str()
            .is_some_and(|s| s != "contentCompleted")
    {
        return Err("fm did not complete the response".into());
    }
    let mut text = String::new();
    for part in last["contents"]
        .as_array()
        .ok_or("missing response contents")?
    {
        match part["type"].as_str() {
            Some("text") => text.push_str(part["text"].as_str().ok_or("invalid response text")?),
            Some("structure") => text.push_str(
                &part
                    .pointer("/structure/content")
                    .ok_or("invalid structured response")?
                    .to_string(),
            ),
            _ => return Err("unsupported response content".into()),
        }
    }
    if text.trim().is_empty() {
        return Err("fm returned an empty response".into());
    }
    Ok(text)
}

#[cfg(all(test, unix))]
mod process_tests {
    use super::*;
    #[test]
    fn terminates_and_reaps_a_timed_out_process() {
        let backend = Apple {
            timeout: Duration::from_millis(40),
            ..Apple::new(1, None)
        };
        let result = backend.call_at(Path::new("/bin/sleep"), &["3"], "");
        assert_eq!(result.unwrap_err(), "fm timed out");
    }
    #[test]
    fn preserves_input_as_data_and_captures_nonzero_exit() {
        let backend = Apple::new(2, None);
        let bytes = backend
            .call_at(Path::new("/bin/cat"), &[], "\u{2603}\n--help")
            .unwrap();
        assert_eq!(bytes, "\u{2603}\n--help".as_bytes());
        assert!(
            backend
                .call_at(Path::new("/usr/bin/false"), &[], "")
                .is_err()
        );
    }
}
