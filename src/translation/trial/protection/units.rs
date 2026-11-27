use super::{Kind, NUMBER, Term, View, normalize};
use std::collections::{BTreeMap, BTreeSet};

// Equivalent spellings only. No conversion of magnitude, scale, or dimension.
const UNITS: &[(&str, &[&str])] = &[
    ("h", &["hours", "hour", "hrs", "hr", "時間"]),
    ("min", &["minutes", "minute", "mins", "分"]),
    ("s", &["seconds", "second", "secs", "sec", "秒"]),
    ("ms", &["milliseconds", "millisecond", "ミリ秒"]),
    (
        "kg",
        &[
            "kilograms",
            "kilogram",
            "kilogrammes",
            "kilogramme",
            "キログラム",
        ],
    ),
    ("g", &["grams", "gram", "grammes", "gramme", "グラム"]),
    ("mg", &["milligrams", "milligram", "ミリグラム"]),
    (
        "km",
        &[
            "kilometers",
            "kilometres",
            "kilometer",
            "kilometre",
            "キロメートル",
        ],
    ),
    ("m", &["meters", "metres", "meter", "metre", "メートル"]),
    (
        "cm",
        &[
            "centimeters",
            "centimetres",
            "centimeter",
            "centimetre",
            "センチメートル",
        ],
    ),
    (
        "mm",
        &[
            "millimeters",
            "millimetres",
            "millimeter",
            "millimetre",
            "ミリメートル",
        ],
    ),
    ("L", &["liters", "litres", "liter", "litre", "リットル"]),
    (
        "mL",
        &[
            "milliliters",
            "millilitres",
            "milliliter",
            "millilitre",
            "ミリリットル",
        ],
    ),
    ("%", &["percent", "per cent", "パーセント"]),
    ("Hz", &["hertz", "ヘルツ"]),
    ("kPa", &["kilopascals", "kilopascal", "キロパスカル"]),
    ("Pa", &["pascals", "pascal", "パスカル"]),
    ("V", &["volts", "volt", "ボルト"]),
];

pub(super) fn restore(terms: &[Term], raw: &str) -> Result<String, String> {
    canonical(terms.iter(), raw)
}

pub(super) fn restore_unmarked(terms: &[Term], seen: &[bool], raw: &str) -> Result<String, String> {
    canonical(
        terms
            .iter()
            .zip(seen)
            .filter_map(|(t, seen)| (!seen).then_some(t)),
        raw,
    )
}

fn canonical<'a>(terms: impl Iterator<Item = &'a Term>, raw: &str) -> Result<String, String> {
    let mut expected: BTreeMap<_, (usize, BTreeSet<&str>)> = BTreeMap::new();
    for term in terms.filter(|t| t.kind == Kind::Number) {
        let canonical = normalize(&term.target);
        let Some(number) = NUMBER.find(&canonical).filter(|n| n.start() == 0) else {
            continue;
        };
        let Some(index) = UNITS
            .iter()
            .position(|(u, _)| *u == canonical[number.end()..].trim())
        else {
            continue;
        };
        let (count, originals) = expected
            .entry((number.as_str().to_owned(), index))
            .or_default();
        *count += 1;
        originals.insert(term.target.as_str());
    }
    if expected.is_empty() {
        return Ok(raw.to_owned());
    }
    let view = View::new(raw, false);
    let mut found = BTreeMap::new();
    let mut replacements = Vec::new();
    for n in NUMBER.find_iter(&view.text) {
        let suffix = &view.text[n.end()..];
        let spaced = suffix.trim_start();
        let token = spaced.strip_prefix('-').unwrap_or(spaced);
        let lower = token.to_ascii_lowercase();
        let mut candidates = Vec::new();
        for ((number, index), (_, originals)) in &expected {
            if number != n.as_str() {
                continue;
            }
            let (symbol, words) = UNITS[*index];
            let length = std::iter::once((symbol, token))
                .chain(words.iter().map(|w| (*w, lower.as_str())))
                .filter_map(|(form, text)| {
                    text.strip_prefix(form).and_then(|tail| {
                        let partial_word =
                            form.as_bytes().last().is_some_and(u8::is_ascii_alphabetic)
                                && tail.as_bytes().first().is_some_and(u8::is_ascii_alphabetic);
                        (!partial_word).then_some(form.len())
                    })
                })
                .max();
            if let Some(length) = length {
                candidates.push((*index, originals, length));
            }
        }
        if candidates.is_empty() {
            continue;
        }
        if candidates.len() != 1 {
            return Err("ambiguous local unit spelling".into());
        }
        let (index, originals, length) = candidates[0];
        let end = n.end() + suffix.len() - token.len() + length;
        let span = view
            .original(n.start()..end)
            .ok_or("incomplete Unicode measurement")?;
        *found.entry((n.as_str().to_owned(), index)).or_insert(0) += 1;
        if !originals.contains(&raw[span.clone()]) {
            if originals.len() != 1 {
                return Err("ambiguous source measurement spelling".into());
            }
            replacements.push((span, *originals.first().unwrap()));
        }
    }
    for (key, (count, _)) in &expected {
        if found.get(key) != Some(count) {
            return Err(format!(
                "preserve the number and its unit: {} {}",
                key.0, UNITS[key.1].0
            ));
        }
    }
    let mut output = raw.to_owned();
    for (span, written) in replacements.into_iter().rev() {
        output.replace_range(span, written);
    }
    Ok(output)
}
