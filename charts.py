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


def line_svg(values, width=880, height=200, pad=28,
             pos_color="#5fae6e", neg_color="#c0564f", grid_color="#2c313c"):
    """values: y-values in x-axis order (already cumulative if that's the
    intent). Renders a line + soft fill area against a dashed zero baseline,
    colored by whether the series ends above or below zero."""
    if not values:
        return None

    n = len(values)
    y_min = min(0.0, min(values))
    y_max = max(0.0, max(values))
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    y_range = y_max - y_min

    def x_of(i):
        return pad if n == 1 else pad + (i / (n - 1)) * (width - 2 * pad)

    def y_of(v):
        return pad + (1 - (v - y_min) / y_range) * (height - 2 * pad)

    zero_y = y_of(0.0)
    points = [(x_of(i), y_of(v)) for i, v in enumerate(values)]
    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_d = path_d + f" L {points[-1][0]:.1f},{zero_y:.1f} L {points[0][0]:.1f},{zero_y:.1f} Z"

    color = pos_color if values[-1] >= 0 else neg_color

    return (
        f'<svg viewBox="0 0 {width} {height}" class="line-chart" preserveAspectRatio="none">'
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" '
        f'stroke="{grid_color}" stroke-width="1" stroke-dasharray="3,3" />'
        f'<path d="{area_d}" fill="{color}" opacity="0.14" stroke="none" />'
        f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" />'
        f'</svg>'
    )
