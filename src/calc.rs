//! Bounded scientific calculator. Arithmetic on decimal literals starts as exact rationals.
use crate::{
    contract,
    engine::{Engine, Fail, Output, Run, request},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

type Result<T> = std::result::Result<T, &'static str>;
const RANGE: &str = "exact intermediate exceeds the symmetric signed 128-bit range";
const ZERO: &str = "division by zero";
const DOMAIN: &str = "outside the real domain of this operation";
const MAX_BYTES: usize = 4096;
const MAX_TOKENS: usize = 512;
const MAX_DEPTH: usize = 48;
const MAX_POWER: u32 = 4096;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Angle {
    #[default]
    Rad,
    Deg,
}

#[derive(Clone, Copy, Debug)]
struct Ratio {
    num: i128,
    den: i128,
}

fn gcd(mut a: i128, mut b: i128) -> i128 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a.abs()
}

impl Ratio {
    const fn integer(n: i128) -> Self {
        Self { num: n, den: 1 }
    }

    fn new(n: i128, d: i128) -> Result<Self> {
        if d == 0 {
            return Err(ZERO);
        }
        if d == i128::MIN || n == i128::MIN {
            return Err(RANGE);
        }
        let common = gcd(n, d);
        let sign = d.signum();
        Ok(Self {
            num: (n / common) * sign,
            den: (d / common) * sign,
        })
    }

    fn decimal(s: &str) -> Result<Self> {
        let (mantissa, exponent) = match s.split_once(['e', 'E']) {
            Some((m, e)) => (
                m,
                e.parse::<i32>()
                    .map_err(|_| "invalid or excessive decimal exponent")?,
            ),
            None => (s, 0),
        };
        if exponent.unsigned_abs() > MAX_POWER {
            return Err("decimal exponent exceeds 4096");
        }
        let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
        let digits = format!("{whole}{fraction}");
        let trimmed = digits.trim_start_matches('0');
        if trimmed.is_empty() {
            return Ok(Self::integer(0));
        }
        let significant = trimmed.trim_end_matches('0');
        let mut n: i128 = significant.parse().map_err(|_| RANGE)?;
        let scale = fraction.len() as i32 - exponent - (trimmed.len() - significant.len()) as i32;
        if scale <= 0 {
            let power = 10i128.checked_pow(scale.unsigned_abs()).ok_or(RANGE)?;
            return Ok(Self::integer(n.checked_mul(power).ok_or(RANGE)?));
        }
        // Cancel before building the denominator, including unequal powers of two and five.
        let mut twos = scale as u32;
        let mut fives = twos;
        while twos > 0 && n % 2 == 0 {
            n /= 2;
            twos -= 1;
        }
        while fives > 0 && n % 5 == 0 {
            n /= 5;
            fives -= 1;
        }
        let d = 2i128
            .checked_pow(twos)
            .and_then(|a| a.checked_mul(5i128.checked_pow(fives)?))
            .ok_or(RANGE)?;
        Self::new(n, d)
    }

    fn neg(self) -> Self {
        Self {
            num: -self.num,
            ..self
        }
    }

    fn add(self, other: Self) -> Result<Self> {
        let common = gcd(self.den, other.den);
        let a = self.num.checked_mul(other.den / common).ok_or(RANGE)?;
        let b = other.num.checked_mul(self.den / common).ok_or(RANGE)?;
        let n = a.checked_add(b).ok_or(RANGE)?;
        if n == i128::MIN {
            return Err(RANGE);
        }
        let reduce = gcd(n, common);
        let den = (self.den / common)
            .checked_mul(other.den / reduce)
            .ok_or(RANGE)?;
        Self::new(n / reduce, den)
    }

    fn multiply(self, other: Self) -> Result<Self> {
        let a = gcd(self.num, other.den);
        let b = gcd(other.num, self.den);
        let n = (self.num / a).checked_mul(other.num / b).ok_or(RANGE)?;
        let d = (self.den / b).checked_mul(other.den / a).ok_or(RANGE)?;
        Self::new(n, d)
    }

    fn reciprocal(self) -> Result<Self> {
        Self::new(self.den, self.num)
    }

