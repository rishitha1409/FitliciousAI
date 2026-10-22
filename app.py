import base64
import html
import io
import json
import math
import os
import random
import re
import time
import wave

import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

st.set_page_config(
    page_title="Fitlicious AI",
    page_icon="🥗",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- BRAND PALETTE ----------
SPICY_RED = "#A93118"
EARTHEN_BROWN = "#673A25"
WARM_RICE = "#E8E2D6"
SCALLION_GREEN = "#6F8B3C"
MISO_TAN = "#D3A275"

# ---------- SESSION STATE ----------
st.session_state.setdefault("recipe_request", None)
st.session_state.setdefault("pending_sound", None)
st.session_state.setdefault("sound_n", 0)
st.session_state.setdefault("ingredients", "")
st.session_state.setdefault("generated_recipe", None)
st.session_state.setdefault("recipe_history", [])                    # titles already made (for "another meal")
st.session_state.setdefault("recipe_id", 0)
st.session_state.setdefault("page", "Home")                       # Home / My Recipes / Saved Recipes / Settings


# ---------- MODEL SETTINGS ----------
# Models are tried in order; if one is unavailable (404), overloaded (503) or rate limited (429)
# after retries, the next one is used. To force a model, add GEMINI_TEXT_MODEL to .streamlit/secrets.toml. Current names: ai.google.dev/gemini-api/docs/models
def _secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default) or default).strip()
    except Exception:
        return default


def _model_list(override: str, defaults: list) -> list:
    out = []
    for m in ([override] if override else []) + defaults:
        if m and m not in out:
            out.append(m)
    return out


TEXT_MODELS = _model_list(
    _secret("GEMINI_TEXT_MODEL", "gemini-3.8-flash"),
    ["gemini-3.8-flash"],
)
NUM_STEPS = 5                     # cooking steps requested from the AI
MAX_VALIDATION_ATTEMPTS = 3       # regenerate this many times if a recipe breaks the user's rules
RETRY_CODES = {429, 500, 502, 503, 504}
FALLBACK_CODES = {404, 429, 500, 502, 503, 504}


# ---------- CUISINES ----------
CUISINE_ANY = "Any cuisine / Surprise me"
CUISINE_OTHER = "Other / Custom cuisine"
CUISINES = [
    CUISINE_ANY, "Indian", "South Indian", "North Indian", "Indo-Chinese", "Chinese", "Italian",
    "Mexican", "Mediterranean", "American", "Japanese", "Korean", "Thai", "Middle Eastern",
    "Continental", CUISINE_OTHER,
]


def resolve_cuisine(choice: str, custom: str):
    """Returns (cuisine label, mode) where mode is 'chosen' or 'any'."""
    if choice == CUISINE_OTHER:
        cleaned = re.sub(r"[^\w\s&'\-/]", "", custom or "", flags=re.U)
        cleaned = " ".join(cleaned.split())[:40]
        return (cleaned, "chosen") if cleaned else (CUISINE_ANY, "any")
    if choice == CUISINE_ANY:
        return CUISINE_ANY, "any"
    return choice, "chosen"


# ---------- FOOD EMOJI LIBRARY ----------
# 131 foods shown in the "All foods" picker as (name, emoji). Names and emojis are unique.
FOOD_CATEGORIES = {
    "🍎 Fruits": [
        ("apple", "🍎"), ("green apple", "🍏"), ("pear", "🍐"), ("orange", "🍊"),
        ("lemon", "🍋"), ("banana", "🍌"), ("watermelon", "🍉"), ("grapes", "🍇"),
        ("strawberry", "🍓"), ("blueberries", "🫐"), ("melon", "🍈"), ("cherries", "🍒"),
        ("peach", "🍑"), ("mango", "🥭"), ("pineapple", "🍍"), ("coconut", "🥥"),
        ("kiwi", "🥝"),
    ],
    "🥦 Vegetables": [
        ("tomato", "🍅"), ("brinjal", "🍆"), ("avocado", "🥑"), ("broccoli", "🥦"),
        ("spinach", "🥬"), ("cucumber", "🥒"), ("chilli", "🌶️"), ("capsicum", "🫑"),
        ("corn", "🌽"), ("carrot", "🥕"), ("garlic", "🧄"), ("onion", "🧅"),
        ("potato", "🥔"), ("sweet potato", "🍠"), ("mushroom", "🍄"), ("sprouts", "🌱"),
    ],
    "🌾 Grains & Cereals": [
        ("rice", "🍚"), ("wheat", "🌾"), ("bread", "🍞"), ("roti", "🫓"),
        ("croissant", "🥐"), ("baguette", "🥖"), ("bagel", "🥯"), ("pretzel", "🥨"),
        ("pancakes", "🥞"), ("waffle", "🧇"), ("noodles", "🍜"), ("pasta", "🍝"),
        ("oats", "🥣"), ("rice cracker", "🍘"), ("rice ball", "🍙"),
    ],
    "🍗 Protein & Meat": [
        ("eggs", "🥚"), ("fried egg", "🍳"), ("chicken", "🍗"), ("whole chicken", "🐔"),
        ("meat", "🍖"), ("steak", "🥩"), ("beef", "🐄"), ("pork", "🐖"),
        ("mutton", "🐑"), ("turkey", "🦃"), ("duck", "🦆"), ("bacon", "🥓"),
        ("bone broth", "🦴"), ("fish", "🐟"), ("prawns", "🍤"), ("shrimp", "🦐"),
        ("squid", "🦑"), ("crab", "🦀"), ("lobster", "🦞"), ("oyster", "🦪"),
        ("tofu", "🍢"), ("fish cake", "🍥"), ("falafel", "🧆"),
    ],
    "🥛 Dairy & Alternatives": [
        ("milk", "🥛"), ("paneer", "🧀"), ("butter", "🧈"), ("baby milk", "🍼"),
        ("cheese fondue", "🫕"),
    ],
    "🍱 Meals & Main Dishes": [
        ("bento", "🍱"), ("pizza", "🍕"), ("burger", "🍔"), ("fries", "🍟"),
        ("hot dog", "🌭"), ("sandwich", "🥪"), ("taco", "🌮"), ("burrito", "🌯"),
        ("wrap", "🥙"), ("curry", "🍛"), ("salad", "🥗"), ("paella", "🥘"),
        ("stew", "🍲"), ("sushi", "🍣"), ("dumplings", "🥟"), ("popcorn", "🍿"),
        ("tamale", "🫔"), ("canned food", "🥫"), ("takeout", "🥡"),
    ],
    "🍰 Sweets & Desserts": [
        ("cake", "🍰"), ("birthday cake", "🎂"), ("cupcake", "🧁"), ("pie", "🥧"),
        ("chocolate", "🍫"), ("candy", "🍬"), ("lollipop", "🍭"), ("custard", "🍮"),
        ("honey", "🍯"), ("donut", "🍩"), ("cookie", "🍪"), ("ice cream", "🍨"),
        ("soft serve", "🍦"), ("shaved ice", "🍧"), ("moon cake", "🥮"), ("dango", "🍡"),
        ("fortune cookie", "🥠"),
    ],
    "🥤 Drinks & Beverages": [
        ("coffee", "☕"), ("tea", "🍵"), ("herbal tea", "🫖"), ("juice", "🧃"),
        ("soft drink", "🥤"), ("bubble tea", "🧋"), ("mate", "🧉"), ("water", "💧"),
        ("ice", "🧊"),
    ],
    "🥜 Nuts, Seeds & Legumes": [
        ("peanuts", "🥜"), ("beans", "🫘"), ("chestnut", "🌰"),
        ("sunflower seeds", "🌻"), ("dates", "🌴"),
    ],
    "🧂 Condiments, Oils & Spices": [
        ("salt", "🧂"), ("olive oil", "🫒"), ("herbs", "🌿"), ("bay leaf", "🍃"),
        ("pickle", "🫙"),
    ],
}

# Extra words people type that should still get the right emoji (matching and search only).
ALIASES = [
    ("eggplant", "🍆"), ("aubergine", "🍆"), ("palak", "🥬"), ("lettuce", "🥬"),
    ("cabbage", "🥬"), ("kale", "🥬"), ("coriander", "🌿"), ("mint", "🌿"),
    ("basil", "🌿"), ("parsley", "🌿"), ("curry leaves", "🌿"), ("ginger", "🌿"),
    ("chili", "🌶️"), ("chillies", "🌶️"), ("bell pepper", "🫑"), ("pepper", "🫑"),
    ("black pepper", "🧂"), ("peas", "🟢"), ("lamb", "🐑"), ("goat", "🐑"),
    ("yogurt", "🥛"), ("yoghurt", "🥛"), ("curd", "🥛"), ("soy milk", "🥛"),
    ("almond milk", "🥛"), ("oat milk", "🥛"), ("coconut milk", "🥛"),
    ("cheese", "🧀"), ("ghee", "🧈"), ("chapati", "🫓"), ("naan", "🫓"),
    ("paratha", "🫓"), ("dosa", "🥞"), ("idli", "🍘"), ("poha", "🍚"),
    ("upma", "🍚"), ("atta", "🌾"), ("flour", "🌾"), ("millet", "🌾"),
    ("ragi", "🌾"), ("quinoa", "🌾"), ("maggi", "🍜"), ("spaghetti", "🍝"),
    ("dal", "🍲"), ("lentils", "🍲"), ("rajma", "🫘"), ("chickpeas", "🫘"),
    ("chana", "🫘"), ("moong", "🫘"), ("almonds", "🥜"), ("cashews", "🥜"),
    ("walnuts", "🥜"), ("nuts", "🥜"), ("raisins", "🍇"), ("lime", "🍋"),
    ("guava", "🍐"), ("papaya", "🍈"), ("blueberry", "🫐"), ("strawberries", "🍓"),
    ("cherry", "🍒"), ("jam", "🍓"), ("jaggery", "🍯"), ("sugar", "🍬"),
    ("oil", "🫒"), ("olives", "🫒"), ("masala", "🧂"), ("turmeric", "🧂"),
    ("cumin", "🧂"), ("cinnamon", "🧂"), ("cardamom", "🧂"), ("spice", "🧂"),
    ("sauce", "🥫"), ("ketchup", "🥫"), ("achar", "🫙"), ("vinegar", "🫙"),
    ("french fries", "🍟"), ("sweet corn", "🌽"), ("tuna", "🐟"), ("salmon", "🐟"),
    ("prawn", "🍤"), ("noodle", "🍜"), ("egg", "🥚"),
]

assert sum(len(v) for v in FOOD_CATEGORIES.values()) == 131


def _build_patterns():
    pairs = {}
    for foods in FOOD_CATEGORIES.values():
        for name, emoji in foods:
            pairs.setdefault(name, emoji)
    for name, emoji in ALIASES:
        pairs.setdefault(name, emoji)
    # also match the singular of plural names (grapes -> grape)
    for name, emoji in list(pairs.items()):
        if name.endswith("s") and not name.endswith("ss") and len(name) > 3:
            pairs.setdefault(name[:-1], emoji)
    ordered = sorted(pairs.items(), key=lambda kv: len(kv[0]), reverse=True)  # longest first
    return [
        (re.compile(r"\b" + re.escape(k) + r"(?:e?s)?\b", re.I), e) for k, e in ordered
    ]


_PATTERNS = _build_patterns()
TOTAL_FOODS = sum(len(v) for v in FOOD_CATEGORIES.values())

QUICK_ADD = ["rice", "eggs", "onion", "tomato"]


def emoji_for(item: str) -> str:
    for pattern, emoji in _PATTERNS:
        if pattern.search(item):
            return emoji
    return "🍽️"


def parse_ingredients(text: str):
    parts = re.split(r",|;|\n|\band\b", text or "", flags=re.I)
    seen, out = set(), []
    for p in parts:
        name = p.strip(" .-•").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out[:20]


