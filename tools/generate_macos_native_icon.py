import math
import os
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

def draw_macos_squircle_mask(size=1024, pad=100, radius=185, scale=4):
    """Standard macOS App Icon continuous superellipse squircle mask."""
    s_size = size * scale
    s_pad = pad * scale
    s_rad = radius * scale
    
    mask_hr = Image.new("L", (s_size, s_size), 0)
    draw = ImageDraw.Draw(mask_hr)
    
    x0, y0 = s_pad, s_pad
    x1, y1 = s_size - s_pad, s_size - s_pad
    
    draw.rounded_rectangle([x0, y0, x1, y1], radius=s_rad, fill=255)
    return mask_hr, scale

def create_metal_pivot_face(size, cx, cy, radius, scale):
    """Create authentic precision-turned stainless steel radial texture."""
    knob = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(knob)
    
    steps = 1440
    for i in range(steps):
        angle = i * (360.0 / steps)
        rad = math.radians(angle)
        intensity = 0.5 + 0.5 * math.cos(4 * rad)
        v = int(135 + 85 * intensity)
        
        x2 = cx + radius * math.cos(rad)
        y2 = cy + radius * math.sin(rad)
        
        draw.line([(cx, cy), (x2, y2)], fill=(v, int(v * 1.02), int(v * 1.06), 255), width=int(2 * scale))
        
    return knob

def draw_debossed_power_text(img, font, cx, cy, scale):
    """Draw debossed 'POWER' text with native kerning and micro-depth."""
    text = "POWER"
    
    # 1. Lower edge specular bevel highlight (offset down by 2px)
    hi_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hi_draw = ImageDraw.Draw(hi_layer)
    hi_draw.text((cx, cy + int(2.5 * scale)), text, font=font, fill=(255, 255, 255, 45), anchor="mm")
    hi_layer = hi_layer.filter(ImageFilter.GaussianBlur(radius=int(1.2 * scale)))
    img.paste(hi_layer, (0, 0), hi_layer)

    # 2. Top edge inner shadow (offset up by 2px)
    sh_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh_draw = ImageDraw.Draw(sh_layer)
    sh_draw.text((cx, cy - int(2.5 * scale)), text, font=font, fill=(0, 0, 0, 200), anchor="mm")
    sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=int(1.2 * scale)))
    img.paste(sh_layer, (0, 0), sh_layer)

    # 3. Main Text Body (Gunmetal Titanium Tone)
    txt_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_layer)
    txt_draw.text((cx, cy), text, font=font, fill=(152, 162, 178, 255), anchor="mm")
    img.paste(txt_layer, (0, 0), txt_layer)

