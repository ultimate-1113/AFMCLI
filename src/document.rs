use serde::Serialize;
use std::collections::BTreeSet;

#[derive(Clone, Copy, Debug)]
pub struct Segment<'a> {
    pub id: usize,
    pub start: usize,
    pub text: &'a str,
}
impl Segment<'_> {
    pub fn end(&self) -> usize {
        self.start + self.text.len()
    }
}
pub fn segments(text: &str) -> Vec<Segment<'_>> {
    let mut res = Vec::new();
    let mut start = 0;
    let mut len = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, ch)) = chars.next() {
        len += 1;
        let stop = matches!(ch, '\n' | '。' | '！' | '？')
            || (matches!(ch, '.' | '!' | '?')
                && chars.peek().is_none_or(|(_, c)| c.is_whitespace()))
            || len >= 384;
        if stop {
            let end = i + ch.len_utf8();
            push(&mut res, text, start, end);
            start = end;
            len = 0;
        }
    }
    push(&mut res, text, start, text.len());
    res
}
fn push<'a>(out: &mut Vec<Segment<'a>>, source: &'a str, start: usize, end: usize) {
    let s = &source[start..end];
    let trimmed = s.trim();
    if !trimmed.is_empty() {
        out.push(Segment {
            id: out.len() + 1,
            start: start + s.len() - s.trim_start().len(),
            text: trimmed,
        });
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Ord, PartialOrd)]
pub struct Span {
    pub start_byte: usize,
    pub end_byte: usize,
}
#[derive(Debug, Serialize)]
pub struct Evidence {
    pub value: String,
    pub spans: BTreeSet<Span>,
}
pub fn locate(source: &str, offset: usize, value: &str) -> Result<BTreeSet<Span>, String> {
    locate_parts(&[(offset, source)], value)
}
/// Search each original range independently. A quote cannot bridge omitted text.
pub(crate) fn locate_parts(parts: &[(usize, &str)], value: &str) -> Result<BTreeSet<Span>, String> {
    if value.trim().is_empty() || value.trim() != value {
        return Err("return a nonempty verbatim span without padding".into());
    }
    let word = |c: char| c.is_ascii_alphanumeric() || c == '_';
    let (first, last) = (
        value.chars().next().unwrap(),
        value.chars().next_back().unwrap(),
    );
    let mut out = BTreeSet::new();
    for &(offset, source) in parts {
        let mut from = 0;
        while let Some(i) = source[from..].find(value) {
            let at = from + i;
            let end = at + value.len();
            let joined_left = word(first) && source[..at].chars().next_back().is_some_and(word);
            let joined_right = word(last) && source[end..].chars().next().is_some_and(word);
            if !joined_left && !joined_right {
                out.insert(Span {
                    start_byte: offset + at,
                    end_byte: offset + end,
                });
                if out.len() > 256 {
                    return Err(
                    "too many indistinguishable source occurrences; return a more specific span"
                        .into(),
                );
                }
            }
            from = at + first.len_utf8();
        }
    }
    if out.is_empty() {
        return Err("a proposed value does not occur verbatim in the supplied source; copy an exact span or leave it empty".into());
    }
    Ok(out)
}
