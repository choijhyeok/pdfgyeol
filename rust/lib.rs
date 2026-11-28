use std::borrow::Cow;
use std::collections::{hash_map::Entry, HashMap};
use std::str;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyString, PyTuple};

mod glyphs;
mod lines;
mod text;
mod vector;

const MAX_STREAM_BYTES: usize = 1024 * 1024;
const MAX_TOKENS: usize = 250_000;
const MAX_DEPTH: usize = 32;
const PDFMINER_BUFFER_BYTES: usize = 4096;

enum ParseError {
    Unsupported,
    Python(PyErr),
}

impl From<PyErr> for ParseError {
    fn from(error: PyErr) -> Self {
        Self::Python(error)
    }
}

type ParseResult<T> = Result<T, ParseError>;

enum Token<'data, 'py> {
    Operand(Bound<'py, PyAny>),
    Keyword(&'data [u8]),
}

struct ContentParser<'data, 'py> {
    data: &'data [u8],
    pos: usize,
    tokens: usize,
    py: Python<'py>,
    literal: &'data Bound<'py, PyAny>,
    keyword: &'data Bound<'py, PyAny>,
    literals: HashMap<Cow<'data, [u8]>, Bound<'py, PyAny>>,
    operators: HashMap<&'data [u8], Bound<'py, PyString>>,
}

fn white(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

fn delimiter(byte: u8) -> bool {
    white(byte) || b"#/%[]()<>{}".contains(&byte)
}

fn nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

impl<'data, 'py> ContentParser<'data, 'py> {
    fn new(
        py: Python<'py>,
        data: &'data [u8],
        literal: &'data Bound<'py, PyAny>,
        keyword: &'data Bound<'py, PyAny>,
    ) -> Self {
        Self {
            data,
            pos: 0,
            tokens: 0,
            py,
            literal,
            keyword,
            literals: HashMap::new(),
            operators: HashMap::new(),
        }
    }

    fn peek(&self) -> Option<u8> {
        self.data.get(self.pos).copied()
    }

    fn starts_with(&self, prefix: &[u8]) -> bool {
        self.data[self.pos..].starts_with(prefix)
    }

    fn skip_space(&mut self) {
        while let Some(byte) = self.peek() {
            match byte {
                b'%' => {
                    while self.peek().is_some_and(|c| !matches!(c, b'\n' | b'\r')) {
                        self.pos += 1;
                    }
                }
                0 => self.pos += 1,
                byte if white(byte) => self.pos += 1,
                _ => break,
            }
        }
    }

    fn count_token(&mut self) -> ParseResult<()> {
        self.tokens += 1;
        if self.tokens > MAX_TOKENS {
            return Err(ParseError::Unsupported);
        }
        Ok(())
    }

    fn token(&mut self, depth: usize) -> ParseResult<Token<'data, 'py>> {
        self.count_token()?;
        if depth > MAX_DEPTH {
            return Err(ParseError::Unsupported);
        }
        let start = self.pos;
        let byte = self.peek().ok_or(ParseError::Unsupported)?;
        let operand = match byte {
            b'/' => self.literal_name()?,
            b'(' => self.string()?,
            b'<' if self.starts_with(b"<<") => self.dict(depth)?,
            b'<' => self.hex_string()?,
            b'[' => self.array(depth)?,
            b'+' | b'-' | b'.' | b'0'..=b'9' => self.number()?,
            b'\'' | b'"' => {
                self.pos += 1;
                return Ok(Token::Keyword(&self.data[start..self.pos]));
            }
            byte if byte.is_ascii_alphabetic() => {
                while self.peek().is_some_and(|c| !delimiter(c)) {
                    self.pos += 1;
                }
                let word = &self.data[start..self.pos];
                match word {
                    b"true" => true.into_pyobject(self.py).unwrap().to_owned().into_any(),
                    b"false" => false.into_pyobject(self.py).unwrap().to_owned().into_any(),
                    // pdfminer's BI and ID can consume arbitrary bytes and seek.
                    b"BI" | b"ID" => return Err(ParseError::Unsupported),
                    word if word.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'*') => {
                        return Ok(Token::Keyword(word));
                    }
                    _ => return Err(ParseError::Unsupported),
                }
            }
            _ => return Err(ParseError::Unsupported),
        };
        Ok(Token::Operand(operand))
    }

    fn operand(&mut self, depth: usize) -> ParseResult<Bound<'py, PyAny>> {
        self.skip_space();
        match self.token(depth)? {
            Token::Operand(value) => Ok(value),
            Token::Keyword(word) => Ok(self.keyword.call1((PyBytes::new(self.py, word),))?),
        }
    }

    fn name_bytes(&mut self) -> ParseResult<Cow<'data, [u8]>> {
        self.pos += 1;
        let start = self.pos;
        while self.peek().is_some_and(|byte| !delimiter(byte)) {
            self.pos += 1;
        }
        let initial = &self.data[start..self.pos];
        if self.peek() != Some(b'#') {
            return Ok(Cow::Borrowed(initial));
        }

        let mut decoded = initial.to_vec();
        while let Some(byte) = self.peek() {
            if byte == b'#' {
                let hex = self.data.get(self.pos + 1..self.pos + 3);
                let Some((high, low)) = hex.and_then(|pair| nibble(pair[0]).zip(nibble(pair[1])))
                else {
                    return Err(ParseError::Unsupported);
                };
                decoded.push(high * 16 + low);
                self.pos += 3;
            } else if delimiter(byte) {
                break;
            } else {
                decoded.push(byte);
                self.pos += 1;
            }
        }
        if self.pos == self.data.len() {
            // At EOF pdfminer uses one injected newline to advance its parser.
            // Defer to it when a final name includes a hex escape.
            return Err(ParseError::Unsupported);
        }
        Ok(Cow::Owned(decoded))
    }

