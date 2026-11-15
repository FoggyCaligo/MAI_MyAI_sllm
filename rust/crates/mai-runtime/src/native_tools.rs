//! First native tools. No MCP schema wrappers or remote execution.
use crate::{Registry, Tool, ToolSchema};
use chrono::{Local, Utc};
use rust_decimal::{prelude::ToPrimitive, Decimal};
use serde_json::{json, Value};
use std::{str::FromStr, sync::Arc, time::Duration};

pub fn register_basics(registry: &mut Registry) -> Result<(), String> {
    registry.register(Tool {
        schema: ToolSchema {
            name: "current_time".into(),
            description: "Read OS-local and UTC clock with timezone offset".into(),
            parameters: json!({"type":"object","properties":{},"additionalProperties":false}),
        },
        timeout: Duration::from_secs(10),
        invoke: Arc::new(|_| {
            Box::pin(async move {
                let now = Local::now();
                Ok(json!({
                    "local_iso":now.to_rfc3339(),
                    "utc_iso":now.with_timezone(&Utc).to_rfc3339(),
                    "timezone_name":now.format("%Z").to_string(),
                    "utc_offset_seconds":now.offset().local_minus_utc(),
                    "date":now.format("%Y-%m-%d").to_string(),
                    "time":now.format("%H:%M:%S%.f%:z").to_string()
                }))
            })
        }),
    })?;
    registry.register(Tool {
        schema: ToolSchema {
            name: "calculator".into(),
            description: "Evaluate decimal arithmetic with parentheses, +, -, *, /, %, **".into(),
            parameters: json!({"type":"object","properties":{"expression":{"type":"string"}},
                "required":["expression"],"additionalProperties":false}),
        },
        timeout: Duration::from_secs(10),
        invoke: Arc::new(|args: Value| {
            Box::pin(async move {
                let source = args
                    .get("expression")
                    .and_then(Value::as_str)
                    .ok_or("missing expression")?
                    .trim();
                if source.is_empty() || source.len() > 500 {
                    return Err("expression length must be 1..500".into());
                }
                let result = compute(source)?;
                Ok(json!({"expression":source,"result":result.normalize().to_string()}))
            })
        }),
    })?;
    Ok(())
}

fn compute(source: &str) -> Result<Decimal, String> {
    let mut p = Parser {
        bytes: source.as_bytes(),
        cursor: 0,
        depth: 0,
    };
    let n = p.expression()?;
    p.spaces();
    if p.cursor != p.bytes.len() {
        return Err("unsupported expression content".into());
    }
    Ok(n)
}
struct Parser<'a> {
    bytes: &'a [u8],
    cursor: usize,
    depth: usize,
}
impl Parser<'_> {
    fn spaces(&mut self) {
        while self.cursor < self.bytes.len() && self.bytes[self.cursor].is_ascii_whitespace() {
            self.cursor += 1;
        }
    }
    fn take(&mut self, c: u8) -> bool {
        self.spaces();
        if self.bytes.get(self.cursor) == Some(&c) {
            self.cursor += 1;
            true
        } else {
            false
        }
    }
    fn expression(&mut self) -> Result<Decimal, String> {
        let mut n = self.term()?;
        loop {
            if self.take(b'+') {
                n = n.checked_add(self.term()?).ok_or("arithmetic overflow")?;
            } else if self.take(b'-') {
                n = n.checked_sub(self.term()?).ok_or("arithmetic overflow")?;
            } else {
                return Ok(n);
            }
        }
    }
    fn term(&mut self) -> Result<Decimal, String> {
        let mut n = self.unary()?;
        loop {
            self.spaces();
            if self.bytes.get(self.cursor) == Some(&b'*')
                && self.bytes.get(self.cursor + 1) != Some(&b'*')
            {
                self.cursor += 1;
                n = n.checked_mul(self.unary()?).ok_or("arithmetic overflow")?;
            } else if self.take(b'/') {
                let d = self.unary()?;
                if d.is_zero() {
                    return Err("division by zero".into());
                }
                n = n.checked_div(d).ok_or("arithmetic overflow")?;
            } else if self.take(b'%') {
                let d = self.unary()?;
                if d.is_zero() {
                    return Err("division by zero".into());
                }
                n = n.checked_rem(d).ok_or("arithmetic overflow")?;
            } else {
                return Ok(n);
            }
        }
    }
    fn unary(&mut self) -> Result<Decimal, String> {
        if self.take(b'+') {
            self.unary()
        } else if self.take(b'-') {
            Decimal::ZERO
                .checked_sub(self.unary()?)
                .ok_or_else(|| "arithmetic overflow".into())
        } else {
            self.power()
        }
    }
    fn power(&mut self) -> Result<Decimal, String> {
        let base = self.primary()?;
        self.spaces();
        if self.bytes.get(self.cursor) == Some(&b'*')
            && self.bytes.get(self.cursor + 1) == Some(&b'*')
        {
            self.cursor += 2;
            let exponent = self.unary()?;
            if exponent.fract() != Decimal::ZERO {
                return Err("exponent must be an integer".into());
            }
            let exp = exponent.to_i32().ok_or("exponent out of range")?;
            if exp.unsigned_abs() > 100 {
                return Err("absolute exponent must be <= 100".into());
            }
            let mut result = Decimal::ONE;
            for _ in 0..exp.unsigned_abs() {
                result = result.checked_mul(base).ok_or("arithmetic overflow")?;
            }
            if exp < 0 {
                if result.is_zero() {
                    return Err("division by zero".into());
                }
                result = Decimal::ONE
                    .checked_div(result)
                    .ok_or("arithmetic overflow")?;
            }
            Ok(result)
        } else {
            Ok(base)
        }
    }
    fn primary(&mut self) -> Result<Decimal, String> {
        self.spaces();
        self.depth += 1;
        if self.depth > 32 {
            return Err("parenthesis depth exceeded".into());
        }
        let result = if self.take(b'(') {
            let n = self.expression()?;
            if !self.take(b')') {
                return Err("missing closing parenthesis".into());
            }
            Ok(n)
        } else {
            self.spaces();
            let start = self.cursor;
            while let Some(c) = self.bytes.get(self.cursor) {
                if c.is_ascii_digit() || *c == b'.' || *c == b'_' {
                    self.cursor += 1;
                } else {
                    break;
                }
            }
            if self.cursor == start {
                return Err("numeric literal expected".into());
            }
            let raw =
                std::str::from_utf8(&self.bytes[start..self.cursor]).map_err(|e| e.to_string())?;
            Decimal::from_str(&raw.replace('_', "")).map_err(|_| "invalid decimal literal".into())
        };
        self.depth -= 1;
        result
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn calculation_contract() {
        for (source, expected) in [
            ("2+3*4", "14"),
            ("(2+3)*4", "20"),
            ("1.2+2.3", "3.5"),
            ("2**3**2", "512"),
            ("-2**2", "-4"),
            ("7%4", "3"),
            ("2**-2", "0.25"),
        ] {
            assert_eq!(
                compute(source).unwrap().normalize().to_string(),
                expected,
                "{source}"
            );
        }
    }
    #[test]
    fn invalid_calculations() {
        for s in ["", "1/0", "x+1", "2**101", "1+", "(1+2", "2**0.5"] {
            assert!(compute(s).is_err(), "{s}");
        }
    }
    #[tokio::test]
    async fn tool_schema_names() {
        let mut r = Registry::new();
        register_basics(&mut r).unwrap();
        assert_eq!(r.names(), vec!["calculator", "current_time"]);
    }
}
