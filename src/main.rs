use afmcli::{
    engine::{Engine, Policy, Record},
    fm::{ADVANCED, Apple, Model},
    job::{Field, Job, Label},
    json,
};
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use std::{
    fs::File,
    io::{self, BufRead, BufReader, IsTerminal, Read, Write},
    path::{Path, PathBuf},
    process::ExitCode,
};

#[derive(Parser)]
#[command(version,about="On-device task harness for AFM 3 Core Advanced",long_about=None)]
struct Args {
    /// Read source or a JSON job from this UTF-8 file (default: stdin).
    #[arg(short = 'i', long, global = true)]
    input: Option<PathBuf>,
    /// Pass a local image to ask, classify or structure.
    #[arg(long, global = true)]
    image: Option<PathBuf>,
    /// Return the model's unguided, unvalidated first answer, for comparison.
    #[arg(long, global = true)]
    direct: bool,
    /// Include raw model answers and validation messages in the JSON output.
    #[arg(long, global = true)]
    trace: bool,
    /// Print just the text for a successful ask or rewrite.
    #[arg(long, global = true)]
    text: bool,
    /// Input token allowance per generation, including instructions and schema.
    #[arg(long,global=true,default_value_t=6200,value_parser=clap::value_parser!(u32).range(256..=6656))]
    input_budget: u32,
    /// Limit generations, including validation retries, for each job.
    #[arg(long,global=true,default_value_t=24,value_parser=clap::value_parser!(u32).range(1..=256))]
    max_calls: u32,
    /// Retry a locally detected output violation at most once.
    #[arg(long,global=true,default_value_t=1,value_parser=clap::value_parser!(u32).range(0..=1))]
    repairs: u32,
    /// Timeout in seconds for each fm process.
    #[arg(long,global=true,default_value_t=90,value_parser=clap::value_parser!(u64).range(1..=600))]
    timeout: u64,
    #[command(subcommand)]
    command: Action,
}
#[derive(Subcommand)]
enum Action {
    /// Check the installed system model.
    Doctor,
    /// Free-form instructions, optionally accompanied by source text or an image.
    Ask { instruction: String },
    /// Rewrite or translate source text under optional numeric and length constraints.
    Rewrite {
        instruction: String,
        #[arg(long)]
        keep_numbers: bool,
        #[arg(long)]
        max_chars: Option<usize>,
    },
    /// Answer with a verbatim span and its UTF-8 byte offsets.
    Answer { question: String },
    /// Extract caller-defined fields and link their values to source spans.
    Extract {
        #[arg(long, value_name = "NAME[=DESCRIPTION]")]
        field: Vec<String>,
        #[arg(long, value_name = "NAME[=DESCRIPTION]")]
        many: Vec<String>,
    },
    /// Choose one of your labels or abstain.
    Classify {
        #[arg(long, value_name = "NAME[=DESCRIPTION]", required = true)]
        label: Vec<String>,
    },
    /// Produce an extractive summary, optionally focused on a question.
    Summarize {
        #[arg(long, default_value = "")]
        focus: String,
        #[arg(short = 'n', long, default_value_t = 3)]
        sentences: usize,
    },
    /// Generate an object conforming to a local, closed JSON Schema.
    Structure {
        schema: PathBuf,
        instruction: String,
    },
    /// Preview an atomic, conditional edit. Read {document,changes} from stdin or --input.
    Edit,
    /// Read one JSON task object, optionally with a string id.
    Run,
    /// Read JSON Lines, process jobs sequentially and emit one record per input line.
    Batch,
}
const LIMIT: usize = 8 * 1024 * 1024;
fn reader(path: Option<&Path>) -> Result<Box<dyn BufRead>, String> {
    if let Some(p) = path {
        Ok(Box::new(BufReader::new(
            File::open(p).map_err(|e| e.to_string())?,
        )))
    } else {
        Ok(Box::new(BufReader::new(io::stdin())))
    }
}
fn read(path: Option<&Path>) -> Result<String, String> {
    if path.is_none() && io::stdin().is_terminal() {
        return Ok(String::new());
    }
    let mut s = String::new();
    reader(path)?
        .take((LIMIT + 1) as u64)
        .read_to_string(&mut s)
        .map_err(|e| e.to_string())?;
    if s.len() > LIMIT {
        return Err("input exceeds the 8 MiB limit".into());
    }
    Ok(s)
}
fn pair(text: String) -> (String, String) {
    match text.split_once('=') {
        Some((a, b)) => (a.into(), b.into()),
        None => (text, String::new()),
    }
}
fn unpack(mut data: Value) -> Result<(Option<String>, Job), String> {
    let o = data.as_object_mut().ok_or("job must be a JSON object")?;
    let id = o
        .remove("id")
        .map(|v| v.as_str().map(str::to_owned).ok_or("id must be a string"))
        .transpose()?;
    Ok((id, serde_json::from_value(data).map_err(|e| e.to_string())?))
}
fn one(command: Action, s: String) -> Result<(Option<String>, Job), String> {
    let job = match command {
        Action::Ask { instruction } => Job::Ask {
            instruction,
            source: s,
        },
        Action::Rewrite {
            instruction,
            keep_numbers,
            max_chars,
        } => Job::Rewrite {
            instruction,
            source: s,
            keep_numbers,
            max_chars,
        },
        Action::Answer { question } => Job::Answer {
            question,
            source: s,
        },
        Action::Extract { field, many } => Job::Extract {
            source: s,
            fields: field
                .into_iter()
                .map(|s| (false, s))
                .chain(many.into_iter().map(|s| (true, s)))
                .map(|(many, x)| {
                    let (name, description) = pair(x);
                    Field {
                        name,
                        description,
                        many,
                    }
                })
                .collect(),
        },
        Action::Classify { label } => Job::Classify {
            source: s,
            labels: label
                .into_iter()
                .map(|x| {
                    let (name, description) = pair(x);
                    Label { name, description }
                })
                .collect(),
        },
        Action::Summarize { focus, sentences } => Job::Summarize {
            focus,
            sentences,
            source: s,
        },
        Action::Structure {
            schema,
            instruction,
        } => Job::Structure {
            schema: json::parse(&read(Some(&schema))?)?,
            instruction,
            source: s,
        },
        Action::Edit => {
            let mut d = json::parse(&s)?;
            let obj = d.as_object_mut().ok_or("edit expects an object")?;
            if obj.contains_key("task") {
                return Err("for a task object use run".into());
            }
            obj.insert("task".into(), "edit".into());
            return unpack(d);
        }
        Action::Run => return unpack(json::parse(&s)?),
        _ => unreachable!(),
    };
    Ok((None, job))
}
fn emit(out: &mut impl Write, value: &Value) -> Result<(), String> {
    serde_json::to_writer(&mut *out, value).map_err(|e| e.to_string())?;
    writeln!(out)
        .and_then(|_| out.flush())
        .map_err(|e| e.to_string())
}
fn output(out: &mut impl Write, rec: Record, id: Option<String>, text: bool) -> Result<u8, String> {
    let code = match rec.status.as_str() {
        "error" => 2,
        "held" => 3,
        _ => 0,
    };
    if text {
        if code == 0 {
            writeln!(
                out,
                "{}",
                rec.result
                    .as_str()
                    .ok_or("text output requires a string result")?
            )
            .map_err(|e| e.to_string())?;
        } else {
            eprintln!("{}: {}", rec.status, rec.notes.join(", "));
        }
    } else {
        let mut v = serde_json::to_value(rec).map_err(|e| e.to_string())?;
        if let Some(id) = id {
            v["id"] = Value::String(id);
        }
        emit(out, &v)?;
    }
    Ok(code)
}
fn batch(
    input: Option<&Path>,
    model: &mut Apple,
    policy: Policy,
    out: &mut impl Write,
) -> Result<u8, String> {
    let mut rd = reader(input)?;
    let mut line = Vec::new();
    let mut index = 0;
    let mut errors = false;
    let mut held = false;
    loop {
        line.clear();
        let n = Read::take(&mut rd, (LIMIT + 1) as u64)
            .read_until(b'\n', &mut line)
            .map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        index += 1;
        if n > LIMIT {
            return Err(format!("batch line {index} exceeds the input limit"));
        }
        let job = std::str::from_utf8(&line)
            .map_err(|e| e.to_string())
            .and_then(json::parse)
            .and_then(unpack);
        match job {
            Ok((id, job)) => {
                let rec = Engine::new(model, policy).execute(&job);
                let c = output(out, rec, id, false)?;
                errors |= c == 2;
                held |= c == 3;
            }
            Err(reason) => {
                errors = true;
                emit(
                    out,
                    &json!({"status":"error","line":index,"notes":[reason]}),
                )?;
            }
        }
    }
    Ok(if errors {
        2
    } else if held {
        3
    } else {
        0
    })
}
fn run() -> Result<u8, String> {
    let args = Args::parse();
    let mut out = io::stdout().lock();
    if args.text && !matches!(args.command, Action::Ask { .. } | Action::Rewrite { .. }) {
        return Err("--text is available for ask and rewrite".into());
    }
    if let Some(p) = &args.image {
        if !p.is_file() {
            return Err("image must be a readable local file".into());
        }
    }
    let policy = Policy {
        input_budget: args.input_budget as usize,
        max_calls: args.max_calls as usize,
        repairs: args.repairs as usize,
        direct: args.direct,
        trace: args.trace,
        has_image: args.image.is_some(),
    };
    let mut model = Apple::new(args.timeout, args.image);
    if matches!(args.command, Action::Doctor) {
        let result = model.ready();
        emit(
            &mut out,
            &json!({"available":result.is_ok(),"target":ADVANCED,"route":"system","detail":result.as_ref().err()}),
        )?;
        return Ok(if result.is_ok() { 0 } else { 2 });
    }
    if matches!(args.command, Action::Batch) {
        return batch(args.input.as_deref(), &mut model, policy, &mut out);
    }
    let (id, job) = one(args.command, read(args.input.as_deref())?)?;
    output(
        &mut out,
        Engine::new(&mut model, policy).execute(&job),
        id,
        args.text,
    )
}
fn main() -> ExitCode {
    match run() {
        Ok(c) => ExitCode::from(c),
        Err(s) => {
            eprintln!("afmcli: {s}");
            ExitCode::from(2)
        }
    }
}