    fn power(self, n: i128) -> Result<Self> {
        let mag = n.unsigned_abs();
        if mag > u128::from(MAX_POWER) {
            return Err("integer power exceeds 4096");
        }
        if n == 0 && self.num == 0 {
            return Err("zero to the zero power is undefined");
        }
        let mut p = mag;
        let mut base = if n < 0 { self.reciprocal()? } else { self };
        let mut out = Self::integer(1);
        while p != 0 {
            if p & 1 == 1 {
                out = out.multiply(base)?;
            }
            p >>= 1;
            if p > 0 {
                base = base.multiply(base)?;
            }
        }
        Ok(out)
    }

    fn display(self) -> String {
        if self.den == 1 {
            return self.num.to_string();
        }
        let mut factors = self.den;
        for prime in [2, 5] {
            while factors % prime == 0 {
                factors /= prime;
            }
        }
        if factors != 1 {
            return format!("{}/{}", self.num, self.den);
        }
        let n = self.num.unsigned_abs();
        let d = self.den as u128;
        let mut out = format!("{}{}.", if self.num < 0 { "-" } else { "" }, n / d);
        let mut remainder = n % d;
        while remainder != 0 {
            // d <= i128::MAX. Modular addition stays within u128 even if 10*remainder would overflow.
            let mut next = 0;
            let mut digit = 0;
            for _ in 0..10 {
                next += remainder;
                if next >= d {
                    next -= d;
                    digit += 1;
                }
            }
            out.push(char::from(b'0' + digit));
            remainder = next;
        }
        out
    }
}

#[derive(Clone, Copy, Debug)]
enum Number {
    Exact(Ratio),
    Approx(f64),
}

impl Number {
    fn float(self) -> f64 {
        match self {
            Self::Exact(r) => r.num as f64 / r.den as f64,
            Self::Approx(v) => v,
        }
    }
    fn finite(v: f64) -> Result<Self> {
        if v.is_nan() {
            return Err(DOMAIN);
        }
        if !v.is_finite() {
            return Err("floating-point overflow or non-finite result");
        }
        Ok(Self::Approx(if v == 0.0 { 0.0 } else { v }))
    }
    fn neg(self) -> Self {
        match self {
            Self::Exact(r) => Self::Exact(r.neg()),
            Self::Approx(v) => Self::Approx(-v),
        }
    }
    fn integer(self) -> Result<i128> {
        match self {
            Self::Exact(r) if r.den == 1 => Ok(r.num),
            _ => Err("an exact integer is required here"),
        }
    }
    fn calculate(self, op: u8, right: Self) -> Result<Self> {
        if op == b'^' {
            return self.power(right);
        }
        if let (Self::Exact(a), Self::Exact(b)) = (self, right) {
            return match op {
                b'+' => a.add(b),
                b'-' => a.add(b.neg()),
                b'*' => a.multiply(b),
                b'/' => a.multiply(b.reciprocal()?),
                _ => unreachable!(),
            }
            .map(Self::Exact);
        }
        let (a, b) = (self.float(), right.float());
        let v = match op {
            b'+' => a + b,
            b'-' => a - b,
            b'*' => a * b,
            b'/' if b == 0.0 => return Err(ZERO),
            b'/' => a / b,
            _ => unreachable!(),
        };
        if matches!(op, b'*' | b'/') && a != 0.0 && b != 0.0 && v == 0.0 {
            return Err("floating-point underflow");
        }
        Self::finite(v)
    }

    fn power(self, right: Self) -> Result<Self> {
        if let (Self::Exact(a), Self::Exact(b)) = (self, right)
            && b.den == 1
        {
            return a.power(b.num).map(Self::Exact);
        }
        let (a, b) = (self.float(), right.float());
        if a == 0.0 && b <= 0.0 {
            return Err("zero to a nonpositive power is undefined");
        }
        let result = match right {
            Self::Exact(r) if a < 0.0 && r.den % 2 == 1 => {
                a.abs().powf(b) * if r.num % 2 == 0 { 1.0 } else { -1.0 }
            }
            _ => a.powf(b),
        };
        if a != 0.0 && result == 0.0 {
            return Err("floating-point underflow");
        }
        Self::finite(result)
    }
}

