"""
nlp/preprocessor.py
====================
User ka raw input (Hindi/Hinglish/English) clean karo aur normalize karo.

Pipeline:
  raw text
    → lowercase
    → unicode normalize (Hindi devanagari safe rahe)
    → punctuation remove
    → tokenize
    → stopwords remove
    → tokens return
"""

import re
import unicodedata
import nltk

# NLTK data ek baar download karo
for pkg in ["punkt", "stopwords", "punkt_tab"]:
    try:
        nltk.data.find(f"tokenizers/{pkg}" if pkg.startswith("punkt") else f"corpora/{pkg}")
    except LookupError:
        nltk.download(pkg, quiet=True)

from nltk.tokenize import word_tokenize
from nltk.corpus   import stopwords

# ── English stopwords (Hindi ke liye custom list) ─────────────
_EN_STOPWORDS = set(stopwords.words("english"))

# Common Hindi/Hinglish stopwords jo command meaning nahi dete
_HI_STOPWORDS = {
    "karo", "kar", "do", "de", "dena", "krna", "karna",
    "mujhe", "mere", "mera", "meri", "hum", "mai", "main",
    "please", "pls", "plz", "yaar", "bhai", "yrr",
    "ek", "isko", "usko", "wala", "waali", "wale",
    "hai", "hain", "tha", "thi", "ho", "hoga",
    "aur", "ya", "ki", "ke", "ka", "ko", "se", "mein", "pe", "par",
    "ab", "abhi", "jaldi", "please", "zara", "thoda",
}

ALL_STOPWORDS = _EN_STOPWORDS | _HI_STOPWORDS


def normalize(text: str) -> str:
    """
    Text ko clean karo:
    - Lowercase
    - Unicode normalize (NFC — Devanagari safe)
    - Extra whitespace hatao
    - Special chars hatao (lekin Devanagari rakho)
    """
    if not text:
        return ""

    # Unicode normalize — Devanagari characters safe rahenge
    text = unicodedata.normalize("NFC", text)

    # Lowercase (English ke liye)
    text = text.lower()

    # Sirf alphanumeric, spaces, aur Devanagari range rakho
    # Devanagari Unicode range: \u0900-\u097F
    text = re.sub(r"[^\w\s\u0900-\u097F]", " ", text)

    # Extra spaces hatao
    text = re.sub(r"\s+", " ", text).strip()

    return text


def tokenize(text: str) -> list[str]:
    """
    Text ko words mein todho (NLTK word_tokenize).
    """
    return word_tokenize(text)


def remove_stopwords(tokens: list[str]) -> list[str]:
    """
    Stopwords hatao — sirf meaningful words rakho.
    """
    return [t for t in tokens if t not in ALL_STOPWORDS and len(t) > 1]


def preprocess(raw_text: str) -> dict:
    """
    Poora preprocessing pipeline chalao.

    Returns:
        {
          'original' : original text,
          'normalized': cleaned text,
          'tokens'   : all tokens,
          'keywords' : tokens without stopwords  ← ye sabse important hai
        }
    """
    normalized = normalize(raw_text)
    tokens     = tokenize(normalized)
    keywords   = remove_stopwords(tokens)

    return {
        "original"  : raw_text,
        "normalized": normalized,
        "tokens"    : tokens,
        "keywords"  : keywords,
    }