# ---------- LOGO ----------
# Built-in logo (bowl + sprout + AI spark). To use your own, save assets/logo.svg or assets/logo.png
# next to app.py: it is picked up automatically. If neither exists, this built-in version is used.
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 130" role="img" aria-label="Fitlicious AI logo">
<defs>
<linearGradient id="bowl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#94593A"/><stop offset=".5" stop-color="#673A25"/><stop offset="1" stop-color="#3C1F10"/></linearGradient>
<linearGradient id="rim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#EDCBA2"/><stop offset="1" stop-color="#B27C48"/></linearGradient>
<radialGradient id="tom" cx=".34" cy=".3" r=".78"><stop offset="0" stop-color="#EC7656"/><stop offset=".5" stop-color="#A93118"/><stop offset="1" stop-color="#651A0A"/></radialGradient>
<radialGradient id="avo" cx=".4" cy=".32" r=".85"><stop offset="0" stop-color="#F1F5D2"/><stop offset=".55" stop-color="#BDD07C"/><stop offset="1" stop-color="#6F8B3C"/></radialGradient>
<radialGradient id="pit" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#9A6040"/><stop offset="1" stop-color="#4A2614"/></radialGradient>
<radialGradient id="egg" cx=".4" cy=".3" r=".85"><stop offset="0" stop-color="#FBE9C9"/><stop offset=".6" stop-color="#E5BC86"/><stop offset="1" stop-color="#C08A52"/></radialGradient>
<linearGradient id="lf" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#A9C46C"/><stop offset="1" stop-color="#47601F"/></linearGradient>
<linearGradient id="frk" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#DD6648"/><stop offset=".5" stop-color="#A93118"/><stop offset="1" stop-color="#7A210F"/></linearGradient>
<filter id="sh" x="-20%" y="-20%" width="150%" height="150%"><feDropShadow dx="0" dy="3" stdDeviation="2.4" flood-color="#2A140A" flood-opacity=".38"/></filter>
</defs>
<ellipse cx="80" cy="123" rx="58" ry="5" fill="#2A140A" opacity=".2"/>
<g filter="url(#sh)">
<path d="M30 58C12 46 16 24 38 18C46 38 44 52 30 58Z" fill="url(#lf)"/>
<path d="M32 54C34 42 36 30 38 22" stroke="#DCE8BE" stroke-width="1.4" fill="none" opacity=".7"/>
<path d="M62 42C52 22 64 6 84 4C92 22 82 38 62 42Z" fill="url(#lf)"/>
<path d="M66 38C72 28 78 16 82 8" stroke="#DCE8BE" stroke-width="1.4" fill="none" opacity=".7"/>
<path d="M98 46C94 26 108 12 128 16C128 34 116 46 98 46Z" fill="url(#lf)"/>
<path d="M102 42C110 34 118 26 124 20" stroke="#DCE8BE" stroke-width="1.4" fill="none" opacity=".7"/>
<circle cx="44" cy="50" r="16" fill="url(#tom)"/>
<path d="M36 36c5 3 9 3 14 0-1 5-5 8-7 8s-6-3-7-8z" fill="#6F8B3C"/>
<ellipse cx="38" cy="43" rx="5" ry="3" fill="#fff" opacity=".28" transform="rotate(-30 38 43)"/>
<ellipse cx="80" cy="45" rx="17" ry="21" fill="url(#avo)"/>
<ellipse cx="80" cy="45" rx="17" ry="21" fill="none" stroke="#52692B" stroke-width="2.2"/>
<circle cx="80" cy="50" r="8" fill="url(#pit)"/>
<ellipse cx="77" cy="47" rx="2.6" ry="1.8" fill="#fff" opacity=".35"/>
<ellipse cx="114" cy="51" rx="15" ry="13" fill="url(#egg)"/>
<ellipse cx="108" cy="46" rx="5" ry="3" fill="#fff" opacity=".35" transform="rotate(-25 108 46)"/>
</g>
<g filter="url(#sh)">
<path d="M16 62H144C144 100 118 119 80 119C42 119 16 100 16 62Z" fill="url(#bowl)"/>
<path d="M24 74C27 94 44 109 68 113" stroke="#fff" stroke-width="3" stroke-linecap="round" fill="none" opacity=".2"/>
<path d="M21 84C52 91 108 91 139 84" stroke="#D3A275" stroke-width="3" stroke-linecap="round" fill="none" opacity=".75"/>
<path d="M11 57H149a6 6 0 0 1 0 10H11a6 6 0 0 1 0-10Z" fill="url(#rim)"/>
<path d="M16 59H144" stroke="#fff" stroke-width="1.6" stroke-linecap="round" opacity=".45"/>
</g>
<g transform="rotate(24 120 60)" fill="url(#frk)" filter="url(#sh)">
<rect x="111" y="2" width="3.6" height="29" rx="1.8"/><rect x="118.2" y="2" width="3.6" height="29" rx="1.8"/><rect x="125.4" y="2" width="3.6" height="29" rx="1.8"/>
<path d="M111 28h18v6c0 5-4 8-7 9v44c0 2.4-4 2.4-4 0V43c-3-1-7-4-7-9z"/>
</g>
<path d="M20 8l2.8 6.8 6.8 2.8-6.8 2.8L20 28l-2.8-6.8L10 18.4l6.8-2.8z" fill="#D3A275" stroke="#fff" stroke-width=".8" stroke-opacity=".6"/>
<path d="M30 30h9" stroke="#6F8B3C" stroke-width="2.2" stroke-linecap="round"/><circle cx="41" cy="30" r="2.6" fill="#6F8B3C"/>
</svg>"""

_APP_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()


@st.cache_data(show_spinner=False)
def logo_uri() -> str:
    """Data URI of the logo: assets/logo.svg, then assets/logo.png, then the built-in SVG."""
    try:
        svg_path = os.path.join(_APP_DIR, "assets", "logo.svg")
        if os.path.exists(svg_path):
            with open(svg_path, "r", encoding="utf-8") as f:
                text = f.read()
            if "<svg" in text:
                return "data:image/svg+xml;base64," + base64.b64encode(text.encode("utf-8")).decode()
        png_path = os.path.join(_APP_DIR, "assets", "logo.png")
        if os.path.exists(png_path):
            with open(png_path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        pass
    return "data:image/svg+xml;base64," + base64.b64encode(LOGO_SVG.encode("utf-8")).decode()


# ---------- SOUND ----------
_SOUND_JS = """
<script>
/* play __N__ */
const kind = "__KIND__";
const AC = window.AudioContext || window.webkitAudioContext;
if (AC) {
  const ctx = new AC();
  ctx.resume();
  const tone = (f, t, d, type, v) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || "sine"; o.frequency.value = f;
    const s = ctx.currentTime + t;
    g.gain.setValueAtTime(0.0001, s);
    g.gain.exponentialRampToValueAtTime(v || 0.18, s + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, s + d);
    o.connect(g); g.connect(ctx.destination);
    o.start(s); o.stop(s + d + 0.05);
  };
  if (kind === "pop") { tone(660, 0, .12); tone(990, .06, .14); }
  else if (kind === "success") { [523, 659, 784, 1047].forEach((f, i) => tone(f, i * .11, .28, "triangle")); }
  else if (kind === "error") { tone(260, 0, .22, "sawtooth", .08); tone(190, .16, .3, "sawtooth", .08); }
  else if (kind === "clear") { tone(520, 0, .1); tone(360, .08, .14); }
  setTimeout(() => ctx.close(), 2500);
}
</script>
"""


def queue_sound(kind: str):
    st.session_state.pending_sound = kind


def sound_script(kind: str) -> str:
    st.session_state.sound_n += 1
    return _SOUND_JS.replace("__KIND__", kind).replace("__N__", str(st.session_state.sound_n))


def play_sound_now(kind: str):
    """Plays right away, in the middle of the run. Browsers only allow sound shortly after a click,
    so anything that waits until the end of a slow AI call can end up silent."""
    if st.session_state.get("sound_effects", True):
        components.html(sound_script(kind), height=0)


# ---------- BACKGROUND MUSIC ----------
# The tune is synthesized here in Python and played with Streamlit's own audio player,
# so there is nothing to download. To use your own song, save it as music.mp3 (or music.wav) next to app.py.
@st.cache_data(show_spinner=False)
def make_music_wav() -> bytes:
    import numpy as np

    sr = 22050
    spb = 60 / 88                                   # seconds per beat (88 bpm)
    bars = 8
    total = int(sr * spb * 4 * bars)
    tail = int(sr * 2)                              # lets the last notes ring into the loop start
    buf = np.zeros(total + tail, dtype=np.float32)
    rng = np.random.default_rng(7)

    chords = [[48, 52, 55, 60], [45, 48, 52, 57], [41, 45, 48, 53], [43, 47, 50, 55]]  # C Am F G
    bass = [36, 33, 29, 31]
    pent = [60, 62, 64, 67, 69, 72, 74, 76]         # C major pentatonic
    pattern = [1, 0, 1, 1, 0, 1, 0, 1]

    def add(midi, t, dur, kind, vol, decay=3.0):
        i0 = int(t * sr)
        n = min(int(dur * sr), len(buf) - i0)
        if n <= 0:
            return
        x = np.arange(n) / sr
        phase = 2 * np.pi * 440.0 * 2 ** ((midi - 69) / 12) * x
        wave_ = np.sin(phase) if kind == "sine" else (2 / np.pi) * np.arcsin(np.sin(phase))
        env = np.minimum(1.0, x / 0.02) * np.exp(-decay * x / dur) * np.clip((dur - x) / 0.03, 0, 1)
        buf[i0:i0 + n] += (wave_ * env * vol).astype(np.float32)

    def hat(t, vol):
        i0 = int(t * sr)
        n = min(int(0.05 * sr), len(buf) - i0)
        if n <= 1:
            return
        noise = np.diff(rng.uniform(-1, 1, n + 1))   # differencing keeps only the bright part
        buf[i0:i0 + n] += (noise * np.exp(-np.arange(n) / sr * 80) * vol).astype(np.float32)

    for b in range(bars):
        t0 = b * spb * 4
        chord = chords[b % 4]
        for m in chord:                                                   # soft pad
            add(m, t0, spb * 3.8, "tri", 0.05, decay=0.8)
        add(bass[b % 4], t0, spb * 1.8, "sine", 0.18)                     # bass
        add(bass[b % 4], t0 + spb * 2, spb * 1.4, "sine", 0.14)
        for i in range(8):                                                # arpeggio
            add(chord[i % 4] + 12, t0 + i * spb / 2, spb * 0.45, "sine", 0.07)
        for i in range(8):                                                # melody
            if pattern[(i + b) % 8]:
                add(pent[(b * 3 + i * 2 + (i % 3)) % len(pent)], t0 + i * spb / 2 + 0.02, spb * 0.5, "tri", 0.07)
        for i in range(8):                                                # hi-hat
            hat(t0 + i * spb / 2, 0.03 if i % 2 == 0 else 0.015)

    buf = np.convolve(buf, np.ones(3) / 3, mode="same")                  # soften = lo-fi warmth
    buf[:tail] += buf[total:]                                            # wrap the tail so the loop is seamless
    buf = buf[:total]
    buf = np.tanh(buf * 2.5)                                             # gentle compression: far louder, same tune
    buf *= 0.98 / max(1e-6, float(np.max(np.abs(buf))))                  # maximum volume without clipping
    pcm = (buf * 32767).astype(np.int16)

    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out.getvalue()


def music_track():
    for filename, mime in (("music.mp3", "audio/mpeg"), ("music.wav", "audio/wav")):
        path = os.path.join(_APP_DIR, filename)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read(), mime
    return make_music_wav(), "audio/wav"


# ---------- CALLBACKS ----------
def toggle_ingredient(name: str):
    """Tap once to add to the basket, tap again to remove."""
    items = parse_ingredients(st.session_state.ingredients)
    if name.lower() in [i.lower() for i in items]:
        items = [i for i in items if i.lower() != name.lower()]
        queue_sound("clear")
    else:
        items.append(name)
        queue_sound("pop")
    st.session_state.ingredients = ", ".join(items)


def clear_ingredients():
    st.session_state.ingredients = ""
    queue_sound("clear")


def request_another_meal():
    """Button callback: the generator below picks this flag up on the next run."""
    st.session_state.regen_requested = True


# ---------- FOOD RAIN ANIMATION ----------
def emoji_rain(emojis, count=26):
    rnd = random.Random(time.time())
    spans = []
    for i in range(count):
        e = emojis[i % len(emojis)]
        spans.append(
            f'<span class="rain" style="left:{rnd.randint(2, 94)}%;'
            f'animation-delay:{rnd.uniform(0, 1.6):.2f}s;'
            f'animation-duration:{rnd.uniform(3, 5.2):.2f}s;'
            f'font-size:{rnd.randint(28, 50)}px">{e}</span>'
        )
    st.markdown('<div class="rain-wrap">' + "".join(spans) + "</div>", unsafe_allow_html=True)


# ---------- THEME ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@500;600;700;800&display=swap');

:root {
    --red: #A93118;
    --red-d: #8A2712;
    --brown: #673A25;
    --rice: #E8E2D6;
    --green: #6F8B3C;
    --green-d: #52692B;
    --tan: #D3A275;
    --paper: #F7F3EA;          /* a lighter tint of Warm Rice for cards */
    --line: #CDBFA6;           /* a darker tint of Warm Rice for borders */
    --muted: #7A5C4B;
    --ink: #673A25;
    --head-font: 'Poppins', 'Segoe UI', Arial, sans-serif;
    --body-font: 'Inter', 'Segoe UI', Arial, sans-serif;
}

.stApp { background: var(--rice); color: var(--ink); }
.stApp, .stApp p, .stApp label, .stApp li, .stApp input, .stApp textarea, .stApp button {
    font-family: var(--body-font);
}
/* Streamlit draws its arrows/chevrons with an icon font. Never override that font, or the icon
   names ("arrow_down", "expand_more") show up as plain text on top of the labels. */
.stApp [data-testid="stIconMaterial"], .stApp span[class*="material"], .stApp .material-icons,
.stApp .material-symbols-rounded, .stApp .material-symbols-outlined {
    font-family: "Material Symbols Rounded", "Material Symbols Outlined", "Material Icons" !important;
}
.stApp p, .stApp li, .stApp label, [data-testid="stWidgetLabel"] p { color: var(--ink); }
[data-testid="stWidgetLabel"] p { font-weight: 600; font-size: 15px; }
.stApp h1, .stApp h2, .stApp h3 { font-family: var(--head-font) !important; color: var(--brown) !important; font-weight: 600 !important; }
.stApp h3 { font-size: 22px !important; }

header[data-testid="stHeader"] { background: transparent; }
footer, #MainMenu { visibility: hidden; }

.block-container { max-width: 1040px; padding-top: 0.8rem; padding-bottom: 3rem; }

/* ---- top bar / brand ---- */
.topbar {
    position: relative; overflow: hidden; display: flex; align-items: center; justify-content: space-between;
    gap: 16px; background: var(--paper); border: 1.5px solid var(--line); border-radius: 24px;
    padding: 14px 22px; margin-bottom: 10px;
}
.topbar::after {
    content: ""; position: absolute; right: -40px; top: 0; bottom: 0; width: 46%;
    background: var(--brown); clip-path: polygon(34% 0, 100% 0, 100% 100%, 0 100%);
}
.topbar::before {
    content: ""; position: absolute; right: -40px; top: 0; bottom: 0; width: 50%;
    background: var(--red); clip-path: polygon(30% 0, 36% 0, 6% 100%, 0 100%);
}
.brand { display: flex; align-items: center; gap: 14px; position: relative; z-index: 2; }
.brand-logo { width: 74px; height: 62px; object-fit: contain; }
.brand-name { font-family: var(--head-font); font-weight: 800; font-size: clamp(28px, 4.6vw, 44px); color: var(--brown); line-height: 1.05; }
.brand-name span { color: var(--green); }
.brand-caption { font-size: 15px; color: var(--ink); margin-top: 2px; }
.ribbon {
    position: relative; z-index: 2; color: var(--rice); font-family: var(--head-font); font-weight: 600;
    font-size: 16px; line-height: 1.25; text-align: right; font-style: italic; padding-right: 6px;
}

.hero {
    position: relative; overflow: hidden; margin: 14px 0 14px;
    background: var(--tan); border: 2px solid #BF8C5E; border-radius: 26px; padding: 30px 34px;
}
.hero-title {
    font-family: var(--head-font); font-weight: 700; font-size: clamp(26px, 4.2vw, 42px);
    line-height: 1.15; color: var(--brown); max-width: 540px; margin-bottom: 10px;
}
.hero-text { font-size: 16.5px; line-height: 1.6; color: var(--brown); max-width: 500px; }
.float { position: absolute; animation: bob 4s ease-in-out infinite; }
.f1 { right: 9%;  top: 14%;    font-size: 64px; }
.f2 { right: 24%; bottom: 12%; font-size: 40px; animation-delay: -1.2s; }
.f3 { right: 6%;  bottom: 10%; font-size: 44px; animation-delay: -2.4s; }
.f4 { right: 28%; top: 12%;    font-size: 34px; animation-delay: -.6s; }

.how { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 8px; }
.how-item {
    display: flex; align-items: center; gap: 7px; background: var(--paper);
    border: 1.5px solid var(--line); border-radius: 999px; padding: 4px 12px 4px 4px;
    font-weight: 600; font-size: 13px; color: var(--ink);
}
.how-num {
    width: 20px; height: 20px; border-radius: 50%; background: var(--red); color: #fff;
    display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700;
}

/* ---- section headings ---- */
.sec { display: flex; align-items: center; gap: 10px; margin: 26px 0 10px; }
.sec-num {
    min-width: 28px; height: 28px; border-radius: 50%; background: var(--red); color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-family: var(--head-font); font-weight: 600; font-size: 14px;
}
.sec-title { font-family: var(--head-font); font-weight: 600; font-size: 20px; color: var(--brown); line-height: 1.2; }
.sec-sub { font-size: 13px; color: var(--muted); }

/* ---- form controls ---- */
div[data-baseweb="input"], div[data-baseweb="textarea"], div[data-baseweb="select"] > div {
    background: var(--paper) !important; border-color: var(--line) !important; border-radius: 12px !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within {
    border-color: var(--red) !important; box-shadow: 0 0 0 2px rgba(169,49,24,.22) !important;
}
input, textarea { color: var(--ink) !important; -webkit-text-fill-color: var(--ink) !important; }
input::placeholder, textarea::placeholder { color: #9A8272 !important; -webkit-text-fill-color: #9A8272 !important; }
div[data-baseweb="select"] * { color: var(--ink) !important; }
div[data-baseweb="popover"], div[data-baseweb="popover"] ul { background: var(--paper) !important; }
div[data-baseweb="popover"] li { color: var(--ink) !important; }
div[data-baseweb="popover"] li:hover { background: var(--rice) !important; }
div[data-testid="stNumberInput"] button { background: var(--rice); color: var(--ink); }

div[data-testid="stRadio"] > div { gap: 12px; }
div[data-testid="stRadio"] label {
    background: var(--paper); border: 2px solid var(--line); border-radius: 14px;
    padding: 12px 18px; cursor: pointer; transition: border-color .15s, background .15s;
}
div[data-testid="stRadio"] label:hover { border-color: var(--red); }
div[data-testid="stRadio"] label:has(input:checked) { background: #F1D9CF; border-color: var(--red); }
div[data-testid="stRadio"] label p { color: var(--ink) !important; font-weight: 600; font-size: 16px; }

div[data-baseweb="slider"] div[role="slider"] { background: var(--red) !important; }
div[data-testid="stSlider"] * { color: var(--ink); }

div[data-testid="stExpander"] { background: var(--paper); border: 1.5px solid var(--line); border-radius: 14px; }
div[data-testid="stExpander"] summary p { font-weight: 600; color: var(--ink) !important; }
div[data-testid="stExpander"] summary, div[data-testid="stExpander"] details > summary { background: var(--paper) !important; border-radius: 14px; }
div[data-testid="stExpander"] summary:hover { background: #F1E9D8 !important; }
div[data-testid="stExpander"] summary svg, div[data-testid="stExpander"] summary [data-testid="stIconMaterial"] { color: var(--brown) !important; }
textarea, input { background: transparent !important; }

div[data-testid="stCheckbox"] label p { color: var(--ink) !important; line-height: 1.45; }
div[data-testid="stCheckbox"]:has(input:checked) label p { color: var(--green-d) !important; }

/* ---- buttons ---- */
.stApp button[kind="secondary"], .stApp button[data-testid="stBaseButton-secondary"] {
    background: var(--paper); border: 1.5px solid var(--line); border-radius: 12px;
    min-height: 44px; transition: border-color .15s, background .15s;
}
.stApp button[kind="secondary"] p, .stApp button[data-testid="stBaseButton-secondary"] p { color: var(--ink) !important; font-weight: 600; }
.stApp button[kind="secondary"]:hover, .stApp button[data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--red); background: #F1D9CF;
}
.stApp button[kind="primary"], .stApp button[data-testid="stBaseButton-primary"] {
    background: var(--red); border: none; border-radius: 14px; min-height: 56px;
    box-shadow: 0 4px 0 #6E1F0F; transition: transform .1s, box-shadow .1s, background .15s;
}
.stApp button[kind="primary"] p, .stApp button[data-testid="stBaseButton-primary"] p {
    color: #FFFFFF !important; font-weight: 700; font-size: 19px; font-family: var(--head-font);
}
.stApp button[kind="primary"]:hover, .stApp button[data-testid="stBaseButton-primary"]:hover { background: var(--red-d); }
.stApp button[kind="primary"]:active, .stApp button[data-testid="stBaseButton-primary"]:active {
    transform: translateY(3px); box-shadow: 0 1px 0 #6E1F0F;
}
.stApp button:focus-visible { outline: 3px solid var(--green) !important; outline-offset: 2px; }

/* ---- music plays in the background: hide the player box (audio keeps playing) ---- */
audio[data-testid="stAudio"], div[data-testid="stAudio"], .stAudio,
div[data-testid="stElementContainer"]:has(audio),
div.element-container:has(audio) {
    position: absolute !important; width: 1px !important; height: 1px !important;
    margin: 0 !important; padding: 0 !important; overflow: hidden !important;
    opacity: 0 !important; pointer-events: none !important;
}

/* ---- all-foods picker ---- */
div[data-testid="stPopoverBody"] { background: var(--paper); min-width: min(470px, 88vw); }
div[data-testid="stPopoverBody"] button { min-height: 40px !important; padding: 0 !important; }
div[data-testid="stPopoverBody"] button p { font-size: 22px !important; }
div[data-testid="stPopoverBody"] button[kind="secondary"]:hover { background: var(--rice) !important; }

/* ---- ingredient basket ---- */
.basket {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    background: var(--paper); border: 2px dashed var(--line); border-radius: 18px;
    padding: 14px 16px; min-height: 84px; margin: 6px 0 10px;
}
.basket-empty { color: var(--muted); font-size: 15px; }
.chip {
    display: inline-flex; align-items: center; gap: 8px; background: #E4EBD3;
    border: 1.5px solid var(--green); border-radius: 999px; padding: 5px 14px 5px 8px;
    font-weight: 600; color: var(--ink); animation: pop .45s cubic-bezier(.34, 1.56, .64, 1) both;
}
.chip-emoji { font-size: 26px; display: inline-block; animation: wiggle 2.8s ease-in-out infinite; }

/* ---- result cards ---- */
div[class*="st-key-card_"] { background: var(--paper) !important; border-radius: 22px !important; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 22px; border-color: var(--line); }

.dish-frame {
    position: relative; width: 100%; aspect-ratio: 4 / 3; border-radius: 18px; overflow: hidden;
    background: var(--rice); border: 1.5px solid var(--line); box-shadow: 0 4px 14px rgba(103,58,37,.18);
}
.dish-frame img { width: 100%; height: 100%; object-fit: cover; display: block; border-radius: 0; }
.dish-loading {
    height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; text-align: center; padding: 16px; font-weight: 600; color: var(--brown);
    background: linear-gradient(100deg, var(--rice) 30%, var(--paper) 50%, var(--rice) 70%);
    background-size: 200% 100%; animation: shimmer 1.6s linear infinite;
}
.dish-loading small { font-weight: 400; color: var(--muted); }
.dish-cap { font-size: 12.5px; color: var(--muted); margin: 6px 2px 4px; line-height: 1.4; }
.dish-cap a { color: var(--red); }

.tags { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.tag {
    display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 4px 12px;
    font-size: 13px; font-weight: 600; border: 1.5px solid transparent;
}
.tag-green { background: #E4EBD3; color: var(--green-d); border-color: var(--green); }
.tag-red { background: var(--red); color: #fff; }
.tag-tan { background: #F1DFC9; color: var(--brown); border-color: var(--tan); }
.plan-title { font-family: var(--head-font); font-weight: 700; font-size: clamp(24px, 3.6vw, 34px); color: var(--brown); line-height: 1.12; margin: 6px 0 8px; }
.plan-desc { font-size: 16px; line-height: 1.55; color: var(--ink); margin-bottom: 4px; }
.serve-line { font-size: 14px; color: var(--muted); margin-top: 6px; }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 14px 0; }
.tile { background: var(--rice); border: 1.5px solid var(--line); border-radius: 14px; padding: 10px 12px; }
.tile-icon { font-size: 22px; }
.tile-label { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.tile-value { font-weight: 700; font-size: 16px; color: var(--brown); }
.tile-value.big { font-size: 21px; color: var(--green-d); }

.card-head {
    display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 12px;
    font-family: var(--head-font); font-weight: 600; font-size: 17px; margin-bottom: 12px; color: #fff;
}
.card-head small { font-family: var(--body-font); font-weight: 400; font-size: 13px; opacity: .92; }
.ch-red { background: var(--red); } .ch-green { background: var(--green); }
.ch-tan { background: var(--tan); color: var(--brown); } .ch-brown { background: var(--brown); }
.group-label { font-weight: 700; font-size: 14px; color: var(--brown); margin: 8px 0 2px; }
.group-label.extra { margin-top: 14px; padding-top: 10px; border-top: 1.5px solid var(--line); }

.nrow {
    display: flex; align-items: center; gap: 12px; background: var(--rice); border: 1.5px solid var(--line);
    border-radius: 14px; padding: 8px 12px; margin-bottom: 8px;
}
.nicon {
    width: 38px; height: 38px; min-width: 38px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-size: 19px; color: #fff;
}
.nl { font-size: 12.5px; color: var(--muted); line-height: 1.1; }
.nv { font-weight: 700; font-size: 17px; color: var(--brown); }
.crow {
    display: flex; align-items: center; gap: 12px; background: var(--rice); border-radius: 12px;
    padding: 7px 12px; margin-bottom: 7px;
}
.cicon { font-size: 20px; width: 26px; text-align: center; }

div[data-testid="stMetric"] { background: var(--rice); border: 1.5px solid var(--line); border-radius: 14px; padding: 10px 14px; }
div[data-testid="stMetricValue"] { color: var(--green-d); }

/* ---- footer ---- */
.footer {
    margin-top: 48px; background: var(--brown); color: var(--rice); border-radius: 24px 24px 0 0;
    padding: 22px 16px 24px; text-align: center; font-size: 14px;
}
.footer-badge { display: inline-flex; background: var(--rice); border-radius: 50%; padding: 6px; margin-bottom: 6px; }
.footer-badge img { width: 54px; height: 46px; object-fit: contain; }
.footer-name { font-family: var(--head-font); font-weight: 700; font-size: 20px; color: #fff; }
.footer small { color: var(--tan); }

/* ---- falling food ---- */
.rain-wrap { position: fixed; inset: 0; pointer-events: none; overflow: hidden; z-index: 9999; }
.rain { position: absolute; top: -70px; animation: rainFall linear forwards; }

@keyframes bob { 0%, 100% { transform: translateY(0) rotate(-4deg); } 50% { transform: translateY(-12px) rotate(5deg); } }
@keyframes pop { from { transform: scale(.2); opacity: 0; } to { transform: scale(1); opacity: 1; } }
@keyframes wiggle { 0%, 100% { transform: rotate(0); } 50% { transform: rotate(10deg); } }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
@keyframes rainFall {
    0% { transform: translateY(-70px) rotate(0); opacity: 0; }
    8% { opacity: 1; }
    100% { transform: translateY(115vh) rotate(360deg); opacity: 1; }
}

@media (max-width: 650px) {
    .hero { padding: 22px 18px; }
    .f2, .f3, .f4 { display: none; }
    .f1 { font-size: 40px; right: 5%; top: 6%; }
    .topbar { flex-direction: column; align-items: flex-start; }
    .topbar::after, .topbar::before { display: none; }
    .ribbon { color: var(--brown); text-align: left; }
    .brand-logo { width: 58px; height: 50px; }
}
@media (prefers-reduced-motion: reduce) {
    * { animation: none !important; }
}
</style>
""", unsafe_allow_html=True)


