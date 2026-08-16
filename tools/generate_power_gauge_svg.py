import math
import sys
import os

def generate_gauge_svg(variant="energy"):
    """
    Generate pixel-perfect light-skeuomorphic SVG matching user's exact power meter reference.
    variant: 'energy' (Warm-to-red gradient), 'contrast' (Ivory/Slate + Red pointer), 'laser_lab' (Titanium + Laser Ray)
    """
    W, H = 1024, 1024
    cx, cy = 512, 510
    r_out = 370
    r_in = 220
    r_pivot = 82
    needle_angle_deg = 32.0  # degrees above positive x-axis
    needle_len = 345
    
    gap = 3.6
    seg_span = (180.0 - 4 * gap) / 5.0  # ~33.12 deg
    
    def polar(deg, r):
        rad = math.radians(deg)
        return cx + r * math.cos(rad), cy - r * math.sin(rad)
        
    def segment_d(start_a, end_a, ri, ro):
        x1_o, y1_o = polar(start_a, ro)
        x2_o, y2_o = polar(end_a, ro)
        x2_i, y2_i = polar(end_a, ri)
        x1_i, y1_i = polar(start_a, ri)
        return (f"M {x1_o:.2f} {y1_o:.2f} "
                f"A {ro:.2f} {ro:.2f} 0 0 1 {x2_o:.2f} {y2_o:.2f} "
                f"L {x2_i:.2f} {y2_i:.2f} "
                f"A {ri:.2f} {ri:.2f} 0 0 0 {x1_i:.2f} {y1_i:.2f} Z")

    segments = []
    curr = 180.0
    for i in range(5):
        sa = curr
        ea = curr - seg_span
        segments.append((i, sa, ea, segment_d(sa, ea, r_in, r_out)))
        curr = ea - gap

    # Needle calculation
    tip_x, tip_y = polar(needle_angle_deg, needle_len)
    rad_n = math.radians(needle_angle_deg)
    perp = rad_n + math.pi / 2.0
    w_base = 28.0
    w_tip = 7.5
    
    bx1 = cx + w_base * math.cos(perp)
    by1 = cy - w_base * math.sin(perp)
    bx2 = cx - w_base * math.cos(perp)
    by2 = cy + w_base * math.sin(perp)
    
    tx1 = tip_x + w_tip * math.cos(perp)
    ty1 = tip_y - w_tip * math.sin(perp)
    tx2 = tip_x - w_tip * math.cos(perp)
    ty2 = tip_y + w_tip * math.sin(perp)
    
    needle_d = (f"M {bx1:.2f} {by1:.2f} L {tx1:.2f} {ty1:.2f} "
                f"A {w_tip:.2f} {w_tip:.2f} 0 0 1 {tx2:.2f} {ty2:.2f} "
                f"L {bx2:.2f} {by2:.2f} Z")

    # Colors per variant
    if variant == "energy":
        colors = [
            ("#faecc2", "#eed082"),
            ("#fcd375", "#f5b02e"),
            ("#fb9d3e", "#ea7216"),
            ("#fa5e34", "#de3210"),
            ("#e62e28", "#b31314")
        ]
        needle_colors = ("#ff552e", "#e8260c", "#bf1202")
    elif variant == "contrast":
        colors = [
            ("#f4ede4", "#ded5c8"),
            ("#f4ede4", "#ded5c8"),
            ("#f4ede4", "#ded5c8"),
            ("#e83820", "#bf1c08"),
            ("#2a2e36", "#1c1f24")
        ]
        needle_colors = ("#ff4422", "#d91c04", "#a81200")
    else:  # laser_lab
        colors = [
            ("#383e4a", "#262b34"),
            ("#383e4a", "#262b34"),
            ("#383e4a", "#262b34"),
            ("#fa5e34", "#de3210"),
            ("#383e4a", "#262b34")
        ]
        needle_colors = ("#ff4422", "#e8260c", "#b01202")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <!-- Background Chassis Gradient -->
    <linearGradient id="chassis-grad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#282c35" />
      <stop offset="40%" stop-color="#1c1f26" />
      <stop offset="100%" stop-color="#101216" />
    </linearGradient>

    <!-- Edge Chamfer Gradient -->
    <linearGradient id="edge-chamfer" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#5a6375" stop-opacity="0.9" />
      <stop offset="30%" stop-color="#2c323d" stop-opacity="0.5" />
      <stop offset="100%" stop-color="#06070a" stop-opacity="0.95" />
    </linearGradient>

    <!-- Metal Center Knob Gradients -->
    <radialGradient id="knob-outer-ring" cx="35%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#555d6e" />
      <stop offset="60%" stop-color="#2b303a" />
      <stop offset="100%" stop-color="#14171d" />
    </radialGradient>

    <radialGradient id="knob-core" cx="35%" cy="30%" r="65%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95" />
      <stop offset="25%" stop-color="#c5cdd9" />
      <stop offset="65%" stop-color="#5e6778" />
      <stop offset="100%" stop-color="#262b34" />
    </radialGradient>

    <radialGradient id="lens-diode" cx="38%" cy="32%" r="65%">
      <stop offset="0%" stop-color="#ff8566" />
      <stop offset="40%" stop-color="#ff3b19" />
      <stop offset="85%" stop-color="#b81600" />
      <stop offset="100%" stop-color="#4a0800" />
    </radialGradient>

    <!-- Needle Gradient -->
    <linearGradient id="needle-body-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{needle_colors[0]}" />
      <stop offset="50%" stop-color="{needle_colors[1]}" />
      <stop offset="100%" stop-color="{needle_colors[2]}" />
    </linearGradient>

    <linearGradient id="needle-highlight-stroke" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#ffb8a3" stop-opacity="0.9" />
      <stop offset="100%" stop-color="#d9240b" stop-opacity="0.2" />
    </linearGradient>

    <!-- Segment Gradients -->
