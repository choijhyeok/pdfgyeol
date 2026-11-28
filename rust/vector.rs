use pyo3::prelude::*;
use pyo3::types::{PyFloat, PyTuple};

type Point = (f64, f64);
type Matrix = (f64, f64, f64, f64, f64, f64);
type Bounds = (f64, f64, f64, f64);
type StraightLine = (Point, Point, Bounds, f64, f64);

// pdfminer's get_bound starts with these finite sentinels. Defer coordinates
// at or beyond them to retain its clipping and exact numeric types.
const BOUND_LIMIT: f64 = i32::MAX as f64;

fn point(segment: &Bound<'_, PyTuple>) -> Option<Point> {
    let x = segment.get_item(1).ok()?;
    let y = segment.get_item(2).ok()?;
    Some((
        x.cast_exact::<PyFloat>().ok()?.value(),
        y.cast_exact::<PyFloat>().ok()?.value(),
    ))
}

fn bounds(first: f64, last: f64) -> (f64, f64) {
    // get_bound retains its first point on ties, including signed zero.
    (
        if last < first { last } else { first },
        if last > first { last } else { first },
    )
}

/// Project both endpoints and compute the bounds of one line before painting it.
#[pyfunction]
pub fn straight_line(
    matrix: Matrix,
    start: &Bound<'_, PyTuple>,
    end: &Bound<'_, PyTuple>,
) -> Option<StraightLine> {
    let (a, b, c, d, e, f) = matrix;
    // Keep Python's multiply/add order. Both operands from m/l are floats.
    let project = |(x, y)| (a * x + c * y + e, b * x + d * y + f);
    let first = project(point(start)?);
    let last = project(point(end)?);
    if ![first.0, first.1, last.0, last.1]
        .iter()
        .all(|value| value.abs() < BOUND_LIMIT)
    {
        return None;
    }
    let (x0, x1) = bounds(first.0, last.0);
    let (y0, y1) = bounds(first.1, last.1);
    Some((first, last, (x0, y0, x1, y1), x1 - x0, y1 - y0))
}