fn combinatorics(a: Number, b: Number, combination: bool) -> Result<Number> {
    let (n, r) = (a.integer()?, b.integer()?);
    if n < 0 || r < 0 || r > n {
        return Err("nCr and nPr require 0 <= r <= n");
    }
    let k = if combination { r.min(n - r) } else { r };
    if k > 1024 {
        return Err("combinatorial iteration limit exceeded");
    }
    let mut answer = Ratio::integer(1);
    for i in 1..=k {
        answer = answer.multiply(Ratio::new(n - k + i, if combination { i } else { 1 })?)?;
    }
    Ok(Number::Exact(answer))
}

fn factorial(value: Number) -> Result<Number> {
    let n = value.integer()?;
    if n < 0 {
        return Err("factorial requires a nonnegative integer");
    }
    if n > 33 {
        return Err("factorial exceeds the exact 128-bit range (maximum 33!)");
    }
    Ok(Number::Exact(Ratio::integer((1..=n).product())))
}

fn trig(name: &str, x: Number, unit: Angle) -> Result<Number> {
    let radians = if unit == Angle::Deg {
        let degrees = if let Number::Exact(r) = x {
            if r.den == 1 {
                let d = r.num.rem_euclid(360);
                // The rational values at these standard angles are known exactly.
                let halves = match (name, d) {
                    ("sin", 0 | 180) | ("cos", 90 | 270) | ("tan", 0 | 180) => Some(0),
                    ("sin", 90) | ("cos", 0) | ("tan", 45 | 225) => Some(2),
                    ("sin", 270) | ("cos", 180) | ("tan", 135 | 315) => Some(-2),
                    ("sin", 30 | 150) | ("cos", 60 | 300) => Some(1),
                    ("sin", 210 | 330) | ("cos", 120 | 240) => Some(-1),
                    ("tan", 90 | 270) => return Err("tangent is undefined at an odd quarter turn"),
                    _ => None,
                };
                if let Some(v) = halves {
                    return Ok(Number::Exact(Ratio::new(v, 2)?));
                }
            }
            ((r.num / r.den) % 360) as f64 + (r.num % r.den) as f64 / r.den as f64
        } else {
            x.float() % 360.0
        };
        degrees.to_radians()
    } else {
        x.float()
    };
    let value = match name {
        "sin" => radians.sin(),
        "cos" => radians.cos(),
        "tan" if radians.cos().abs() <= 4.0 * f64::EPSILON => {
            return Err(
                "tangent is undefined or numerically indistinguishable from an odd quarter turn",
            );
        }
        "tan" => radians.tan(),
        _ => unreachable!(),
    };
    Number::finite(value)
}

fn function(name: &str, first: Number, second: Option<Number>, unit: Angle) -> Result<Number> {
    if matches!(name, "ncr" | "npr") {
        return combinatorics(
            first,
            second.ok_or("nCr and nPr take two arguments")?,
            name == "ncr",
        );
    }
    if second.is_some() {
        return Err("this function takes one argument");
    }
    let x = first.float();
    let v = match name {
        "sqrt" => {
            if x < 0.0 {
                return Err("square root requires a nonnegative operand");
            }
            if let Number::Exact(r) = first {
                let (a, b) = (r.num.isqrt(), r.den.isqrt());
                if a * a == r.num && b * b == r.den {
                    return Ok(Number::Exact(Ratio::new(a, b)?));
                }
            }
            x.sqrt()
        }
        "abs" => return Ok(if x < 0.0 { first.neg() } else { first }),
        "exp" => {
            let y = x.exp();
            if y == 0.0 {
                return Err("exponential underflow");
            }
            y
        }
        "ln" | "log" | "log10" if x <= 0.0 => return Err("logarithm requires a positive operand"),
        "ln" => x.ln(),
        "log" | "log10" => x.log10(),
        "sin" | "cos" | "tan" => return trig(name, first, unit),
        "asin" | "acos" | "atan" => {
            let rad = match name {
                "asin" => x.asin(),
                "acos" => x.acos(),
                _ => x.atan(),
            };
            if unit == Angle::Deg {
                rad.to_degrees()
            } else {
                rad
            }
        }
        "sinh" => x.sinh(),
        "cosh" => x.cosh(),
        "tanh" => x.tanh(),
        _ => return Err("unknown calculator function"),
    };
    Number::finite(v)
}