def section(num: int, title: str, sub: str):
    st.markdown(
        f'<div class="sec"><div class="sec-num">{num}</div>'
        f'<div><div class="sec-title">{title}</div><div class="sec-sub">{sub}</div></div></div>',
        unsafe_allow_html=True,
    )


def stat_tiles(rows, big: bool = False):
    """Cards that never truncate their text (st.metric cuts long values to 'Approx...')."""
    cls = "tile-value big" if big else "tile-value"
    cards = "".join(
        f'<div class="tile"><div class="tile-icon">{ic}</div>'
        f'<div class="tile-label">{html.escape(lb)}</div>'
        f'<div class="{cls}">{html.escape(val)}</div></div>'
        for ic, lb, val in rows
    )
    st.markdown(f'<div class="tiles">{cards}</div>', unsafe_allow_html=True)


def card_head(icon: str, title: str, sub: str = "", tone: str = "red"):
    small = f" <small>{html.escape(sub)}</small>" if sub else ""
    st.markdown(
        f'<div class="card-head ch-{tone}"><span>{icon}</span><span>{html.escape(title)}{small}</span></div>',
        unsafe_allow_html=True,
    )


def card(key: str):
    """Bordered container; gets the card background when this Streamlit version supports container keys."""
    try:
        return st.container(border=True, key=f"card_{key}")
    except TypeError:
        return st.container(border=True)


# =====================================================================
# AI LAYER: errors, retries, recipe generation, validation, image
# =====================================================================
class RecipeError(Exception):
    """A problem we can explain to the user in plain words."""


class NoSuitableRecipe(Exception):
    """The AI says the ingredients/rules cannot make a safe, suitable recipe."""


def get_api_key():
    try:
        key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        return None
    return key.strip() if isinstance(key, str) and key.strip() else None


def _scrub(text: str) -> str:
    """Make sure API keys can never be shown in an error message or log line."""
    for key in (get_api_key(),):
        if key:
            text = text.replace(key, "***")
    return text


def _short(e: Exception, limit: int = 240) -> str:
    msg = getattr(e, "message", None) or str(e)
    return _scrub(" ".join(str(msg).split()))[:limit]


def make_client(api_key: str, timeout_ms: int = 90_000):
    try:
        return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
    except Exception:
        return genai.Client(api_key=api_key)


def _is_network_error(e: Exception) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(k in s for k in (
        "getaddrinfo", "connecterror", "connecttimeout", "readtimeout", "timeout",
        "name or service not known", "failed to resolve", "connection", "11001", "10060",
    ))


def call_with_retry(fn, tries: int = 4, first_delay: float = 2.0, retry_codes=RETRY_CODES):
    """Retry temporary failures (HTTP 5xx, optionally 429, network blips) with growing pauses."""
    delay = first_delay
    for attempt in range(tries):
        try:
            return fn()
        except genai_errors.APIError as e:
            if getattr(e, "code", None) in retry_codes and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except Exception as e:
            if _is_network_error(e) and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def friendly_error(e: Exception):
    """Returns (message for the user, short technical detail)."""
    detail = _scrub(f"{type(e).__name__}: {e}")[:300]
    if isinstance(e, genai_errors.APIError):
        code = getattr(e, "code", None)
        text = str(e).lower()
        if "api key" in text or code in (401, 403):
            return ("Gemini rejected your API key or it doesn't have permission for this model. "
                    "Check GEMINI_API_KEY in .streamlit/secrets.toml.", detail)
        if code == 404:
            return ("That Gemini model isn't available to your API key. "
                    "Set GEMINI_TEXT_MODEL in secrets.toml to a model from Google's current list.", detail)
        if code == 429:
            return ("You've reached the Gemini rate or quota limit. Wait a minute and try again, "
                    "or check your plan and quota in Google AI Studio.", detail)
        if code in (500, 502, 503, 504):
            return (f"Google's AI servers are busy right now (HTTP {code}). We retried several times. "
                    "Please try again in a minute.", detail)
        if code == 400:
            return ("Gemini couldn't process that request (HTTP 400). Try again, "
                    "or simplify your ingredients and preferences.", detail)
        return (f"Gemini returned an error (HTTP {code}). Please try again.", detail)
    if _is_network_error(e):
        return ("Can't reach Google's servers. Check your internet connection, VPN or proxy, "
                "and that generativelanguage.googleapis.com isn't blocked on your network.", detail)
    return ("Something unexpected went wrong while talking to the AI. Please try again.", detail)


# ---------- safe number handling ----------
NUTRIENT_FIELDS = {
    "calories": ("calories", "calories_kcal", "kcal", "energy_kcal"),
    "protein": ("protein_g", "protein"),
    "carbs": ("carbs_g", "carbohydrates_g", "carbohydrates", "carbs"),
    "fat": ("fat_g", "fat"),
    "fiber": ("fiber_g", "fibre_g", "fiber", "fibre"),
}
NUTRIENT_LIMITS = {"calories": 3000, "protein": 300, "carbs": 400, "fat": 250, "fiber": 120}  # per serving