    fn literal_name(&mut self) -> ParseResult<Bound<'py, PyAny>> {
        let name = self.name_bytes()?;
        if let Some(cached) = self.literals.get(name.as_ref()) {
            return Ok(cached.clone());
        }
        let arg = match str::from_utf8(&name) {
            Ok(text) => PyString::new(self.py, text).into_any(),
            Err(_) => PyBytes::new(self.py, &name).into_any(),
        };
        let literal = self.literal.call1((arg,))?;
        self.literals.insert(name, literal.clone());
        Ok(literal)
    }

    fn number(&mut self) -> ParseResult<Bound<'py, PyAny>> {
        let start = self.pos;
        if matches!(self.peek(), Some(b'+' | b'-')) {
            self.pos += 1;
        }
        while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
            self.pos += 1;
        }
        let decimal = self.peek() == Some(b'.');
        if decimal {
            self.pos += 1;
            while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                self.pos += 1;
            }
        }
        if self.pos - start > 64 || self.peek().is_some_and(|byte| !delimiter(byte)) {
            return Err(ParseError::Unsupported);
        }
        let text =
            str::from_utf8(&self.data[start..self.pos]).map_err(|_| ParseError::Unsupported)?;
        if decimal {
            let value = text.parse::<f64>().map_err(|_| ParseError::Unsupported)?;
            Ok(value.into_pyobject(self.py).unwrap().into_any())
        } else {
            let value = text.parse::<i64>().map_err(|_| ParseError::Unsupported)?;
            Ok(value.into_pyobject(self.py).unwrap().into_any())
        }
    }

    fn string(&mut self) -> ParseResult<Bound<'py, PyAny>> {
        self.pos += 1;
        let mut start = self.pos;
        let mut nesting = 1;
        let mut decoded = Vec::new();
        while let Some(byte) = self.peek() {
            let index = self.pos;
            self.pos += 1;
            match byte {
                b'(' => nesting += 1,
                b')' => {
                    nesting -= 1;
                    if nesting == 0 {
                        let tail = &self.data[start..index];
                        if decoded.is_empty() {
                            return Ok(PyBytes::new(self.py, tail).into_any());
                        }
                        decoded.extend_from_slice(tail);
                        return Ok(PyBytes::new(self.py, &decoded).into_any());
                    }
                }
                b'\\' => {
                    decoded.extend_from_slice(&self.data[start..index]);
                    self.string_escape(&mut decoded)?;
                    start = self.pos;
                }
                _ => {}
            }
        }
        Err(ParseError::Unsupported)
    }

    fn string_escape(&mut self, decoded: &mut Vec<u8>) -> ParseResult<()> {
        let byte = self.peek().ok_or(ParseError::Unsupported)?;
        if matches!(byte, b'0'..=b'7') {
            let mut code = 0;
            for _ in 0..3 {
                let Some(digit @ b'0'..=b'7') = self.peek() else {
                    break;
                };
                code = code * 8 + u16::from(digit - b'0');
                self.pos += 1;
            }
            decoded.push(u8::try_from(code).map_err(|_| ParseError::Unsupported)?);
            return Ok(());
        }
        self.pos += 1;
        match byte {
            b'b' => decoded.push(8),
            b't' => decoded.push(b'\t'),
            b'n' => decoded.push(b'\n'),
            b'f' => decoded.push(12),
            b'r' => decoded.push(b'\r'),
            b'(' | b')' | b'\\' => decoded.push(byte),
            // pdfminer can only see the LF when it's in the current buffer.
            b'\r' if self.pos % PDFMINER_BUFFER_BYTES != 0 && self.peek() == Some(b'\n') => {
                self.pos += 1;
            }
            // pdfminer drops unknown escapes, including bare line endings.
            _ => {}
        }
        Ok(())
    }

    fn hex_string(&mut self) -> ParseResult<Bound<'py, PyAny>> {
        self.pos += 1;
        let mut decoded = Vec::new();
        let mut high = None;
        while let Some(byte) = self.peek() {
            self.pos += 1;
            if byte == b'>' {
                // pdfminer handles a final unpaired digit as the low nibble.
                decoded.extend(high);
                return Ok(PyBytes::new(self.py, &decoded).into_any());
            }
            if white(byte) {
                continue;
            }
            let digit = nibble(byte).ok_or(ParseError::Unsupported)?;
            if let Some(first) = high.take() {
                decoded.push(first * 16 + digit);
            } else {
                high = Some(digit);
            }
        }
        Err(ParseError::Unsupported)
    }

    fn array(&mut self, depth: usize) -> ParseResult<Bound<'py, PyAny>> {
        self.pos += 1;
        let values = PyList::empty(self.py);
        loop {
            self.skip_space();
            if self.peek() == Some(b']') {
                self.pos += 1;
                return Ok(values.into_any());
            }
            values.append(self.operand(depth + 1)?)?;
        }
    }

    fn dict(&mut self, depth: usize) -> ParseResult<Bound<'py, PyAny>> {
        self.pos += 2;
        let dict = PyDict::new(self.py);
        loop {
            self.skip_space();
            if self.starts_with(b">>") {
                self.pos += 2;
                return Ok(dict.into_any());
            }
            if self.peek() != Some(b'/') {
                return Err(ParseError::Unsupported);
            }
            self.count_token()?;
            let name = self.name_bytes()?;
            let key = str::from_utf8(&name).map_err(|_| ParseError::Unsupported)?;
            dict.set_item(key, self.operand(depth + 1)?)?;
        }
    }

    fn parse(mut self) -> ParseResult<Bound<'py, PyList>> {
        let ops = PyList::empty(self.py);
        let mut args = PyList::empty(self.py);
        loop {
            self.skip_space();
            if self.pos == self.data.len() {
                if !args.is_empty() {
                    ops.append((args, self.py.None()))?;
                }
                return Ok(ops);
            }
            match self.token(0)? {
                Token::Operand(value) => args.append(value)?,
                Token::Keyword(word) => {
                    let operator = self.operators.entry(word).or_insert_with(|| {
                        PyString::new(self.py, str::from_utf8(word).expect("ASCII keyword"))
                    });
                    ops.append((args, &*operator))?;
                    args = PyList::empty(self.py);
                }
            }
        }
    }

    fn prepare_dispatch(
        mut self,
        interpreter: &Bound<'py, PyAny>,
        operators: &Bound<'py, PyDict>,
    ) -> ParseResult<ContentDispatch> {
        let mut methods = HashMap::new();
        let mut instructions = Vec::new();
        let mut args = vec![interpreter.clone()];
        let no_operands = PyTuple::new(self.py, [interpreter])?;
        loop {
            self.skip_space();
            if self.pos == self.data.len() {
                if args.len() != 1 {
                    return Err(ParseError::Unsupported);
                }
                return Ok(ContentDispatch { instructions });
            }
            match self.token(0)? {
                Token::Operand(value) => args.push(value),
                Token::Keyword(word) => {
                    let (method, arity) = match methods.entry(word) {
                        Entry::Occupied(entry) => entry.into_mut(),
                        Entry::Vacant(entry) => {
                            let name = str::from_utf8(word).expect("ASCII keyword");
                            let operator =
                                operators.get_item(name)?.ok_or(ParseError::Unsupported)?;
                            entry.insert(operator.extract::<(Bound<'py, PyAny>, usize)>()?)
                        }
                    };
                    if args.len() - 1 != *arity {
                        return Err(ParseError::Unsupported);
                    }
                    let args = if *arity == 0 {
                        no_operands.clone()
                    } else {
                        let tuple = PyTuple::new(self.py, args.drain(..))?;
                        args.push(interpreter.clone());
                        tuple
                    };
                    instructions.push((method.clone().unbind(), args.unbind()));
                }
            }
        }
    }
}

/// Lex a bounded PDF content stream into (operands, operator) batches.
/// None leaves the entire stream to the original pdfminer parser.
#[pyfunction]
fn parse_content<'py>(
    py: Python<'py>,
    data: &[u8],
    literal: &Bound<'py, PyAny>,
    keyword: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    if data.len() > MAX_STREAM_BYTES {
        return Ok(None);
    }
    match ContentParser::new(py, data, literal, keyword).parse() {
        Ok(ops) => Ok(Some(ops)),
        Err(ParseError::Unsupported) => Ok(None),
        Err(ParseError::Python(error)) => Err(error),
    }
}

