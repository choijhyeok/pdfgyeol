use crate::glyphs::{color, number, numeric_tuple};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyFloat, PyInt, PyList, PyString, PyTuple};

const MAX_BATCH: usize = 1024;
const MAX_DASH: usize = 32;
type Point = (f64, f64);
type Geometry = (f64, f64, f64, f64, f64);
type Constructors<'py> = (
    Bound<'py, PyAny>,
    Bound<'py, PyAny>,
    Bound<'py, PyAny>,
    Bound<'py, PyAny>,
);

fn exact_float(value: &Bound<'_, PyAny>) -> Option<f64> {
    Some(value.cast_exact::<PyFloat>().ok()?.value())
}

fn point(item: &Bound<'_, PyAny>) -> Option<Point> {
    let tuple = item.cast_exact::<PyTuple>().ok()?;
    if tuple.len() != 2 {
        return None;
    }
    Some((
        exact_float(&tuple.get_item(0).ok()?)?,
        exact_float(&tuple.get_item(1).ok()?)?,
    ))
}

fn dash<'py>(value: Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    if value.is_none() {
        return Ok(Some(value));
    }
    let Ok(pair) = value.cast_exact::<PyTuple>() else {
        return Ok(None);
    };
    if pair.len() != 2 {
        return Ok(None);
    }
    let sequence = pair.get_item(0)?;
    let numbers = if let Ok(tuple) = sequence.cast_exact::<PyTuple>() {
        if tuple.len() > MAX_DASH {
            return Ok(None);
        }
        numeric_tuple(tuple)?
    } else if let Ok(list) = sequence.cast_exact::<PyList>() {
        if list.len() > MAX_DASH {
            return Ok(None);
        }
        let mut values = Vec::with_capacity(list.len());
        for item in list.iter() {
            let Some(item) = number(item)? else {
                return Ok(None);
            };
            values.push(item);
        }
        Some(PyTuple::new(value.py(), values)?)
    } else {
        return Ok(None);
    };
    let (Some(numbers), Some(phase)) = (numbers, number(pair.get_item(1)?)?) else {
        return Ok(None);
    };
    (numbers, phase)
        .into_pyobject(value.py())
        .map(|result| Some(result.into_any()))
}

fn line_color<'py>(
    raw: Bound<'py, PyAny>,
    pattern: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if raw.is_none() {
        Ok(Some(raw))
    } else {
        color(&raw, pattern)
    }
}

/// Project audited LTLine objects directly into the public source records.
#[pyfunction]
pub fn assemble_lines<'py>(
    py: Python<'py>,
    lines: &Bound<'py, PyList>,
    page_id: &Bound<'py, PyString>,
    number_on_document: &Bound<'py, PyInt>,
    first_index: usize,
    geometry: Geometry,
    constructors: Constructors<'py>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    if lines.len() > MAX_BATCH {
        return Ok(None);
    }
    let prefix = page_id.to_str()?;
    let (media_x, media_top, height, origin_x, origin_y) = geometry;
    let (bbox, segment, evidence, pattern) = constructors;
    // pdfplumber uses different arithmetic for path points and box edges.
    let project = |(x, y)| {
        (
            (media_x + x) - origin_x,
            ((media_top + height) - y) - origin_y,
        )
    };
    let bbox_x = |x| (if media_x == 0.0 { x } else { x + media_x }) - origin_x;
    let bbox_y = |y| ((height - y) + media_top) - origin_y;
    let kind = intern!(py, "line");
    let result = PyList::empty(py);

    for (index, line) in (first_index..).zip(lines.iter()) {
        let (Some(x0), Some(x1), Some(y0), Some(y1)) = (
            exact_float(&line.getattr(intern!(py, "x0"))?),
            exact_float(&line.getattr(intern!(py, "x1"))?),
            exact_float(&line.getattr(intern!(py, "y0"))?),
            exact_float(&line.getattr(intern!(py, "y1"))?),
        ) else {
            return Ok(None);
        };
        let bounds = bbox.call1((bbox_x(x0), bbox_y(y1), bbox_x(x1), bbox_y(y0)))?;
        let points = line.getattr(intern!(py, "pts"))?;
        let Ok(points) = points.cast_exact::<PyList>() else {
            return Ok(None);
        };
        if points.len() != 2 {
            return Ok(None);
        }
        let raw_first = points.get_item(0)?;
        let raw_last = points.get_item(1)?;
        let (Some(first), Some(last)) = (point(&raw_first), point(&raw_last)) else {
            return Ok(None);
        };
        let a = project(first).into_pyobject(py)?;
        let b = project(last).into_pyobject(py)?;
        let points = (&a, &b).into_pyobject(py)?;

        let path = line.getattr(intern!(py, "original_path"))?;
        let Ok(path) = path.cast_exact::<PyList>() else {
            return Ok(None);
        };
        // The stock painter can keep a redundant closing line in original_path.
        if !(2..=4).contains(&path.len()) {
            return Ok(None);
        }
        let mut segments = Vec::with_capacity(path.len());
        for entry in path.iter() {
            let Ok(entry) = entry.cast_exact::<PyTuple>() else {
                return Ok(None);
            };
            let operator = entry.get_item(0)?;
            let Ok(operator) = operator.cast_exact::<PyString>() else {
                return Ok(None);
            };
            let coordinates = match (operator.to_str()?, entry.len()) {
                ("h", 1) => PyTuple::empty(py),
                ("m" | "l", 2) => {
                    let raw = entry.get_item(1)?;
                    let lifted = if raw.is(&raw_first) {
                        a.clone()
                    } else if raw.is(&raw_last) {
                        b.clone()
                    } else {
                        let Some(coordinates) = point(&raw) else {
                            return Ok(None);
                        };
                        project(coordinates).into_pyobject(py)?
                    };
                    (lifted,).into_pyobject(py)?
                }
                _ => return Ok(None),
            };
            segments.push(segment.call1((operator, coordinates))?);
        }
        let path = PyTuple::new(py, segments)?;

        let stroke = line.getattr(intern!(py, "stroke"))?;
        let fill = line.getattr(intern!(py, "fill"))?;
        let even_odd = line.getattr(intern!(py, "evenodd"))?;
        if [&stroke, &fill, &even_odd]
            .iter()
            .any(|flag| flag.cast_exact::<PyBool>().is_err())
        {
            return Ok(None);
        }
        let Some(width) = number(line.getattr(intern!(py, "linewidth"))?)? else {
            return Ok(None);
        };
        let Some(stroking) = line_color(line.getattr(intern!(py, "stroking_color"))?, &pattern)?
        else {
            return Ok(None);
        };
        let Some(non_stroking) =
            line_color(line.getattr(intern!(py, "non_stroking_color"))?, &pattern)?
        else {
            return Ok(None);
        };
        let Some(dash) = dash(line.getattr(intern!(py, "dashing_style"))?)? else {
            return Ok(None);
        };
        let args = PyTuple::new(
            py,
            [
                PyString::new(py, &format!("{prefix}:line{index:08}")).into_any(),
                page_id.clone().into_any(),
                number_on_document.clone().into_any(),
                index.into_pyobject(py)?.into_any(),
                kind.clone().into_any(),
                bounds,
                points.into_any(),
                path.into_any(),
                width,
                stroke,
                fill,
                even_odd,
                stroking,
                non_stroking,
                dash,
            ],
        )?;
        result.append(evidence.call1(args)?)?;
    }
    Ok(Some(result))
}