def to_number(value):
    """Turns 385, 34.5, '385', 'Approx. 385 kcal', '350-400 kcal' into a float. None if there is no real number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
    elif isinstance(value, str):
        if re.match(r"\s*-\s*\d", value):                  # "-5" is not a valid nutrition value
            return None
        s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value)      # 1,200 -> 1200
        s = re.sub(r"(?<=\d),(?=\d)", ".", s)              # 3,5 -> 3.5
        rng = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)", s)
        if rng:
            x = (float(rng.group(1)) + float(rng.group(2))) / 2
        else:
            m = re.search(r"\d+(?:\.\d+)?", s)
            if not m:
                return None
            x = float(m.group(0))
    else:
        return None
    if not math.isfinite(x) or x < 0:
        return None
    return x


def _pick(d, *names):
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d:
            v = to_number(d[n])
            if v is not None:
                return v
    return None


def _round_nutrient(key: str, v):
    if v is None:
        return None
    if key == "calories":
        return float(int(round(v / 5.0) * 5))     # nearest 5 kcal: avoids fake precision
    return float(round(v, 1)) if v < 10 else float(int(round(v)))


def fmt_nutrient(key: str, v) -> str:
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "Not available"
    unit = "kcal" if key == "calories" else "g"
    num = str(int(v)) if float(v).is_integer() else f"{v:.1f}"
    return f"{num} {unit}"


def compute_nutrition(raw_ingredients: list, data: dict, servings: int) -> dict:
    """Per-serving nutrition = sum of the ingredient totals / servings.
    Falls back to the AI's own per-serving numbers only for values that could not be summed."""
    servings = max(1, int(servings or 1))
    n_ing = len(raw_ingredients)
    result = {k: None for k in NUTRIENT_FIELDS}
    from_ingredients = False

    for key, names in NUTRIENT_FIELDS.items():
        have = [v for v in (_pick(i, *names) for i in raw_ingredients) if v is not None]
        if have and len(have) >= max(1, math.ceil(0.5 * n_ing)):
            result[key] = sum(have) / servings
            from_ingredients = True

    per_serving = data.get("nutrition_per_serving")
    if not isinstance(per_serving, dict):
        per_serving = data.get("nutrition") if isinstance(data.get("nutrition"), dict) else {}
    used_model_values = False
    for key, names in NUTRIENT_FIELDS.items():
        if result[key] is None:
            v = _pick(per_serving, *names)
            if v is not None:
                result[key] = v
                used_model_values = True

    # Calories must agree with the macros (4 kcal/g protein and carbs, 9 kcal/g fat).
    p, c, f = result["protein"], result["carbs"], result["fat"]
    if None not in (p, c, f):
        derived = 4 * p + 4 * c + 9 * f
        cal = result["calories"]
        if cal is None or abs(cal - derived) > max(60.0, 0.35 * derived):
            result["calories"] = derived

    for key, hi in NUTRIENT_LIMITS.items():     # drop absurd values instead of showing them
        v = result[key]
        if v is not None and not (0 <= v <= hi):
            result[key] = None

    result = {k: _round_nutrient(k, v) for k, v in result.items()}

    if from_ingredients:
        note = (f"Estimates per serving: the listed ingredient quantities are added up and divided by "
                f"{servings} serving{'s' if servings > 1 else ''}. Real values vary with brands and "
                "preparation. Not lab-verified.")
    elif used_model_values:
        note = ("Rough AI estimate per serving. Real values vary with brands and preparation. "
                "Not lab-verified.")
    else:
        note = "A nutrition estimate could not be produced for this recipe."
    result["note"] = note
    return result


def _minutes_from(data: dict, number_key: str, text_key: str):
    v = to_number(data.get(number_key))
    if v is None:
        v = to_number(data.get(text_key))
    return None if v is None else int(round(v))


def build_preference_match(recipe: dict, req: dict) -> list:
    """'How this matches your choices' lines, written in code from the real numbers (no extra AI tokens)."""
    n = recipe["nutrition"]
    cal = fmt_nutrient("calories", n["calories"])
    pro = fmt_nutrient("protein", n["protein"])
    fib = fmt_nutrient("fiber", n["fiber"])
    goal = req["goal"]
    if "Fat loss" in goal:
        first = (f"Fat loss: about {cal} per serving with {pro} protein."
                 if n["calories"] is not None and n["protein"] is not None
                 else "Fat loss: a light, protein-friendly meal.")
    elif "Muscle gain" in goal:
        first = (f"Muscle gain: about {pro} protein and {cal} per serving."
                 if n["calories"] is not None and n["protein"] is not None
                 else "Muscle gain: a protein-focused meal.")
    else:
        first = (f"Healthy eating: a balanced meal with {fib} fiber per serving."
                 if n["fiber"] is not None else "Healthy eating: a balanced everyday meal.")
    lines = [first, f'{req["diet"]} diet: the ingredients were checked against it automatically.']
    if req["cuisine_mode"] == "chosen":
        lines.append(f'{req["cuisine"]} cuisine: dish, seasonings and cooking method are styled to match.')
    else:
        lines.append(f'Cuisine: chosen for you based on your ingredients ({recipe["cuisine"]}).')
    if req["allergies"]:
        lines.append(f'Checked against your avoid list: {req["allergies"]}.')
    lines.append(f'Spice level set to {req["spice"].lower()}.')
    if recipe["total_min"] <= int(req["cooking_time"]):
        lines.append(f'Ready in {recipe["total_min"]} min, within your {req["cooking_time"]} min limit.')
    else:
        lines.append(f'Takes {recipe["total_min"]} min, over your {req["cooking_time"]} min limit.')
    lines.append(f'Written for a {req["experience"].lower()} cook.')
    return lines


# ---------- prompt (kept short: fewer tokens = faster answer) ----------
def build_prompt(req: dict, avoid_titles: list, feedback: str) -> str:
    if req["diet"] == "Vegetarian":
        diet_rule = "No chicken, fish, meat, seafood or gelatin. Eggs and dairy are allowed."
    elif req["diet"] == "Vegan":
        diet_rule = "No meat, fish, seafood, eggs, dairy, honey or any other animal product."
    else:
        diet_rule = "Meat, fish and eggs are permitted if appropriate."

    if req["cuisine_mode"] == "chosen":
        cuisine_line = (
            f'{req["cuisine"]}. The dish name, spice blend and seasonings, cooking method, flavour profile, '
            f'cooking steps and serving style must all clearly reflect {req["cuisine"]} cuisine. '
            "Respect the ingredients, diet and avoid list first; add only a few cuisine-typical extras "
            "(sauce, spice, herb) if needed."
        )
        indian_line = ""
    else:
        cuisine_line = "Any - choose the cuisine that suits these ingredients best and name it in the \"cuisine\" field."
        indian_line = f' | INDIAN STYLE: {req["indian_food"]}'

    avoid_block = ""
    if avoid_titles:
        avoid_block = (
            "\nThe user already got these recipes. Make a clearly DIFFERENT dish "
            "(different main idea and cooking style): " + "; ".join(avoid_titles[-5:]) + "\n"
        )
    feedback_block = f"\nYOUR PREVIOUS ATTEMPT WAS REJECTED: {feedback}\nFix this.\n" if feedback else ""

    return f"""
You are Fitlicious AI. Create ONE realistic, beginner-friendly recipe built mainly from the user's ingredients.

INGREDIENTS: {", ".join(req["items"])}
GOAL: {req["goal"]}
DIET: {req["diet"]} -> {diet_rule}
CUISINE: {cuisine_line}
TIME: prep_minutes + cook_minutes must be {req["cooking_time"]} or less
SERVINGS: {req["servings"]}
SPICE: {req["spice"]}
COOKING EXPERIENCE: {req["experience"]}
ALLERGIES / AVOID: {req["allergies"] or "None"}
HIGH PROTEIN: {req["high_protein"]}{indian_line} | USE LEFTOVERS: {req["use_leftovers"]}
USE ONLY LISTED INGREDIENTS: {req["available_only"]}
SUBSTITUTE FOR: {req["missing_ingredients"] or "None"}
INVENTORY / EXPIRY: {req["inventory"] or "None"}
{avoid_block}{feedback_block}
RULES
- Use the listed ingredients. Add as few extras as possible (water, salt, oil, common spices are fine).
- Never use anything that conflicts with the diet or the avoid list, even if it is listed: leave it out.
- If "use only listed ingredients" is True, add only water, salt, oil and basic spices.
- If no safe recipe is possible, return "title": "No suitable recipe", explain in "description", empty lists.
- Exact quantity for every ingredient, scaled for {req["servings"]} serving(s).
- For EVERY ingredient give plain numbers (no units, no words) for the WHOLE quantity listed
  (all {req["servings"]} serving(s) together, not per 100 g): calories, protein_g, carbs_g, fat_g, fiber_g.
  Use 0 for water and salt. "nutrition_per_serving" = those totals divided by {req["servings"]}.
- prep_minutes and cook_minutes are whole numbers; step timings must match them.
- EXACTLY {NUM_STEPS} steps for a {req["experience"]} cook: say exactly what to do, "heat" (e.g. "medium heat"
  or "180 C") when cooking, "timing" (e.g. "3 min"), and a "tip" of under 12 words (or "").
- "cuisine" is the cuisine of the dish (one or two words). "serving_suggestion" is one short sentence
  with an optional garnish or side that fits the cuisine and the diet.
- difficulty is Easy, Medium or Hard.

Return ONLY JSON in exactly this shape:
{{
  "title": "Recipe name",
  "description": "One appetizing sentence",
  "cuisine": "Indian",
  "difficulty": "Easy",
  "prep_minutes": 5,
  "cook_minutes": 12,
  "serving_suggestion": "Serve warm with a squeeze of lemon.",
  "ingredients": [
    {{"name": "Rice", "quantity": "100 g", "calories": 130, "protein_g": 2.7, "carbs_g": 28, "fat_g": 0.3, "fiber_g": 0.4}}
  ],
  "instructions": [
    {{"step": "Rinse the rice until the water runs clear.", "heat": "", "timing": "2 min", "tip": "Removes extra starch."}}
  ],
  "nutrition_per_serving": {{"calories": 350, "protein_g": 20, "carbs_g": 45, "fat_g": 8, "fiber_g": 6}}
}}
"""


# ---------- text generation: low "thinking" for speed, retries, model fallback ----------
def _text_configs() -> list:
    """Fastest settings first. Models/SDKs that don't accept a thinking setting get the next option."""
    base = dict(response_mime_type="application/json", temperature=0.7, max_output_tokens=6000)
    configs = []
    for thinking in ({"thinking_level": "minimal"}, {"thinking_budget": 0}, None):
        try:
            if thinking is None:
                configs.append(types.GenerateContentConfig(**base))
            else:
                configs.append(types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(**thinking), **base))
        except Exception:
            continue
    return configs



def generate_text(client, prompt: str) -> str:
    """Generate text using the Gemini Interactions API."""
    model = _secret("GEMINI_TEXT_MODEL", "gemini-3.8-flash")

    try:
        response = call_with_retry(
            lambda: client.interactions.create(
                model=model,
                input=prompt,
                generation_config={"max_output_tokens": 6000},
            ),
            tries=3,
            first_delay=1.5,
        )
    except Exception as e:
        raise RecipeError(
            f"Gemini text generation failed using {model}: {e}"
        ) from e

    text = (response.output_text or "").strip()

    if not text:
        raise RecipeError("The AI sent back an empty answer.")

    return text


def parse_json_text(raw: str):
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def normalize_recipe(data, servings: int) -> dict:
    """Checks the shape of the AI's answer, computes nutrition and total time in code.
    Raises ValueError if the answer is unusable."""
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("the answer was not a JSON object")

    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("the recipe has no title")
    if title.lower() == "no suitable recipe":
        raise NoSuitableRecipe(str(data.get("description", "")).strip())

    ingredients, raw_ingredients = [], []
    for i in data.get("ingredients") or []:
        if isinstance(i, dict) and str(i.get("name", "")).strip():
            ingredients.append({
                "name": str(i["name"]).strip(),
                "quantity": str(i.get("quantity", "")).strip() or "as needed",
            })
            raw_ingredients.append(i)
    if len(ingredients) < 2:
        raise ValueError("the recipe has too few ingredients")

    steps = []
    for s in data.get("instructions") or []:
        if isinstance(s, str):
            s = {"step": s}
        if isinstance(s, dict) and str(s.get("step", "")).strip():
            steps.append({
                "step": str(s["step"]).strip(),
                "heat": str(s.get("heat", "")).strip(),
                "timing": str(s.get("timing", "")).strip(),
                "tip": str(s.get("tip", "")).strip(),
            })
    if len(steps) < 3:
        raise ValueError("the recipe has too few cooking steps")

    prep = _minutes_from(data, "prep_minutes", "prep_time")
    cook = _minutes_from(data, "cook_minutes", "cook_time")
    if prep is None or cook is None:
        raise ValueError("the prep and cooking times are missing")

    return {
        "title": title,
        "description": str(data.get("description", "")).strip(),
        "cuisine": " ".join(str(data.get("cuisine", "")).split())[:40],
        "serving": str(data.get("serving_suggestion", "")).strip()[:200],
        "difficulty": str(data.get("difficulty", "")).strip() or "Easy",
        "prep_min": max(0, prep),
        "cook_min": max(0, cook),
        "total_min": max(0, prep) + max(0, cook),     # always computed here, never trusted from the AI
        "ingredients": ingredients,
        "instructions": steps,
        "nutrition": compute_nutrition(raw_ingredients, data, servings),
        "preference_match": [],
    }


# ---------- validation: diet, allergies, ingredients, time ----------
_DIET_TERMS = {
    "meat": ["chicken", "mutton", "lamb", "beef", "pork", "bacon", "ham", "sausage", "mince", "keema",
             "steak", "turkey", "duck", "meat", "fish", "salmon", "tuna", "sardine", "anchovy", "prawn",
             "shrimp", "crab", "lobster", "squid", "oyster", "seafood", "gelatin", "gelatine", "lard"],
    "egg": ["egg"],
    "dairy": ["milk", "paneer", "butter", "ghee", "cheese", "curd", "yogurt", "yoghurt", "cream",
              "whey", "buttermilk", "khoa", "mawa"],
    "honey": ["honey"],
}

_DAIRY_TERMS = _DIET_TERMS["dairy"]
_NUT_TERMS = ["peanut", "groundnut", "almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan"]
_GLUTEN_TERMS = ["wheat", "flour", "atta", "maida", "bread", "roti", "chapati", "naan", "paratha", "pasta",
                 "noodle", "semolina", "rava", "suji", "barley", "couscous", "spaghetti", "maggi"]
_SHELLFISH = ["prawn", "shrimp", "crab", "lobster", "oyster", "squid"]
_FISH = ["fish", "salmon", "tuna", "sardine", "anchovy"]

ALLERGY_EXPANSIONS = {
    "dairy": _DAIRY_TERMS, "milk": _DAIRY_TERMS, "lactose": _DAIRY_TERMS,
    "nut": _NUT_TERMS, "nuts": _NUT_TERMS, "tree nut": _NUT_TERMS, "tree nuts": _NUT_TERMS,
    "peanut": ["peanut", "groundnut"], "peanuts": ["peanut", "groundnut"],
    "gluten": _GLUTEN_TERMS, "wheat": ["wheat", "flour", "atta", "maida", "semolina", "rava", "suji"],
    "egg": ["egg"], "eggs": ["egg"],
    "soy": ["soy", "soya", "tofu", "tempeh"], "soya": ["soy", "soya", "tofu", "tempeh"],
    "shellfish": _SHELLFISH, "seafood": _SHELLFISH + _FISH, "fish": _FISH,
    "sesame": ["sesame", "tahini"],
}

_PLANT_BASED = re.compile(
    r"\b(coconut|almond|soy|soya|oat|cashew|rice|hemp)\s+(milk|cream|yogurt|yoghurt|curd)\b"
    r"|\b(peanut|almond|cashew|shea|cocoa|cacao|vegan|plant)\s+butter\b",
    re.I,
)
_BASIC_ITEM = re.compile(r"\b(water|salt|oil|spice|spices|masala|powder|turmeric|cumin|pepper|mustard)\b", re.I)


