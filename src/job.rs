use crate::{contract, edit::Change};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Field {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub many: bool,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Label {
    pub name: String,
    #[serde(default)]
    pub description: String,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "task", rename_all = "snake_case", deny_unknown_fields)]
pub enum Job {
    Auto {
        instruction: String,
        #[serde(default)]
        source: String,
    },
    Ask {
        instruction: String,
        #[serde(default)]
        source: String,
    },
    Rewrite {
        instruction: String,
        source: String,
        #[serde(default)]
        keep_numbers: bool,
        #[serde(default)]
        require_change: bool,
        #[serde(default)]
        max_chars: Option<usize>,
    },
    Answer {
        question: String,
        source: String,
    },
    Extract {
        fields: Vec<Field>,
        source: String,
    },
    Classify {
        labels: Vec<Label>,
        source: String,
    },
    Summarize {
        source: String,
        #[serde(default)]
        focus: String,
        #[serde(default = "default_n")]
        sentences: usize,
    },
    Structure {
        schema: Value,
        instruction: String,
        source: String,
    },
    Edit {
        document: Value,
        changes: Vec<Change>,
        #[serde(default, deserialize_with = "crate::edit::deserialize_row")]
        row: Option<crate::edit::RowSelector>,
    },
    Solve {
        problem: Option<crate::solve::Problem>,
        source: Option<String>,
    },
}
fn default_n() -> usize {
    3
}
impl Job {
    pub fn name(&self) -> &'static str {
        match self {
            Self::Auto { .. } => "auto",
            Self::Ask { .. } => "ask",
            Self::Rewrite { .. } => "rewrite",
            Self::Answer { .. } => "answer",
            Self::Extract { .. } => "extract",
            Self::Classify { .. } => "classify",
            Self::Summarize { .. } => "summarize",
            Self::Structure { .. } => "structure",
            Self::Edit { .. } => "edit",
            Self::Solve { .. } => "solve",
        }
    }
    pub fn validate(&self, has_image: bool) -> Result<(), String> {
        if has_image
            && !matches!(
                self,
                Self::Auto { .. }
                    | Self::Ask { .. }
                    | Self::Structure { .. }
                    | Self::Classify { .. }
            )
        {
            return Err("images are accepted by ask, classify and structure".into());
        }
        let mut source = None;
        let mut instruction = None;
        match self {
            Self::Solve { source: s, problem } => {
                if s.is_some() == problem.is_some() {
                    return Err("provide one of source or problem".into());
                }
                source = s.as_ref();
            }
            Self::Auto { instruction: i, .. } | Self::Ask { instruction: i, .. } => {
                instruction = Some(i)
            }
            Self::Rewrite {
                source: s,
                instruction: i,
                max_chars,
                ..
            } => {
                source = Some(s);
                instruction = Some(i);
                if *max_chars == Some(0) {
                    return Err("max_chars must be positive".into());
                }
            }
            Self::Answer {
                source: s,
                question: q,
            } => {
                source = Some(s);
                instruction = Some(q);
            }
            Self::Classify { source: s, labels } => {
                source = Some(s);
                names(labels.iter().map(|e| e.name.as_str()), 2)?;
            }
            Self::Summarize {
                source: s,
                sentences,
                ..
            } => {
                source = Some(s);
                if !(1..=24).contains(sentences) {
                    return Err("sentences must be between 1 and 24".into());
                }
            }
            Self::Extract { source: s, fields } => {
                source = Some(s);
                names(fields.iter().map(|e| e.name.as_str()), 1)?;
            }
            Self::Structure {
                source: s,
                schema,
                instruction: i,
            } => {
                source = Some(s);
                instruction = Some(i);
                contract::check(schema)?;
            }
            Self::Edit { changes, .. } => {
                if changes.is_empty() {
                    return Err("edit requires at least one change".into());
                }
            }
        }
        if source.is_some_and(|s| s.trim().is_empty()) && !has_image {
            return Err("provide a nonempty source".into());
        }
        if instruction.is_some_and(|i| i.trim().is_empty()) {
            return Err("provide a nonempty instruction or question".into());
        }
        Ok(())
    }
}
fn names<'a>(values: impl Iterator<Item = &'a str>, min: usize) -> Result<(), String> {
    let mut names = BTreeSet::new();
    for s in values {
        if s.trim().is_empty() || s.trim() != s || !names.insert(s) {
            return Err("names must be distinct nonempty strings without padding".into());
        }
    }
    if !(min..=32).contains(&names.len()) {
        return Err(format!("supply between {min} and 32 names"));
    }
    Ok(())
}