#[pyclass(module = "pdfgyeol._native")]
struct ContentDispatch {
    instructions: Vec<(Py<PyAny>, Py<PyTuple>)>,
}

#[pymethods]
impl ContentDispatch {
    fn __len__(&self) -> usize {
        self.instructions.len()
    }

    fn run(&mut self, py: Python<'_>) -> PyResult<()> {
        for (method, args) in self.instructions.drain(..) {
            method.bind(py).call1(args.into_bound(py))?;
        }
        Ok(())
    }
}

/// Resolve an exact-arity stream before invoking any audited handlers.
#[pyfunction]
fn prepare_dispatch(
    py: Python<'_>,
    data: &[u8],
    literal: &Bound<'_, PyAny>,
    keyword: &Bound<'_, PyAny>,
    interpreter: &Bound<'_, PyAny>,
    operators: &Bound<'_, PyDict>,
) -> PyResult<Option<ContentDispatch>> {
    if data.len() > MAX_STREAM_BYTES {
        return Ok(None);
    }
    match ContentParser::new(py, data, literal, keyword).prepare_dispatch(interpreter, operators) {
        Ok(dispatch) => Ok(Some(dispatch)),
        Err(ParseError::Unsupported) => Ok(None),
        Err(ParseError::Python(error)) => Err(error),
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<ContentDispatch>()?;
    module.add_function(wrap_pyfunction!(parse_content, module)?)?;
    module.add_function(wrap_pyfunction!(prepare_dispatch, module)?)?;
    module.add_function(wrap_pyfunction!(text::horizontal_glyphs, module)?)?;
    module.add_function(wrap_pyfunction!(text::identity_cached_glyphs, module)?)?;
    module.add_function(wrap_pyfunction!(glyphs::assemble_glyphs, module)?)?;
    module.add_function(wrap_pyfunction!(lines::assemble_lines, module)?)?;
    module.add_function(wrap_pyfunction!(vector::straight_line, module)?)
}
