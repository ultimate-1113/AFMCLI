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
    r"(?ix)[+＋\-−－]?(?:(?:[0-9０-９]+(?:[,，][0-9０-９]+)*)(?:[.．][0-9０-９]+)?|[.．][0-9０-９]+)(?:[eEｅＥ][+＋\-−－]?[0-9０-９]+)?[十百千万億兆]?|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
).unwrap()
});
const UNSUPPORTED: &str = "unsupported or ambiguous numeric notation in calculation source; write the full quantity as a decimal with a unit";
static UNSUPPORTED_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)\b(?:thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|trillions?|dozens?)\b|[一二三四五六七八九零〇]+[十百千万億兆]|[十百千][万億兆]"
).unwrap()
});
static FRACTION_SUFFIX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"(?ix)^\s*(?:(?:and\s+)?(?:a|one)\s+|[-‐‑–\s]+)(?:half|halves|quarters?|thirds?|fourths?|fifths?|sixths?|sevenths?|eighths?|ninths?|tenths?)\b|^[kmb]\s*(?:dollars?|euros?|yen|usd|eur|jpy|gbp)\b"
).unwrap()
});
const SCALES: [char; 6] = ['十', '百', '千', '万', '億', '兆'];
static FRACTION_LITERAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[0-9０-９]\s*[/／⁄]\s*[0-9０-９]|[\u{00bc}-\u{00be}\u{2150}-\u{215e}]").unwrap()
});
const CARDINALS: [&str; 13] = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve",
];

fn decimal_source(raw: &str, before: &str, after: &str) -> Result<String, &'static str> {
    let norm = folded(raw).replace('−', "-");
    let left = folded(before);
    let right = folded(after);
    if left.trim_end().ends_with(SCALES)
        || right.starts_with(SCALES)
        || FRACTION_SUFFIX.is_match(&right)
    {
        return Err(UNSUPPORTED);
    }
    if let Some(n) = CARDINALS.iter().position(|word| *word == norm) {
        return Ok(n.to_string());
    }
    let (body, scale) = norm
        .char_indices()
        .last()
        .and_then(|(i, c)| {
            SCALES.iter().position(|ch| *ch == c).map(|power| {
                (
                    &norm[..i],
                    [10, 100, 1000, 10000, 100000000, 1000000000000i128][power],
                )
            })
        })
        .unwrap_or((&norm, 1));
    if scale != 1
        && right
            .trim_start()
            .starts_with(|c: char| c.is_ascii_digit() || c == '.')
    {
        // A full stop is punctuation. A dot followed by digits continues the number.
        let next = right.trim_start();
        if !next.starts_with('.') || next[1..].starts_with(|c: char| c.is_ascii_digit()) {
            return Err(UNSUPPORTED);
        }
    }
    let neg = body.starts_with('-');
    let magnitude = body.trim_start_matches(['+', '-']);
    let mantissa = magnitude.split(['e', 'E']).next().unwrap_or(magnitude);
    let whole = mantissa.split('.').next().unwrap_or(mantissa);
    if whole.contains(',') {
        let mut groups = whole.split(',');
        let first = groups.next().unwrap_or("");
        if !(1..=3).contains(&first.len()) || groups.any(|g| g.len() != 3) {
            return Err(UNSUPPORTED);
        }
    }
    // Do not split a malformed grouped or decimal literal into plausible operands.
    if (right.starts_with(['.', ',']) && right[1..].starts_with(|c: char| c.is_ascii_digit()))
        || (magnitude.starts_with('.') && left.ends_with(|c: char| c.is_ascii_digit()))
    {
        return Err(UNSUPPORTED);
    }
    let mut magnitude = magnitude.replace(',', "");
    if magnitude.starts_with('.') {
        magnitude.insert(0, '0');
    }
    if scale == 1 {
        return Ok(format!("{}{magnitude}", if neg { "-" } else { "" }));
    }
    let exact =
        super::super::Ratio::decimal(&magnitude)?.multiply(super::super::Ratio::integer(scale))?;
    Ok(if neg { exact.neg() } else { exact }.display())
}
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
            if UNSUPPORTED_WORDS.is_match(span.text) || FRACTION_LITERAL.is_match(span.text) {
                return Err(UNSUPPORTED);
            }
            let numbers: Vec<_> = NUMBER.find_iter(span.text).collect();
            for (i, found) in numbers.iter().enumerate() {
                let left = numbers.get(i.wrapping_sub(1)).map_or(0, |m| m.end());
                let right = numbers.get(i + 1).map_or(span.text.len(), |m| m.start());
                let before = &span.text[left..found.start()];
                let after = &span.text[found.end()..right];
                let whole_before = &span.text[..found.start()];
                let whole_after = &span.text[found.end()..];
                // A digit inside an identifier is not a source quantity.
                if whole_before.ends_with(|c: char| c.is_ascii_alphabetic() || c == '_')
                    && !["usd", "jpy", "eur", "gbp"]
                        .iter()
                        .any(|name| folded(whole_before).ends_with(name))
                {
                    continue;
                }
                let literal = decimal_source(found.as_str(), whole_before, whole_after)?;
                let left = before.rfind([',', '、', '，']).map_or(left, |p| {
                    let delimiter = before[p..].chars().next().unwrap();
                    left + p + delimiter.len_utf8()
                });
                let right = after
                    .find([',', '、', '，'])
                    .map_or(right, |p| found.end() + p);
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

#[cfg(test)]
mod number_tests {
    use super::*;

    #[test]
    fn decimals_and_scales_keep_the_entire_source_amount() {
        for (source, original, value, unit) in [
            ("差額は.5円。", ".5", "0.5", "円"),
            ("差額は−.5円。", "−.5", "-0.5", "円"),
            ("差額は＋．５円。", "＋．５", "0.5", "円"),
            ("支払額は1万円。", "1万", "10000", "円"),
            (
                "予算は１，２５０．５万円。",
                "１，２５０．５万",
                "12505000",
                "円",
            ),
            ("評価額は-.25億円。", "-.25億", "-25000000", "円"),
            ("Balance: USD.5.", ".5", "0.5", "usd"),
        ] {
            let ledger = Ledger::new(source).unwrap();
            assert_eq!(ledger.quantities.len(), 1, "{source}");
            let q = &ledger.quantities[0];
            assert_eq!(
                (&source[q.range.clone()], q.literal.as_str()),
                (original, value),
                "{source}"
            );
            assert_eq!(q.unit.as_ref().unwrap().1, unit);
        }
    }

    #[test]
    fn rejects_compound_or_malformed_notation_without_using_a_partial_amount() {
        for source in [
            "It cost two hundred dollars.",
            "It cost twenty-one dollars.",
            "It cost seven million dollars.",
            "The batch weighed one and a half kilograms.",
            "支払額は1万2500円。",
            "評価額は2億3千万円。",
            "予算は一万3000円。",
            "The amount is 1,23 euros.",
            "The label says 3.2.4 kg.",
            "It cost 2.5k dollars.",
            "It cost 1/2 dollar.",
            "The bag weighed ３ ／ ４ kg.",
            "It cost 1½ dollars.",
        ] {
            assert!(matches!(Ledger::new(source), Err(UNSUPPORTED)), "{source}");
        }
        let source = "The V12 series sold twelve units.";
        let ledger = Ledger::new(source).unwrap();
        assert_eq!(ledger.quantities.len(), 1);
        assert_eq!(ledger.quantities[0].text, "twelve");
    }
}