def _has_term(text: str, term: str) -> bool:
    return re.search(r"\b" + re.escape(term) + r"(?:e?s)?\b", text, re.I) is not None


def _matches(name: str, item: str) -> bool:
    n, i = name.lower().strip(), item.lower().strip()
    if not n or not i:
        return False
    return (
        re.search(r"\b" + re.escape(i) + r"(?:e?s)?\b", n) is not None
        or re.search(r"\b" + re.escape(n) + r"(?:e?s)?\b", i) is not None
    )


def in_basket(name: str, items: list) -> bool:
    return any(_matches(name, b) for b in items)


def _allergy_phrases(text: str):
    out = []
    for p in re.split(r",|;|\n|\band\b", text or "", flags=re.I):
        p = re.sub(r"^\s*(no|avoid|allergic to|allergy to|without)\s+", "", p.strip(), flags=re.I).strip(" .-")
        if p:
            out.append(p.lower())
    return out


def validate_recipe(recipe: dict, req: dict) -> list:
    """Returns a list of safety/ingredient problems. Empty list means the recipe respects the user's rules."""
    problems = []
    names = [i["name"] for i in recipe["ingredients"]]
    full_text = " . ".join(names + [s["step"] for s in recipe["instructions"]] + [recipe["title"]])
    plant_stripped = _PLANT_BASED.sub(" ", full_text)

    banned = []
    if req["diet"] == "Vegetarian":
        banned = _DIET_TERMS["meat"]
    elif req["diet"] == "Vegan":
        banned = _DIET_TERMS["meat"] + _DIET_TERMS["egg"] + _DIET_TERMS["dairy"] + _DIET_TERMS["honey"]
    for term in banned:
        if _has_term(plant_stripped, term):
            problems.append(f'it contains "{term}", which breaks the {req["diet"]} diet')

    found = set()
    for phrase in _allergy_phrases(req["allergies"]):
        if _has_term(full_text, phrase):
            found.add(phrase)
        words = [phrase] + [w for w in re.split(r"\W+", phrase) if len(w) > 2]
        for w in words:
            for term in ALLERGY_EXPANSIONS.get(w, []):
                if _has_term(plant_stripped, term):
                    found.add(term)
    for term in sorted(found):
        problems.append(f'it contains "{term}", which the user must avoid')

    items = req["items"]
    if not any(in_basket(n, items) for n in names):
        problems.append("it uses none of the user's available ingredients")
    if req["available_only"]:
        extras = [n for n in names if not in_basket(n, items) and not _BASIC_ITEM.search(n)]
        if extras:
            problems.append("it adds ingredients that are not listed (" + ", ".join(extras) + ")")

    return problems


def time_problem(recipe: dict, req: dict) -> str:
    """Prep + cooking must fit inside the time the user selected."""
    limit = int(req["cooking_time"])
    if recipe["total_min"] > limit:
        return (f'prep ({recipe["prep_min"]} min) plus cooking ({recipe["cook_min"]} min) is '
                f'{recipe["total_min"]} min, over the {limit} min limit')
    return ""


def create_recipe(client, req: dict, avoid_titles: list) -> dict:
    """Asks Gemini for a recipe, validates it, and regenerates with feedback if it breaks a rule."""
    feedback = ""
    last_problems = []
    last_format_error = ""
    time_fallback = None     # a safe recipe that is only slightly over the time limit
    for _ in range(MAX_VALIDATION_ATTEMPTS):
        raw = generate_text(client, build_prompt(req, avoid_titles, feedback))
        try:
            recipe = normalize_recipe(parse_json_text(raw), req["servings"])
        except (json.JSONDecodeError, ValueError) as e:
            last_format_error = str(e)
            feedback = f"the reply was unusable ({last_format_error}). Return only the JSON in the required shape."
            continue
        # The user's own cuisine choice always wins; for "Surprise me" show the cuisine the AI picked.
        if req["cuisine_mode"] == "chosen":
            recipe["cuisine"] = req["cuisine"]
        else:
            recipe["cuisine"] = recipe["cuisine"] or "Chef's choice"
        recipe["preference_match"] = build_preference_match(recipe, req)

        problems = validate_recipe(recipe, req)
        t_problem = time_problem(recipe, req)
        if not problems and not t_problem:
            return recipe
        if not problems:
            recipe["time_warning"] = t_problem
            time_fallback = recipe
            feedback = t_problem + ". Shorten the recipe so prep plus cooking fits the limit."
            continue
        last_problems = problems
        last_format_error = ""
        feedback = "; ".join(problems + ([t_problem] if t_problem else [])) + "."

    if time_fallback:
        return time_fallback    # safe recipe; the page shows a clear time warning
    if last_problems:
        raise RecipeError(
            "We couldn't build a recipe that safely follows your diet, allergies and ingredient choices "
            f"({'; '.join(last_problems[:3])}). Try adding more ingredients or relaxing an option, "
            "then press the button again."
        )
    raise RecipeError(
        "The AI kept sending back a recipe in an unreadable format"
        + (f" ({last_format_error})" if last_format_error else "")
        + ". Please try again."
    )


# ---------- downloads ----------
def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "recipe"


NUTRIENT_LABELS = (("calories", "Calories"), ("protein", "Protein"), ("carbs", "Carbs"),
                   ("fat", "Fat"), ("fiber", "Fiber"))


def recipe_to_text(recipe: dict, req: dict) -> str:
    n = recipe["nutrition"]
    lines = [
        recipe["title"],
        "=" * len(recipe["title"]),
        recipe["description"],
        "",
        f'Cuisine: {recipe["cuisine"]}  |  Diet: {req["diet"]}  |  Goal: {req["goal"].split(" ", 1)[-1]}',
        f'Difficulty: {recipe["difficulty"]}  |  Prep: {recipe["prep_min"]} min  |  '
        f'Cooking: {recipe["cook_min"]} min  |  Total: {recipe["total_min"]} min  |  Servings: {req["servings"]}',
        "",
        f'INGREDIENTS (for {req["servings"]} serving{"s" if req["servings"] > 1 else ""})',
    ]
    for i in recipe["ingredients"]:
        extra = "" if in_basket(i["name"], req["items"]) else "  [extra - not in your basket]"
        lines.append(f'- {i["name"]}: {i["quantity"]}{extra}')
    lines += ["", "COOKING STEPS"]
    for k, s in enumerate(recipe["instructions"], 1):
        meta = ", ".join(x for x in (s["heat"], s["timing"]) if x)
        lines.append(f'{k}. {s["step"]}' + (f" ({meta})" if meta else ""))
        if s["tip"]:
            lines.append(f'   Tip: {s["tip"]}')
    if recipe.get("serving"):
        lines += ["", f'Serving idea: {recipe["serving"]}']
    lines += ["", "ESTIMATED NUTRITION PER SERVING"]
    lines += [f"{label}: {fmt_nutrient(key, n[key])}" for key, label in NUTRIENT_LABELS]
    lines += [n["note"], "", "Made with Fitlicious AI"]
    return "\n".join(lines)


def recipe_to_html(recipe: dict, req: dict) -> str:
    e = html.escape
    n = recipe["nutrition"]
    img_html = f'<img class="logo" src="{logo_uri()}" alt="Fitlicious AI">'
    ing = "".join(
        f'<li><b>{e(i["name"])}</b>: {e(i["quantity"])}'
        + ("" if in_basket(i["name"], req["items"]) else " <em>(extra)</em>") + "</li>"
        for i in recipe["ingredients"]
    )
    steps = ""
    for s in recipe["instructions"]:
        meta = ", ".join(x for x in (s["heat"], s["timing"]) if x)
        steps += (f'<li>{e(s["step"])}' + (f' <span class="m">({e(meta)})</span>' if meta else "")
                  + (f'<div class="t">Tip: {e(s["tip"])}</div>' if s["tip"] else "") + "</li>")
    nut = "".join(
        f'<div class="n"><span>{label}</span><b>{e(fmt_nutrient(key, n[key]))}</b></div>'
        for key, label in NUTRIENT_LABELS
    )
    serving = f'<p class="meta">Serving idea: {e(recipe["serving"])}</p>' if recipe.get("serving") else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(recipe["title"])} - Fitlicious AI</title>