#[derive(Clone, Copy, PartialEq)]
enum Token<'a> {
    Literal(&'a str),
    Name(&'a str),
    Op(u8),
    End,
}

fn normalized(input: &str) -> String {
    let mut s = String::with_capacity(input.len());
    for c in input.chars() {
        match c {
            '−' => s.push('-'),
            '×' => s.push('*'),
            '÷' => s.push('/'),
            'π' => s.push_str("pi"),
            _ if c.is_whitespace() => s.push(' '),
            _ => s.push(c),
        }
    }
    s
}

fn tokens(s: &str) -> Result<Vec<Token<'_>>> {
    let raw = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < raw.len() {
        let start = i;
        let c = raw[i];
        if c == b' ' {
            i += 1;
            continue;
        }
        if out.len() == MAX_TOKENS {
            return Err("expression exceeds 512 tokens");
        }
        let token = match c {
            b'0'..=b'9' | b'.' => {
                while raw.get(i).is_some_and(u8::is_ascii_digit) {
                    i += 1;
                }
                if raw.get(i) == Some(&b'.') {
                    i += 1;
                    while raw.get(i).is_some_and(u8::is_ascii_digit) {
                        i += 1;
                    }
                }
                if i == start + 1 && c == b'.' {
                    return Err("a decimal point needs digits");
                }
                if matches!(raw.get(i), Some(b'e' | b'E')) {
                    i += 1;
                    if matches!(raw.get(i), Some(b'+' | b'-')) {
                        i += 1;
                    }
                    let exponent = i;
                    while raw.get(i).is_some_and(u8::is_ascii_digit) {
                        i += 1;
                    }
                    if i == exponent {
                        return Err("scientific notation needs an exponent");
                    }
                }
                Token::Literal(&s[start..i])
            }
            b'a'..=b'z' | b'A'..=b'Z' => {
                i += 1;
                while raw.get(i).is_some_and(u8::is_ascii_alphanumeric) {
                    i += 1;
                }
                Token::Name(&s[start..i])
            }
            b'+' | b'-' | b'*' | b'/' | b'^' | b'!' | b'(' | b')' | b',' => {
                i += 1;
                Token::Op(c)
            }
            _ => return Err("unsupported calculator character"),
        };
        out.push(token);
    }
    out.push(Token::End);
    Ok(out)
}

struct Parser<'a> {
    tokens: Vec<Token<'a>>,
    at: usize,
    angle: Angle,
}

impl Parser<'_> {
    fn peek(&self) -> Token<'_> {
        self.tokens[self.at]
    }
    fn eat(&mut self, ch: u8) -> bool {
        if self.peek() == Token::Op(ch) {
            self.at += 1;
            true
        } else {
            false
        }
    }
    fn expression(&mut self, min_binding: u8, depth: usize) -> Result<Number> {
        if depth > MAX_DEPTH {
            return Err("expression nesting exceeds 48");
        }
        let token = self.tokens[self.at];
        if token == Token::End {
            return Err("expected a calculator operand");
        }
        self.at += 1;
        let mut left = match token {
            Token::Literal(s) => Number::Exact(Ratio::decimal(s)?),
            Token::Op(b'-') => self.expression(25, depth + 1)?.neg(),
            Token::Op(b'+') => self.expression(25, depth + 1)?,
            Token::Op(b'(') => {
                let n = self.expression(0, depth + 1)?;
                if !self.eat(b')') {
                    return Err("missing closing parenthesis");
                }
                n
            }
            Token::Name(s) => {
                let name = s.to_ascii_lowercase();
                match name.as_str() {
                    "pi" => Number::Approx(std::f64::consts::PI),
                    "e" => Number::Approx(std::f64::consts::E),
                    _ => {
                        if !self.eat(b'(') {
                            return Err("unknown name or missing function parentheses");
                        }
                        let first = self.expression(0, depth + 1)?;
                        let second = if self.eat(b',') {
                            Some(self.expression(0, depth + 1)?)
                        } else {
                            None
                        };
                        if !self.eat(b')') {
                            return Err("missing closing parenthesis or wrong function arity");
                        }
                        function(&name, first, second, self.angle)?
                    }
                }
            }
            _ => return Err("expected a calculator operand"),
        };
        while let Token::Op(op) = self.peek() {
            let (lbp, rbp) = match op {
                b'+' | b'-' => (10, 11),
                b'*' | b'/' => (20, 21),
                b'^' => (30, 30), // right-associative, tighter than a prefix sign
                b'!' => (40, 41),
                _ => break,
            };
            if lbp < min_binding {
                break;
            }
            self.at += 1;
            left = if op == b'!' {
                factorial(left)?
            } else {
                left.calculate(op, self.expression(rbp, depth + 1)?)?
            };
        }
        Ok(left)
    }
}

