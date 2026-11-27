//! All source IDs and byte ranges are minted by Rust. A model can select a quantity
//! but cannot supply its arithmetic literal.
use regex::Regex;
use serde_json::{Value, json};
use std::{ops::Range, sync::LazyLock};
use unicode_normalization::UnicodeNormalization;

pub(super) fn folded(s: &str) -> String {
    s.nfkc().collect::<String>().to_lowercase()
}

static NUMBER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)[+＋\-−－]?(?:[0-9０-９]{1,3}(?:[,，][0-9０-９]{3})+|[0-9０-９]+)(?:[.．][0-9０-９]+)?(?:[eE][+\-]?[0-9]+)?|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
).unwrap()
});
const CARDINALS: [&str; 13] = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve",
];
static UNIT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)^(\%|パーセント|percent(?:age)?\b|円|ドル|ユーロ|ポンド|米ドル|usd\b|jpy\b|eur\b|gbp\b|dollars?\b|yen\b|euros?\b|pounds?\b|キログラム|グラム|キロメートル|メートル|センチメートル|ミリメートル|ミリリットル|リットル|kg\b|g\b|mm\b|cm\b|km\b|m\b|ml\b|l\b|kilograms?\b|grams?\b|kilometers?\b|meters?\b|liters?\b|hours?\b|minutes?\b|seconds?\b|days?\b|時間|分間|秒間|分|秒|ヶ月|か月|年間|年|日間|日|枚|個|本|台|冊|箱|袋|人|名|件|票|席|組|束|着|点|匹|頭|羽|軒|棟|回|杯|皿|足|粒|パック|セット|ケース|ロット|[a-z]+(?:-[a-z]+)?)"
).unwrap()
});

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) enum Unit {
    Money(String),
    Percent,
    Measure(String),
    Count(String),
}

impl Unit {
    pub(super) fn parse(s: &str) -> Option<Self> {
        let name = folded(s);
        let name = name.trim();
        let money = match name {
            "$" | "dollar" | "dollars" | "usd" | "ドル" | "米ドル" => Some("USD"),
            "¥" | "円" | "yen" | "jpy" => Some("JPY"),
            "€" | "euro" | "euros" | "eur" | "ユーロ" => Some("EUR"),
            "£" | "pound" | "pounds" | "gbp" | "ポンド" => Some("GBP"),
            _ => None,
        };
        if let Some(currency) = money {
            return Some(Self::Money(currency.into()));
        }
        if matches!(name, "%" | "percent" | "percentage" | "パーセント") {
            return Some(Self::Percent);
        }
        let measure = match name {
            "oz" | "ounce" | "ounces" => Some("oz"),
            "lb" | "lbs" => Some("lb"),
            "kg" | "kilogram" | "kilograms" | "キログラム" => Some("kg"),
            "g" | "gram" | "grams" | "グラム" => Some("g"),
            "m" | "meter" | "meters" | "メートル" => Some("m"),
            "cm" | "センチメートル" => Some("cm"),
            "mm" | "ミリメートル" => Some("mm"),
            "km" | "kilometer" | "kilometers" | "キロメートル" => Some("km"),
            "l" | "liter" | "liters" | "リットル" => Some("L"),
            "ml" | "ミリリットル" => Some("mL"),
            "hour" | "hours" | "時間" => Some("hour"),
            "minute" | "minutes" | "分" | "分間" => Some("minute"),
            "second" | "seconds" | "秒" | "秒間" => Some("second"),
            "day" | "days" | "日" | "日間" => Some("day"),
            "年" | "年間" | "ヶ月" | "か月" => Some(name),
            _ => None,
        };
        if let Some(key) = measure {
            return Some(Self::Measure(key.into()));
        }
        if name.is_empty()
            || matches!(
                name,
                "is" | "are"
                    | "was"
                    | "were"
                    | "of"
                    | "or"
                    | "and"
                    | "from"
                    | "at"
                    | "to"
                    | "per"
                    | "each"
                    | "a"
                    | "an"
                    | "the"
                    | "in"
                    | "with"
                    | "by"
                    | "for"
                    | "on"
                    | "as"
                    | "have"
                    | "has"
                    | "had"
                    | "out"
                    | "more"
                    | "less"
                    | "left"
                    | "remained"
                    | "remaining"
                    | "than"
            )
        {
            return None;
        }
        let singular = match name {
            "人" | "名" | "people" | "person" | "persons" => "person",
            "boxes" => "box",
            other => other.strip_suffix('s').unwrap_or(other),
        };
        Some(Self::Count(singular.to_owned()))
    }
}

pub(super) struct Span<'a> {
    pub text: &'a str,
    start: usize,
}

pub(super) struct Quantity<'a> {
    pub span: usize,
    pub range: Range<usize>,
    pub text: &'a str,
    pub literal: String,
    pub unit: Option<(Unit, String)>,
    pub neighborhood: &'a str,
}

pub(super) struct Ledger<'a> {
    pub spans: Vec<Span<'a>>,
    pub quantities: Vec<Quantity<'a>>,
    pub requested: usize,
}

