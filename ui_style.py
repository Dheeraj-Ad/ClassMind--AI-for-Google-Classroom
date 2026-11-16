"""Small, local design system for the Streamlit interface."""
import html
import re

import streamlit as st


def escape_markdown(text):
    return re.sub(r"([\\`*_{}\[\]()#+!|<>])", r"\\\1", str(text))


def install_style():
    st.markdown("""<style>
    .stApp {background:#f6f8fc; color:#18243a;}
    .block-container {max-width:1240px; padding-top:2rem; padding-bottom:4rem;}
    section[data-testid="stSidebar"] {background:#edf1f8; border-right:1px solid #dfe5ef;}
    h1 {font-size:2.2rem !important; letter-spacing:-.065rem; line-height:1.15 !important;}
    h2,h3 {letter-spacing:-.025rem;}
    [data-testid="stMetric"] {background:white; border:1px solid #e1e7f0; border-radius:16px; padding:18px 22px;}
    [data-testid="stMetricValue"] {font-size:2rem;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px !important; background:white;}
    .stButton button,.stLinkButton a,.stDownloadButton button {border-radius:10px; min-height:2.6rem; font-weight:600;}
    .stTextInput input,.stTextArea textarea {border-radius:10px;}
    [data-testid="stChatMessage"] {background:white; border:1px solid #e1e7f0; border-radius:16px; padding:20px;}
    .cm-brand {display:flex; align-items:center; gap:12px; margin:6px 0 26px;}
    .cm-mark {display:flex; align-items:center; justify-content:center; width:42px; height:42px; background:#4f46e5; color:white; border-radius:12px; font-size:15px; font-weight:800;}
    .cm-brand strong {font-size:23px; letter-spacing:-.7px;}
    .cm-brand small {display:block; color:#61708a; font-size:11px; letter-spacing:1.6px; margin-top:2px;}
    .cm-eyebrow {font-size:11px; letter-spacing:2px; font-weight:750; color:#6558df; margin-bottom:9px;}
    .cm-subtitle {color:#61708a; font-size:16px; max-width:760px; margin-top:8px; margin-bottom:26px;}
    .cm-badge {display:inline-block; font-size:11px; letter-spacing:.4px; font-weight:700; padding:5px 10px; border-radius:30px; margin:0 0 8px;}
    .cm-ready {color:#13745f; background:#e5f5ef;}.cm-wait {color:#6d5b1a; background:#fff3ce;}
    .cm-muted {color:#52627a; background:#edf1f7;}.cm-error {color:#a44545; background:#fdecec;}
    .cm-hero {background:linear-gradient(125deg,#17233d,#303d6c); color:white; border-radius:22px; padding:42px; margin:10px 0 28px;}
    .cm-hero h1 {color:white; max-width:660px; font-size:2.9rem !important;}
    .cm-hero p {color:#d4ddf2; max-width:640px; font-size:17px; line-height:1.65;}
    .cm-hero .cm-eyebrow {color:#b7b4ff;}
    .cm-note {border-left:3px solid #8278ed; padding:10px 16px; color:#61708a; background:#f1f0ff; border-radius:0 10px 10px 0;}
    @media(max-width:700px) {.cm-hero{padding:24px;} .cm-hero h1{font-size:2.1rem !important;} .block-container{padding-top:1rem;}}
    </style>""", unsafe_allow_html=True)


def brand():
    st.markdown('<div class="cm-brand"><div class="cm-mark">CM</div><div><strong>ClassMind</strong><small>YOUR STUDY SPACE</small></div></div>', unsafe_allow_html=True)


def page_header(eyebrow, title, subtitle):
    st.markdown(f'<div class="cm-eyebrow">{html.escape(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<p class="cm-subtitle">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


def badge(text, style="muted"):
    style = style if style in {"ready", "wait", "muted", "error"} else "muted"
    st.markdown(f'<span class="cm-badge cm-{style}">{html.escape(text)}</span>', unsafe_allow_html=True)