pub fn evaluate(expression: &str, angle: Angle) -> Result<Value> {
    if expression.len() > MAX_BYTES {
        return Err("expression exceeds 4096 UTF-8 bytes");
    }
    let expression = expression.trim();
    let clean = normalized(expression);
    let mut parser = Parser {
        tokens: tokens(&clean)?,
        at: 0,
        angle,
    };
    let n = parser.expression(0, 0)?;
    if parser.peek() != Token::End {
        return Err("unexpected token; use explicit operators between operands");
    }
    let (value, approximate) = match n {
        Number::Exact(r) => (r.display(), false),
        Number::Approx(x) => (if x == 0.0 { "0".into() } else { x.to_string() }, true),
    };
    Ok(json!({"expression":expression,"value":value,"approximate":approximate,"angle_unit":angle}))
}

fn looks_like_formula(s: &str) -> bool {
    // A plain formula that fails a domain or syntax check must not be reinterpreted by a model.
    let clean = normalized(s);
    if clean.chars().any(|c| c.is_alphabetic() && !c.is_ascii()) {
        return false;
    }
    if clean.split_once('(').is_some_and(|(head, _)| {
        let name = head.trim();
        !name.is_empty() && name.bytes().all(|b| b.is_ascii_alphanumeric())
    }) {
        return true;
    }
    clean
        .split(|c: char| !c.is_ascii_alphabetic())
        .filter(|part| !part.is_empty())
        .all(|name| {
            name.len() <= 1
                || matches!(
                    name.to_ascii_lowercase().as_str(),
                    "pi" | "sqrt"
                        | "abs"
                        | "exp"
                        | "ln"
                        | "log"
                        | "sin"
                        | "cos"
                        | "tan"
                        | "asin"
                        | "acos"
                        | "atan"
                        | "sinh"
                        | "cosh"
                        | "tanh"
                        | "ncr"
                        | "npr"
                )
        })
}

pub(crate) fn run(
    engine: &mut Engine<'_>,
    expression: Option<&str>,
    source: Option<&str>,
    angle: Angle,
) -> Run<Output> {
    if let Some(s) = expression {
        return evaluate(s, angle)
            .map(|v| Output::new("ok", v))
            .map_err(|e| Fail::Invalid(e.into()));
    }
    let src = source.ok_or_else(|| Fail::Invalid("supply one of expression or source".into()))?;
    if src.len() > 16384 {
        return Err(Fail::Held(
            "natural-language calculation input exceeds 16384 UTF-8 bytes".into(),
        ));
    }
    match evaluate(src, angle) {
        Ok(v) => return Ok(Output::new("ok", v)),
        Err(e) if looks_like_formula(src) => return Err(Fail::Held(e.into())),
        _ => {}
    }
    let request = request(
        "Translate the arithmetic problem into one calculator expression, without evaluating it. Use the source quantities, explicit operators + - * / ^ !, parentheses, and decimal literals. Available names: pi, e, sqrt, abs, exp, ln, log10, log (base ten), sin, cos, tan, asin, acos, atan, sinh, cosh, tanh, nCr(n,r), nPr(n,r). The stated angle unit applies to circular functions. If the problem has missing quantities, unclear meaning, incompatible units or an unsupported operation, set expression to the empty string. Return only the expression field.",
        json!({"source":src,"angle_unit":angle}),
        Some(contract::object([(
            "expression".into(),
            json!({"type":"string"}),
        )])),
    );
    if engine.policy.direct {
        return engine.raw(request);
    }
    let proposal = engine.structured(request, |v| {
        Ok(v["expression"].as_str().unwrap().to_owned())
    })?;
    if proposal.trim().is_empty() {
        return Err(Fail::Held(
            "no unambiguous supported expression was proposed".into(),
        ));
    }
    let value = evaluate(&proposal, angle).map_err(|e| Fail::Held(e.into()))?;
    Ok(Output {
        status: "review",
        value,
        notes: vec!["Review the proposed expression against the original problem.".into()],
    })
}
