use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyFloat, PyInt, PyList, PyString, PyTuple};

const MAX_BATCH: usize = 1024;
type Constructor<'py> = Bound<'py, PyAny>;
type Constructors<'py> = (
    Constructor<'py>,
    Constructor<'py>,
    Constructor<'py>,
    Constructor<'py>,
);
type PageGeometry = (f64, f64, f64, f64, f64);

fn number<'py>(value: Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    if value.cast_exact::<PyFloat>().is_ok() {
        return Ok(Some(value));
    }
    if value.cast_exact::<PyInt>().is_ok() {
        let converted = PyFloat::new(value.py(), value.extract::<f64>()?);
        return Ok(Some(converted.into_any()));
    }
    Ok(None)
}

fn numeric_tuple<'py>(values: &Bound<'py, PyTuple>) -> PyResult<Option<Bound<'py, PyTuple>>> {
    // Exact floats and their tuple are immutable, so no conversion is needed.
    if values
        .iter()
        .all(|value| value.cast_exact::<PyFloat>().is_ok())
    {
        return Ok(Some(values.clone()));
    }
    let mut converted = Vec::with_capacity(values.len());
    for value in values.iter() {
        let Some(value) = number(value)? else {
            return Ok(None);
        };
        converted.push(value);
    }
    PyTuple::new(values.py(), converted).map(Some)
}

fn color<'py>(
    raw: &Bound<'py, PyAny>,
    pattern: &Constructor<'py>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let py = raw.py();
    if raw.cast_exact::<PyString>().is_ok() {
        return pattern.call1((raw,)).map(Some);
    }
    if let Ok(values) = raw.cast_exact::<PyTuple>() {
        if values.len() > 4 {
            return Ok(None);
        }
        if values.len() == 1 {
            let name = values.get_item(0)?;
            if name.cast_exact::<PyString>().is_ok() {
                return pattern.call1((name,)).map(Some);
            }
        }
        if values.len() == 2 {
            let name = values.get_item(1)?;
            if name.cast_exact::<PyString>().is_ok() {
                let base = values.get_item(0)?;
                let components = if let Ok(base) = base.cast_exact::<PyTuple>() {
                    if base.len() > 4 {
                        return Ok(None);
                    }
                    numeric_tuple(base)?
                } else {
                    number(base)?
                        .map(|value| PyTuple::new(py, [value]))
                        .transpose()?
                };
                return components
                    .map(|values| pattern.call1((name, values)))
                    .transpose();
            }
        }
        return numeric_tuple(values).map(|value| value.map(Bound::into_any));
    }
    number(raw.clone())?
        .map(|value| PyTuple::new(py, [value]).map(Bound::into_any))
        .transpose()
}

/// Build public records from one bounded batch of audited LTChar objects.
#[pyfunction]
pub fn assemble_glyphs<'py>(
    py: Python<'py>,
    glyphs: &Bound<'py, PyList>,
    page_id: &Bound<'py, PyString>,
    number_on_document: &Bound<'py, PyInt>,
    first_index: usize,
    geometry: PageGeometry,
    constructors: Constructors<'py>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    if glyphs.len() > MAX_BATCH {
        return Ok(None);
    }
    let prefix = page_id.to_str()?;
    let (media_x, media_top, height, origin_x, origin_y) = geometry;
    let (bbox, style, fragment, pattern) = constructors;
    let result = PyList::empty(py);
    for (index, glyph) in (first_index..).zip(glyphs.iter()) {
        let font = glyph.getattr(intern!(py, "fontname"))?;
        // For audited LTChar, get_text returns this field verbatim.
        let text = glyph.getattr(intern!(py, "_text"))?;
        let upright = glyph.getattr(intern!(py, "upright"))?;
        let matrix = glyph.getattr(intern!(py, "matrix"))?;
        if font.cast_exact::<PyString>().is_err()
            || text.cast_exact::<PyString>().is_err()
            || upright.cast_exact::<PyBool>().is_err()
        {
            return Ok(None);
        }
        let Ok(matrix) = matrix.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        if matrix.len() != 6 {
            return Ok(None);
        }
        let Some(matrix) = numeric_tuple(matrix)? else {
            return Ok(None);
        };
        let Some(size) = number(glyph.getattr(intern!(py, "size"))?)? else {
            return Ok(None);
        };
        let Some(advance) = number(glyph.getattr(intern!(py, "adv"))?)? else {
            return Ok(None);
        };
        let x0 = glyph.getattr(intern!(py, "x0"))?;
        let x1 = glyph.getattr(intern!(py, "x1"))?;
        let y0 = glyph.getattr(intern!(py, "y0"))?;
        let y1 = glyph.getattr(intern!(py, "y1"))?;
        let (Ok(x0), Ok(x1), Ok(y0), Ok(y1)) = (
            x0.cast_exact::<PyFloat>(),
            x1.cast_exact::<PyFloat>(),
            y0.cast_exact::<PyFloat>(),
            y1.cast_exact::<PyFloat>(),
        ) else {
            return Ok(None);
        };
        // Match pdfplumber's projection and _bbox rebasing in that order.
        let page_x = |x| if media_x == 0.0 { x } else { x + media_x };
        let page_y = |y| (height - y) + media_top;
        let bbox = bbox.call1((
            page_x(x0.value()) - origin_x,
            page_y(y1.value()) - origin_y,
            page_x(x1.value()) - origin_x,
            page_y(y0.value()) - origin_y,
        ))?;
        let state = glyph.getattr(intern!(py, "graphicstate"))?;
        let Some(stroke) = color(&state.getattr(intern!(py, "scolor"))?, &pattern)? else {
            return Ok(None);
        };
        let Some(fill) = color(&state.getattr(intern!(py, "ncolor"))?, &pattern)? else {
            return Ok(None);
        };
        let style = style.call1((font, size, upright, stroke, fill))?;
        result.append(fragment.call1((
            format!("{prefix}:c{index:08}"),
            page_id,
            number_on_document,
            index,
            text,
            bbox,
            style,
            matrix,
            advance,
        ))?)?;
    }
    Ok(Some(result))
}
