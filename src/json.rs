//! Parse JSON without silently keeping the last duplicate key.
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use std::fmt;

struct Unique(Value);

impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Values;
        impl<'de> Visitor<'de> for Values {
            type Value = Unique;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON value with unique object keys")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Unique, E> {
                Number::from_f64(v)
                    .map(|n| Unique(n.into()))
                    .ok_or_else(|| E::custom("non-finite number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Unique, A::Error> {
                let mut values = Vec::new();
                while let Some(Unique(v)) = a.next_element()? {
                    values.push(v);
                }
                Ok(Unique(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Unique, A::Error> {
                let mut values = Map::new();
                while let Some((key, Unique(v))) = a.next_entry::<String, Unique>()? {
                    if values.insert(key, v).is_some() {
                        return Err(de::Error::custom("duplicate JSON key"));
                    }
                }
                Ok(Unique(Value::Object(values)))
            }
        }
        d.deserialize_any(Values)
    }
}

pub fn parse(text: &str) -> Result<Value, String> {
    serde_json::from_str::<Unique>(text)
        .map(|v| v.0)
        .map_err(|e| e.to_string())
}