"""
    for idx, (c1, c2) in enumerate(colors):
        svg += f"""    <linearGradient id="seg-grad-{idx}" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{c1}" />
      <stop offset="100%" stop-color="{c2}" />
    </linearGradient>\n"""

    svg += f"""
    <!-- Drop Shadow Filters -->
    <filter id="chassis-drop" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="28" stdDeviation="32" flood-color="#000000" flood-opacity="0.6" />
      <feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#000000" flood-opacity="0.4" />
    </filter>

    <filter id="block-drop" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="6" stdDeviation="5" flood-color="#000000" flood-opacity="0.45" />
    </filter>

    <filter id="needle-drop" x="-40%" y="-40%" width="180%" height="180%">
      <feDropShadow dx="5" dy="14" stdDeviation="10" flood-color="#020305" flood-opacity="0.75" />
    </filter>

    <filter id="laser-spot-glow" x="-80%" y="-80%" width="260%" height="260%">
      <feGaussianBlur stdDeviation="10" result="blur" />
      <feMerge>
        <feMergeNode in="blur" />
        <feMergeNode in="SourceGraphic" />
      </feMerge>
    </filter>

    <filter id="text-deboss-filter">
      <feDropShadow dx="0" dy="2" stdDeviation="1.5" flood-color="#ffffff" flood-opacity="0.08" />
      <feDropShadow dx="0" dy="-2" stdDeviation="1.5" flood-color="#000000" flood-opacity="0.9" />
    </filter>

    <clipPath id="squircle-shape">
      <rect x="36" y="36" width="952" height="952" rx="216" />
    </clipPath>
  </defs>

  <!-- Chassis Body -->
  <g filter="url(#chassis-drop)">
    <rect x="36" y="36" width="952" height="952" rx="216" fill="url(#chassis-grad)" />
    <rect x="37.5" y="37.5" width="949" height="949" rx="214.5" fill="none" stroke="url(#edge-chamfer)" stroke-width="3.5" />
  </g>

  <!-- Clipped Chassis Internal Plate -->
  <g clip-path="url(#squircle-shape)">
    <!-- Top Diffused Ambient Lighting -->
    <ellipse cx="512" cy="130" rx="440" ry="180" fill="#ffffff" fill-opacity="0.035" />

    <!-- Recessed Semicircular Bed Trench -->
    <path d="{segment_d(180, 0, r_in - 14, r_out + 14)}" fill="#090b0e" fill-opacity="0.85" />
    <path d="{segment_d(180, 0, r_in - 14, r_out + 14)}" fill="none" stroke="#000000" stroke-width="3" opacity="0.7" />

    <!-- 5 Segment Blocks -->
    <g id="meter-segments" filter="url(#block-drop)">
