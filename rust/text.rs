use pyo3::prelude::*;
use pyo3::types::{PyFloat, PyInt, PyList, PyString, PyTuple};

const MAX_RUN_ITEMS: usize = 4096;

#[derive(Clone, Copy)]
struct Number {
    value: f64,
    integer: bool,
}

impl Number {
    fn from_python(value: &Bound<'_, PyAny>) -> Option<Self> {
        if let Ok(value) = value.cast_exact::<PyFloat>() {
            let value = value.value();
            return value.is_finite().then_some(Self {
                value,
                integer: false,
            });
        }
        // Integral coordinates remain exact when converted to doubles. An
        // integral starting position is further restricted to zero below.
        let value: i32 = value.cast_exact::<PyInt>().ok()?.extract().ok()?;
        Some(Self {
            value: f64::from(value),
            integer: true,
        })
    }

    fn times_zero(self, integer: bool) -> f64 {
        if integer && self.integer {
            0.0
        } else {
            self.value * 0.0
        }
    }
}

fn numbers<const N: usize>(values: &Bound<'_, PyTuple>) -> Option<[Number; N]> {
    if values.len() != N {
        return None;
    }
    let mut result = [Number {
        value: 0.0,
        integer: false,
    }; N];
    for (value, output) in values.iter().zip(&mut result) {
        *output = Number::from_python(&value)?;
    }
    Some(result)
}

// Python min/max retain the first operand on ties, including signed zero.
fn bounds(values: [f64; 4]) -> (f64, f64) {
    let mut low = values[0];
    let mut high = low;
    for value in &values[1..] {
        if *value < low {
            low = *value;
        }
        if *value > high {
            high = *value;
        }
    }
    (low, high)
}

/// Lay out one horizontal text-show operation without mutating its device.
/// Items are either (glyph width, Unicode text, word-space flag) or TJ offsets.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn horizontal_glyphs<'py>(
    py: Python<'py>,
    items: &Bound<'py, PyList>,
    matrix: &Bound<'py, PyTuple>,
    pos: &Bound<'py, PyTuple>,
    fontsize: f64,
    scaling: f64,
    charspace: f64,
    wordspace: f64,
    rise: f64,
    dxscale: f64,
    descent: f64,
) -> PyResult<Option<(f64, bool, Bound<'py, PyList>)>> {
    if items.is_empty()
        || items.len() > MAX_RUN_ITEMS
        || ![
            fontsize, scaling, charspace, wordspace, rise, dxscale, descent,
        ]
        .iter()
        .all(|n| n.is_finite())
    {
        return Ok(None);
    }
    let Some([a, b, c, d, e, f]) = numbers(matrix) else {
        return Ok(None);
    };
    let Some([start_x, y]) = numbers(pos) else {
        return Ok(None);
    };
    if (start_x.integer && start_x.value != 0.0) || (y.integer && y.value != 0.0) {
        return Ok(None);
    }
    let y_c = if y.integer {
        c.times_zero(true)
    } else {
        y.value * c.value
    };
    let y_d = if y.integer {
        d.times_zero(true)
    } else {
        y.value * d.value
    };
    let lower = descent * fontsize + rise;
    let upper = lower + fontsize;
    let upright = a.value * d.value * scaling > 0.0 && b.value * c.value <= 0.0;
    let mut x = start_x.value;
    let mut integer_x = start_x.integer;
    let mut needs_spacing = false;
    let glyphs = PyList::empty(py);
    for item in items.iter() {
        if let Ok(glyph) = item.cast_exact::<PyTuple>() {
            let (width, text, space): (f64, Bound<'_, PyString>, bool) = glyph.extract()?;
            if needs_spacing {
                x += charspace;
                integer_x = false;
            }
            let x_a = if integer_x {
                a.times_zero(true)
            } else {
                x * a.value
            };
            let x_b = if integer_x {
                b.times_zero(true)
            } else {
                x * b.value
            };
            let tx = x_a + y_c + e.value;
            let ty = x_b + y_d + f.value;
            let advance = width * fontsize * scaling;

            // Match pdfminer's corner order and multiply/add order.
            let left = a.times_zero(true);
            let right = a.value * advance;
            let bottom = b.times_zero(true);
            let top = b.value * advance;
            let xs = [
                left + c.value * lower + tx,
                left + c.value * upper + tx,
                right + c.value * lower + tx,
                right + c.value * upper + tx,
            ];
            let ys = [
                bottom + d.value * lower + ty,
                top + d.value * lower + ty,
                top + d.value * upper + ty,
                bottom + d.value * upper + ty,
            ];
            if !xs.iter().chain(&ys).all(|n| n.is_finite()) || !advance.is_finite() {
                return Ok(None);
            }
            let (x0, x1) = bounds(xs);
            let (y0, y1) = bounds(ys);
            let width = x1 - x0;
            let height = y1 - y0;
            if !width.is_finite() || !height.is_finite() {
                return Ok(None);
            }
            glyphs.append((text, tx, ty, advance, (x0, y0, x1, y1), width, height))?;
            x += advance;
            if space && wordspace != 0.0 {
                x += wordspace;
            }
        } else {
            let Some(offset) = Number::from_python(&item) else {
                return Ok(None);
            };
            x -= offset.value * dxscale;
        }
        integer_x = false;
        needs_spacing = true;
        if !x.is_finite() {
            return Ok(None);
        }
    }
    Ok(Some((x, upright, glyphs)))
}