fn push_span<'a>(spans: &mut Vec<Span<'a>>, src: &'a str, start: usize, end: usize) {
    let raw = &src[start..end];
    let text = raw.trim();
    if !text.is_empty() {
        spans.push(Span {
            text,
            start: start + raw.len() - raw.trim_start().len(),
        });
    }
}

fn bound_unit(left: &str, right: &str) -> Option<(Unit, String)> {
    let left = folded(left);
    for prefix in ["¥", "$", "€", "£", "usd", "jpy", "eur", "gbp"] {
        if left.trim_end().ends_with(prefix) {
            return Unit::parse(prefix).map(|unit| (unit, prefix.into()));
        }
    }
    let right = folded(right);
    let right = right.trim_start();
    let mut word = UNIT.find(right)?.as_str();
    // An adjective between a cardinal and a plural count noun is common in prose.
    // Keep the noun, not its state or colour, as the dimensional unit.
    if word.is_ascii() && matches!(Unit::parse(word), Some(Unit::Count(_))) {
        for next in right[word.len()..].split_whitespace().take(3) {
            let token = next.trim_end_matches(|c: char| !c.is_ascii_alphabetic());
            if token.len() < 2
                || !token.bytes().all(|b| b.is_ascii_alphabetic())
                || [
                    "is", "was", "has", "does", "plus", "minus", "costs", "weighs", "holds",
                    "contains", "includes", "were", "are", "in", "of", "per", "each", "at", "for",
                    "cost", "held", "weighed", "had", "from", "onto", "then",
                ]
                .contains(&token)
                || token.ends_with("ed")
            {
                break;
            }
            if token.ends_with('s') {
                word = token;
                break;
            }
            if next != token {
                break;
            }
        }
    }
    Unit::parse(word).map(|unit| (unit, word.to_owned()))
}

impl<'a> Ledger<'a> {
    pub fn new(src: &'a str) -> Result<Self, &'static str> {
        let mut spans = Vec::new();
        let mut start = 0;
        for (i, c) in src.char_indices() {
            let end = i + c.len_utf8();
            let stop = matches!(c, '。' | '?' | '？' | '!' | '！' | ';' | '；' | '\n')
                || (c == '.' && !src[end..].starts_with(|ch: char| ch.is_ascii_digit()));
            if stop {
                push_span(&mut spans, src, start, end);
                start = end;
            }
        }
        push_span(&mut spans, src, start, src.len());
        if spans.is_empty() {
            return Err("empty calculation source");
        }
        let requested = spans
            .iter()
            .rposition(|s| s.text.contains(['?', '？']))
            .or_else(|| {
                spans.iter().rposition(|s| {
                    let text = folded(s.text);
                    [
                        "求め",
                        "算出",
                        "計算して",
                        "教えて",
                        "いくら",
                        "いくつ",
                        "何個",
                        "何人",
                        "何円",
                        "calculate ",
                        "compute ",
                        "find ",
                        "determine ",
                        "what is",
                        "how much",
                        "how many",
                    ]
                    .iter()
                    .any(|word| text.contains(word))
                })
            })
            .unwrap_or(spans.len() - 1);
        let mut quantities = Vec::new();
        for (si, span) in spans.iter().enumerate() {
            let numbers: Vec<_> = NUMBER.find_iter(span.text).collect();
            for (i, found) in numbers.iter().enumerate() {
                let left = numbers.get(i.wrapping_sub(1)).map_or(0, |m| m.end());
                let right = numbers.get(i + 1).map_or(span.text.len(), |m| m.start());
                let before = &span.text[left..found.start()];
                let after = &span.text[found.end()..right];
                let left = before.rfind([',', '、', '，']).map_or(left, |p| {
                    let delimiter = before[p..].chars().next().unwrap();
                    left + p + delimiter.len_utf8()
                });
                let right = after
                    .find([',', '、', '，'])
                    .map_or(right, |p| found.end() + p);
                let normalized = folded(found.as_str());
                let literal = match CARDINALS.iter().position(|word| *word == normalized) {
                    Some(n) => n.to_string(),
                    None => normalized.replace(',', "").replace('−', "-"),
                };
                quantities.push(Quantity {
                    span: si,
                    range: span.start + found.start()..span.start + found.end(),
                    text: found.as_str(),
                    literal,
                    unit: bound_unit(before, after),
                    neighborhood: &span.text[left..right],
                });
            }
        }
        if quantities.len() > 64 || spans.len() > 64 {
            return Err(
                "calculation source exceeds the trial limit of 64 evidence spans or 64 quantities",
            );
        }
        Ok(Self {
            spans,
            quantities,
            requested,
        })
    }

    pub fn payload(&self) -> Value {
        let spans: Vec<_> = self
            .spans
            .iter()
            .enumerate()
            .map(|(i, s)| json!({"id":format!("s{i}"),"text":s.text}))
            .collect();
        let quantities: Vec<_> = self
            .quantities
            .iter()
            .enumerate()
            .map(|(i, q)| {
                json!({"id":format!("q{i}"),"span":format!("s{}",q.span),"number":q.text,
                "near":q.neighborhood,"unit":q.unit.as_ref().map(|(_,s)|s)})
            })
            .collect();
        json!({"source_spans":spans, "source_quantities":quantities})
    }
}
