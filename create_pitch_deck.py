import os
import sys
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor

def build_presentation():
    prs = Presentation()
    # 16:9 Widescreen dimensions
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6] # Blank slide layout

    # Color Palette Definitions
    DARK_BG = RGBColor(11, 19, 43)        # #0B132B Deep Tech Navy
    CARD_BG = RGBColor(28, 37, 65)        # #1C2541 Slate Navy Card
    CARD_BG_LIGHT = RGBColor(34, 46, 80)  # Slightly lighter card
    ACCENT_GREEN = RGBColor(16, 185, 129) # #10B981 Emerald
    ACCENT_CYAN = RGBColor(6, 182, 212)   # #06B6D4 Electric Cyan
    ACCENT_CORAL = RGBColor(244, 63, 94)  # #F43F5E Warning / Friction
    ACCENT_GOLD = RGBColor(245, 158, 11)  # #F59E0B Metric Gold
    TEXT_WHITE = RGBColor(255, 255, 255)
    TEXT_MUTED = RGBColor(148, 163, 184)  # #94A3B8 Slate Gray
    BORDER_COLOR = RGBColor(51, 65, 85)   # #334155 Subdued border

    def set_bg(slide):
        bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
        bg.fill.solid()
        bg.fill.fore_color.rgb = DARK_BG
        bg.line.fill.background()
        return bg

    def add_header(slide, category, title, subtitle=None):
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.733), Inches(1.1))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0
        
        # Category / Tag
        p1 = tf.paragraphs[0]
        p1.text = category.upper()
        p1.font.size = Pt(11)
        p1.font.bold = True
        p1.font.color.rgb = ACCENT_CYAN
        p1.space_after = Pt(2)
        
        # Title
        p2 = tf.add_paragraph()
        p2.text = title
        p2.font.size = Pt(24)
        p2.font.bold = True
        p2.font.color.rgb = TEXT_WHITE
        
        if subtitle:
            p3 = tf.add_paragraph()
            p3.text = subtitle
            p3.font.size = Pt(12)
            p3.font.color.rgb = TEXT_MUTED
            p3.space_before = Pt(2)

    def add_card(slide, left, top, width, height, border_color=ACCENT_CYAN, fill_color=CARD_BG):
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        card.fill.solid()
        card.fill.fore_color.rgb = fill_color
        if border_color:
            card.line.color.rgb = border_color
            card.line.width = Pt(1.5)
        else:
            card.line.fill.background()
        return card

    # ==========================================
    # SLIDE 1: Title Slide (Dark, High-Tech, Impactful)
    # ==========================================
    s1 = prs.slides.add_slide(blank_layout)
    set_bg(s1)

    # Decorative background accent elements
    add_card(s1, Inches(0.8), Inches(1.2), Inches(11.733), Inches(5.2), border_color=BORDER_COLOR, fill_color=CARD_BG)

    # Tag Badge
    badge = s1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.4), Inches(1.7), Inches(3.2), Inches(0.42))
    badge.fill.solid()
    badge.fill.fore_color.rgb = RGBColor(15, 35, 60)
    badge.line.color.rgb = ACCENT_CYAN
    badge.text_frame.text = "🇦🇪 UAE SEED FUNDING OPPORTUNITY"
    p = badge.text_frame.paragraphs[0]
    p.font.size = Pt(10)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN
    p.alignment = PP_ALIGN.CENTER

    # Main Title & Subtitle Box
    tb_title = s1.shapes.add_textbox(Inches(1.4), Inches(2.3), Inches(10.5), Inches(2.2))
    tf_title = tb_title.text_frame
    tf_title.word_wrap = True
    
    p = tf_title.paragraphs[0]
    p.text = "ServiceFlow Vision"
    p.font.size = Pt(46)
    p.font.bold = True
    p.font.color.rgb = TEXT_WHITE
    p.space_after = Pt(8)

    p2 = tf_title.add_paragraph()
    p2.text = "Turning passive CCTV cameras into an autonomous operational copilot for high-turnover hospitality venues."
    p2.font.size = Pt(18)
    p2.font.color.rgb = ACCENT_GREEN

    # 3 Stat / Highlights Pills
    pills = [
        ("ZERO HARDWARE CAPEX", "Runs on standard off-the-shelf IP CCTV cameras"),
        ("100% UAE PRIVACY COMPLIANT", "Ephemeral tokens · No diner biometrics stored"),
        ("TARGET: HUB71 / FLAT6LABS", "Seeking $250K Seed to scale across Dubai & GCC")
    ]
    pill_w = Inches(3.28)
    for i, (pill_title, pill_desc) in enumerate(pills):
        px = Inches(1.4) + i * (pill_w + Inches(0.34))
        py = Inches(4.7)
        add_card(s1, px, py, pill_w, Inches(1.25), border_color=ACCENT_CYAN, fill_color=CARD_BG_LIGHT)
        
        tbp = s1.shapes.add_textbox(px + Inches(0.15), py + Inches(0.15), pill_w - Inches(0.3), Inches(0.95))
        tfp = tbp.text_frame
        tfp.word_wrap = True
        pt1 = tfp.paragraphs[0]
        pt1.text = pill_title
        pt1.font.size = Pt(11)
        pt1.font.bold = True
        pt1.font.color.rgb = ACCENT_CYAN
        pt1.space_after = Pt(4)
        
        pt2 = tfp.add_paragraph()
        pt2.text = pill_desc
        pt2.font.size = Pt(10)
        pt2.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 2: The Problem (The Operational Blind Spot)
    # ==========================================
    s2 = prs.slides.add_slide(blank_layout)
    set_bg(s2)
    add_header(s2, "The Market Problem", "The Hidden Margin Bleed in Restaurant Operations", 
               "Restaurants in the UAE spend millions on décor and marketing, but lose up to 15% of table capacity to unmonitored floor friction.")

    problems = [
        ("40%", "First-Service Friction", 
         "Customers who wait >3 minutes for initial menu presentation or greeting report 40% lower satisfaction and tip rates. Managers only find out after bad Google Reviews.", ACCENT_CORAL),
        ("8 - 15 MIN", "Unbussed Table Lag", 
         "During peak weekend lunch and dinner rushes, finished tables sit unbussed and unseated for up to 15 minutes, directly sacrificing 1 to 2 table turns per shift.", ACCENT_GOLD),
        ("70% / YR", "High Staff Turnover", 
         "GCC hospitality staff turnover averages 70% annually. Floor managers cannot continuously monitor, coach, and balance table coverage across blind spots.", ACCENT_CYAN),
        ("ZERO", "POS Visibility on Physical Space", 
         "POS software only knows when a dish is punched into a computer. It has zero real-time awareness of guest arrival, dwell time, or empty drink glasses.", ACCENT_CORAL)
    ]

    card_w = Inches(2.7)
    card_h = Inches(4.8)
    for i, (stat, title, desc, color) in enumerate(problems):
        cx = Inches(0.8) + i * (card_w + Inches(0.31))
        cy = Inches(1.8)
        add_card(s2, cx, cy, card_w, card_h, border_color=color)

        tb = s2.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.25), card_w - Inches(0.4), card_h - Inches(0.5))
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = stat
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = color
        p.space_after = Pt(12)

        p = tf.add_paragraph()
        p.text = title
        p.font.size = Pt(14)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(10)

        p = tf.add_paragraph()
        p.text = desc
        p.font.size = Pt(11)
        p.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 3: The Solution
    # ==========================================
    s3 = prs.slides.add_slide(blank_layout)
    set_bg(s3)
    add_header(s3, "The Solution", "Continuous Floor Intelligence via Computer Vision", 
               "An intelligent vision copilot that watches the floor, tracks physical latency, and alerts staff before friction occurs.")

    sol_cards = [
        ("📹 ZERO NEW HARDWARE", "Leverage Existing CCTV", 
         "Connects seamlessly to existing RTSP/CCTV security camera feeds. No proprietary sensors, no table buzzers, and zero upfront CAPEX for venue operators.", ACCENT_GREEN),
        ("🗺️ MULTI-CAMERA SYNCHRONIZATION", "Homography 2D Mapping", 
         "Calibrates perspective views into a unified 2D floor plan. Tracks diners and staff across blind spots, partition walls, and camera handoffs.", ACCENT_CYAN),
        ("🤖 AUTONOMOUS DISPATCH COPILOT", "Proactive Floor Alerts", 
         "Instantly generates routed tasks: 'Table 4 unserved for 90s', 'Table 12 departed - reset needed'. Eliminates manager blind spots.", ACCENT_GOLD),
        ("🔒 PRIVACY-FIRST BY DESIGN", "Compliant with UAE Law", 
         "Guests are tracked using temporary anonymous mathematical vectors that self-delete. 100% compliant with UAE Federal Decree-Law No. 45 of 2021.", ACCENT_GREEN)
    ]

    card_w2 = Inches(5.6)
    card_h2 = Inches(2.25)
    for i, (badge_txt, title, desc, col) in enumerate(sol_cards):
        row = i // 2
        col_idx = i % 2
        cx = Inches(0.8) + col_idx * (card_w2 + Inches(0.533))
        cy = Inches(1.8) + row * (card_h2 + Inches(0.35))

        add_card(s3, cx, cy, card_w2, card_h2, border_color=col)
        tb = s3.shapes.add_textbox(cx + Inches(0.25), cy + Inches(0.2), card_w2 - Inches(0.5), card_h2 - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = badge_txt
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = col
        p.space_after = Pt(4)

        p = tf.add_paragraph()
        p.text = title
        p.font.size = Pt(16)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(6)

        p = tf.add_paragraph()
        p.text = desc
        p.font.size = Pt(11)
        p.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 4: Technology Architecture & Moat (With Embedded Infographic)
    # ==========================================
    s4 = prs.slides.add_slide(blank_layout)
    set_bg(s4)
    add_header(s4, "Proprietary Moat", "Edge AI Pipeline & Multi-Camera Re-Identification", 
               "Engineered to solve the extreme visual clutter, heavy occlusion, and lighting shifts of busy restaurant dining rooms.")

    # Left: Embedded Graphic Card
    img_path = r"C:\Users\afsal\.gemini\antigravity\brain\11eeb1e5-9f5e-4165-ad78-a728ba1c7b29\architecture_infographic_1789282526604.jpg"
    if os.path.exists(img_path):
        add_card(s4, Inches(0.8), Inches(1.8), Inches(5.8), Inches(4.9), border_color=BORDER_COLOR)
        s4.shapes.add_picture(img_path, Inches(0.95), Inches(1.95), width=Inches(5.5), height=Inches(4.6))
    else:
        add_card(s4, Inches(0.8), Inches(1.8), Inches(5.8), Inches(4.9), border_color=ACCENT_CYAN)

    # Right: Key Technical Moats
    tech_points = [
        ("Hybrid ByteTracker + OpenVINO Re-ID", "Combines high-frame-rate bounding box kinematics with a 256-D whole-body feature vector to maintain track continuity across camera blind zones."),
        ("Perspective Homography Projection", "Translates distorted ceiling CCTV angles into clean Euclidean floor coordinates (x, y) with boundary clamping against horizon distortion."),
        ("Dwell-Time & Occlusion Gap Filtering", "Requires >= 15 seconds of sustained table presence before registering a dining party, ignoring aisle pedestrians and transient staff pass-bys."),
        ("Local Edge Acceleration (Zero Cloud Lag)", "Optimized for local CPU and Edge AI inference (OpenVINO / ONNX Runtime), keeping sensitive footage 100% on-premises.")
    ]

    rx = Inches(6.9)
    th_box = Inches(1.1)
    for i, (t_title, t_desc) in enumerate(tech_points):
        ty = Inches(1.8) + i * (th_box + Inches(0.16))
        add_card(s4, rx, ty, Inches(5.633), th_box, border_color=ACCENT_CYAN, fill_color=CARD_BG_LIGHT)

        tb = s4.shapes.add_textbox(rx + Inches(0.2), ty + Inches(0.12), Inches(5.233), th_box - Inches(0.24))
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = f"0{i+1}. {t_title}"
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = ACCENT_CYAN
        p.space_after = Pt(3)

        p = tf.add_paragraph()
        p.text = t_desc
        p.font.size = Pt(10)
        p.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 5: How It Works: 4-Step Pipeline (With Embedded Floor Plan)
    # ==========================================
    s5 = prs.slides.add_slide(blank_layout)
    set_bg(s5)
    add_header(s5, "Product Walkthrough", "From Raw CCTV Video to Optimized Floor Turnover", 
               "A rapid 10-minute setup per venue that runs automatically in the background.")

    # 4 horizontal step blocks
    steps = [
        ("STEP 1: CALIBRATE", "Pair 4 floor points to map angles in <10 mins"),
        ("STEP 2: ENROLL STAFF", "One frontal photo enrolls consented servers"),
        ("STEP 3: AUTONOMOUS RUN", "AI tracks guests, orders, visits & dwell"),
        ("STEP 4: DISPATCH ACTIONS", "Copilot triggers proactive table resets & alerts")
    ]
    step_w = Inches(2.7)
    for i, (s_head, s_sub) in enumerate(steps):
        sx = Inches(0.8) + i * (step_w + Inches(0.31))
        sy = Inches(1.75)
        add_card(s5, sx, sy, step_w, Inches(1.1), border_color=ACCENT_GREEN if i==0 else (ACCENT_CYAN if i==1 else (ACCENT_GOLD if i==2 else ACCENT_CORAL)), fill_color=CARD_BG_LIGHT)
        
        tb = s5.shapes.add_textbox(sx + Inches(0.15), sy + Inches(0.15), step_w - Inches(0.3), Inches(0.8))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = s_head
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(2)
        p = tf.add_paragraph()
        p.text = s_sub
        p.font.size = Pt(9.5)
        p.font.color.rgb = TEXT_MUTED

    # Bottom Area: Floor plan graphic on left, Live review stats on right
    fp_path = r"C:\Users\afsal\.gemini\antigravity\brain\11eeb1e5-9f5e-4165-ad78-a728ba1c7b29\restaurant_floor_plan_1788555800459.jpg"
    if os.path.exists(fp_path):
        add_card(s5, Inches(0.8), Inches(3.1), Inches(6.0), Inches(3.7), border_color=BORDER_COLOR)
        s5.shapes.add_picture(fp_path, Inches(0.95), Inches(3.2), width=Inches(5.7), height=Inches(3.5))

    # Right side: Live metrics output card
    add_card(s5, Inches(7.1), Inches(3.1), Inches(5.433), Inches(3.7), border_color=ACCENT_CYAN)
    tb_m = s5.shapes.add_textbox(Inches(7.35), Inches(3.3), Inches(4.933), Inches(3.3))
    tf_m = tb_m.text_frame
    tf_m.word_wrap = True

    p = tf_m.paragraphs[0]
    p.text = "OPERATIONAL DISPATCH IN ACTION"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN
    p.space_after = Pt(10)

    demo_bullets = [
        ("⏱️ First Service Greeting", "Alerts triggered if guest sits >90 seconds without waiter"),
        ("🔄 Table Turnaround Time", "Exact tracking from guest departure to fresh table reset"),
        ("📊 Waiter Workload Balance", "Identifies zones with staff deficit and bottlenecks"),
        ("📈 Ground-Truth Auditing", "Every metric tied to exact video timestamps for manager review")
    ]
    for b_title, b_sub in demo_bullets:
        p = tf_m.add_paragraph()
        p.text = f"{b_title}: {b_sub}"
        p.font.size = Pt(10.5)
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(6)

    # ==========================================
    # SLIDE 6: Market Opportunity (UAE & GCC Focus)
    # ==========================================
    s6 = prs.slides.add_slide(blank_layout)
    set_bg(s6)
    add_header(s6, "Market Opportunity", "Targeting the World's Most Lucrative F&B Market", 
               "The UAE and Saudi Arabia are undergoing an unprecedented hospitality boom with aggressive digital transformation mandates.")

    market_stats = [
        ("$65 BILLION", "MENA Foodservice TAM", "Total annual food and dining expenditure across GCC markets by 2028.", ACCENT_CYAN),
        ("15,000+", "UAE Dine-In Venues (SAM)", "Over 13,000 venues in Dubai alone with high footfall and heavy peak congestion.", ACCENT_GREEN),
        ("AED 35,000", "Monthly Revenue Unlock", "Per venue value generated by shortening table reset and greeting lag by just 8 minutes.", ACCENT_GOLD),
        ("350 VENUES", "Year 1–3 SOM Target", "Focused capture of casual dining chains, luxury hotel outlets, and high-volume cafes.", ACCENT_CYAN)
    ]
    mw = Inches(2.7)
    for i, (stat, title, desc, col) in enumerate(market_stats):
        mx = Inches(0.8) + i * (mw + Inches(0.31))
        my = Inches(1.8)
        add_card(s6, mx, my, mw, Inches(3.0), border_color=col)
        
        tb = s6.shapes.add_textbox(mx + Inches(0.18), my + Inches(0.2), mw - Inches(0.36), Inches(2.6))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = stat
        p.font.size = Pt(26)
        p.font.bold = True
        p.font.color.rgb = col
        p.space_after = Pt(8)
        p = tf.add_paragraph()
        p.text = title
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(6)
        p = tf.add_paragraph()
        p.text = desc
        p.font.size = Pt(10)
        p.font.color.rgb = TEXT_MUTED

    # Bottom Callout Banner
    add_card(s6, Inches(0.8), Inches(5.1), Inches(11.733), Inches(1.6), border_color=ACCENT_GREEN, fill_color=CARD_BG_LIGHT)
    tb_b = s6.shapes.add_textbox(Inches(1.1), Inches(5.25), Inches(11.133), Inches(1.3))
    tf_b = tb_b.text_frame
    tf_b.word_wrap = True
    p = tf_b.paragraphs[0]
    p.text = "🇦🇪 WHY UAE IS THE IDEAL GLOBAL LAUNCHPAD"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = ACCENT_GREEN
    p.space_after = Pt(4)
    p = tf_b.add_paragraph()
    p.text = "• National AI Strategy 2031: World-first Minister of State for AI backing commercial AI deployments.\n• High Average Check & Intense Competition: Restaurant operators eagerly adopt software that yields tangible table turnover margins.\n• Regional Expansion Springboard: Instant scalability into Saudi Arabia's giga-projects (NEOM, Red Sea, Riyadh Season)."
    p.font.size = Pt(10.5)
    p.font.color.rgb = TEXT_WHITE

    # ==========================================
    # SLIDE 7: Business Model & Unit Economics
    # ==========================================
    s7 = prs.slides.add_slide(blank_layout)
    set_bg(s7)
    add_header(s7, "Business Model", "High-Margin B2B SaaS Recurring Revenue", 
               "Predictable multi-tier recurring subscriptions with zero hardware manufacturing overhead.")

    tiers = [
        ("STARTER TIER", "$250 / mo", "Single-Camera Cafes & Quick Service", 
         ["Up to 4 CCTV Feeds", "Daily Table Turnover Reports", "First-Service Latency Tracker", "Manager Daily Email Digest"], ACCENT_CYAN),
        ("PRO TIER", "$500 / mo", "Casual Dining & High-Turnover Venues", 
         ["Up to 8 CCTV Feeds", "Full Cross-Camera Re-ID", "Shift Copilot Live Dispatch", "Waiter Workload Heatmaps", "WhatsApp Real-Time Alerts"], ACCENT_GREEN),
        ("ENTERPRISE TIER", "$1,200+ / mo", "Multi-Branch Hospitality Groups", 
         ["Unlimited Camera Feeds", "Centralized Multi-Venue Dashboard", "POS Integration (Foodics, Sapaad)", "Custom SLA & Dedicated Support"], ACCENT_GOLD)
    ]
    tw = Inches(3.68)
    for i, (t_name, t_price, t_sub, feats, col) in enumerate(tiers):
        tx = Inches(0.8) + i * (tw + Inches(0.34))
        ty = Inches(1.8)
        add_card(s7, tx, ty, tw, Inches(4.9), border_color=col, fill_color=CARD_BG_LIGHT if i==1 else CARD_BG)

        tb = s7.shapes.add_textbox(tx + Inches(0.25), ty + Inches(0.25), tw - Inches(0.5), Inches(4.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = t_name
        p.font.size = Pt(12)
        p.font.bold = True
        p.font.color.rgb = col

        p = tf.add_paragraph()
        p.text = t_price
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(2)

        p = tf.add_paragraph()
        p.text = t_sub
        p.font.size = Pt(10)
        p.font.color.rgb = TEXT_MUTED
        p.space_after = Pt(14)

        for feat in feats:
            p = tf.add_paragraph()
            p.text = f"✓  {feat}"
            p.font.size = Pt(10.5)
            p.font.color.rgb = TEXT_WHITE
            p.space_after = Pt(6)

    # ==========================================
    # SLIDE 8: Competitive Matrix
    # ==========================================
    s8 = prs.slides.add_slide(blank_layout)
    set_bg(s8)
    add_header(s8, "Competitive Landscape", "Unmatched Accuracy Without Proprietary Hardware", 
               "How ServiceFlow Vision compares against legacy options and big-tech cloud alternatives.")

    # Create visual comparison table
    rows = 5
    cols = 5
    tbl_shape = s8.shapes.add_table(rows, cols, Inches(0.8), Inches(1.8), Inches(11.733), Inches(4.8))
    table = tbl_shape.table

    headers = ["FEATURE / METRIC", "LEGACY CCTV", "BLE BEACONS / SENSORS", "CLOUD BIG-TECH AI", "SERVICEFLOW VISION"]
    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(16, 28, 54) if j < 4 else ACCENT_GREEN
        p = cell.text_frame.paragraphs[0]
        p.font.bold = True
        p.font.size = Pt(11)
        p.font.color.rgb = TEXT_WHITE
        p.alignment = PP_ALIGN.CENTER

    data = [
        ["Hardware Cost", "Already Installed", "High (Batteries/Sensors)", "High (Bandwidth Costs)", "ZERO EXTRA HARDWARE"],
        ["Staff Latency Tracking", "Manual (Reviewing Tapes)", "Inaccurate Proximity", "Face Only (High Risk)", "HYBRID FACE + UNIFORM RE-ID"],
        ["UAE Privacy Compliance", "Passive Recording", "Neutral", "Low (Biometric Cloud Risk)", "100% EPHEMERAL TOKENS"],
        ["Live Operations Copilot", "None", "Basic Notification", "Batch Reports Next Day", "REAL-TIME DISPATCH ALERTS"]
    ]

    for i, row in enumerate(data):
        for j, val in enumerate(row):
            cell = table.cell(i+1, j)
            cell.text = val
            cell.fill.solid()
            cell.fill.fore_color.rgb = CARD_BG if j < 4 else RGBColor(15, 45, 40)
            p = cell.text_frame.paragraphs[0]
            p.font.size = Pt(10.5)
            p.font.color.rgb = ACCENT_GREEN if j == 4 else (TEXT_WHITE if j == 0 else TEXT_MUTED)
            p.font.bold = (j == 0 or j == 4)
            p.alignment = PP_ALIGN.CENTER if j > 0 else PP_ALIGN.LEFT

    # ==========================================
    # SLIDE 9: Strategic Roadmap (12 Months)
    # ==========================================
    s9 = prs.slides.add_slide(blank_layout)
    set_bg(s9)
    add_header(s9, "Execution Plan", "12-Month Milestones & Commercial Expansion", 
               "From UAE flagship pilots to GCC-wide expansion.")

    roadmap_steps = [
        ("Q1 2027: PILOTS", "UAE Flagship Deployments", 
         "• Deploy pilots in 3 flagship casual dining venues in Dubai & Sharjah.\n• Secure initial customer testimonials and quantitative turnover benchmarks.\n• Finalize Hub71 / Flat6Labs accelerator onboarding.", ACCENT_CYAN),
        ("Q2 2027: STREAMING", "Live RTSP Edge Pipeline", 
         "• Launch real-time RTSP camera streaming module.\n• Release WhatsApp & Telegram manager dispatch notifications.\n• Expand pilot network to 15 paid restaurant venues.", ACCENT_GREEN),
        ("Q3 2027: INTEGRATION", "POS & Ecosystem Integrations", 
         "• Complete native integrations with leading GCC POS platforms (Foodics, Sapaad, Micros).\n• Cross-correlate physical table occupancy with bill checkout times.\n• Launch self-serve venue onboarding.", ACCENT_GOLD),
        ("Q4 2027: GCC SCALE", "Expansion to Saudi Arabia", 
         "• Scale into Riyadh & Jeddah hospitality markets (Vision 2030 F&B boom).\n• Achieve $50K MRR across 100+ active venues.\n• Open institutional Seed funding round.", ACCENT_CYAN)
    ]

    rw = Inches(2.7)
    for i, (q_title, q_sub, q_body, col) in enumerate(roadmap_steps):
        rx = Inches(0.8) + i * (rw + Inches(0.31))
        ry = Inches(1.8)
        add_card(s9, rx, ry, rw, Inches(4.9), border_color=col, fill_color=CARD_BG_LIGHT)

        tb = s9.shapes.add_textbox(rx + Inches(0.18), ry + Inches(0.2), rw - Inches(0.36), Inches(4.5))
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = q_title
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col
        p.space_after = Pt(4)

        p = tf.add_paragraph()
        p.text = q_sub
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(10)

        p = tf.add_paragraph()
        p.text = q_body
        p.font.size = Pt(9.5)
        p.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 10: The Ask & Investment Allocation
    # ==========================================
    s10 = prs.slides.add_slide(blank_layout)
    set_bg(s10)
    add_header(s10, "Investment Opportunity", "Raising $250,000 Seed / Accelerator Round", 
               "Partner with us to transform hospitality operations across the UAE and broader GCC.")

    # Top stats
    ask_cards = [
        ("$250,000", "SEED ROUND SIZE", "Targeting Hub71+ AI, Flat6Labs, or Angel Co-Investment", ACCENT_GREEN),
        ("18 MONTHS", "RUNWAY TARGET", "Sufficient to achieve cash-flow breakeven and $50K MRR", ACCENT_CYAN),
        ("100+ VENUES", "TARGET DEPLOYMENTS", "Proven scalability across Dubai, Abu Dhabi, and Riyadh", ACCENT_GOLD)
    ]
    aw = Inches(3.68)
    for i, (astat, atitle, adesc, col) in enumerate(ask_cards):
        ax = Inches(0.8) + i * (aw + Inches(0.34))
        ay = Inches(1.8)
        add_card(s10, ax, ay, aw, Inches(1.4), border_color=col, fill_color=CARD_BG_LIGHT)

        tb = s10.shapes.add_textbox(ax + Inches(0.2), ay + Inches(0.15), aw - Inches(0.4), Inches(1.1))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = astat
        p.font.size = Pt(26)
        p.font.bold = True
        p.font.color.rgb = col
        p = tf.add_paragraph()
        p.text = atitle
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = TEXT_WHITE
        p = tf.add_paragraph()
        p.text = adesc
        p.font.size = Pt(9.5)
        p.font.color.rgb = TEXT_MUTED

    # Bottom Area: Use of Funds breakdown
    add_card(s10, Inches(0.8), Inches(3.5), Inches(11.733), Inches(3.2), border_color=ACCENT_CYAN)
    tb_funds = s10.shapes.add_textbox(Inches(1.1), Inches(3.7), Inches(11.133), Inches(2.8))
    tf_funds = tb_funds.text_frame
    tf_funds.word_wrap = True

    p = tf_funds.paragraphs[0]
    p.text = "ALLOCATION OF FUNDS"
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN
    p.space_after = Pt(10)

    allocations = [
        ("45% - Core AI & RTSP Engineering", "Build native live camera streaming ingestion, mobile alerts, and POS integrations."),
        ("35% - UAE Business Development & Pilots", "On-the-ground deployments, restaurant chain onboarding, and customer success in Dubai/Abu Dhabi."),
        ("20% - Regulatory, Licensing & Operations", "ADGM / DIFC / in5 corporate entity setup, UAE data compliance certifications, and IP protection.")
    ]
    for alloc_head, alloc_desc in allocations:
        p = tf_funds.add_paragraph()
        p.text = f"• {alloc_head}: {alloc_desc}"
        p.font.size = Pt(11)
        p.font.color.rgb = TEXT_WHITE
        p.space_after = Pt(6)

    p_close = tf_funds.add_paragraph()
    p_close.text = "Contact: founders@serviceflow.ai | UAE Launchpad"
    p_close.font.size = Pt(11)
    p_close.font.bold = True
    p_close.font.color.rgb = ACCENT_GREEN
    p_close.space_before = Pt(8)

    # Output path
    output_path = r"c:\Users\afsal\Desktop\work\resra\ServiceFlow_Vision_Pitch_Deck.pptx"
    prs.save(output_path)
    print(f"Presentation saved successfully to {output_path}")

if __name__ == "__main__":
    build_presentation()