<style>
body{{font-family:Inter,Arial,Helvetica,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:{EARTHEN_BROWN};background:{WARM_RICE}}}
.logo{{width:110px;height:auto}}
h1{{font-family:Poppins,Arial,sans-serif;margin:12px 0 4px}} h2{{font-family:Poppins,Arial,sans-serif;margin-top:26px;color:{SPICY_RED}}}
.meta{{color:#7A5C4B}} li{{margin:6px 0}} .m{{color:#7A5C4B}} .t{{color:#7A5C4B;font-size:14px}}
.nutri{{display:flex;flex-wrap:wrap;gap:10px}} .n{{background:#F7F3EA;border:1px solid #CDBFA6;border-radius:12px;padding:10px 14px}}
.n span{{display:block;font-size:12px;color:#7A5C4B}} .note{{font-size:13px;color:#7A5C4B}}
</style></head><body>
{img_html}
<h1>{e(recipe["title"])}</h1>
<p>{e(recipe["description"])}</p>
<p class="meta">Cuisine: {e(recipe["cuisine"])} &middot; Diet: {e(req["diet"])} &middot; Goal: {e(req["goal"].split(" ", 1)[-1])}</p>
<p class="meta">Difficulty: {e(recipe["difficulty"])} &middot; Prep: {recipe["prep_min"]} min &middot; Cooking: {recipe["cook_min"]} min &middot; Total: {recipe["total_min"]} min &middot; Servings: {req["servings"]}</p>
{serving}
<h2>Ingredients</h2><ul>{ing}</ul>
<h2>Cooking steps</h2><ol>{steps}</ol>
<h2>Estimated nutrition per serving</h2><div class="nutri">{nut}</div>
<p class="note">{e(n["note"])}</p>
<p class="note">Made with Fitlicious AI</p>
</body></html>"""


def _md(text: str) -> str:
    """Keeps model text from being read as math/markdown inside labels."""
    return text.replace("$", "\\$")


# ---------- the whole "plan a meal" run ----------
def run_generation(req: dict, avoid_titles: list):
    api_key = get_api_key()
    if not api_key:
        st.error("No API key found. Add GEMINI_API_KEY to .streamlit/secrets.toml and restart the app.")
        queue_sound("error")
        return

    # Instant feedback on the click (browsers only allow sound right after a click).
    emoji_rain([emoji_for(i) for i in req["items"]])
    play_sound_now("success")

    client = make_client(api_key)
    started = time.time()
    try:
        with st.spinner("👩‍🍳 Creating your personalized recipe..."):
            recipe = create_recipe(client, req, avoid_titles)
    except NoSuitableRecipe as e:
        st.warning(
            "These ingredients and choices can't make a suitable recipe. "
            + (str(e) or "Try adding more ingredients or relaxing an option.")
        )
        queue_sound("error")
        return
    except RecipeError as e:
        st.error(str(e))
        queue_sound("error")
        return
    except Exception as e:
        message, detail = friendly_error(e)
        st.error(message)
        st.caption(f"Technical details: {detail}")
        queue_sound("error")
        return

    recipe["gen_seconds"] = round(time.time() - started, 1)

    # The recipe is valid: save it so the page can show it right away. The picture comes afterwards.
    st.session_state.generated_recipe = recipe
    st.session_state.recipe_history.append(recipe["title"])
    st.session_state.recipe_id += 1   # fresh tick boxes for this new recipe
    add_to_my_recipes(recipe, req)


# =====================================================================
# SAVED DATA, SETTINGS AND NAVIGATION (Home / My Recipes / Saved Recipes / Settings)
# Saved recipes, history and your default preferences are stored as small JSON files next to app.py.
# =====================================================================
MAX_HISTORY = 30
TIME_OPTIONS = [10, 15, 20, 30, 45, 60]
GOAL_OPTIONS = ["🔥 Fat loss", "💪 Muscle gain", "💚 Healthy eating"]
DIET_OPTIONS = ["Vegetarian", "Non-vegetarian", "Vegan"]
SPICE_OPTIONS = ["Mild", "Medium", "Hot"]
EXPERIENCE_OPTIONS = ["Beginner", "Intermediate", "Advanced"]
SETTINGS_DEFAULTS = {
    "goal": GOAL_OPTIONS[0], "diet": "Vegetarian", "cuisine": CUISINE_ANY, "time": 20,
    "servings": 1, "spice": "Medium", "experience": "Beginner", "indian": True,
}
WIDGET_MAP = {   # widget key on the Home page -> key in the saved settings
    "w_goal": "goal", "w_diet": "diet", "w_cuisine": "cuisine", "w_time": "time",
    "w_servings": "servings", "w_spice": "spice", "w_exp": "experience", "w_indian": "indian",
}
NAV_ITEMS = [("Home", "🏠"), ("My Recipes", "🥘"), ("Saved Recipes", "❤️"), ("Settings", "⚙️")]
_RECIPE_KEYS = ("title", "description", "cuisine", "difficulty", "prep_min", "cook_min", "total_min",
                "ingredients", "instructions", "nutrition", "preference_match")
_REQ_KEYS = ("goal", "items", "diet", "cuisine", "cuisine_mode", "cooking_time", "servings",
             "spice", "experience", "allergies")


def _data_path(name: str) -> str:
    return os.path.join(_APP_DIR, name)


def _load_json(name: str, default):
    try:
        with open(_data_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(name: str, data) -> bool:
    try:
        tmp = _data_path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _data_path(name))
        return True
    except Exception:
        return False


def _valid_entry(e) -> bool:
    return (
        isinstance(e, dict) and isinstance(e.get("id"), str)
        and isinstance(e.get("recipe"), dict) and isinstance(e.get("req"), dict)
        and all(k in e["recipe"] for k in _RECIPE_KEYS) and all(k in e["req"] for k in _REQ_KEYS)
    )


def load_entries(name: str) -> list:
    data = _load_json(name, [])
    return [e for e in data if _valid_entry(e)] if isinstance(data, list) else []


def load_settings() -> dict:
    s = dict(SETTINGS_DEFAULTS)
    saved = _load_json("settings.json", {})
    if isinstance(saved, dict):
        for key, options in (("goal", GOAL_OPTIONS), ("diet", DIET_OPTIONS), ("cuisine", CUISINES),
                             ("time", TIME_OPTIONS), ("spice", SPICE_OPTIONS), ("experience", EXPERIENCE_OPTIONS)):
            if saved.get(key) in options:
                s[key] = saved[key]
        if isinstance(saved.get("servings"), int) and 1 <= saved["servings"] <= 10:
            s["servings"] = saved["servings"]
        if isinstance(saved.get("indian"), bool):
            s["indian"] = saved["indian"]
    return s


if "settings" not in st.session_state:
    st.session_state.settings = load_settings()
if "my_recipes" not in st.session_state:
    st.session_state.my_recipes = load_entries("my_recipes.json")[:MAX_HISTORY]
if "saved_recipes" not in st.session_state:
    st.session_state.saved_recipes = load_entries("saved_recipes.json")
for _wkey, _skey in WIDGET_MAP.items():     # the Home form starts from the user's saved defaults
    st.session_state.setdefault(_wkey, st.session_state.settings[_skey])


def make_entry(recipe: dict, req: dict) -> dict:
    return {
        "id": f"{int(time.time() * 1000)}{random.randint(100, 999)}",
        "saved_at": time.strftime("%d %b %Y, %H:%M"),
        "recipe": recipe,
        "req": req,
    }


def _toast(message: str):
    try:
        st.toast(message)
    except Exception:
        pass


def go_page(name: str):
    st.session_state.page = name


def add_to_my_recipes(recipe: dict, req: dict):
    entries = ([make_entry(recipe, req)] + st.session_state.my_recipes)[:MAX_HISTORY]
    st.session_state.my_recipes = entries
    _write_json("my_recipes.json", entries)


def is_saved(recipe: dict) -> bool:
    return any(
        s["recipe"]["title"] == recipe["title"] and s["recipe"]["ingredients"] == recipe["ingredients"]
        for s in st.session_state.saved_recipes
    )


def save_entry(entry: dict):
    if not is_saved(entry["recipe"]):
        saved = [dict(entry, saved_at=time.strftime("%d %b %Y, %H:%M"))] + st.session_state.saved_recipes
        st.session_state.saved_recipes = saved
        _write_json("saved_recipes.json", saved)
    queue_sound("pop")
    _toast("Recipe saved ❤️")


def save_current_recipe():
    if st.session_state.generated_recipe and st.session_state.recipe_request:
        save_entry(make_entry(st.session_state.generated_recipe, st.session_state.recipe_request))


def remove_saved(entry_id: str):
    saved = [s for s in st.session_state.saved_recipes if s["id"] != entry_id]
    st.session_state.saved_recipes = saved
    _write_json("saved_recipes.json", saved)
    queue_sound("clear")


def clear_my_recipes():
    st.session_state.my_recipes = []
    _write_json("my_recipes.json", [])
    queue_sound("clear")


def clear_saved():
    st.session_state.saved_recipes = []
    _write_json("saved_recipes.json", [])
    queue_sound("clear")


def open_entry(entry: dict):
    st.session_state.generated_recipe = entry["recipe"]
    st.session_state.recipe_request = entry["req"]
    st.session_state.recipe_id += 1
    st.session_state.page = "Home"
    queue_sound("pop")


def keep_widget_state():
    """Streamlit forgets widgets that are not drawn on the current page. Re-assigning their values keeps the
    Home form (ingredients, preferences, ticked steps) intact while the user visits another page."""
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and (k.startswith(("w_", "fit_")) or k == "ingredients"):
            st.session_state[k] = st.session_state[k]


def flush_sound():
    if st.session_state.get("pending_sound"):
        if st.session_state.get("sound_effects", True):
            components.html(sound_script(st.session_state.pending_sound), height=0)
        st.session_state.pending_sound = None


def render_footer():
    st.markdown(
        '<div class="footer">'
        '<span class="deco ft-l"></span><span class="deco ft-r"></span>'
        f'<div class="footer-badge"><img src="{logo_uri()}" alt=""></div>'
        '<div class="footer-name">Fitlicious AI</div>'
        "<div>Meals from what you already have</div>"
        "<small>Smart nutrition, brighter living. Made for better choices, one meal at a time. 🥑</small>"
        "</div>",
        unsafe_allow_html=True,
    )


def print_button_html(recipe: dict, req: dict) -> str:
    """A Print button that opens the recipe alone in a new tab and prints it."""
    doc = json.dumps(recipe_to_html(recipe, req)).replace("</", "<\\/")
    template = (
        '<html><body style="margin:0"><button id="p" style="width:100%;height:44px;border:none;border-radius:12px;'
        "background:#A93118;color:#fff;font:700 15px Inter,Arial,sans-serif;cursor:pointer;"
        'box-shadow:0 3px 0 #6E1F0F">🖨️ Print</button><script>'
        "const doc = __DOC__;"
        'document.getElementById("p").onclick = function () {'
        'const w = window.open("", "_blank");'
        'if (!w) { alert("Please allow pop-ups to print the recipe."); return; }'
        "w.document.write(doc); w.document.close(); w.focus();"
        "setTimeout(function () { w.print(); }, 400);"
        "};</script></body></html>"
    )
    return template.replace("__DOC__", doc)


def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="side-title">Menu</div>', unsafe_allow_html=True)
        counts = {"My Recipes": len(st.session_state.my_recipes), "Saved Recipes": len(st.session_state.saved_recipes)}
        for name, icon in NAV_ITEMS:
            count = f"  ({counts[name]})" if counts.get(name) else ""
            st.button(
                f"{icon}  {name}{count}", key=f"nav_{name}", on_click=go_page, args=(name,),
                use_container_width=True, type="primary" if st.session_state.page == name else "secondary",
            )
        st.markdown(
            '<div class="side-tag"><span class="deco sprig-s"></span>'
            "Good Food<br>Better Choices<br>Brighter Future ♡</div>",
            unsafe_allow_html=True,
        )


def page_title(icon: str, title: str, sub: str):
    st.markdown(
        f'<div class="plan-title">{icon} {html.escape(title)}</div>'
        f'<div class="sec-sub" style="margin-bottom:14px">{html.escape(sub)}</div>',
        unsafe_allow_html=True,
    )


def empty_state(icon: str, title: str, text: str):
    with card("empty"):
        st.markdown(
            f'<div style="text-align:center;padding:26px 8px"><div style="font-size:46px">{icon}</div>'
            f'<div class="plan-title" style="font-size:22px">{html.escape(title)}</div>'
            f'<div class="sec-sub">{html.escape(text)}</div></div>',
            unsafe_allow_html=True,
        )
        st.button("🏠 Plan a meal", on_click=go_page, args=("Home",), key="empty_go_home", use_container_width=True)


def entry_card(entry: dict, kind: str):
    r, rq = entry["recipe"], entry["req"]
    n = r["nutrition"]
    with card(f"{kind}_{entry['id']}"):
        c1, c2 = st.columns([3, 1.2], gap="medium")
        with c1:
            st.markdown(
                '<div class="tags">'
                f'<span class="tag tag-green">🌿 {html.escape(str(rq["diet"]))}</span>'
                f'<span class="tag tag-red">💪 {html.escape(str(rq["goal"]).split(" ", 1)[-1])}</span>'
                f'<span class="tag tag-tan">🌍 {html.escape(str(r["cuisine"]))}</span></div>'
                f'<div class="plan-title" style="font-size:22px">{html.escape(r["title"])}</div>'
                f'<div class="plan-desc" style="font-size:14.5px">{html.escape(r["description"])}</div>'
                f'<div class="serve-line">⏱️ {r["total_min"]} min · 🔥 {html.escape(fmt_nutrient("calories", n["calories"]))} · '
                f'💪 {html.escape(fmt_nutrient("protein", n["protein"]))} · 🍽️ {rq["servings"]} serving(s) · '
                f'🕒 {html.escape(entry.get("saved_at", ""))}</div>',
                unsafe_allow_html=True,
            )
        with c2:
            st.button("📖 Open", key=f"open_{kind}_{entry['id']}", on_click=open_entry, args=(entry,),
                      use_container_width=True)
            if kind == "my":
                done = is_saved(r)
                st.button("✅ Saved" if done else "🤍 Save", key=f"save_{kind}_{entry['id']}",
                          on_click=save_entry, args=(entry,), disabled=done, use_container_width=True)
            else:
                st.button("🗑️ Remove", key=f"rm_{kind}_{entry['id']}", on_click=remove_saved,
                          args=(entry["id"],), use_container_width=True)


def page_my_recipes():
    page_title("🥘", "My Recipes", "Every recipe you planned (latest first, up to 30). Open one to cook it again.")
    entries = st.session_state.my_recipes
    if not entries:
        empty_state("🥘", "No recipes yet", "Plan your first meal and it will appear here.")
        return
    for e in entries:
        entry_card(e, "my")
    st.button("🧹 Clear history", on_click=clear_my_recipes, key="clear_history_btn")


def page_saved_recipes():
    page_title("❤️", "Saved Recipes", "Recipes you saved with the Save Recipe button.")
    entries = st.session_state.saved_recipes
    if not entries:
        empty_state("❤️", "Nothing saved yet", "Press Save Recipe on a finished recipe to keep it here.")
        return
    for e in entries:
        entry_card(e, "saved")


def page_settings():
    page_title("⚙️", "Settings", "Your defaults fill the form on the Home page. The sound and music switches "
                                  "are at the top of every page.")
    s = st.session_state.settings
    with card("settings"):
        card_head("🍽️", "My default preferences", "", "green")
        with st.form("settings_form"):
            c1, c2 = st.columns(2)
            with c1:
                f_goal = st.selectbox("🎯 Meal goal", GOAL_OPTIONS, index=GOAL_OPTIONS.index(s["goal"]))
                f_diet = st.selectbox("🥬 Diet preference", DIET_OPTIONS, index=DIET_OPTIONS.index(s["diet"]))
                f_cuisine = st.selectbox("🌍 Cuisine", CUISINES, index=CUISINES.index(s["cuisine"]))
                f_indian = st.checkbox("Prefer Indian-style recipes when Cuisine is 'Surprise me'", value=bool(s["indian"]))
            with c2:
                f_time = st.select_slider("⏱️ Available cooking time", options=TIME_OPTIONS, value=s["time"],
                                          format_func=lambda x: f"{x} min")
                f_serv = st.number_input("🍽️ Number of servings", min_value=1, max_value=10, value=int(s["servings"]))
                f_spice = st.select_slider("🌶️ Spice level", options=SPICE_OPTIONS, value=s["spice"])
                f_exp = st.selectbox("👩‍🍳 Cooking experience", EXPERIENCE_OPTIONS,
                                     index=EXPERIENCE_OPTIONS.index(s["experience"]))
            submitted = st.form_submit_button("💾 Save my defaults", type="primary", use_container_width=True)
        if submitted:
            new = {"goal": f_goal, "diet": f_diet, "cuisine": f_cuisine, "time": f_time, "servings": int(f_serv),
                   "spice": f_spice, "experience": f_exp, "indian": bool(f_indian)}
            st.session_state.settings = new
            _write_json("settings.json", new)
            for wkey, skey in WIDGET_MAP.items():
                st.session_state[wkey] = new[skey]
            queue_sound("success")
            st.success("Saved. The Home form now starts with these choices.")
    with card("data"):
        card_head("🧹", "Your data", "", "tan")
        st.caption(f"Recipes, history and settings are stored in JSON files next to app.py "
                   f"(saved_recipes.json, my_recipes.json, settings.json). "
                   f"Now: {len(st.session_state.my_recipes)} in history, {len(st.session_state.saved_recipes)} saved.")
        d1, d2 = st.columns(2)
        with d1:
            st.button("🧹 Clear my recipes history", on_click=clear_my_recipes, key="set_clear_hist",
                      use_container_width=True)
        with d2:
            st.button("🗑️ Remove all saved recipes", on_click=clear_saved, key="set_clear_saved",
                      use_container_width=True)


# ---------- realistic leaves, sidebar, plate and surface styling ----------
_LEAF_COLORS = {   # (light, base, dark, vein)
    "g": ("#A9C46C", "#6F8B3C", "#47601F", "#E3EDC9"),
    "t": ("#F0CFA6", "#D3A275", "#A9733F", "#FAECD8"),
    "r": ("#DB6446", "#A93118", "#741F0D", "#F5C2B3"),
}


def _svg_defs() -> str:
    d = ""
    for k, (light, base, dark, _vein) in _LEAF_COLORS.items():
        d += (f"<linearGradient id='g{k}' x1='0' y1='0' x2='1' y2='0'><stop offset='0' stop-color='{light}'/>"
              f"<stop offset='.5' stop-color='{base}'/><stop offset='1' stop-color='{dark}'/></linearGradient>")
    d += ("<filter id='sh' x='-25%' y='-25%' width='150%' height='150%'>"
          "<feDropShadow dx='1.2' dy='2.6' stdDeviation='1.8' flood-color='#3A1F10' flood-opacity='.3'/></filter>")
    return f"<defs>{d}</defs>"


def _leaf_g(k: str, x: float, y: float, rot: float, s: float = 1.0) -> str:
    """One shaded leaf with a midrib, side veins and a soft highlight, growing from (x, y)."""
    vein = _LEAF_COLORS[k][3]
    return (
        f"<g transform='translate({x} {y}) rotate({rot}) scale({s})'>"
        f"<path d='M0 0C-24 -30 -24 -80 0 -114C24 -80 24 -30 0 0Z' fill='url(#g{k})'/>"
        "<path d='M0 0C-24 -30 -24 -80 0 -114C-9 -80 -9 -30 0 0Z' fill='#fff' opacity='.14'/>"
        f"<path d='M0 -2L0 -104' stroke='{vein}' stroke-width='2.2' stroke-linecap='round' opacity='.9'/>"
        f"<g stroke='{vein}' stroke-width='1.1' stroke-linecap='round' opacity='.5' fill='none'>"
        "<path d='M0 -24L-12 -40M0 -24L12 -40M0 -46L-14 -64M0 -46L14 -64M0 -68L-10 -85M0 -68L10 -85'/></g></g>"
    )


def _uri(svg: str) -> str:
    return 'url("data:image/svg+xml;base64,' + base64.b64encode(svg.encode("utf-8")).decode() + '")'


def _scene(w: int, h: int, inner: str, par: str = "xMidYMid meet") -> str:
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {w} {h}' preserveAspectRatio='{par}'>"
                f"{_svg_defs()}<g filter='url(#sh)'>{inner}</g></svg>")


_STEM = "stroke='#8A5A35' stroke-width='3.4' stroke-linecap='round' fill='none'"
_ASSETS = {
    "__LG__": _scene(120, 130, _leaf_g("g", 44, 124, 30)),
    "__LGF__": _scene(120, 130, _leaf_g("g", 76, 124, -30)),
    "__LT__": _scene(120, 130, _leaf_g("t", 44, 124, 30)),
    "__LTF__": _scene(120, 130, _leaf_g("t", 76, 124, -30)),
    "__LR__": _scene(120, 130, _leaf_g("r", 44, 124, 30)),
    "__LRF__": _scene(120, 130, _leaf_g("r", 76, 124, -30)),
    # top-left branch beside the logo
    "__SPRIG_L__": _scene(180, 130,
        f"<path d='M6 124C40 108 80 92 118 60C132 48 146 36 164 22' {_STEM}/>"
        + _leaf_g("g", 30, 114, -38, .5) + _leaf_g("t", 46, 107, 28, .46) + _leaf_g("g", 70, 94, -42, .56)
        + _leaf_g("t", 88, 84, 32, .5) + _leaf_g("g", 110, 66, -36, .56) + _leaf_g("g", 128, 52, 38, .46)
        + _leaf_g("t", 150, 34, 8, .5) + _leaf_g("g", 164, 22, 42, .58)),
    # leaf pair in the middle of the header
    "__PAIR__": _scene(120, 110,
        f"<path d='M58 106C58 92 60 84 62 76' {_STEM}/>"
        + _leaf_g("t", 58, 104, -30, .8) + _leaf_g("g", 60, 102, 32, .74) + _leaf_g("g", 60, 100, 2, .42)),
    # small sprig above the sidebar tagline
    "__SPRIG_S__": _scene(110, 80,
        f"<path d='M30 76C46 60 62 48 82 30' {_STEM}/>"
        + _leaf_g("t", 40, 66, -40, .42) + _leaf_g("g", 56, 56, 30, .44) + _leaf_g("g", 74, 40, -20, .46)
        + _leaf_g("t", 82, 30, 38, .4)),
    # footer corners: leaves fanning out of the bottom corners
    "__FT_L__": _scene(230, 130,
        _leaf_g("g", 30, 138, 8, .82) + _leaf_g("t", 56, 138, 38, .7) + _leaf_g("g", 80, 140, 62, .78)
        + _leaf_g("g", 14, 140, -14, .6) + _leaf_g("t", 110, 142, 72, .56), "xMinYMax meet"),
    "__FT_R__": _scene(230, 130,
        _leaf_g("r", 200, 138, -10, .84) + _leaf_g("g", 174, 140, -40, .72) + _leaf_g("t", 150, 140, -64, .66)
        + _leaf_g("g", 214, 140, 14, .6) + _leaf_g("r", 124, 142, -74, .5), "xMaxYMax meet"),
    # green swoosh in the top-right corner
    "__SWOOSH__": _uri(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 100' preserveAspectRatio='none'>"
        "<defs><linearGradient id='s' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#93B057'/>"
        "<stop offset='.6' stop-color='#6F8B3C'/><stop offset='1' stop-color='#4C6326'/></linearGradient></defs>"
        "<path d='M96 0C130 62 250 98 400 62V0Z' fill='url(#s)'/>"
        "<path d='M150 0C178 50 270 80 400 48V0Z' fill='#fff' opacity='.08'/>"
        "<path d='M96 0C130 62 250 98 400 62' fill='none' stroke='#CFE0A6' stroke-width='3' opacity='.65'/></svg>"),
    # wavy footer edge: tan, red and brown waves
    "__WAVE__": _uri(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1000 64' preserveAspectRatio='none'>"
        "<path d='M0 34C180 -4 330 58 560 28S880 6 1000 26V64H0Z' fill='#D3A275'/>"
        "<path d='M0 44C200 14 380 66 600 38S900 22 1000 40V64H0Z' fill='#A93118'/>"
        "<path d='M0 52C220 26 420 70 640 48S920 36 1000 50V64H0Z' fill='#673A25'/></svg>"),
    # fine paper grain for the page background
    "__NOISE__": _uri(
        "<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'>"
        "<feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/>"
        "<feColorMatrix values='0 0 0 0 .4 0 0 0 0 .3 0 0 0 0 .2 0 0 0 .07 0'/></filter>"
        "<rect width='100%' height='100%' filter='url(#n)'/></svg>"),
}

_LEAF_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Caveat:wght@600;700&display=swap');
:root{--script:'Caveat','Segoe Script','Brush Script MT',cursive}
.stApp{background-color:#E8E2D6 !important;background-image:__NOISE__,radial-gradient(1100px 520px at 18% -4%,#F4EFE4 0%,rgba(244,239,228,0) 70%),linear-gradient(180deg,#EAE4D8,#E1D9CA) !important;background-attachment:fixed}
.deco,.leaf{position:absolute;display:block;pointer-events:none;background-repeat:no-repeat;background-position:center;background-size:contain;z-index:1}
.leaf{width:56px;height:56px}
.lg{background-image:__LG__}.lgf{background-image:__LGF__}.lt{background-image:__LT__}.ltf{background-image:__LTF__}.lr{background-image:__LR__}.lrf{background-image:__LRF__}
/* header: branch beside the logo, leaf pair mid-header, green swoosh top-right (as in the mockup) */
.sprig-l{left:0;top:4px;width:150px;height:110px;background-image:__SPRIG_L__}
.pair-m{left:47%;top:8px;width:96px;height:84px;background-image:__PAIR__}
.swoosh{right:0;top:0;bottom:0;width:36%;background-image:__SWOOSH__;background-size:100% 100%}
.topbar{background:linear-gradient(180deg,#FCF9F2,#F1EADC) !important;box-shadow:inset 0 1px 0 #fff,0 12px 28px -16px rgba(103,58,37,.5),0 2px 6px rgba(103,58,37,.1)}
.topbar::before,.topbar::after{display:none !important}
.brand{padding-left:88px}
.brand-logo{width:106px !important;height:88px !important;filter:drop-shadow(0 4px 5px rgba(60,30,10,.3))}
.brand-name{text-shadow:0 1px 0 rgba(255,255,255,.75)}
.ribbon{font-family:var(--script) !important;font-style:normal !important;font-weight:700;font-size:30px !important;line-height:1 !important;color:#fff !important;transform:rotate(-5deg);text-shadow:0 2px 3px rgba(35,48,12,.5);padding-right:20px !important}
/* hero */
.hero{background:linear-gradient(135deg,#E3BA92,#D3A275 52%,#C58F5E) !important;box-shadow:inset 0 1px 0 rgba(255,255,255,.45),0 14px 26px -16px rgba(103,58,37,.6)}
.hero-title,.hero-text{position:relative;z-index:2}
.hr1{right:3%;bottom:-12px;width:78px;height:78px;transform:rotate(10deg)}
.hr2{right:-14px;top:-16px;width:64px;height:64px;transform:rotate(95deg)}
/* sidebar: sprig above the handwritten tagline */
section[data-testid="stSidebar"]{background:linear-gradient(180deg,#FBF8F1,#F1EADC);border-right:1.5px solid var(--line);box-shadow:6px 0 22px -14px rgba(103,58,37,.45)}
.side-title{font-family:var(--head-font);font-weight:600;font-size:15px;color:var(--muted);margin:4px 0 10px}
section[data-testid="stSidebar"] .stButton button{justify-content:flex-start;text-align:left;border-radius:14px;min-height:48px}
section[data-testid="stSidebar"] button[kind="secondary"],section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]{background:transparent !important;border-color:transparent !important;box-shadow:none !important}
section[data-testid="stSidebar"] button[kind="primary"],section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]{background:linear-gradient(180deg,#FBEDE6,#F1D9CF) !important;border:1.5px solid var(--red) !important;box-shadow:inset 0 1px 0 #fff,0 3px 8px -4px rgba(169,49,24,.5) !important;min-height:48px !important}
section[data-testid="stSidebar"] button[kind="primary"] p,section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] p{color:var(--red) !important;font-size:16px !important}
.side-tag{position:relative;margin-top:60px;padding:78px 6px 14px;font-family:var(--script);font-weight:700;color:var(--brown);font-size:30px;line-height:1.05;transform:rotate(-4deg);text-shadow:0 1px 0 rgba(255,255,255,.7)}
.sprig-s{left:18px;top:0;width:110px;height:80px;background-image:__SPRIG_S__}
/* footer: tan, red and brown waves with leaves in both bottom corners */
.footer{position:relative;overflow:hidden;border-radius:0 !important;padding-top:78px !important;background-color:transparent !important;background-image:__WAVE__,linear-gradient(180deg,#673A25,#4B2918) !important;background-repeat:no-repeat !important;background-position:top center,0 63px !important;background-size:100% 64px,100% calc(100% - 63px) !important}
.footer > *:not(.deco){position:relative;z-index:2}
.ft-l{left:0;bottom:-6px;width:230px;height:130px;background-image:__FT_L__;background-position:left bottom}
.ft-r{right:0;bottom:-6px;width:230px;height:130px;background-image:__FT_R__;background-position:right bottom}
/* surfaces: layered shadows, soft gradients, inner highlights */
div[class*="st-key-card_"]{background:linear-gradient(180deg,#FCF9F3,#F3ECDF) !important;border:1.5px solid #D6C9B0 !important;border-radius:22px !important;box-shadow:inset 0 1px 0 #fff,0 14px 26px -16px rgba(103,58,37,.55),0 2px 6px rgba(103,58,37,.1)}
.card-head{box-shadow:inset 0 1px 0 rgba(255,255,255,.28),0 3px 6px -2px rgba(0,0,0,.25);text-shadow:0 1px 1px rgba(0,0,0,.2)}
.ch-red{background:linear-gradient(180deg,#C13C20,#A93118 55%,#8C2712) !important}
.ch-green{background:linear-gradient(180deg,#86A248,#6F8B3C 55%,#5A7430) !important}
.ch-tan{background:linear-gradient(180deg,#E3BC94,#D3A275 55%,#BC8A5A) !important;text-shadow:0 1px 0 rgba(255,255,255,.35)}
.ch-brown{background:linear-gradient(180deg,#85502F,#673A25 55%,#4F2B1A) !important}
.tile,.nrow,.crow{background:linear-gradient(180deg,#EFE8D9,#E7DFCC);box-shadow:inset 0 1px 3px rgba(103,58,37,.14),0 1px 0 #fff}
.tag{box-shadow:0 2px 4px -1px rgba(103,58,37,.3)}
.how-item{box-shadow:0 2px 5px -2px rgba(103,58,37,.35),inset 0 1px 0 #fff}
.sec-num,.how-num{background:linear-gradient(180deg,#C7452A,#A93118 60%,#8A2612) !important;box-shadow:0 2px 4px rgba(110,31,15,.4),inset 0 1px 0 rgba(255,255,255,.3)}
.chip{background:linear-gradient(180deg,#EFF4E0,#D9E4BA) !important;box-shadow:0 3px 6px -2px rgba(82,105,43,.4),inset 0 1px 0 #fff}
.basket{background:linear-gradient(180deg,#F2EBDC,#FAF6EC) !important;box-shadow:inset 0 3px 8px rgba(103,58,37,.14)}
div[data-baseweb="input"],div[data-baseweb="textarea"],div[data-baseweb="select"] > div{background:linear-gradient(180deg,#F0E9DB,#F8F4EA) !important;box-shadow:inset 0 2px 5px rgba(103,58,37,.14) !important}
.stApp button[kind="secondary"],.stApp button[data-testid="stBaseButton-secondary"]{background:linear-gradient(180deg,#FFFDF8,#F0E8D8) !important;box-shadow:inset 0 1px 0 #fff,0 3px 6px -2px rgba(103,58,37,.28)}
.stApp button[kind="primary"],.stApp button[data-testid="stBaseButton-primary"]{background:linear-gradient(180deg,#C93F22,#A93118 55%,#8C2712) !important;box-shadow:inset 0 1px 0 rgba(255,255,255,.35),0 4px 0 #6E1F0F,0 12px 18px -8px rgba(110,31,15,.6) !important}
/* ingredient plate: a ceramic plate on a wooden table */
.plate{position:relative;overflow:hidden;border-radius:20px;min-height:270px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:22px 16px;text-align:center;background:repeating-linear-gradient(93deg,rgba(103,58,37,.13) 0 2px,transparent 2px 11px),repeating-linear-gradient(88deg,rgba(255,255,255,.07) 0 1px,transparent 1px 7px),linear-gradient(135deg,#CFA06F,#B9854F 60%,#A8733F);box-shadow:inset 0 2px 10px rgba(60,30,10,.35),0 10px 20px -12px rgba(103,58,37,.6)}
.plate-dish{position:relative;z-index:2;width:206px;height:206px;max-width:100%;border-radius:50%;display:flex;align-items:center;justify-content:center;background:radial-gradient(circle at 34% 28%,#FFFFFF,#F1ECE0 52%,#D6CDB9 100%);box-shadow:0 16px 20px -8px rgba(50,25,8,.6),inset 0 0 0 11px rgba(255,255,255,.65),inset 0 0 0 12px rgba(0,0,0,.07),inset 0 -10px 18px rgba(0,0,0,.08)}
.plate-emojis{display:flex;flex-wrap:wrap;gap:2px 4px;justify-content:center;align-items:center;font-size:38px;width:150px;filter:drop-shadow(0 4px 3px rgba(60,30,10,.35))}
.plate-emojis span{display:inline-block;animation:wiggle 3s ease-in-out infinite}
.plate-cap{position:relative;z-index:2;display:flex;flex-direction:column;gap:1px;background:rgba(252,249,243,.92);border-radius:12px;padding:6px 14px;box-shadow:0 3px 8px -3px rgba(60,30,10,.5)}
.plate-cap b{font-family:var(--head-font);font-size:17px;color:var(--brown)}
.plate-cap span{font-size:13px;color:var(--muted)}
.pl1{left:-12px;top:-12px;width:70px;height:70px;transform:rotate(-20deg)}
.pl2{right:-10px;bottom:-12px;width:64px;height:64px;transform:rotate(15deg)}
@media (max-width:650px){.sprig-l,.pair-m,.swoosh{display:none}.brand{padding-left:0}.ribbon{color:var(--brown) !important;text-shadow:none}.ft-l,.ft-r{width:130px}}
</style>"""
for _tok, _val in _ASSETS.items():
    _LEAF_CSS = _LEAF_CSS.replace(_tok, _val)
st.markdown(_LEAF_CSS, unsafe_allow_html=True)


# ---------- HEADER ----------
st.markdown(
    '<div class="topbar">'
    '<span class="deco sprig-l"></span><span class="deco pair-m"></span><span class="deco swoosh"></span>'
    '<div class="brand">'
    f'<img class="brand-logo" src="{logo_uri()}" alt="Fitlicious AI logo">'
    '<div><div class="brand-name">Fitlicious <span>AI</span></div>'
    '<div class="brand-caption">Meals from what you already have</div></div>'
    "</div>"
    '<div class="ribbon">Healthy Food<br>Happy You ♡</div>'
    "</div>",
    unsafe_allow_html=True,
)
tog_l, tog_r, _tog_gap = st.columns([1, 1, 2])
with tog_l:
    st.toggle("🔊 Sound effects", value=True, key="sound_effects")
with tog_r:
    if st.toggle("🎵 Background music", value=False, key="music_on"):
        track, track_format = music_track()
        try:
            st.audio(track, format=track_format, loop=True, autoplay=True)
        except TypeError:   # older Streamlit: press play on the player instead
            st.audio(track, format=track_format)

render_sidebar()

# Pages other than Home are drawn here and the script stops; Home continues below.
if st.session_state.page != "Home":
    keep_widget_state()
    {"My Recipes": page_my_recipes, "Saved Recipes": page_saved_recipes,
     "Settings": page_settings}.get(st.session_state.page, page_my_recipes)()
    flush_sound()
    render_footer()
    st.stop()

st.markdown(
    '<div class="hero">'
    '<span class="leaf lg hr1"></span><span class="leaf lr hr2"></span>'
    '<div class="hero-title">What is in your kitchen today?</div>'
    '<div class="hero-text">Add the ingredients you have. Fitlicious AI plans a meal around '
    "your goal, your time and your taste.</div>"
    '<div class="float f1">🥑</div><div class="float f2">🍋</div>'
    '<div class="float f3">🥦</div><div class="float f4">🍅</div>'
    "</div>"
    '<div class="how">'
    '<div class="how-item"><span class="how-num">1</span>Pick a goal</div>'
    '<div class="how-item"><span class="how-num">2</span>Add ingredients</div>'
    '<div class="how-item"><span class="how-num">3</span>Set preferences</div>'
    '<div class="how-item"><span class="how-num">4</span>Plan your meal</div>'
    '<div class="how-item"><span class="how-num">5</span>Make your meal</div>'
    "</div>",
    unsafe_allow_html=True,
)

# ---------- 1. GOAL ----------
section(1, "Pick your goal", "Your goal shapes the meals we suggest.")
goal = st.radio(
    "Choose a fitness goal",
    ["🔥 Fat loss", "💪 Muscle gain", "💚 Healthy eating"],
    horizontal=True,
    key="w_goal",
    label_visibility="collapsed",
)

# ---------- 2. INGREDIENTS ----------
section(2, "Add your ingredients", "Type them, tap a quick pick, or browse every food emoji.")
st.text_area(
    "Your ingredients",
    key="ingredients",
    placeholder="e.g. rice, eggs, paneer, tomatoes, onions",
    height=90,
    label_visibility="collapsed",
    on_change=queue_sound,
    args=("pop",),
)
st.caption("After typing, click outside the box or press Ctrl+Enter to add them to your basket.")


def food_picker(basket_now):
    """Compact emoji shelf: search box + category tabs. Tap to add, tap again to remove."""
    q = st.text_input(
        "Search foods", key="food_search", label_visibility="collapsed",
        placeholder="Search foods: mango, curry, tea...",
    )

    def grid(foods, tag):
        for start in range(0, len(foods), 6):
            cols = st.columns(6)
            for col, (name, emoji) in zip(cols, foods[start:start + 6]):
                tick = "✓" if name.lower() in basket_now else ""
                col.button(
                    f"{emoji}{tick}", key=f"pk_{tag}_{name}", help=name.capitalize(),
                    on_click=toggle_ingredient, args=(name,), use_container_width=True,
                )

    if q.strip():
        ql = q.strip().lower()
        found, seen_names = [], set()
        for n, e in [f for foods in FOOD_CATEGORIES.values() for f in foods] + ALIASES:
            if ql in n and n not in seen_names:
                seen_names.add(n)
                found.append((n, e))
        if found:
            grid(found, "s")
        else:
            st.caption("No match. You can still type it in the box.")
    else:
        st.caption(f"{TOTAL_FOODS} foods. Tap to add, tap again to remove. Hover for the name.")
        tabs = st.tabs(list(FOOD_CATEGORIES))
        for i, (tab, foods) in enumerate(zip(tabs, FOOD_CATEGORIES.values())):
            with tab:
                grid(foods, str(i))


basket_now = {i.lower() for i in parse_ingredients(st.session_state.ingredients)}
quick_cols = st.columns(5)
for col, name in zip(quick_cols[:4], QUICK_ADD):
    tick = "✓ " if name.lower() in basket_now else ""
    col.button(
        f"{tick}{emoji_for(name)} {name.capitalize()}",
        key=f"qa_{name}",
        on_click=toggle_ingredient,
        args=(name,),
        use_container_width=True,
    )
with quick_cols[4]:
    if hasattr(st, "popover"):
        picker = st.popover("🍴 All foods", use_container_width=True)
    else:
        picker = st.expander("🍴 All foods")
    with picker:
        food_picker(basket_now)

items = parse_ingredients(st.session_state.ingredients)

if items:
    chips = "".join(
        f'<span class="chip" style="animation-delay:{i * 0.06:.2f}s">'
        f'<span class="chip-emoji" style="animation-delay:{i * 0.3:.1f}s">{emoji_for(it)}</span>'
        f"{html.escape(it)}</span>"
        for i, it in enumerate(items)
    )
    st.markdown(f'<div class="basket">{chips}</div>', unsafe_allow_html=True)
    st.button("Clear basket", on_click=clear_ingredients, key="clear_basket_btn")
else:
    st.markdown(
        '<div class="basket"><span class="basket-empty">🧺 Your basket is empty. '
        "Add an ingredient to see it appear here.</span></div>",
        unsafe_allow_html=True,
    )

# ---------- 3. PREFERENCES ----------
section(3, "Set your preferences", "Tell us how you like to eat and cook.")
col1, col2 = st.columns(2)

with col1:
    diet = st.selectbox("🥬 Diet preference", ["Vegetarian", "Non-vegetarian", "Vegan"], key="w_diet")
    cuisine_choice = st.selectbox(
        "🌍 Cuisine", CUISINES, key="w_cuisine",
        help="The recipe name, spices, cooking method and serving style follow this cuisine. "
             "'Surprise me' lets the AI pick the best fit for your ingredients.",
    )
    custom_cuisine = ""
    if cuisine_choice == CUISINE_OTHER:
        custom_cuisine = st.text_input(
            "✍️ Your cuisine", placeholder="e.g. Ethiopian, Hyderabadi, Filipino", max_chars=40, key="w_custom",
        )
    cooking_time = st.select_slider(
        "⏱️ Available cooking time",
        options=[10, 15, 20, 30, 45, 60],
        key="w_time",
        format_func=lambda x: f"{x} min",
    )
    servings = st.number_input("🍽️ Number of servings", min_value=1, max_value=10, key="w_servings")

with col2:
    spice = st.select_slider("🌶️ Spice level", options=["Mild", "Medium", "Hot"], key="w_spice")
    experience = st.selectbox("👩‍🍳 Cooking experience", ["Beginner", "Intermediate", "Advanced"], key="w_exp")
    allergies = st.text_input("⚠️ Allergies or ingredients to avoid", placeholder="e.g. peanuts, dairy", key="w_allergies")

with st.expander("✨ More ways to personalize your meal"):
    high_protein = st.checkbox("Prioritize high-protein options", key="w_hp")
    indian_food = st.checkbox(
        "Prefer Indian-style recipes", key="w_indian",
        help="Only used when Cuisine is 'Any cuisine / Surprise me'. A cuisine you pick above always wins.",
    )
    use_leftovers = st.checkbox("Help me use leftovers", key="w_leftovers")
    available_only = st.checkbox("Use only my listed ingredients", key="w_only")
    missing_ingredients = st.text_input(
        "Missing ingredient to substitute (optional)", placeholder="e.g. paneer", key="w_missing"
    )
    inventory = st.text_area(
        "Kitchen inventory with expiry dates (optional)",
        placeholder="e.g. spinach: use by 20 Oct; tomatoes: use by 22 Oct", key="w_inventory",
    )

# ---------- 4. PLAN YOUR MEAL ----------
section(4, "Plan your meal", "Check your basket, then press the button.")
generate = st.button(
    "✨ Plan my meal", type="primary", use_container_width=True, key="plan_meal_btn"
)
regenerate = st.session_state.pop("regen_requested", False)

if generate:
    if not items:
        st.warning("Add at least one ingredient in step 2 first. Type some or tap a quick-add button.")
        queue_sound("error")
    else:
        cuisine_label, cuisine_mode = resolve_cuisine(cuisine_choice, custom_cuisine)
        st.session_state.generated_recipe = None
        st.session_state.recipe_history = []
        st.session_state.recipe_request = {
            "goal": goal,
            "items": items,
            "diet": diet,
            "cuisine": cuisine_label,
            "cuisine_mode": cuisine_mode,
            "cooking_time": cooking_time,
            "servings": servings,
            "spice": spice,
            "experience": experience,
            "allergies": allergies.strip(),
            "high_protein": high_protein,
            "indian_food": indian_food,
            "use_leftovers": use_leftovers,
            "available_only": available_only,
            "missing_ingredients": missing_ingredients.strip(),
            "inventory": inventory.strip(),
        }
        run_generation(st.session_state.recipe_request, [])
elif regenerate and st.session_state.recipe_request:
    # "Generate another meal": same saved preferences and ingredients, a different dish.
    # The current recipe stays on screen if this attempt fails.
    run_generation(st.session_state.recipe_request, list(st.session_state.recipe_history))

# ---------- 5. MAKE YOUR MEAL ----------
req = st.session_state.recipe_request
recipe = st.session_state.generated_recipe

if req and recipe:
    section(5, "Make your meal", "Follow the steps and tick each one off as you go.")
    rid = st.session_state.recipe_id
    nutrition = recipe["nutrition"]
    goal_text = req["goal"].split(" ", 1)[-1]
    plural = "s" if req["servings"] > 1 else ""

    # ---- hero card: ingredient plate on the left, title and quick facts on the right ----
    with card("hero"):
        plate_col, info_col = st.columns([1, 1.6], gap="large")
        with plate_col:
            plate_emojis = []
            for ing in recipe["ingredients"]:
                em = emoji_for(ing["name"])
                if em not in plate_emojis:
                    plate_emojis.append(em)
            plate_html = "".join(
                f'<span style="animation-delay:{i * 0.25:.2f}s">{em}</span>' for i, em in enumerate(plate_emojis[:6])
            )
            st.markdown(
                '<div class="plate"><span class="leaf lg pl1"></span><span class="leaf lgf pl2"></span>'
                f'<div class="plate-dish"><div class="plate-emojis">{plate_html}</div></div>'
                f'<div class="plate-cap"><b>🌍 {html.escape(recipe["cuisine"])}</b>'
                f'<span>{len(recipe["ingredients"])} ingredients · {recipe["total_min"]} min</span></div></div>',
                unsafe_allow_html=True,
            )
        with info_col:
            st.markdown(
                '<div class="tags">'
                f'<span class="tag tag-green">🌿 {html.escape(req["diet"])}</span>'
                f'<span class="tag tag-red">💪 {html.escape(goal_text)}</span>'
                f'<span class="tag tag-tan">🌍 {html.escape(recipe["cuisine"])}</span>'
                "</div>"
                f'<div class="plan-title">{html.escape(recipe["title"])}</div>'
                f'<div class="plan-desc">{html.escape(recipe["description"])}</div>'
                + (f'<div class="serve-line">🍽️ Serving idea: {html.escape(recipe["serving"])}</div>' if recipe.get("serving") else ""),
                unsafe_allow_html=True,
            )
            stat_tiles([
                ("⏱️", "Prep time", f'{recipe["prep_min"]} min'),
                ("🔥", "Cooking time", f'{recipe["cook_min"]} min'),
                ("⏳", "Total time", f'{recipe["total_min"]} min'),
                ("🍽️", "Servings", str(req["servings"])),
                ("📶", "Difficulty", recipe["difficulty"]),
                ("🌍", "Cuisine", recipe["cuisine"]),
            ])
            if recipe.get("time_warning"):
                st.warning(
                    f'⏰ This recipe takes {recipe["total_min"]} min in total, a little over your '
                    f'{req["cooking_time"]} min limit. Press Generate another meal for a quicker one.'
                )
            else:
                st.caption(f'✅ Total {recipe["total_min"]} min (prep {recipe["prep_min"]} + cooking '
                           f'{recipe["cook_min"]}), within your {req["cooking_time"]} min limit.')
            if recipe.get("gen_seconds") is not None:
                st.caption(f'⚡ Recipe created in {recipe["gen_seconds"]} s')

    # ---- three info cards: nutrition | ingredients | your choices ----
    c_nut, c_ing, c_pref = st.columns(3, gap="medium")

    with c_nut:
        with card("nutrition"):
            card_head("📊", "Estimated nutrition", "per serving", "red")
            icons = {"calories": ("🔥", SPICY_RED), "protein": ("💪", SCALLION_GREEN), "carbs": ("🌾", MISO_TAN),
                     "fat": ("💧", EARTHEN_BROWN), "fiber": ("🥦", SCALLION_GREEN)}
            rows = ""
            for key, label in NUTRIENT_LABELS:
                ic, bg = icons[key]
                rows += (f'<div class="nrow"><div class="nicon" style="background:{bg}">{ic}</div>'
                         f'<div><div class="nl">{label}</div>'
                         f'<div class="nv">{html.escape(fmt_nutrient(key, nutrition[key]))}</div></div></div>')
            st.markdown(rows, unsafe_allow_html=True)
            st.caption(nutrition["note"])

    with c_ing:
        with card("ingredients"):
            card_head("🧺", "Ingredients", f'for {req["servings"]} serving{plural}', "green")
            have_idx, extra_idx = [], []
            for i, ing in enumerate(recipe["ingredients"]):
                (have_idx if in_basket(ing["name"], req["items"]) else extra_idx).append(i)

            def ingredient_box(i, mark):
                ing = recipe["ingredients"][i]
                st.checkbox(f'{mark} {_md(ing["name"])} — {_md(ing["quantity"])}', key=f"fit_ing_{rid}_{i}")

            st.markdown('<div class="group-label">You already have</div>', unsafe_allow_html=True)
            for i in have_idx:
                ingredient_box(i, "✅")
            if not have_idx:
                st.caption("None of your basket items were used as main ingredients.")
            if extra_idx:
                st.markdown('<div class="group-label extra">Extra ingredients you need to add</div>',
                            unsafe_allow_html=True)
                for i in extra_idx:
                    ingredient_box(i, "➕")
            else:
                st.success("✅ Only ingredients you already have (plus basics like water or salt).")
            if req["allergies"]:
                st.caption(
                    f'⚠️ Avoiding: {req["allergies"]}. Always check packaged-food labels yourself '
                    "if you have a serious allergy."
                )

    with c_pref:
        with card("choices"):
            card_head("🎯", "Your choices", "", "tan")
            choices = [
                ("🏋️", "Goal", goal_text), ("🥬", "Diet", req["diet"]), ("🌍", "Cuisine", recipe["cuisine"]),
                ("⏱️", "Time limit", f'{req["cooking_time"]} min'), ("🍽️", "Servings", str(req["servings"])),
                ("🌶️", "Spice level", req["spice"]), ("👩‍🍳", "Experience", req["experience"]),
            ]
            st.markdown("".join(
                f'<div class="crow"><div class="cicon">{ic}</div><div><div class="nl">{html.escape(lb)}</div>'
                f'<div class="nv" style="font-size:15px">{html.escape(val)}</div></div></div>'
                for ic, lb, val in choices
            ), unsafe_allow_html=True)

    # ---- cooking steps with live progress ----
    with card("steps"):
        card_head("👩‍🍳", "Cooking steps", "tick each step as you go", "brown")
        steps = recipe["instructions"]
        progress_slot = st.empty()
        done = 0
        for i, s in enumerate(steps, start=1):
            timing = f' ⏱️ *{_md(s["timing"])}*' if s["timing"] else ""
            ticked = st.checkbox(f'**Step {i}.** {_md(s["step"])}{timing}', key=f"fit_step_{rid}_{i}")
            done += int(bool(ticked))
            extra_line = "  ·  ".join(
                x for x in (f'🔥 {_md(s["heat"])}' if s["heat"] else "", f'💡 {_md(s["tip"])}' if s["tip"] else "") if x
            )
            if extra_line:
                st.caption(extra_line)
        progress_slot.progress(done / len(steps), text=f"{done} of {len(steps)} steps done")
        if done == len(steps):
            st.success("🎉 All done. Enjoy your meal!")

    # ---- how this matches ----
    with card("match"):
        card_head("🌿", "How this matches your choices", "", "green")
        for line in recipe["preference_match"]:
            st.write(f"✅ {_md(line)}")

    # ---- save / download / print ----
    with card("save"):
        card_head("💾", "Save your recipe", "", "red")
        slug = _slug(recipe["title"])
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            already = is_saved(recipe)
            st.button(
                "✅ Saved" if already else "🤍 Save Recipe",
                on_click=save_current_recipe, disabled=already,
                use_container_width=True, key=f"save_{rid}",
            )
        with b2:
            st.download_button(
                "⬇️ Download (HTML)",
                data=recipe_to_html(recipe, req),
                file_name=f"{slug}.html",
                mime="text/html",
                use_container_width=True,
                key=f"dl_html_{rid}",
            )
        with b3:
            st.download_button(
                "📄 Text version",
                data=recipe_to_text(recipe, req),
                file_name=f"{slug}.txt",
                mime="text/plain",
                use_container_width=True,
                key=f"dl_txt_{rid}",
            )
        with b4:
            components.html(print_button_html(recipe, req), height=52)
        st.caption("Print opens the recipe in a clean new tab (allow pop-ups for this site). "
                   "Saved recipes are in the sidebar under Saved Recipes.")
        st.button(
            "🔄 Generate another meal",
            on_click=request_another_meal,
            use_container_width=True,
            key="another_meal_btn",
        )

flush_sound()
render_footer()