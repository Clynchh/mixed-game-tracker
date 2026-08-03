"""Tiny inline SVG line-chart renderer.

No JS, no CDN - MixTrack never makes a network request, so pulling in a
charting library would break that. A cumulative line + zero baseline is
all a bankroll graph needs, and it's cheap to build directly as SVG.
"""


def cumulative(values):
    total = 0.0
    out = []
    for v in values:
        total += v
        out.append(total)
    return out


def multi_line_svg(main_values, overlays=(), width=880, height=200, pad=28,
                    pos_color="#5fae6e", neg_color="#c0564f", grid_color="#2c313c"):
    """main_values: primary series - gets the soft area fill and is drawn on
    top, colored by whether it ends above or below zero. overlays: list of
    (values, color) tuples, same length as main_values, drawn underneath as
    plain lines sharing the same y-scale (so they're directly comparable)."""
    if not main_values:
        return None

    n = len(main_values)
    all_vals = list(main_values)
    for values, _ in overlays:
        all_vals.extend(values)
    y_min = min(0.0, min(all_vals))
    y_max = max(0.0, max(all_vals))
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    y_range = y_max - y_min

    def x_of(i):
        return pad if n == 1 else pad + (i / (n - 1)) * (width - 2 * pad)

    def y_of(v):
        return pad + (1 - (v - y_min) / y_range) * (height - 2 * pad)

    zero_y = y_of(0.0)

    def path_for(values):
        points = [(x_of(i), y_of(v)) for i, v in enumerate(values)]
        return "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points), points

    main_path, main_points = path_for(main_values)
    area_d = main_path + f" L {main_points[-1][0]:.1f},{zero_y:.1f} L {main_points[0][0]:.1f},{zero_y:.1f} Z"
    main_color = pos_color if main_values[-1] >= 0 else neg_color

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="line-chart" preserveAspectRatio="none">',
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" '
        f'stroke="{grid_color}" stroke-width="1" stroke-dasharray="3,3" />',
        f'<path d="{area_d}" fill="{main_color}" opacity="0.14" stroke="none" />',
    ]
    for values, color in overlays:
        path_d, _ = path_for(values)
        parts.append(f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="1.5" stroke-opacity="0.85" />')
    parts.append(f'<path d="{main_path}" fill="none" stroke="{main_color}" stroke-width="2" />')
    parts.append('</svg>')
    return "".join(parts)


def line_svg(values, **kwargs):
    return multi_line_svg(values, overlays=(), **kwargs)