"""
    for i, sa, ea, p_d in segments:
        svg += f"""      <path id="segment-{i}" d="{p_d}" fill="url(#seg-grad-{i})" stroke="#ffffff" stroke-opacity="0.22" stroke-width="2" />\n"""

    svg += f"""    </g>

    <!-- Laser Spot at Needle Tip Target (4th Segment) -->
    <g filter="url(#laser-spot-glow)">
      <circle cx="{tip_x:.2f}" cy="{tip_y:.2f}" r="14" fill="#ffffff" fill-opacity="0.95" />
      <circle cx="{tip_x:.2f}" cy="{tip_y:.2f}" r="30" fill="#ff3314" fill-opacity="0.45" />
    </g>

    <!-- Pointer Needle -->
    <g id="needle-group">
      <!-- Shadow -->
      <path d="{needle_d}" fill="#000000" filter="url(#needle-drop)" />
      <!-- Needle Body -->
      <path d="{needle_d}" fill="url(#needle-body-grad)" />
      <path d="{needle_d}" fill="none" stroke="url(#needle-highlight-stroke)" stroke-width="2.5" />
      <!-- Longitudinal Center Ridge Line -->
      <line x1="{cx}" y1="{cy}" x2="{tip_x:.2f}" y2="{tip_y:.2f}" stroke="#ffccbd" stroke-width="2.5" stroke-linecap="round" opacity="0.8" />
    </g>

    <!-- Center Pivot Knob Assembly -->
    <g id="center-pivot-hardware" filter="url(#block-drop)">
      <!-- Outer Socket -->
      <circle cx="{cx}" cy="{cy}" r="{r_pivot + 16}" fill="#0b0d10" stroke="#252932" stroke-width="3.5" />
      <!-- Precision Milled Bezel -->
      <circle cx="{cx}" cy="{cy}" r="{r_pivot + 4}" fill="url(#knob-outer-ring)" stroke="#414856" stroke-width="2" />
      <!-- Turned Metal Face -->
      <circle cx="{cx}" cy="{cy}" r="{r_pivot - 12}" fill="url(#knob-core)" />
      <!-- Concentric Micro Groove -->
      <circle cx="{cx}" cy="{cy}" r="{r_pivot - 24}" fill="none" stroke="#262b34" stroke-width="2" opacity="0.6" />
      <!-- Laser Emitter / Diode Optical Bead -->
      <circle cx="{cx}" cy="{cy}" r="{r_pivot - 36}" fill="#090a0d" stroke="#2c313a" stroke-width="2" />
      <circle cx="{cx - 3}" cy="{cy - 3}" r="{r_pivot - 46}" fill="url(#lens-diode)" filter="url(#laser-spot-glow)" />
      <circle cx="{cx - 7}" cy="{cy - 7}" r="5.5" fill="#ffffff" fill-opacity="0.85" />
    </g>

    <!-- Bottom POWER Typography (Debossed Precision Lab Plate) -->
    <g id="power-brand" filter="url(#text-deboss-filter)">
      <text x="512" y="785" 
            text-anchor="middle" 
            font-family="-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', 'Helvetica Neue', 'Arial Black', sans-serif" 
            font-size="96" 
            font-weight="900" 
            letter-spacing="14" 
            fill="#7b8494">POWER</text>
    </g>
  </g>
</svg>
"""
    return svg

def main():
    out_dir = "/Users/zh/Documents/test/ARP/pixel2motion_arp_icon"
    os.makedirs(out_dir, exist_ok=True)
    
    for variant in ["energy", "contrast", "laser_lab"]:
        svg_content = generate_gauge_svg(variant)
        svg_path = os.path.join(out_dir, f"logo_{variant}.svg")
        with open(svg_path, "w") as f:
            f.write(svg_content)
        print(f"Generated {svg_path}")
        
    # Default logo.svg
    default_svg = os.path.join(out_dir, "logo.svg")
    with open(default_svg, "w") as f:
        f.write(generate_gauge_svg("energy"))
    print(f"Updated default logo: {default_svg}")

if __name__ == "__main__":
    main()