def render_macos_native_icon():
    W, H = 1024, 1024
    scale = 4
    SW, SH = W * scale, H * scale
    
    # macOS standard icon grid: 824x824 squircle centered in 1024x1024
    pad = 100 * scale
    rad = 185 * scale
    
    x0, y0 = pad, pad
    x1, y1 = SW - pad, SH - pad
    
    cx = SW // 2
    cy = int(y0 + (y1 - y0) * 0.49)  # Golden ratio pivot height
    
    # 1. Base Plate Canvas (Space-Gray Anodized Aluminum)
    plate = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    p_draw = ImageDraw.Draw(plate)
    
    for y in range(y0, y1):
        t = (y - y0) / (y1 - y0)
        r = int(42 * (1 - t) + 22 * t)
        g = int(46 * (1 - t) + 25 * t)
        b = int(54 * (1 - t) + 30 * t)
        p_draw.line([(x0, y), (x1, y)], fill=(r, g, b, 255))
        
    # Top Diffuse Specular Highlight
    sheen = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(sheen)
    s_draw.ellipse([cx - int(380 * scale), y0 - int(40 * scale), 
                    cx + int(380 * scale), y0 + int(240 * scale)], 
                   fill=(255, 255, 255, 16))
    sheen = sheen.filter(ImageFilter.GaussianBlur(radius=int(24 * scale)))
    plate.paste(sheen, (0, 0), sheen)

    # 2. Recessed Arc Track (Clean Crisp Inner Channel)
    r_out = int(305 * scale)
    r_in = int(175 * scale)
    t_pad = int(8 * scale)
    
    to = r_out + t_pad
    ti = r_in - t_pad
    
    track_base = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    tb_draw = ImageDraw.Draw(track_base)
    tb_draw.pieslice([cx - to, cy - to, cx + to, cy + to], start=180, end=0, fill=(12, 14, 18, 255))
    tb_draw.pieslice([cx - ti, cy - ti, cx + ti, cy + ti], start=180, end=0, fill=(0, 0, 0, 0))
    
    tb_draw.arc([cx - to, cy - to, cx + to, cy + to], start=180, end=0, fill=(0, 0, 0, 255), width=int(2.5 * scale))
    tb_draw.arc([cx - ti, cy - ti, cx + ti, cy + ti], start=180, end=0, fill=(255, 255, 255, 30), width=int(1.5 * scale))
    
    plate.paste(track_base, (0, 0), track_base)

    # 3. Five Segment Blocks (Crisp Tactile Relief)
    gap_deg = 3.6
    seg_span = (180.0 - 4 * gap_deg) / 5.0
    
    colors = [
        ((253, 241, 202), (236, 205, 126)),  # 1 Warm Ivory
        ((254, 216, 120), (243, 175, 45)),   # 2 Amber
        ((253, 156, 60),  (232, 110, 20)),   # 3 Signal Orange
        ((249, 88, 48),   (220, 46, 16)),    # 4 Laser Active Target
        ((226, 42, 38),   (174, 18, 20))     # 5 High Range Crimson
    ]
    
    def polar_pt(deg, r):
        rad = math.radians(deg)
        return (cx + r * math.cos(rad), cy - r * math.sin(rad))

    # Segment Ambient Shadow
    seg_sh = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    ss_draw = ImageDraw.Draw(seg_sh)
    for i in range(5):
        sa = 180.0 - i * (seg_span + gap_deg)
        ea = sa - seg_span
        pts = []
        for step in range(24 + 1):
            a = sa - step * (sa - ea) / 24
            pts.append(polar_pt(a, r_out))
        for step in range(24 + 1):
            a = ea + step * (sa - ea) / 24
            pts.append(polar_pt(a, r_in))
        sh_pts = [(x + int(2 * scale), y + int(6 * scale)) for x, y in pts]
        ss_draw.polygon(sh_pts, fill=(0, 0, 0, 190))
        
    seg_sh = seg_sh.filter(ImageFilter.GaussianBlur(radius=int(4 * scale)))
    plate.paste(seg_sh, (0, 0), seg_sh)

    # Draw Segments Layer
    seg_layer = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(seg_layer)
    for i in range(5):
        sa = 180.0 - i * (seg_span + gap_deg)
        ea = sa - seg_span
        c_top, c_bot = colors[i]
        
        pts = []
        for step in range(32 + 1):
            a = sa - step * (sa - ea) / 32
            pts.append(polar_pt(a, r_out))
        for step in range(32 + 1):
            a = ea + step * (sa - ea) / 32
            pts.append(polar_pt(a, r_in))
            
        s_draw.polygon(pts, fill=c_bot + (255,))
        s_draw.line(pts[:33], fill=(255, 255, 255, 120), width=int(2.5 * scale))
        
    plate.paste(seg_layer, (0, 0), seg_layer)

    # 4. Needle & Laser Target
    needle_angle = 32.0  # degrees
    needle_len = int(285 * scale)
    w_base = int(21 * scale)
    w_tip = int(6 * scale)
    
    tip_pt = polar_pt(needle_angle, needle_len)
    rad_n = math.radians(needle_angle)
    perp = rad_n + math.pi / 2.0
    
    bx1, by1 = cx + w_base * math.cos(perp), cy - w_base * math.sin(perp)
    bx2, by2 = cx - w_base * math.cos(perp), cy + w_base * math.sin(perp)
    tx1, ty1 = tip_pt[0] + w_tip * math.cos(perp), tip_pt[1] - w_tip * math.sin(perp)
    tx2, ty2 = tip_pt[0] - w_tip * math.cos(perp), tip_pt[1] + w_tip * math.sin(perp)
    
    needle_pts = [(bx1, by1), (tx1, ty1), (tx2, ty2), (bx2, by2)]

    # Realistic Needle Drop Shadow
    needle_sh = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    nsh_draw = ImageDraw.Draw(needle_sh)
    nsh_pts = [(x + int(6 * scale), y + int(14 * scale)) for x, y in needle_pts]
    nsh_draw.polygon(nsh_pts, fill=(0, 0, 0, 230))
    needle_sh = needle_sh.filter(ImageFilter.GaussianBlur(radius=int(6 * scale)))
    plate.paste(needle_sh, (0, 0), needle_sh)

    # Draw Needle Body
    needle_layer = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    nd = ImageDraw.Draw(needle_layer)
    nd.polygon(needle_pts, fill=(245, 48, 18, 255))
    nd.line([(cx, cy), tip_pt], fill=(255, 195, 180, 240), width=int(2 * scale))
    nd.line([(bx1, by1), (tx1, ty1)], fill=(255, 215, 200, 180), width=int(1.5 * scale))
    plate.paste(needle_layer, (0, 0), needle_layer)

    # Crisp Laser Focal Point at Needle Tip
    spot_layer = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    sp_draw = ImageDraw.Draw(spot_layer)
    sp_draw.circle(tip_pt, radius=int(18 * scale), fill=(255, 45, 20, 80))
    sp_draw.circle(tip_pt, radius=int(9 * scale), fill=(255, 160, 140, 180))
    sp_draw.circle(tip_pt, radius=int(4 * scale), fill=(255, 255, 255, 255))
    spot_layer = spot_layer.filter(ImageFilter.GaussianBlur(radius=int(2 * scale)))
    plate.paste(spot_layer, (0, 0), spot_layer)

    # 5. Center Dial Hardware Pivot (Brushed Stainless Steel Knob)
    r_piv = int(68 * scale)
    
    p_sh = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    ps_draw = ImageDraw.Draw(p_sh)
    ps_draw.circle((cx + int(2*scale), cy + int(10*scale)), radius=r_piv + int(14*scale), fill=(0, 0, 0, 210))
    p_sh = p_sh.filter(ImageFilter.GaussianBlur(radius=int(6 * scale)))
    plate.paste(p_sh, (0, 0), p_sh)

    p_layer = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    pd = ImageDraw.Draw(p_layer)
    pd.circle((cx, cy), radius=r_piv + int(14 * scale), fill=(14, 16, 20, 255), outline=(52, 58, 70, 255), width=int(2*scale))
    pd.circle((cx, cy), radius=r_piv + int(4 * scale), fill=(42, 48, 58, 255), outline=(85, 95, 110, 255), width=int(2*scale))
    
    r_face = r_piv - int(10 * scale)
    face_sheen = create_metal_pivot_face(SW, cx, cy, r_face, scale)
    face_mask = Image.new("L", (SW, SH), 0)
    fm_draw = ImageDraw.Draw(face_mask)
    fm_draw.circle((cx, cy), radius=r_face, fill=255)
    p_layer.paste(face_sheen, (0, 0), face_mask)
    
    pd.circle((cx, cy), radius=r_face, outline=(100, 110, 125, 200), width=int(1.5 * scale))
    pd.circle((cx, cy), radius=r_piv - int(34 * scale), fill=(16, 18, 24, 255), outline=(65, 74, 86, 255), width=int(1.5*scale))
    pd.circle((cx, cy), radius=r_piv - int(44 * scale), fill=(255, 45, 20, 255))
    pd.circle((cx - int(3.5*scale), cy - int(3.5*scale)), radius=int(5 * scale), fill=(255, 255, 255, 230))
    
    plate.paste(p_layer, (0, 0), p_layer)

    # 6. Laser-Etched "POWER" Typography (Flawless Kerning)
    font_path = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    if not os.path.exists(font_path):
        font_path = "/System/Library/Fonts/SFNS.ttf"
        
    font_size = int(68 * scale)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
        
    text_cy = int(y0 + (y1 - y0) * 0.81)
    draw_debossed_power_text(plate, font, cx, text_cy, scale)

    # 7. Apply macOS Continuous Squircle Mask to the Plate
    mask_hr, _ = draw_macos_squircle_mask(size=W, pad=100, radius=185, scale=scale)
    
    edge_layer = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    ed_draw = ImageDraw.Draw(edge_layer)
    ed_w = int(3 * scale)
    ed_draw.rounded_rectangle([x0 + ed_w//2, y0 + ed_w//2, x1 - ed_w//2, y1 - ed_w//2],
                              radius=rad - ed_w//2, outline=(255, 255, 255, 40), width=ed_w)
    
    plate.paste(edge_layer, (0, 0), edge_layer)
    
    masked_body = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    masked_body.paste(plate, (0, 0), mask_hr)

    # 8. Standard macOS System Multi-Layer Drop Shadow
    final_canvas = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    
    amb = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    amb_d = ImageDraw.Draw(amb)
    amb_d.rounded_rectangle([x0, y0 + int(10*scale), x1, y1 + int(10*scale)], radius=rad, fill=(0, 0, 0, 70))
    amb = amb.filter(ImageFilter.GaussianBlur(radius=int(26 * scale)))
    
    key = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    key_d = ImageDraw.Draw(key)
    key_d.rounded_rectangle([x0, y0 + int(22*scale), x1, y1 + int(22*scale)], radius=rad, fill=(0, 0, 0, 110))
    key = key.filter(ImageFilter.GaussianBlur(radius=int(12 * scale)))
    
    final_canvas.paste(amb, (0, 0), amb)
    final_canvas.paste(key, (0, 0), key)
    final_canvas.paste(masked_body, (0, 0), masked_body)
    
    master_1024 = final_canvas.resize((W, H), Image.Resampling.LANCZOS)
    return master_1024

def main():
    print("Re-rendering macOS native power meter icon with balanced POWER typography...")
    icon = render_macos_native_icon()
    target_png = "/Users/zh/Documents/test/ARP/tauri-ui/src-tauri/icons/icon.png"
    icon.save(target_png, "PNG")
    print(f"Updated {target_png}")

if __name__ == "__main__":
    main()
