"""Small, local design system for the Streamlit interface."""
import html
import re

import streamlit as st


def escape_markdown(text):
    return re.sub(r"([\\`*_{}\[\]()#+!|<>])", r"\\\1", str(text))


def install_style():
    st.markdown("""<style>
    .stApp {background:#f7f8fa; color:#18243a;}
    .block-container {max-width:1080px; padding-top:2.8rem; padding-bottom:3rem;}
    section[data-testid="stSidebar"] {background:#fff; border-right:1px solid #e8ebf0;}
    h1 {font-size:2rem !important; letter-spacing:-.05rem; line-height:1.2 !important; padding-bottom:.25rem !important;}
    h2,h3 {letter-spacing:-.025rem; font-size:1.2rem !important;}
    [data-testid="stMetric"] {background:white; border:1px solid #e8ebf0; border-radius:10px; padding:12px 18px;}
    [data-testid="stMetricValue"] {font-size:1.6rem;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:10px !important;}
    .stButton button,.stLinkButton a,.stDownloadButton button {border-radius:8px; min-height:2.4rem; font-weight:500; box-shadow:none;}
    .stTextInput input,.stTextArea textarea {border-radius:8px;}
    [data-testid="stChatMessage"] {background:white; border:1px solid #e8ebf0; border-radius:12px; padding:20px;}
    [data-testid="stChatInput"] {border-radius:12px;}
    [data-testid="stSidebarNav"] a {border-radius:8px;}
    .cm-brand {display:flex; align-items:center; gap:10px; margin:4px 0 20px;}
    .cm-mark {display:flex; align-items:center; justify-content:center; width:34px; height:34px; background:#4f46e5; color:white; border-radius:9px; font-size:12px; font-weight:700;}
    .cm-brand strong {font-size:20px; letter-spacing:-.5px;}
    .cm-brand small {display:block; color:#68758b; font-size:10px; letter-spacing:1px; margin-top:2px;}
    .cm-eyebrow {font-size:10px; letter-spacing:1.3px; font-weight:650; color:#68758b; margin-bottom:4px;}
    .cm-subtitle {color:#68758b; font-size:15px; max-width:760px; margin-top:0; margin-bottom:18px;}
    .cm-badge {display:inline-block; font-size:11px; font-weight:600; padding:4px 9px; border-radius:6px; margin:0 0 4px;}
    .cm-ready {color:#13745f; background:#e5f5ef;}.cm-wait {color:#6d5b1a; background:#fff3ce;}
    .cm-muted {color:#52627a; background:#edf1f7;}.cm-error {color:#a44545; background:#fdecec;}
    @media(max-width:700px) {h1{font-size:1.7rem !important;} .block-container{padding:2.7rem 1rem 2rem;} [data-testid="stMetric"]{padding:10px;} }
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
