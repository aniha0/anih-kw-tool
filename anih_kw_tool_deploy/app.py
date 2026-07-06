"""ANIHA 勝ちKW抽出ツール 最終確定版"""
from __future__ import annotations
import io, re, unicodedata, zipfile
from typing import Optional
import pandas as pd
import streamlit as st

import datetime as _anls_dt
import pathlib as _anls_plib
import json as _anls_json


# ===================================================
# 定数
# ===================================================
ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$", re.IGNORECASE)

def is_asin_kn(kn: str) -> bool:
    """ASIN判定の単一基準。ファイル全体でこの関数のみを使用する。"""
    kn = str(kn)
    return bool(ASIN_RE.match(kn)) or kn.startswith("asin:")

def is_category_kn(kn: str) -> bool:
    """category判定の単一基準。ファイル全体でこの関数のみを使用する。"""
    return str(kn).startswith("category:")

DEFAULT_BRANDS = "アニハ\nANIHA\nゾイック\nノルバサン\nマラセブ"

CAMPAIGNS = [
    "液体", "涙やけ", "イヤー", "ジェル", "ふりかけ犬",
    "グルーミング", "お口周り", "乳酸菌猫", "ダニ捕り", "肉球",
    "ふりかけ猫", "除菌消臭", "シャンプー", "アイケア", "関節",
    "乳酸菌犬", "肉球S",
]

PRICES = {
    "ふりかけ犬": 2450, "お口周り": 1480, "ふりかけ猫": 2380,
    "アイケア": 1880, "イヤー": 1480, "グルーミング": 1980,
    "シャンプー": 1880, "ジェル": 1980, "ダニ捕り": 1480,
    "乳酸菌犬": 1880, "乳酸菌猫": 1880, "涙やけ": 1480,
    "液体": 1980, "肉球": 1450, "肉球S": 1480,
    "関節": 1880, "除菌消臭": 1980,
}

RENAME = {
    "campaign_theme": "キャンペーン名", "keyword": "検索語句",
    "ROAS": "ROAS", "sales": "売上",
    "cost": "広告費", "orders": "注文数",
    "CVR": "CVR", "clicks": "クリック数", "impressions": "インプレ",
}

# ===================================================
# 同一意図KW統合関数群
# ===================================================
def _k2h(t: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)

def canonical_keyword(kw: str) -> str:
    """全角半角・カナひら・記号・助詞の差異を吸収した正規化形式を返す。"""
    t = unicodedata.normalize("NFKC", str(kw)).lower()
    t = _k2h(t)
    t = re.sub(r"[-・/／\\｜〜～·]", " ", t)
    t = re.sub(r"\s*(の|用|向け|専用|対応|ための?|への?|にも?|での?)\s*", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def grouping_key(kw: str) -> str:
    """語順を正規化したグルーピングキー。"""
    can = canonical_keyword(kw)
    return "".join(sorted(t for t in can.split() if t))

def _compact(kw: str) -> str:
    return re.sub(r"\s+", "", canonical_keyword(kw))

def same_intent_keyword(kw1: str, kw2: str) -> bool:
    """2KWが同一検索意図かどうか判定する。"""
    return grouping_key(kw1) == grouping_key(kw2) or _compact(kw1) == _compact(kw2)

def rank_keyword_cluster(df: pd.DataFrame) -> pd.Series:
    """クラスタ内で最も強い代表KWを選定する（①インプレ②売上③注文④ROAS）。"""
    cols = [c for c in ["impressions", "orders", "sales", "ROAS"] if c in df.columns]
    return df.sort_values(cols or ["ROAS"], ascending=False).iloc[0]

def deduplicate_keyword_intent(df: pd.DataFrame) -> pd.DataFrame:
    """同一意図KWを統合し代表KW1件のみに絞り込む（2段階グループ化）。"""
    if df.empty:
        return df
    d = df.copy()
    d["_gs"] = d["keyword"].apply(grouping_key)
    d["_gc"] = d["keyword"].apply(_compact)
    r1 = pd.DataFrame([rank_keyword_cluster(g) for _, g in d.groupby("_gs", sort=False)]).reset_index(drop=True)
    r1["_gc2"] = r1["keyword"].apply(_compact)
    r2 = pd.DataFrame([rank_keyword_cluster(g) for _, g in r1.groupby("_gc2", sort=False)]).reset_index(drop=True)
    return r2.drop(columns=[c for c in ["_gs", "_gc", "_gc2"] if c in r2.columns], errors="ignore")

# ===================================================
# ユーティリティ
# ===================================================
def norm(x) -> str:
    if x is None or (isinstance(x, float) and x != x): return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(x)).strip().lower())

def get_theme(name: str) -> str:
    m = re.search(r"「(.*?)」|【(.*?)】", str(name))
    return (m.group(1) or m.group(2)) if m else ""

def official(t: str) -> str:
    if not t: return "未分類"
    if t in CAMPAIGNS: return t
    for c in CAMPAIGNS:
        if c in t: return c
    for c in CAMPAIGNS:
        if t in c: return c
    return "未分類"

def fcol(df: pd.DataFrame, cands: list) -> Optional[str]:
    for c in cands:
        if c in df.columns: return c
    low = {col.lower(): col for col in df.columns}
    for c in cands:
        if c.lower() in low: return low[c.lower()]
    return None

def rcsv(file) -> pd.DataFrame:
    r = file.read(); file.seek(0)
    if r[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return pd.read_csv(io.BytesIO(r), encoding="utf-16", sep="\t")
    try:
        return pd.read_csv(io.BytesIO(r), encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(io.BytesIO(r), encoding="cp932")

def covered(kw: str, reg: set) -> bool:
    return any(r in kw for r in reg if len(r.split()) >= 2)

def is_code(kw: str) -> bool:
    s = re.sub(r"[\s\-]", "", kw)
    return bool(
        ASIN_RE.match(kw.strip()) or
        re.match(r"^\d{8,}$", s) or
        re.match(r"^[a-zA-Z0-9]{8,}$", s)
    )

def is_title(kw: str) -> bool:
    return bool(re.search(r"[「」『』（）()]", kw)) or len(kw) >= 20 or kw.count(" ") >= 3

def tonum(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", "").str.replace("¥", ""),
        errors="coerce"
    ).fillna(0)

def clear():
    for k in ["has_results", "df_win", "df_del", "df_cpc", "df_cpc_product", "df_cpc_video",
              "df_pt_add", "df_pt_del", "stats", "dbg",
              "df_auto_del_kw_keyword", "df_auto_del_kw_product", "df_auto_del_kw_video",
              "df_auto_del_product", "df_auto_del_video"]:
        st.session_state.pop(k, None)

# ===================================================
# CSV / ZIP
# ===================================================
def bcols(df: pd.DataFrame, ex: list = []) -> list:
    base = ["campaign_theme", "keyword", "ROAS", "sales", "cost"]
    for c in ["orders", "CVR", "clicks"] + ex:
        if c in df.columns: base.append(c)
    return [c for c in base if c in df.columns]

def to_csv(df: pd.DataFrame, ex: list = []) -> bytes:
    d = df[bcols(df, ex)].copy().sort_values("ROAS", ascending=False)
    d["ROAS"] = d["ROAS"].round(2)
    d = d.rename(columns=RENAME)
    if "CVR" in d.columns:
        d["CVR"] = d["CVR"].apply(lambda x: f"{x:.1f}%")
    return d.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

def all_zip(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in CAMPAIGNS:
            dc = df[df["campaign_theme"] == c]
            if dc.empty: continue
            zf.writestr(
                f"winner_{c}.csv",
                dc.sort_values("ROAS", ascending=False)[["keyword"]]
                .rename(columns={"keyword": "検索語句"})
                .to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            )
    return buf.getvalue()

def del_camp_zip(df_del: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in CAMPAIGNS:
            dc = df_del[df_del["campaign_theme"] == c]
            if dc.empty: continue
            kws = dc.sort_values("cost", ascending=False)["keyword"].tolist()
            csv_content = "keyword\n" + "\n".join(kws)
            zf.writestr(f"{c}_削除KW.csv", csv_content.encode("utf-8-sig"))
    return buf.getvalue()

# ===================================================
# CPC調整ロジック
# ===================================================
CPC_RANK_ORDER = ["SS+", "SS", "S", "A", "B", "C", "D", "即削除", "判断保留"]

def assign_cpc_rank(cost: float, orders: float, roas: float, price: float):
    """STEP1→STEP2→STEP3→STEP4 の順で CPC ランクを返す。(rank, action, delta)"""
    orders = orders or 0
    if price <= 1500:
        del_thresh = 3000
    elif price <= 2000:
        del_thresh = 4000
    else:
        del_thresh = 5000
    if cost < 3000 and orders < 4:
        return ("判断保留", "変更なし", 0)
    if orders >= 20 and roas >= 4.0:
        rank, action, delta = "SS+", "CPC上げ", 5
    elif orders >= 20 and roas >= 2.0:
        rank, action, delta = "SS", "現状維持", 0
    elif roas >= 4.0:
        rank, action, delta = "S", "CPC上げ", 5
    elif roas >= 3.0:
        rank, action, delta = "A", "現状維持", 0
    elif roas >= 1.8:
        rank, action, delta = "B", "現状維持", 0
    elif roas >= 1.5:
        rank, action, delta = "C", "CPC下げ", -5
    else:
        rank, action, delta = "D", "CPC下げ", -10
    if cost >= del_thresh and roas < 0.8:
        rank, action, delta = "即削除", "即削除", 0
    return (rank, action, delta)

def build_cpc_df(df: pd.DataFrame) -> pd.DataFrame:
    """agg（price付き）から CPC 調整テーブルを生成する。"""
    if df.empty:
        return df
    d = df.copy()
    results = d.apply(
        lambda r: assign_cpc_rank(
            r.get("cost", 0) or 0,
            r.get("orders", 0) or 0,
            r.get("ROAS", 0) or 0,
            r.get("price", 3000) or 3000,
        ), axis=1
    )
    d["cpc_rank"]   = results.apply(lambda x: x[0])
    d["cpc_action"] = results.apply(lambda x: x[1])
    d["cpc_delta"]  = results.apply(lambda x: x[2])
    avg_cpc = (d["cost"] / d["clicks"].replace(0, float("nan"))).round(0) if "clicks" in d.columns else None
    if avg_cpc is not None:
        d["avg_cpc"]  = avg_cpc.fillna(0).astype(int)
        d["rec_cpc"]  = (d["avg_cpc"] + d["cpc_delta"]).clip(lower=1)
    return d

def cpc_camp_zip(df_cpc: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    cols_out = [c for c in ["campaign_name","ad_group","keyword","ROAS","cost","sales","orders",
                             "avg_cpc","cpc_rank","cpc_action","cpc_delta","rec_cpc"] if c in df_cpc.columns]
    rename_map = {"campaign_name":"キャンペーン名","ad_group":"広告グループ","keyword":"KWテキスト",
                  "cost":"広告費","sales":"売上","orders":"購入数","avg_cpc":"現在CPC",
                  "cpc_rank":"判定ランク","cpc_action":"推奨アクション","cpc_delta":"変更幅","rec_cpc":"推奨CPC"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in CAMPAIGNS:
            dc = df_cpc[df_cpc["campaign_theme"] == c]
            if dc.empty: continue
            out = dc[cols_out].rename(columns=rename_map)
            zf.writestr(f"{c}_CPC調整表.csv", out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"))
    return buf.getvalue()

# ===================================================
# Streamlit アプリ
# ===================================================

st.set_page_config(
    page_title="ANIHA Command Center",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown("""<style>
/* ── サイドバー ── */
[data-testid="stSidebar"] {
    background: #F7FAFC;
    border-right: 1px solid #E2E8F0;
}
[data-testid="stSidebar"] .stRadio label {
    font-size: .92rem;
    color: #4A5568;
    padding: 6px 0;
    cursor: pointer;
}
[data-testid="stSidebar"] .stRadio label:hover { color: #1A202C; }

/* ── KPIカード ── */
.kpi-card {
    border-radius: 10px;
    padding: 14px 16px;
    text-align: center;
    border: 1px solid #E2E8F0;
    margin-bottom: 4px;
}
.kpi-icon { font-size: 1.3rem; }
.kpi-label {
    font-size: .68rem;
    color: #718096;
    text-transform: uppercase;
    letter-spacing: .07em;
    margin-top: 3px;
}
.kpi-value {
    font-size: 1.7rem;
    font-weight: 700;
    line-height: 1.15;
    margin-top: 2px;
}
.kpi-sub { font-size: .68rem; color: #A0AEC0; margin-top: 3px; }

/* ── 条件バー ── */
.cond-bar {
    background: #EBF4FF;
    border: 1px solid #BEE3F8;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: .78rem;
    color: #2C5282;
    margin-bottom: 12px;
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
}
.cond-item { display: flex; align-items: center; gap: 4px; }
.cond-check { color: #3182CE; font-weight: 700; }

/* ── セクションヘッダー ── */
.section-header {
    font-size: .75rem;
    font-weight: 700;
    color: #718096;
    text-transform: uppercase;
    letter-spacing: .1em;
    padding: 4px 0 4px;
    border-bottom: 1px solid #E2E8F0;
    margin-bottom: 10px;
}

/* ── 件数バッジ ── */
.count-badge {
    background: #EBF4FF;
    border: 1px solid #90CDF4;
    border-left: 4px solid #3182CE;
    border-radius: 6px;
    padding: 8px 14px;
    margin: 6px 0 10px;
    font-size: .85rem;
    color: #2C5282;
}
</style>""", unsafe_allow_html=True)

import pathlib as _pl, base64 as _b64
def _load_logo(width_px: int = 180) -> str:
    for p in [_pl.Path("assets/logo.png"), _pl.Path("logo.png")]:
        if p.exists():
            b64 = _b64.b64encode(p.read_bytes()).decode()
            return '<img src="data:image/png;base64,' + b64 + '" width="' + str(width_px) + '" style="object-fit:contain;">'
    return ""

def _cond_bar(items: list):
    parts = "".join(
        f'<span class="cond-item"><span class="cond-check">✓</span> {l}: <b>{v}</b></span>'
        for l, v in items
    )
    st.markdown(f'<div class="cond-bar">{parts}</div>', unsafe_allow_html=True)

# ─── 固定パラメータ（デフォルト値で固定）──────────────────────
brands   = [norm(b) for b in DEFAULT_BRANDS.strip().splitlines() if b.strip()]
min_ord  = 3
min_clk  = 5
min_cost = 300

# ─── Sidebar ツリーナビゲーション ─────────────────────
_VALID_PAGES = {
    "📋 キーワード追加", "📊 DateDive売れる予測KW",
    "🚫 キーワード削除", "📈 キーワードCPC調整", "🎯 商品CPC調整", "📹 動画CPC調整",
    "➕ 商品追加", "🗑️ 商品削除",
    "📹 動画追加",     "📹 動画削除",
    "📄 オートKW削除", "🎯 オート商品削除", "🎥 オート動画削除",
    "📥 ダウンロード", "📖 取扱説明書",
}
_ADD_PAGES = {"📋 キーワード追加", "➕ 商品追加", "📹 動画追加"}
_DEL_PAGES = {"🚫 キーワード削除", "🗑️ 商品削除", "📹 動画削除"}
_AUTO_DEL_PAGES = {"📄 オートKW削除", "🎯 オート商品削除", "🎥 オート動画削除"}
_CPC_PAGES = {"📈 キーワードCPC調整", "🎯 商品CPC調整", "📹 動画CPC調整"}

if "current_page" not in st.session_state or st.session_state["current_page"] not in _VALID_PAGES:
    st.session_state["current_page"] = "📋 キーワード追加"
_cp = st.session_state["current_page"]

def _nav_btn(label: str, page_key: str, icon: str = "") -> None:
    is_active = st.session_state["current_page"] == page_key
    _lbl = f"{'▶' if is_active else '　'} {icon}{label}" if icon else f"{'▶' if is_active else '　'} {label}"
    if st.button(_lbl, key=f"_nav_{hash(page_key) & 0xFFFFFF}",
                 use_container_width=True,
                 type="primary" if is_active else "secondary"):
        st.session_state["current_page"] = page_key

with st.sidebar:
    # ── 追加
    with st.expander("➕  キーワード追加", expanded=(_cp in _ADD_PAGES)):
        _nav_btn("キーワード",  "📋 キーワード追加",               "📋 ")
        _nav_btn("商品",        "➕ 商品追加", "🎯 ")
        _nav_btn("動画",        "📹 動画追加",       "📹 ")
    # ── 削除
    with st.expander("🚫  キーワード削除", expanded=(_cp in _DEL_PAGES)):
        _nav_btn("キーワード",  "🚫 キーワード削除",               "📋 ")
        _nav_btn("商品",        "🗑️ 商品削除", "🎯 ")
        _nav_btn("動画",        "📹 動画削除",        "📹 ")
    # ── CPC調整
    with st.expander("📈  CPC調整", expanded=(_cp in _CPC_PAGES)):
        _nav_btn("キーワード",  "📈 キーワードCPC調整",   "📋 ")
        _nav_btn("商品",        "🎯 商品CPC調整", "🎯 ")
        _nav_btn("動画",        "📹 動画CPC調整", "📹 ")
    # ── オート除外KW
    with st.expander("🧹  オート除外KW", expanded=(_cp in _AUTO_DEL_PAGES)):
        _nav_btn("キーワード",  "📄 オートKW削除",    "📄 ")
        _nav_btn("商品",        "🎯 オート商品削除",  "🎯 ")
        _nav_btn("動画",        "🎥 オート動画削除",  "🎥 ")
    # ── その他
    st.markdown("---")
    _nav_btn("DateDive売れる予測KW",  "📊 DateDive売れる予測KW", "📊 ")
    _nav_btn("ダウンロード",           "📥 ダウンロード",          "📥 ")
    _nav_btn("取扱説明書",             "📖 取扱説明書",            "📖 ")
    st.markdown("---")
    # 💲 売価マスタ
    st.markdown('<p class="section-header">💲 売価マスタ</p>', unsafe_allow_html=True)
    for _c, _p in PRICES.items(): st.caption(f"{_c}：¥{_p:,}")
    st.markdown("---")
    st.caption("ANIHA Command Center v2.0")

current_page = st.session_state["current_page"]

# ─── Header ─────────────────────────────────────────
_h_logo = _load_logo(190)
_h1, _h2 = st.columns([1, 4])
with _h1:
    if _h_logo: st.markdown(_h_logo, unsafe_allow_html=True)
    else: st.markdown('<div style="font-size:2.8rem;">🐾</div>', unsafe_allow_html=True)
with _h2:
    st.markdown("""<div style="padding-top:6px;">
        <div style="font-size:1.55rem;font-weight:800;color:#1A202C;letter-spacing:-.01em;">
            🚀 ANIHA Command Center</div>
        <div style="font-size:.9rem;color:#4A5568;margin-top:3px;">
            Amazon Advertising Intelligence Platform</div>
        <div style="font-size:.78rem;color:#718096;margin-top:2px;">
            ANIHA専用のAmazon広告運用分析プラットフォーム</div>
    </div>""", unsafe_allow_html=True)
st.markdown("---")

# ─── File Upload（入力元を sf 単体から3つのCSVバケットへ差し替え） ──
# 【重要】この下の「if run:」以降（既存の抽出ロジック本体）は無変更。
# 変更したのは sf の取得元のみ：廃止した単一の「検索用語レポート」
# アップロード欄の代わりに、3つのCSVバケットのうち保持件数が1件の
# ものを既存のsfと同じ入力として使う（優先順位: 7日→30日→その他）。
# 複数件保持されているバケットは保持のみ行い、抽出には使用しない
# （比較分析ロジックは今回実装しない）。
def _csv_bucket_uploader(label: str, state_key: str, widget_key: str, help_text: str = ""):
    st.markdown(f"**{label}**")
    if help_text:
        st.caption(help_text)
    _new_files = st.file_uploader(
        label, type="csv", accept_multiple_files=True,
        key=widget_key, label_visibility="collapsed",
    )
    if state_key not in st.session_state:
        st.session_state[state_key] = {}
    if _new_files:
        for _f in _new_files:
            st.session_state[state_key][_f.name] = _f
    _held = st.session_state[state_key]
    if _held:
        st.caption(f"📂 保持中のCSV（{len(_held)}件）")
        for _name in sorted(_held.keys()):
            st.markdown(f"・{_name}")
    else:
        st.caption("保持中のCSVはありません")

_b1, _b2, _b3 = st.columns(3)
with _b1:
    _csv_bucket_uploader("📅 7日比較CSV", "csv_bucket_7d", "csv_bucket_7d_uploader")
with _b2:
    _csv_bucket_uploader("📅 30日比較CSV", "csv_bucket_30d", "csv_bucket_30d_uploader")
with _b3:
    _csv_bucket_uploader("📊 その他CSV", "csv_bucket_other", "csv_bucket_other_uploader")

if st.button("🗑 比較CSVをクリア", use_container_width=True):
    for _bk in ("csv_bucket_7d", "csv_bucket_30d", "csv_bucket_other"):
        st.session_state[_bk] = {}

def _sf_earliest_by_period(_held_files):
    # 「期間」列（既存の_anls_render_tabのcpc_kw等と同じ"YYYY/MM/DD - YYYY/MM/DD"形式）
    # の開始日が最も古いファイルを選ぶ。rcsv/fcolは呼び出すのみで一切変更しない。
    # ファイル名・アップロード順（保持順）は一切参照しない。「期間」列が1件も
    # 解析できない場合はNoneを返す（順序によるフォールバックは行わない）。
    _best_file = None
    _best_start = None
    for _f in _held_files:
        try:
            _df_peek = rcsv(_f)
            _pc = fcol(_df_peek, ["期間"])
            if not _pc:
                continue
            _parts = _df_peek[_pc].astype(str).str.split(" - ", expand=True)
            _starts = pd.to_datetime(_parts[0], format="%Y/%m/%d", errors="coerce")
            if _starts.notna().any():
                _start_date = _starts.min()
                if _best_start is None or _start_date < _best_start:
                    _best_start = _start_date
                    _best_file = _f
        except Exception:
            continue
    return _best_file

# 3バケットを差別せず「分析対象バケット」として統一的に扱う。
# 順序（保持順・アップロード順）・ファイル名には一切依存せず、
# 全バケット合算したCSV群の中から「期間」列の開始日が最も古い1件をsfとする。
_all_held_files = []
for _bk in ("csv_bucket_7d", "csv_bucket_30d", "csv_bucket_other"):
    _all_held_files.extend(st.session_state.get(_bk, {}).values())

sf = None
if len(_all_held_files) == 1:
    sf = _all_held_files[0]
elif len(_all_held_files) >= 2:
    sf = _sf_earliest_by_period(_all_held_files)

run = st.button("🚀 分析開始", type="primary", use_container_width=True)
st.markdown("---")

# ─── Processing ─────────────────────────────────────
if run:
    if not sf:
        st.error("検索用語レポートをアップロードしてください"); st.stop()
    with st.spinner("分析中..."):
        dfs = rcsv(sf)
        kc  = fcol(dfs, ["検索用語", "カスタマーの検索用語", "Customer Search Term", "search term"])
        cc  = fcol(dfs, ["キャンペーン名", "Campaign Name", "campaign name"])
        sc  = fcol(dfs, ["売上", "売上額", "合計売上", "広告費売上高", "7日間の総売上高", "Attributed Sales", "Sales"])
        oc_ = fcol(dfs, ["合計費用", "費用", "広告費", "コスト", "Cost", "Spend", "spend"])
        od  = fcol(dfs, ["商品購入数", "注文数", "注文された商品点数", "Orders", "Purchases"])
        clk = fcol(dfs, ["クリック数", "クリック", "Clicks", "clicks"])
        imp = fcol(dfs, ["インプレッション数", "インプレッション", "Impressions", "impressions"])
        tkc = fcol(dfs, ["ターゲティング", "ターゲッティング", "キーワード", "Targeting", "targeting", "Keyword", "keyword"])
        # CPC用: Keyword Text列を最優先 / なければ tkc（ターゲティング）にフォールバック
        kwt_col = fcol(dfs, ["Keyword Text", "Keyword text", "keyword text", "キーワードテキスト"])
        cpc_kw_col = kwt_col if kwt_col else tkc   # Keyword Text優先・Targeting フォールバック
        agn = fcol(dfs, ["Ad Group Name", "広告グループ名", "Ad Group", "広告グループ", "ad group"])
        ttype = fcol(dfs, ["Campaign Targeting Type", "ターゲティングタイプ", "Targeting Type", "targeting type"])
        miss = [n for v, n in [(kc,"検索用語"),(cc,"キャンペーン名"),(sc,"売上"),(oc_,"広告費")] if not v]
        if miss: st.error(f"列が見つかりません: {miss}"); st.write(list(dfs.columns)); st.stop()
        if not tkc:
            st.error("「ターゲティング」列が見つかりません。レポート出力時にターゲティング列を含めてください。")
            st.write("検出された列:", list(dfs.columns)); st.stop()
        dfs[sc]  = tonum(dfs[sc])
        dfs[oc_] = tonum(dfs[oc_])
        for _col in [od, clk, imp]:
            if _col: dfs[_col] = tonum(dfs[_col])
        dfs["kn"] = dfs[kc].apply(norm)
        dfs["ct"] = dfs[cc].apply(lambda x: official(get_theme(str(x))))
        mask   = dfs[cc].str.contains("オート|auto", case=False, na=False)
        n_auto = int(mask.sum())
        d0     = dfs[mask].copy()
        reg  = set(dfs[tkc].apply(norm)); reg.discard("")
        n_ex = int(d0["kn"].isin(reg).sum())
        d0   = d0[~d0["kn"].isin(reg)]
        n_pt = int(d0["kn"].apply(lambda k: covered(k, reg)).sum())
        d0   = d0[~d0["kn"].apply(lambda k: covered(k, reg))]
        n_ar = len(d0)
        n_br = int(d0["kn"].apply(lambda k: any(b in k for b in brands)).sum())
        d0   = d0[~d0["kn"].apply(lambda k: any(b in k for b in brands))]
        n_cd = int(d0["kn"].apply(is_code).sum())
        d0   = d0[~d0["kn"].apply(is_code)]
        n_tl = int(d0[kc].apply(is_title).sum())
        d0   = d0[~d0[kc].apply(is_title)]
        n_ae = len(d0)
        agg_d = {
            "keyword":        (kc,   "first"),
            "campaign_theme": ("ct", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
            "sales":          (sc,   "sum"),
            "cost":           (oc_,  "sum"),
        }
        if od:  agg_d["orders"]      = (od,  "sum")
        if clk: agg_d["clicks"]      = (clk, "sum")
        if imp: agg_d["impressions"] = (imp, "sum")
        agg = d0.groupby("kn").agg(**agg_d).reset_index(drop=True)
        agg["ROAS"] = agg.apply(
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
        if "clicks" in agg.columns and "orders" in agg.columns:
            agg["CVR"] = agg.apply(
                lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0, axis=1)
        agg["price"] = agg["campaign_theme"].map(PRICES)
        agg = agg[agg["price"].notna()].copy()
        n_pre    = len(agg)
        n_sl     = int((agg["sales"] >= agg["price"] * 2).sum())
        d1       = agg[agg["sales"] >= agg["price"] * 2].copy()
        n_ro     = int((d1["ROAS"] >= 2.0).sum())
        d1       = d1[d1["ROAS"] >= 2.0].copy()
        if "orders" in d1.columns:
            n_of = int((d1["orders"] < min_ord).sum())
            d1   = d1[d1["orders"] >= min_ord].copy()
        else: n_of = 0
        if "clicks" in d1.columns:
            n_clk_f = int((d1["clicks"] < min_clk).sum())
            d1 = d1[d1["clicks"] >= min_clk].copy()
        else: n_clk_f = 0
        n_cost_f = int((d1["cost"] < min_cost).sum())
        d1 = d1[d1["cost"] >= min_cost].copy()
        n_af = len(d1)
        d1.drop(columns=["price"], inplace=True, errors="ignore")
        dw = deduplicate_keyword_intent(d1)
        nf = len(dw)
        win_kws = set(dw["keyword"].tolist())
        # ── キーワード削除用: マニュアルキャンペーンのみを母集団にして集計 ──
        # オートキャンペーンを含まない行のみ抽出（オート除外KWとは完全に別ロジック）
        _del_manual_mask = ~dfs[cc].str.contains("オート|auto", case=False, na=False)
        _del_d0 = dfs[_del_manual_mask].copy()
        # ── ASIN / asin: / category: を groupby前に除外（検索語のみ残す）──
        _del_d0 = _del_d0[~_del_d0["kn"].apply(
            lambda k: is_asin_kn(k) or is_category_kn(k)
        )].copy()
        _del_agg_d = {
            "keyword":        (kc,   "first"),
            "campaign_theme": ("ct", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
            "sales":          (sc,   "sum"),
            "cost":           (oc_,  "sum"),
        }
        if od:  _del_agg_d["orders"]      = (od,  "sum")
        if clk: _del_agg_d["clicks"]      = (clk, "sum")
        if imp: _del_agg_d["impressions"] = (imp, "sum")
        _del_agg = _del_d0.groupby("kn").agg(**_del_agg_d).reset_index(drop=True)
        _del_agg["ROAS"] = _del_agg.apply(
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
        if "clicks" in _del_agg.columns and "orders" in _del_agg.columns:
            _del_agg["CVR"] = _del_agg.apply(
                lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0, axis=1)
        _del_agg["price"] = _del_agg["campaign_theme"].map(PRICES)
        _del_agg = _del_agg[_del_agg["price"].notna()].copy()
        del_mask = (_del_agg["cost"] >= _del_agg["price"] * 2) & (_del_agg["ROAS"] < 0.8)
        df_del_ = _del_agg[del_mask].copy()
        df_del_ = df_del_[~df_del_["keyword"].isin(win_kws)].copy()
        df_del_.drop(columns=["price"], inplace=True, errors="ignore")
        # ── CPC用: Manual KWのみ抽出（Customer Search Termではなく Keyword Text単位）
        import re as _re
        _cpc_raw = dfs.copy()
        _cpc_raw["ct"] = _cpc_raw[cc].apply(lambda x: official(get_theme(str(x))))

        # ⓪ SP広告(マニュアル) KWキャンペーンのみ抽出
        # 対象: SP広告(マニュアル) のみ
        # 除外: SB広告(動画) / SP広告(動画) / 商品ターゲ / 動画ターゲ / その他
        _sp_manual_mask = (
            _cpc_raw[cc].str.contains("SP広告.*マニュアル|SP.*manual", case=False, na=False)
            & ~_cpc_raw[cc].str.contains("商品ターゲ|動画ターゲ", case=False, na=False)
        )
        _cpc_raw = _cpc_raw[_sp_manual_mask].copy()

        # ①Auto除外
        _auto_mask = _cpc_raw[cc].str.contains("オート|auto", case=False, na=False)
        if ttype:
            _auto_mask = _auto_mask | _cpc_raw[ttype].str.contains("auto", case=False, na=False)
        n_cpc_auto = int(_auto_mask.sum())
        _cpc_raw = _cpc_raw[~_auto_mask].copy()

        # ②Product Targeting除外 (ASIN/category/asin:/complement/substitute)
        def _is_pt(s):
            kn = norm(s)
            return (is_asin_kn(kn)
                    or is_category_kn(kn)
                    or "complement" in kn
                    or "substitute" in kn)
        if cpc_kw_col:
            _pt_mask = _cpc_raw[cpc_kw_col].apply(_is_pt)
        else:
            _pt_mask = pd.Series(False, index=_cpc_raw.index)
        n_cpc_pt = int(_pt_mask.sum())
        _cpc_raw = _cpc_raw[~_pt_mask].copy()

        # ③Keyword Text空欄除外
        if cpc_kw_col:
            _empty_mask = _cpc_raw[cpc_kw_col].isna() | (_cpc_raw[cpc_kw_col].astype(str).str.strip() == "")
        else:
            _empty_mask = pd.Series(True, index=_cpc_raw.index)
        n_cpc_empty = int(_empty_mask.sum())
        _cpc_raw = _cpc_raw[~_empty_mask].copy()

        # ④ ブランドKW除外（アニハ・あには・アニは を含む語を除外）
        _BRAND_KW = ["アニハ", "あには", "アニは"]
        if cpc_kw_col:
            _brand_mask = _cpc_raw[cpc_kw_col].astype(str).str.contains(
                "|".join(_BRAND_KW), na=False
            )
            _cpc_raw = _cpc_raw[~_brand_mask].copy()

        n_cpc_manual = len(_cpc_raw)

        # Keyword Text単位で集計 (Keyword Text優先 / Targeting フォールバック)
        if cpc_kw_col and not _cpc_raw.empty:
            _cpc_raw["_kw_norm"] = _cpc_raw[cpc_kw_col].apply(norm)
            _agg_cpc_d = {
                "keyword":        (cpc_kw_col, "first"),
                "campaign_name":  (cc,  "first"),
                "campaign_theme": ("ct", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
                "sales":          (sc,  "sum"),
                "cost":           (oc_, "sum"),
            }
            if od:  _agg_cpc_d["orders"] = (od, "sum")
            if clk: _agg_cpc_d["clicks"] = (clk, "sum")
            if agn: _agg_cpc_d["ad_group"] = (agn, "first")
            _agg_cpc = _cpc_raw.groupby(["ct", "_kw_norm"]).agg(**_agg_cpc_d).reset_index(drop=True)
            _agg_cpc["ROAS"] = _agg_cpc.apply(
                lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
            _agg_cpc["price"] = _agg_cpc["campaign_theme"].map(PRICES)
            _agg_cpc = _agg_cpc[_agg_cpc["price"].notna()].copy()
            df_cpc_ = build_cpc_df(_agg_cpc)
        else:  # cpc_kw_col が取得できなかった場合
            df_cpc_ = pd.DataFrame()
            n_cpc_auto = n_cpc_pt = n_cpc_empty = n_cpc_manual = 0

        # ── 商品ターゲ分析: マニュアル / 動画 を分離抽出 ──────────────
        import re as _re_asin

        def _extract_asin(s):
            """asin="B0...", asin-expanded="B0...", 裸の B0... からASINを抽出"""
            m = _re_asin.search(r'B0[A-Z0-9]{8}', str(s), _re_asin.IGNORECASE)
            return m.group(0).upper() if m else ""

        def _build_pt_dfs(camp_mask):
            """camp_mask で絞った行から追加/削除候補DataFrameを返す"""
            _d = _mpt_base[camp_mask].copy()
            if _d.empty or not tkc:
                return pd.DataFrame(), pd.DataFrame()
            _d["_asin_clean"] = _d[tkc].apply(_extract_asin)
            _d = _d[_d["_asin_clean"] != ""].copy()
            if _d.empty:
                return pd.DataFrame(), pd.DataFrame()
            _d["_asin_key"] = _d["_asin_clean"]
            _agg_d = {
                "asin":           ("_asin_clean", "first"),
                "campaign_name":  (cc, "first"),
                "campaign_theme": ("ct", lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else "未分類"),
                "sales":          (sc, "sum"),
                "cost":           (oc_, "sum"),
            }
            if od:  _agg_d["orders"]   = (od,  "sum")
            if clk: _agg_d["clicks"]   = (clk, "sum")
            if agn: _agg_d["ad_group"] = (agn, "first")
            _agg = _d.groupby("_asin_key").agg(**_agg_d).reset_index(drop=True)
            _agg["ROAS"]  = _agg.apply(
                lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
            _agg["price"] = _agg["campaign_theme"].map(PRICES)
            _agg = _agg[_agg["price"].notna()].copy()
            # 追加: 信頼度フィルター + 条件
            _tr = _agg.copy()
            if "orders" in _tr.columns: _tr = _tr[_tr["orders"] >= 3]
            if "clicks" in _tr.columns: _tr = _tr[_tr["clicks"] >= 5]
            _tr = _tr[_tr["cost"] >= 300].copy()
            _add = _tr[(_tr["sales"] >= _tr["price"] * 2) & (_tr["ROAS"] >= 2.0)].copy()
            _sc2 = [c for c in ["ROAS","sales","orders"] if c in _add.columns]
            _add = _add.sort_values(_sc2, ascending=[False]*len(_sc2)).reset_index(drop=True)
            # 削除: 条件
            _del = _agg[(_agg["cost"] >= _agg["price"] * 2) & (_agg["ROAS"] < 0.8)].copy()
            _ds2 = [c for c in ["cost","ROAS"] if c in _del.columns]
            _del = _del.sort_values(_ds2, ascending=[False, True]).reset_index(drop=True)
            return (_add.drop(columns=["price"], errors="ignore"),
                    _del.drop(columns=["price"], errors="ignore"))

        _mpt_base = dfs.copy()
        _mpt_base["ct"] = _mpt_base[cc].apply(lambda x: official(get_theme(str(x))))

        # マニュアル: 「商品ターゲ」含む AND「動画」含まない AND「オート」含まない
        _mask_m = (
            _mpt_base[cc].str.contains("商品ターゲ", na=False) &
            ~_mpt_base[cc].str.contains("動画", na=False) &
            ~_mpt_base[cc].str.contains("オート|auto", case=False, na=False)
        )
        # 動画: 「商品ターゲ」含む AND「動画」含む
        _mask_v = (
            _mpt_base[cc].str.contains("商品ターゲ", na=False) &
            _mpt_base[cc].str.contains("動画", na=False)
        )

        df_pt_add_m_, df_pt_del_m_ = _build_pt_dfs(_mask_m)
        df_pt_add_v_, df_pt_del_v_ = _build_pt_dfs(_mask_v)

        n_mpt_add = len(df_pt_add_m_) + len(df_pt_add_v_)
        n_mpt_del = len(df_pt_del_m_) + len(df_pt_del_v_)
        n_mpt_auto_ex = int((~_mask_m & ~_mask_v).sum())
        n_mpt_kw_ex = 0; n_mpt_dup_ex = 0

        # ── 商品ターゲ CPC調整用 DataFrame ────────────────────────────
        def _build_pt_cpc_df(camp_mask):
            """商品ターゲデータをASIN単位で集計してCPC調整DataFrameを返す"""
            _d = _mpt_base[camp_mask].copy()
            if _d.empty or not tkc:
                return pd.DataFrame()
            _d["_asin_clean"] = _d[tkc].apply(_extract_asin)
            _d = _d[_d["_asin_clean"] != ""].copy()
            if _d.empty:
                return pd.DataFrame()
            _d["_asin_key"] = _d["_asin_clean"]
            _agg_d2 = {
                "asin":           ("_asin_clean", "first"),
                "campaign_name":  (cc, "first"),
                "campaign_theme": ("ct", lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else "未分類"),
                "sales":          (sc, "sum"),
                "cost":           (oc_, "sum"),
            }
            if od:  _agg_d2["orders"]   = (od,  "sum")
            if clk: _agg_d2["clicks"]   = (clk, "sum")
            if agn: _agg_d2["ad_group"] = (agn, "first")
            _agg2 = _d.groupby("_asin_key").agg(**_agg_d2).reset_index(drop=True)
            _agg2["ROAS"]  = _agg2.apply(
                lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
            _agg2["price"] = _agg2["campaign_theme"].map(PRICES)
            _agg2 = _agg2[_agg2["price"].notna()].copy()
            return build_cpc_df(_agg2)

        df_cpc_product_ = _build_pt_cpc_df(_mask_m)
        df_cpc_video_   = _build_pt_cpc_df(_mask_v)

        # ── オート除外KW用 DataFrame ───────────────────────────────
        # キーワード / 商品(ASIN) / 動画(category:) を最初から独立して生成する
        if kc and tkc:
            _auto_kw_base = dfs[dfs[cc].str.contains("オート|auto", case=False, na=False)].copy()
            _n_akw1 = len(_auto_kw_base)                                       # ① オート広告抽出（行数）
            def _ct_auto(name):
                s = str(name)
                r = official(get_theme(s))
                if r != "未分類": return r
                for c in CAMPAIGNS:
                    if c in s: return c
                return "未分類"
            _auto_kw_base["ct"] = _auto_kw_base[cc].apply(_ct_auto)
            _auto_kw_base["kn"] = _auto_kw_base[kc].apply(norm)
            _manual_mask_kw = ~dfs[cc].str.contains("オート|auto", case=False, na=False)
            _manual_reg_kw = set(dfs[_manual_mask_kw][tkc].apply(norm)); _manual_reg_kw.discard("")
            _dup_kw = _auto_kw_base["kn"].isin(_manual_reg_kw)
            _auto_kw_base = _auto_kw_base[~_dup_kw].copy()
            _n_akw2 = len(_auto_kw_base)                                       # ② マニュアル重複除外後（行数）

            # 共通agg定義ビルダー（商品・動画専用。keyword列を含む）
            def _make_agg_d():
                _d = {
                    "keyword":        (kc,   "first"),
                    "campaign_theme": ("ct", lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else "未分類"),
                    "sales":          (sc,   "sum"),
                    "cost":           (oc_,  "sum"),
                }
                if od:  _d["orders"]   = (od,  "sum")
                if agn: _d["ad_group"] = (agn, "first")
                return _d

            # キーワード専用agg定義ビルダー（keyword列を含めない。kn由来でしか作らない）
            def _make_agg_d_kw():
                _d = {
                    "campaign_theme": ("ct", lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else "未分類"),
                    "sales":          (sc,   "sum"),
                    "cost":           (oc_,  "sum"),
                }
                if od:  _d["orders"]   = (od,  "sum")
                if agn: _d["ad_group"] = (agn, "first")
                return _d

            def _apply_del_filter(df_agg):
                df_agg["ROAS"]  = df_agg.apply(
                    lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
                df_agg["price"] = df_agg["campaign_theme"].map(PRICES)
                df_agg = df_agg[df_agg["price"].notna()].copy()
                result = df_agg[
                    (df_agg["cost"] >= df_agg["price"] * 2) & (df_agg["ROAS"] <= 0.8)
                ].copy()
                result.drop(columns=["price"], errors="ignore", inplace=True)
                return result

            # ── キーワード専用DataFrame: ASIN / asin: / category: をgroupby前に除外 ──
            _base_kw = _auto_kw_base[~_auto_kw_base["kn"].apply(
                lambda k: is_asin_kn(k) or is_category_kn(k)
            )].copy()

            _agg_kw = (
                _base_kw
                .groupby("kn", as_index=False)
                .agg(**_make_agg_d_kw())
            )
            _agg_kw.insert(0, "keyword", _agg_kw["kn"])

            df_auto_del_kw_keyword_ = _apply_del_filter(_agg_kw)

            # ── 商品専用DataFrame: ASINのみ残す ──
            _base_pt = _auto_kw_base[_auto_kw_base["kn"].apply(
                lambda k: is_asin_kn(k)
            )].copy()
            _agg_pt = _base_pt.groupby("kn").agg(**_make_agg_d()).reset_index(drop=True)
            df_auto_del_kw_product_ = _apply_del_filter(_agg_pt)

            # ── 動画専用DataFrame: category: のみ残す ──
            _base_vid = _auto_kw_base[_auto_kw_base["kn"].apply(
                lambda k: is_category_kn(k)
            )].copy()
            _agg_vid = _base_vid.groupby("kn").agg(**_make_agg_d()).reset_index(drop=True)
            df_auto_del_kw_video_ = _apply_del_filter(_agg_vid)

            _n_akw3 = len(_agg_kw)
            _n_akw7 = len(df_auto_del_kw_keyword_)
            _dbg_auto_kw_ = {"n1":_n_akw1,"n2":_n_akw2,"n3":_n_akw3,"n4":_n_akw3,
                             "n5":_n_akw7,"n6":_n_akw7,"n7":_n_akw7}
        else:
            df_auto_del_kw_keyword_ = pd.DataFrame()
            df_auto_del_kw_product_ = pd.DataFrame()
            df_auto_del_kw_video_   = pd.DataFrame()
            _dbg_auto_kw_ = {"n1":0,"n2":0,"n3":0,"n4":0,"n5":0,"n6":0,"n7":0}

        # 商品/動画: オートASIN中、マニュアルASINと重複しない出血ASIN
        def _build_auto_asin_del(camp_mask, manual_mask):
            _zero = {"n1":0,"n2":0,"n3":0,"n4":0,"n5":0,"n6":0,"n7":0}
            _d = _mpt_base[camp_mask].copy()
            if _d.empty or not tkc: return pd.DataFrame(), _zero
            _d["_asin_clean"] = _d[tkc].apply(_extract_asin)
            _d = _d[_d["_asin_clean"] != ""].copy()
            _c1 = len(_d)                                                          # ① オート抽出（ASIN有効行数）
            if _d.empty: return pd.DataFrame(), {**_zero, "n1":_c1}
            _manual_asins = set(_mpt_base[manual_mask][tkc].apply(_extract_asin)); _manual_asins.discard("")
            _d = _d[~_d["_asin_clean"].isin(_manual_asins)].copy()
            _c2 = len(_d)                                                          # ② マニュアル重複除外後（行数）
            if _d.empty: return pd.DataFrame(), {**_zero, "n1":_c1, "n2":_c2}
            _d["_asin_key"] = _d["_asin_clean"]
            _agg_d3 = {
                "asin":           ("_asin_clean", "first"),
                "campaign_name":  (cc,            "first"),
                "campaign_theme": ("ct",           lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else "未分類"),
                "sales":          (sc,             "sum"),
                "cost":           (oc_,            "sum"),
            }
            if od:  _agg_d3["orders"]   = (od,  "sum")
            if agn: _agg_d3["ad_group"] = (agn, "first")
            _agg3 = _d.groupby("_asin_key").agg(**_agg_d3).reset_index(drop=True)
            _c3 = len(_agg3)                                                       # ③ groupby後ASIN数
            _agg3["ROAS"]  = _agg3.apply(
                lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
            _agg3["price"] = _agg3["campaign_theme"].map(PRICES)
            _agg3 = _agg3[_agg3["price"].notna()].copy()
            _c4 = len(_agg3)                                                       # ④ price取得成功数
            _c5 = int((_agg3["cost"] >= _agg3["price"] * 2).sum())                # ⑤ 広告費条件通過
            _c6 = int((_agg3["ROAS"] <= 0.8).sum())                               # ⑥ ROAS条件通過
            _result = _agg3[
                (_agg3["cost"] >= _agg3["price"] * 2) & (_agg3["ROAS"] <= 0.8)
            ].copy()
            _result.drop(columns=["price"], errors="ignore", inplace=True)
            _c7 = len(_result)                                                     # ⑦ 最終表示件数
            return _result, {"n1":_c1,"n2":_c2,"n3":_c3,"n4":_c4,"n5":_c5,"n6":_c6,"n7":_c7}

        _mask_auto_pt_del = (
            _mpt_base[cc].str.contains("商品ターゲ", na=False) &
            _mpt_base[cc].str.contains("オート|auto", case=False, na=False) &
            ~_mpt_base[cc].str.contains("動画", na=False)
        )
        _mask_auto_vid_del = (
            _mpt_base[cc].str.contains("動画", na=False) &
            _mpt_base[cc].str.contains("オート|auto", case=False, na=False)
        )
        df_auto_del_product_, _dbg_auto_pt_  = _build_auto_asin_del(_mask_auto_pt_del, _mask_m)
        df_auto_del_video_,   _dbg_auto_vid_ = _build_auto_asin_del(_mask_auto_vid_del, _mask_v)

        # ── オートKW集計でASIN/category判定された行を、商品/動画ページ側へ合流 ──
        # 「📄 オートKW削除」はキーワードのみを表示するため、ここで分離する。
        if not df_auto_del_kw_product_.empty:
            _kw_as_pt = df_auto_del_kw_product_.rename(columns={"keyword": "asin"}).copy()
            df_auto_del_product_ = pd.concat(
                [df_auto_del_product_, _kw_as_pt], ignore_index=True
            )

        if not df_auto_del_kw_video_.empty:
            _kw_as_vid = df_auto_del_kw_video_.rename(columns={"keyword": "asin"}).copy()
            df_auto_del_video_ = pd.concat(
                [df_auto_del_video_, _kw_as_vid], ignore_index=True
            )
        # ────────────────────────────────────────────────────────────

        st.session_state.update({
            "has_results": True, "df_win": dw,
            "df_del": df_del_, "df_cpc": df_cpc_,
            "df_pt_add_m": df_pt_add_m_, "df_pt_del_m": df_pt_del_m_,
            "df_pt_add_v": df_pt_add_v_, "df_pt_del_v": df_pt_del_v_,
            "df_cpc_product": df_cpc_product_, "df_cpc_video": df_cpc_video_,
            "df_auto_del_kw_keyword": df_auto_del_kw_keyword_,
            "df_auto_del_product": df_auto_del_product_,
            "df_auto_del_video":   df_auto_del_video_,
            "dbg_auto_kw": _dbg_auto_kw_,
            "dbg_auto_pt": _dbg_auto_pt_,
            "dbg_auto_vid": _dbg_auto_vid_,
            "stats": {
                "n_auto":n_auto,"n_ex":n_ex,"n_pt":n_pt,"n_ar":n_ar,
                "n_br":n_br,"n_cd":n_cd,"n_tl":n_tl,"n_ae":n_ae,
                "n_sl":n_sl,"n_ro":n_ro,"n_of":n_of,
                "n_clk_f":n_clk_f,"n_cost_f":n_cost_f,
                "n_pre":n_pre,"n_af":n_af,"nf":nf,
                "mo":int(min_ord),"mc":int(min_clk),"mco":int(min_cost),
                "n_cpc_auto":n_cpc_auto,"n_cpc_pt":n_cpc_pt,
                "n_cpc_empty":n_cpc_empty,"n_cpc_manual":n_cpc_manual,
                "n_mpt_auto_ex":n_mpt_auto_ex,"n_mpt_kw_ex":n_mpt_kw_ex,
                "n_mpt_dup_ex":n_mpt_dup_ex,"n_mpt_add":n_mpt_add,"n_mpt_del":n_mpt_del,
            },
            "dbg":{"kc":kc,"sc":sc,"oc_":oc_,"od":od,
                   "clk":clk,"imp":imp,"rn":len(reg),"br":brands},
        })
        st.write("keyword rows", len(df_auto_del_kw_keyword_))
        st.write("product rows", len(df_auto_del_kw_product_))
        st.write("video rows", len(df_auto_del_kw_video_))

# ─── No results: placeholder ─────────────────────────
if not st.session_state.get("has_results"):
    st.markdown("""<div style="text-align:center;padding:80px 20px;">
        <div style="font-size:3.5rem;">📂</div>
        <p style="font-size:1.1rem;font-weight:600;color:#2D3748;margin-top:16px;">
            CSVをアップロードして「分析開始」を押してください</p>
        <p style="color:#718096;font-size:.875rem;">「検索用語」と「ターゲティング」列を含めたレポートが必要です</p>
    </div>""", unsafe_allow_html=True)
    st.stop()

# ─── Retrieve session data ───────────────────────────
dw:  pd.DataFrame = st.session_state["df_win"]
dd:  pd.DataFrame = st.session_state.get("df_del", pd.DataFrame())
dc_cpc:         pd.DataFrame = st.session_state.get("df_cpc",         pd.DataFrame())
dc_cpc_product: pd.DataFrame = st.session_state.get("df_cpc_product", pd.DataFrame())
dc_cpc_video:   pd.DataFrame = st.session_state.get("df_cpc_video",   pd.DataFrame())
sv = st.session_state["stats"]
df_auto_del_product: pd.DataFrame = st.session_state.get("df_auto_del_product", pd.DataFrame())
df_auto_del_video:   pd.DataFrame = st.session_state.get("df_auto_del_video",   pd.DataFrame())
nw = len(dw)

# ─── KPIカード ヘルパー ──────────────────────────────
def kpi(col, icon: str, label: str, value: str, sub: str = "",
        bg: str = "#F4F6F8", color: str = "#718096"):
    col.markdown(f'''<div class="kpi-card" style="background:{bg};">
        <div class="kpi-icon">{icon}</div>
        <div class="kpi-label">{label}</div>
        <div class="kpi-value" style="color:{color};">{value}</div>
        <div class="kpi-sub">{sub}</div>
    </div>''', unsafe_allow_html=True)


# ─── 判定ロジック表示ヘルパー ────────────────────────────
_LOGIC_BOX_STYLE = (
    "background:#F8FBFF;border:1px solid #D9E8FF;"
    "border-radius:8px;padding:16px 20px;line-height:1.7;"
)
def render_logic_section(title: str, content_html: str):
    """📖 判定ロジックを見る — 各ページ共通の折りたたみ式ロジック表示エリア。
    title        : 表示タイトル（例: "📋 キーワード追加 判定ロジック"）
    content_html : ロジック本文（HTML文字列）
    """
    with st.expander("📖 判定ロジックを見る", expanded=False):
        st.markdown(
            f'''<div style="{_LOGIC_BOX_STYLE}">
<div style="font-weight:700;font-size:.92rem;color:#1A365D;margin-bottom:12px;">{title}</div>
{content_html}
</div>''',
            unsafe_allow_html=True,
        )

# ===================================================
# ページ関数
# ===================================================

def page_add_kw():
    _t1, _t2 = st.tabs(["追加候補", "分析"])
    with _t1:
        _cond_bar([
            ("最小注文数",  f'{sv["mo"]}件'),
            ("最小クリック数", f'{sv["mc"]}回'),
            ("最小広告費",  f'¥{sv["mco"]:,}'),
        ])

        render_logic_section(
            "📋 キーワード追加 判定ロジック",
            '''
    <table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">
    <thead>
      <tr style="background:#DBEAFE;">
        <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:30%;">項目</th>
        <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:70%;">内容</th>
      </tr>
    </thead>
    <tbody>
      <tr style="background:#F1F5F9;">
        <td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【目的】</td>
      </tr>
      <tr>
        <td style="padding:6px 10px;border:1px solid #BFDBFE;" colspan="2">
          オート広告で成果が出た検索語句を、手動広告（部分一致）のマニュアルキーワードへ追加する候補を抽出します。
        </td>
      </tr>
      <tr style="background:#F1F5F9;">
        <td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【抽出条件】</td>
      </tr>
      <tr>
        <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">信頼度フィルター</td>
        <td style="padding:6px 10px;border:1px solid #BFDBFE;">
          注文数 ≥ 3件 <b>かつ</b> クリック数 ≥ 5 <b>かつ</b> 広告費 ≥ ¥300<br>
          <span style="font-size:.8rem;color:#718096;">サイドバーで変更可能</span>
        </td>
      </tr>
      <tr style="background:#F0FFF4;">
        <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">採用条件</td>
        <td style="padding:6px 10px;border:1px solid #BFDBFE;">売上 ≥ 売価 × 2 <b>かつ</b> ROAS ≥ 2.0</td>
      </tr>
    </tbody>
    </table>
    <p style="font-size:.78rem;color:#718096;margin-top:10px;">
      ▶ 同一意図KW統合: 語順・表記ゆれが同じKWは代表1件に集約<br>
      ▶ ブランドワード・商品コード・タイトル文字列は自動除外
    </p>''',
        )
        st.markdown("")
        # ② キャンペーン選択
        _c1, _c3 = st.columns([3, 2])
        with _c1:
            kw_camp = st.selectbox(
                "キャンペーン",
                ["全キャンペーン"] + CAMPAIGNS,
                label_visibility="visible",
                key="add_camp_sel",
            )
        sel_df = dw.copy()

        if kw_camp != "全キャンペーン":
            sel_df = sel_df[sel_df["campaign_theme"] == kw_camp].copy()

        n_sel = len(sel_df)

        # 件数表示
        st.markdown(
            f'<div class="count-badge">該当件数: <b style="font-size:1.1rem;">{n_sel}件</b>'
            f'　<span style="color:#718096;font-size:.8rem;">キャンペーン: {kw_camp}</span></div>',
            unsafe_allow_html=True,
        )

        if sel_df.empty:
            st.info("条件に合うキーワードはありません。")
            return

        # ④ コピー用KW一覧
        kw_list = "\n".join(sel_df.sort_values("ROAS", ascending=False)["keyword"].tolist())
        st.markdown("**📋 Amazon広告登録用KW一覧**（右上のコピーボタンでコピー）")
        st.code(kw_list, language=None)

        # ④-2 CSVダウンロード（History保存トリガー）
        _kw_add_hist_cols = [c for c in ["campaign_name", "ad_group", "keyword",
                                          "orders", "clicks", "cost", "sales", "ROAS"]
                              if c in sel_df.columns]
        _kw_add_hist_df = sel_df.sort_values("ROAS", ascending=False)[_kw_add_hist_cols].copy()
        _kw_add_csv = _kw_add_hist_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        _anls_save_kw_add_history(_kw_add_hist_df)
        st.download_button(
            f"📥 {kw_camp}_キーワード追加候補.csv", data=_kw_add_csv,
            file_name=f"{kw_camp}_キーワード追加候補.csv", mime="text/csv",
        )

        # ⑤ 詳細テーブル
        st.markdown("##### KW詳細テーブル")
        _dd = sel_df[bcols(sel_df)].copy().sort_values("ROAS", ascending=False).reset_index(drop=True)
        _dd.index = _dd.index + 1
        _dd = _dd.rename(columns=RENAME)
        _dd["売上"]  = _dd["売上"].apply(lambda x: f"¥{x:,.0f}")
        _dd["広告費"] = _dd["広告費"].apply(lambda x: f"¥{x:,.0f}")
        _dd["ROAS"]  = _dd["ROAS"].round(2)
        if "CVR" in _dd.columns:
            _dd["CVR"] = _dd["CVR"].apply(lambda x: f"{x:.1f}%")
        st.dataframe(_dd, use_container_width=True)
    with _t2:
        _anls_render_tab(
            st.session_state.get("df_win", pd.DataFrame()),
            7, "anls_kw_add.json", "anls_kw_add",
            "KW追加分析", "kw_add", "keyword", "kw_add_history.json")

def page_del_kw():
    _cond_bar([("広告費", "≥ 商品売価×2"), ("ROAS", "< 0.8"), ("勝ちKW", "除外")])
    render_logic_section(
        "🚫 キーワード削除 判定ロジック",
        '''
<table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">
<thead>
  <tr style="background:#DBEAFE;">
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:30%;">項目</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:70%;">内容</th>
  </tr>
</thead>
<tbody>
  <tr style="background:#F1F5F9;">
    <td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【対象】</td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">分析対象</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">オート広告の検索語句のみ</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【削除条件】</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C53030;">🚫 削除対象</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">
      広告費 ≥ 売価 × 2 <b>かつ</b> ROAS &lt; 0.8<br>
      <span style="font-size:.8rem;color:#718096;">→ 完全一致で除外登録することを推奨</span>
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#718096;">⚪ データ不足</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">
      上記条件を満たさない場合<br>
      <span style="font-size:.8rem;color:#718096;">→ 変更なし（経過観察）</span>
    </td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【除外ルール】</td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">勝ちKW除外</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">
      追加用KW（勝ちKW）と重複するものは削除対象から除外<br>
      <span style="font-size:.8rem;color:#718096;">→ 勝ちKWを誤って削除しないための保護処理</span>
    </td>
  </tr>
</tbody>
</table>
<p style="font-size:.78rem;color:#718096;margin-top:10px;">
  ▶ 基本思想: 売価の2倍以上広告費を使っても売上が立たない検索語句を除外する<br>
  ▶ ダウンロードページからキャンペーン別ZIPで出力可能
</p>''',
    )
    st.markdown("")
    _del_camps = ["全キャンペーン"] + CAMPAIGNS
    _sc4, _ = st.columns([3, 2])
    with _sc4:
        del_camp = st.selectbox("キャンペーン（削除用KW）",
            _del_camps, label_visibility="visible", key="del_camp_sel")
    sel_dd = dd.copy()
    if del_camp != "全キャンペーン" and "campaign_theme" in sel_dd.columns:
        sel_dd = sel_dd[sel_dd["campaign_theme"] == del_camp].copy()
    n_del = len(sel_dd)
    st.markdown(
        f'<div class="count-badge" style="border-left-color:#E53E3E;">削除対象件数: '
        f'<b style="font-size:1.1rem;color:#C53030;">{n_del}件</b></div>',
        unsafe_allow_html=True,
    )
    if not sel_dd.empty:
        kw_list_del = "\n".join(sel_dd["keyword"].tolist())
        st.markdown("**📋 削除対象KW一覧**（右上のコピーボタンでコピー）")
        st.code(kw_list_del, language=None)
        st.markdown("##### 削除KW詳細テーブル")
        _disp_cols = [c for c in ["keyword", "campaign_theme", "ROAS", "cost", "sales"] if c in sel_dd.columns]
        _dd2 = sel_dd[_disp_cols].copy().sort_values("ROAS", ascending=True).reset_index(drop=True)
        _dd2.index = _dd2.index + 1
        _rn2 = {"keyword": "KW", "campaign_theme": "キャンペーン", "cost": "広告費", "sales": "売上"}
        _dd2 = _dd2.rename(columns=_rn2)
        if "広告費" in _dd2.columns: _dd2["広告費"] = _dd2["広告費"].apply(lambda x: f"¥{x:,.0f}")
        if "売上"   in _dd2.columns: _dd2["売上"]   = _dd2["売上"].apply(lambda x: f"¥{x:,.0f}")
        if "ROAS"   in _dd2.columns: _dd2["ROAS"]   = _dd2["ROAS"].round(2)
        st.dataframe(_dd2, use_container_width=True)
    else:
        st.info("削除対象キーワードはありません。")




def _classify_auto_kw_type(kraw):
    """オートKW種別を判定する（is_asin_kn / is_category_kn と完全同一基準）。

    対応関係:
        is_asin_kn(kn)     → 戻り値 "商品"
        is_category_kn(kn) → 戻り値 "動画"
        else                → 戻り値 "キーワード"

    is_asin_kn() / is_category_kn(): モジュールレベル関数を共有
        （_base_kw / _base_pt / _base_vid と同一の判定基準）。
    norm()  : モジュールレベル関数を共有（_auto_kw_base["kn"]=norm(kc) と同一処理）。

    Parameters
    ----------
    kraw : str  keyword 列の値（元値）

    Returns
    -------
    str : "商品" | "動画" | "キーワード"
    """
    kn = norm(str(kraw))                          # _auto_kw_base["kn"] = apply(norm) と同一正規化
    if is_asin_kn(kn): return "商品"              # _base_kw / _base_pt と同一基準
    if is_category_kn(kn): return "動画"          # _base_kw / _base_vid と同一基準
    return "キーワード"                            # 上記いずれにも該当しない場合


def _render_del_kw_block(df, badge_label, list_label, table_label,
                         camp_label, camp_key,
                         empty_msg=None, csv_fname=None, dl_key=None):
    """page_del_kw() と完全に同一のUIコンポーネント・レイアウト・スタイルで1ブロックを描画する。

    page_del_kw() の描画コードを共通関数として抽出したもの。
    page_auto_del_kw() の各セクション（キーワード/商品/動画）がこの関数を呼び出す。
    この関数は渡された df を表示するだけで、分類は一切行わない。

    Parameters
    ----------
    df          : pd.DataFrame  表示対象 DataFrame（呼び出し側で分類済みのものを渡すこと）
    badge_label : str  カウントバッジのラベル
    list_label  : str  コードブロックのヘッダー
    table_label : str  詳細テーブルのヘッダー
    camp_label  : str  selectbox のラベル
    camp_key    : str  selectbox の key
    empty_msg   : str  空のときの st.info メッセージ
    csv_fname   : str  CSVファイル名（None なら DL ボタンなし）
    dl_key      : str  download_button の key
    """
    _rn = {"keyword": "KW", "campaign_theme": "キャンペーン",
           "cost": "広告費", "sales": "売上"}
    _del_camps = ["全キャンペーン"] + CAMPAIGNS
    _sc, _ = st.columns([3, 2])
    with _sc:
        _sel = st.selectbox(camp_label, _del_camps,
                            label_visibility="visible", key=camp_key)
    _sec = df.copy()
    if _sel != "全キャンペーン" and "campaign_theme" in _sec.columns:
        _sec = _sec[_sec["campaign_theme"] == _sel].copy()
    n = len(_sec)
    st.markdown(
        f'<div class="count-badge" style="border-left-color:#E53E3E;">{badge_label}: '
        f'<b style="font-size:1.1rem;color:#C53030;">{n}件</b></div>',
        unsafe_allow_html=True,
    )
    if not _sec.empty:
        kw_list = "\n".join(_sec["keyword"].tolist())
        st.markdown(f"**📋 {list_label}**（右上のコピーボタンでコピー）")
        st.code(kw_list, language=None)
        st.markdown(f"##### {table_label}")
        _disp = [c for c in ["keyword", "campaign_theme", "ROAS", "cost", "sales"]
                 if c in _sec.columns]
        _dd = _sec[_disp].copy().sort_values("ROAS", ascending=True).reset_index(drop=True)
        _dd.index = _dd.index + 1
        _dd = _dd.rename(columns=_rn)
        if "広告費" in _dd.columns: _dd["広告費"] = _dd["広告費"].apply(lambda x: f"¥{x:,.0f}")
        if "売上"   in _dd.columns: _dd["売上"]   = _dd["売上"].apply(lambda x: f"¥{x:,.0f}")
        if "ROAS"   in _dd.columns: _dd["ROAS"]   = _dd["ROAS"].round(2)
        st.dataframe(_dd, use_container_width=True)
        if csv_fname and dl_key:
            _rn_csv = {**_rn, "orders": "購入数", "ad_group": "広告グループ"}
            _all = [c for c in ["keyword", "campaign_theme", "cost", "ROAS",
                                 "sales", "orders", "ad_group"] if c in _sec.columns]
            _csv = _sec[_all].rename(columns=_rn_csv).to_csv(
                index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(f"📥 {csv_fname}", data=_csv,
                               file_name=csv_fname, mime="text/csv", key=dl_key)
    else:
        st.info(empty_msg or "削除対象キーワードはありません。")

def page_auto_del_kw():
    # ── session_stateからキーワードDataFrameを取得（商品/動画は別ページへ合流済み）──
    df_auto_del_kw_keyword = st.session_state.get("df_auto_del_kw_keyword", pd.DataFrame())

    if df_auto_del_kw_keyword.empty:
        st.info("除外候補のキーワードはありません。（オートKWで出血中かつマニュアル未登録のものなし）")
        return

    # ── 件数検証（必須）─────────────────────────────────────────────────
    n_kw = len(df_auto_del_kw_keyword)
    st.metric("📄 キーワード件数", f"{n_kw}件")

    # ── キーワードセクション ─────────────────────────────────────────────
    st.markdown("### 📄 キーワード")
    _render_del_kw_block(
        df_auto_del_kw_keyword,
        badge_label="キーワード除外候補",
        list_label="除外対象KW一覧",
        table_label="除外KW詳細テーブル",
        camp_label="キャンペーン（キーワード）",
        camp_key="auto_kw_camp_kw",
        empty_msg="除外候補のキーワードはありません。",
        csv_fname="auto_negative_keyword.csv",
        dl_key="dl_akw_kw",
    )

def page_auto_del_product():
    df = st.session_state.get("df_auto_del_product", pd.DataFrame())
    if df.empty:
        st.info("除外候補の商品ASINはありません。（オート商品広告で出血中かつマニュアル未登録のものなし）")
        return
    st.metric("🎯 商品件数", f"{len(df)}件")
    _del_camps = ["全キャンペーン"] + CAMPAIGNS
    _sc, _ = st.columns([3, 2])
    with _sc:
        _sel = st.selectbox("キャンペーン（商品）", _del_camps,
                            label_visibility="visible", key="auto_pt_camp_sel")
    if _sel != "全キャンペーン" and "campaign_theme" in df.columns:
        df = df[df["campaign_theme"] == _sel].copy()
    if df.empty:
        st.info("除外候補の商品ASINはありません。")
        return
    st.markdown(f"**除外候補: {len(df)}件** — 広告費 ≥ 売価×2 かつ ROAS ≤ 0.8 / マニュアル商品重複除外済み")
    _dcols = [c for c in ["asin","campaign_theme","cost","ROAS","sales","orders","campaign_name","ad_group"] if c in df.columns]
    _rn = {"asin":"ASIN","campaign_theme":"キャンペーン","cost":"広告費",
           "sales":"売上","orders":"購入数","campaign_name":"キャンペーン名","ad_group":"広告グループ"}
    _d = df[_dcols].rename(columns=_rn).copy()
    if "広告費" in _d.columns: _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
    if "売上"   in _d.columns: _d["売上"]   = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
    if "ROAS"   in _d.columns: _d["ROAS"]   = _d["ROAS"].round(2)
    st.dataframe(_d, use_container_width=True)
    _csv = df[_dcols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 除外商品ASIN候補.csv", data=_csv, file_name="除外商品ASIN候補.csv", mime="text/csv")

def page_auto_del_video():
    df = st.session_state.get("df_auto_del_video", pd.DataFrame())
    if df.empty:
        st.info("除外候補の動画ASINはありません。（オート動画広告で出血中かつマニュアル未登録のものなし）")
        return
    st.metric("🎬 動画件数", f"{len(df)}件")
    _del_camps = ["全キャンペーン"] + CAMPAIGNS
    _sc, _ = st.columns([3, 2])
    with _sc:
        _sel = st.selectbox("キャンペーン（動画）", _del_camps,
                            label_visibility="visible", key="auto_vid_camp_sel")
    if _sel != "全キャンペーン" and "campaign_theme" in df.columns:
        df = df[df["campaign_theme"] == _sel].copy()
    if df.empty:
        st.info("除外候補の動画ASINはありません。")
        return
    st.markdown(f"**除外候補: {len(df)}件** — 広告費 ≥ 売価×2 かつ ROAS ≤ 0.8 / マニュアル動画重複除外済み")
    _dcols = [c for c in ["asin","campaign_theme","cost","ROAS","sales","orders","campaign_name","ad_group"] if c in df.columns]
    _rn = {"asin":"ASIN","campaign_theme":"キャンペーン","cost":"広告費",
           "sales":"売上","orders":"購入数","campaign_name":"キャンペーン名","ad_group":"広告グループ"}
    _d = df[_dcols].rename(columns=_rn).copy()
    if "広告費" in _d.columns: _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
    if "売上"   in _d.columns: _d["売上"]   = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
    if "ROAS"   in _d.columns: _d["ROAS"]   = _d["ROAS"].round(2)
    st.dataframe(_d, use_container_width=True)
    _csv = df[_dcols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 除外動画ASIN候補.csv", data=_csv, file_name="除外動画ASIN候補.csv", mime="text/csv")


# ===================================================
# 分析ヘルパー関数
# ===================================================

def _get_analysis_dir() -> _anls_plib.Path:
    """History保存/読込で使用する唯一のディレクトリ解決関数。
    __file__基準の絶対Pathに統一し、実行時のCWDに依存しない。"""
    d = _anls_plib.Path(__file__).resolve().parent / "analysis_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _anls_load(fname: str) -> list:
    p = _get_analysis_dir() / fname
    if not p.exists():
        return []
    try:
        return _anls_json.loads(p.read_text(encoding="utf-8")).get("records", [])
    except Exception:
        return []


def _anls_save(fname: str, records: list) -> bool:
    p = _get_analysis_dir() / fname
    _tmp_p = p.with_name(p.name + ".tmp")
    _tmp_p.write_text(
        _anls_json.dumps({"records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    _tmp_p.replace(p)
    try:
        _readback = _anls_json.loads(p.read_text(encoding="utf-8")).get("records", [])
    except Exception:
        _readback = None
    if _readback is None or len(_readback) != len(records):
        st.error(f"⚠️ History保存の検証に失敗しました（{fname}）。保存内容を確認してください。")
        return False
    return True


def _anls_parse_csv(csv_file):
    df = rcsv(csv_file)
    kc  = fcol(df, ["検索用語", "Search Term", "Customer Search Term"])
    cc  = fcol(df, ["キャンペーン名", "Campaign Name", "campaign_name"])
    sc  = fcol(df, ["売上", "Sales", "売上金額", "7日間の合計売上高", "14日間の合計売上高", "合計売上高"])
    oc_ = fcol(df, ["広告費", "Cost", "Spend", "費用", "合計費用"])
    od  = fcol(df, ["注文数", "Orders", "購入数", "7日間の総注文数", "14日間の総注文数", "合計注文数"])
    clk = fcol(df, ["クリック数", "Clicks"])
    imp = fcol(df, ["インプレッション数", "Impressions", "表示回数"])
    tkc = fcol(df, ["ターゲティング", "Targeting"])
    kwt = fcol(df, ["キーワードテキスト", "Keyword Text", "キーワード"])
    agn = fcol(df, ["広告グループ名", "Ad Group Name", "広告グループ"])
    return df, kc, cc, sc, oc_, od, clk, imp, tkc, kwt, agn


def _anls_build_kw_after(df, kc, cc, sc, oc_, od, clk) -> pd.DataFrame:
    if not kc or not cc or not sc or not oc_:
        return pd.DataFrame()
    d = df.copy()
    d["_cost_n"] = tonum(d[oc_])
    d = d[d["_cost_n"] > 0].copy()
    d["_kn_key"] = d[kc].apply(norm)
    d["_sc_n"]  = tonum(d[sc])
    d["_oc_n"]  = tonum(d[oc_])
    agg_d = {"_sc_n": "sum", "_oc_n": "sum"}
    if od:  d["_od_n"]  = tonum(d[od]);  agg_d["_od_n"]  = "sum"
    if clk: d["_clk_n"] = tonum(d[clk]); agg_d["_clk_n"] = "sum"
    out = d.groupby("_kn_key", as_index=True).agg(agg_d).reset_index()
    out = out.rename(columns={"_sc_n": "sales", "_oc_n": "cost"})
    if "_od_n"  in out.columns: out = out.rename(columns={"_od_n": "orders"})
    if "_clk_n" in out.columns: out = out.rename(columns={"_clk_n": "clicks"})
    out["ROAS"] = out.apply(lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
    if "orders" in out.columns and "clicks" in out.columns:
        out["CVR"] = out.apply(lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0, axis=1)
    if "clicks" in out.columns:
        out["avg_cpc"] = out.apply(lambda r: round(r["cost"] / r["clicks"], 0) if r["clicks"] > 0 else 0, axis=1)
    return out


def _anls_build_cpc_after(df, cc, sc, oc_, od, clk, kwt_col) -> pd.DataFrame:
    if not cc or not sc or not oc_:
        return pd.DataFrame()
    d = df.copy()
    d["_cost_n"] = tonum(d[oc_])
    d = d[d["_cost_n"] > 0].copy()
    kc2 = kwt_col if kwt_col else fcol(d, ["ターゲティング", "Targeting", "キーワードテキスト", "Keyword Text"])
    if not kc2:
        return pd.DataFrame()
    _agn2 = fcol(d, ["広告グループ名", "Ad Group Name", "広告グループ"])
    if _agn2:
        d["_kn_key"] = d[cc].apply(norm) + "|" + d[_agn2].apply(norm) + "|" + d[kc2].apply(norm)
    else:
        d["_kn_key"] = d[cc].apply(norm) + "||" + d[kc2].apply(norm)
    d["_sc_n"]  = tonum(d[sc])
    d["_oc_n"]  = tonum(d[oc_])
    agg_d = {"_sc_n": "sum", "_oc_n": "sum"}
    if od:  d["_od_n"]  = tonum(d[od]);  agg_d["_od_n"]  = "sum"
    if clk: d["_clk_n"] = tonum(d[clk]); agg_d["_clk_n"] = "sum"
    out = d.groupby("_kn_key", as_index=True).agg(agg_d).reset_index()
    out = out.rename(columns={"_sc_n": "sales", "_oc_n": "cost"})
    if "_od_n"  in out.columns: out = out.rename(columns={"_od_n": "orders"})
    if "_clk_n" in out.columns: out = out.rename(columns={"_clk_n": "clicks"})
    out["ROAS"] = out.apply(lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
    if "orders" in out.columns and "clicks" in out.columns:
        out["CVR"] = out.apply(lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0, axis=1)
    if "clicks" in out.columns:
        out["avg_cpc"] = out.apply(lambda r: round(r["cost"] / r["clicks"], 0) if r["clicks"] > 0 else 0, axis=1)
    return out


def _anls_build_asin_after(df, cc, sc, oc_, od, clk, tkc, camp_pat) -> pd.DataFrame:
    if not cc or not sc or not oc_ or not tkc:
        return pd.DataFrame()
    d = df.copy()
    d["_cost_n"] = tonum(d[oc_])
    d = d[d["_cost_n"] > 0].copy()
    if camp_pat:
        camps = set(camp_pat.split("|"))
        d = d[d[cc].astype(str).isin(camps)].copy()
    def _ext_asin(v):
        m = re.search(r'B0[A-Z0-9]{8}', str(v), re.IGNORECASE)
        return m.group(0).upper() if m else ""
    d["_kn_key"] = d[tkc].apply(_ext_asin)
    d = d[d["_kn_key"] != ""].copy()
    if d.empty:
        return pd.DataFrame()
    d["_sc_n"]  = tonum(d[sc])
    d["_oc_n"]  = tonum(d[oc_])
    agg_d = {"_sc_n": "sum", "_oc_n": "sum"}
    if od:  d["_od_n"]  = tonum(d[od]);  agg_d["_od_n"]  = "sum"
    if clk: d["_clk_n"] = tonum(d[clk]); agg_d["_clk_n"] = "sum"
    out = d.groupby("_kn_key", as_index=True).agg(agg_d).reset_index()
    out = out.rename(columns={"_sc_n": "sales", "_oc_n": "cost"})
    if "_od_n"  in out.columns: out = out.rename(columns={"_od_n": "orders"})
    if "_clk_n" in out.columns: out = out.rename(columns={"_clk_n": "clicks"})
    out["ROAS"] = out.apply(lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
    if "orders" in out.columns and "clicks" in out.columns:
        out["CVR"] = out.apply(lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0, axis=1)
    if "clicks" in out.columns:
        out["avg_cpc"] = out.apply(lambda r: round(r["cost"] / r["clicks"], 0) if r["clicks"] > 0 else 0, axis=1)
    return out


def _anls_judge(b, a, higher_ok=True):
    try:
        b = float(b or 0); a = float(a or 0)
    except Exception:
        return "→ 変化なし"
    if b == 0 and a == 0:
        return "→ 変化なし"
    if b == 0:
        return ("↑ 改善 (+∞%)" if higher_ok else "↓ 悪化 (+∞%)")
    pct = (a - b) / abs(b) * 100
    if abs(pct) < 3.0:
        return "→ 変化なし"
    if pct > 0:
        return (f"↑ 改善 (+{pct:.1f}%)" if higher_ok else f"↓ 悪化 (+{pct:.1f}%)")
    else:
        return (f"↓ 悪化 ({pct:.1f}%)" if higher_ok else f"↑ 改善 ({pct:.1f}%)")


def _anls_pct_str(b, a):
    try:
        b = float(b or 0); a = float(a or 0)
        if pd.isna(b) or pd.isna(a):
            return "ー"
        if b == 0: return "ー"
        return f"{(a - b) / abs(b) * 100:+.0f}%"
    except Exception:
        return "ー"


def _anls_diff_str(b, a, unit=""):
    try:
        return f"{float(a or 0) - float(b or 0):+.0f}{unit}"
    except Exception:
        return "ー"


def _anls_row_judge(row):
    try:
        b = float(row.get("ROAS_b", 0) or 0)
        a = float(row.get("ROAS_a", 0) or 0)
        if b == 0 and a == 0: return "変化なし"
        if b == 0: return "改善"
        pct = (a - b) / abs(b) * 100
        if abs(pct) < 3.0: return "変化なし"
        return "改善" if pct > 0 else "悪化"
    except Exception:
        return "変化なし"


def _anls_generate_insight(row):
    """既存の_判定結果(_anls_row_judgeの出力)と、算出済みのBefore/After値のみから
    「理由」「広告運用で触るべき項目」の文字列リストを組み立てる。
    新しい集計・新しい判定基準・AI推論は一切行わない。単純な数値の大小比較のみ。
    使用する値: ROAS_b/a, orders_b/a, sales_b/a, cost_b/a, avg_cpc_b/a, _判定"""
    def _f(key):
        try:
            v = row.get(key)
            if v is None:
                return 0.0
            v = float(v)
            return v if v == v else 0.0  # NaN check
        except Exception:
            return 0.0

    roas_b, roas_a   = _f("ROAS_b"), _f("ROAS_a")
    orders_b, orders_a = _f("orders_b"), _f("orders_a")
    sales_b, sales_a = _f("sales_b"), _f("sales_a")
    cost_b, cost_a   = _f("cost_b"), _f("cost_a")
    cpc_b, cpc_a     = _f("avg_cpc_b"), _f("avg_cpc_a")

    j = row.get("_判定", "変化なし")

    reasons = []
    if roas_a > roas_b:
        reasons.append(f"・ROASが{roas_b:.2f}→{roas_a:.2f}へ上昇")
    elif roas_a < roas_b:
        reasons.append(f"・ROASが{roas_b:.2f}→{roas_a:.2f}へ低下")
    else:
        reasons.append(f"・ROASの変動は小さい（{roas_b:.2f}→{roas_a:.2f}）")

    cpc_maintained = (cpc_a == cpc_b)
    if cpc_maintained:
        reasons.append("・平均CPCは維持")
    elif cpc_a > cpc_b:
        reasons.append(f"・平均CPCが¥{cpc_b:.0f}→¥{cpc_a:.0f}へ上昇")
    else:
        reasons.append(f"・平均CPCが¥{cpc_b:.0f}→¥{cpc_a:.0f}へ低下")

    if orders_a > orders_b:
        reasons.append(f"・注文数が{orders_b:.0f}→{orders_a:.0f}件へ増加")
    elif orders_a < orders_b:
        reasons.append(f"・注文数が{orders_b:.0f}→{orders_a:.0f}件へ減少")

    if cost_a > cost_b and sales_a <= sales_b:
        reasons.append("・広告費が増加したが売上は伸びていない")

    actions = []
    if j == "改善":
        if cpc_maintained:
            actions.append("・CPCを維持")
        if orders_a > orders_b:
            actions.append("・予算増額を検討")
        if not actions:
            actions.append("・現状維持")
    elif j == "悪化":
        actions.append("・CPCを下げる")
        if cost_a > cost_b and orders_a <= orders_b:
            actions.append("・検索語句レポート確認")
            actions.append("・不要ターゲット停止候補")
    else:
        actions.append("・現状維持")
        actions.append("・1週間様子を見る")

    return reasons, actions


def _anls_summary_html(n_total, n_kaizen, n_akka, n_henko, rate):
    def _card(bg, brd, lc, vc, label, val):
        return (f'<div style="background:{bg};border:1px solid {brd};border-radius:10px;'
                f'padding:12px 20px;text-align:center;min-width:90px;">'
                f'<div style="font-size:.7rem;color:{lc};font-weight:700;letter-spacing:.05em;">{label}</div>'
                f'<div style="font-size:1.55rem;font-weight:800;color:{vc};margin-top:2px;">{val}</div>'
                f'</div>')
    cards = (
        _card("#EBF8FF", "#90CDF4", "#2C5282", "#2B6CB0", "分析対象",  f"{n_total}件") +
        _card("#F0FFF4", "#9AE6B4", "#276749", "#276749", "🟢 改善",   f"{n_kaizen}件") +
        _card("#FFF5F5", "#FEB2B2", "#C53030", "#C53030", "🔴 悪化",   f"{n_akka}件") +
        _card("#FFFFF0", "#F6E05E", "#744210", "#744210", "🟡 変化なし", f"{n_henko}件") +
        _card("#FAF5FF", "#D6BCFA", "#553C9A", "#553C9A", "改善率",    f"{rate:.0f}%")
    )
    return f'<div style="display:flex;gap:10px;margin:14px 0;flex-wrap:wrap;">{cards}</div>'


def _anls_camp_table_html(merged):
    rows_html = ""
    grp_col = "campaign_theme" if "campaign_theme" in merged.columns else None
    if grp_col is None:
        return ""
    for ct, grp in merged.groupby(grp_col):
        n  = len(grp)
        nk = int((grp["_判定"] == "改善").sum())
        na = int((grp["_判定"] == "悪化").sum())
        nv = int((grp["_判定"] == "変化なし").sum())
        rate = f"{nk / n * 100:.0f}%" if n > 0 else "ー"
        def _avg_pct(col_b, col_a, _grp=grp):
            if col_b not in _grp.columns or col_a not in _grp.columns: return "ー"
            return _anls_pct_str(_grp[col_b].mean(), _grp[col_a].mean())
        roas_chg = _avg_pct("ROAS_b", "ROAS_a")
        cvr_chg = "ー"
        if "CVR_b" in grp.columns and "CVR_a" in grp.columns:
            _cvr_diff = grp['CVR_a'].mean() - grp['CVR_b'].mean()
            if pd.notna(_cvr_diff):
                cvr_chg = f"{_cvr_diff:+.1f}pt"
        if "cost_b" in grp.columns and "clicks_b" in grp.columns and grp["clicks_b"].sum() > 0:
            cpc_b = grp["cost_b"].sum() / grp["clicks_b"].sum()
            cpc_a = grp["cost_a"].sum() / grp["clicks_a"].sum() if "clicks_a" in grp.columns and grp["clicks_a"].sum() > 0 else 0
            cpc_chg = f"{cpc_a - cpc_b:+.0f}円"
        else:
            cpc_chg = "ー"
        rows_html += (
            f'<tr><td style="padding:7px 10px;border:1px solid #E2E8F0;font-weight:600;">{ct}</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;color:#276749;text-align:center;">🟢 {nk}件</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;color:#C53030;text-align:center;">🔴 {na}件</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;color:#744210;text-align:center;">🟡 {nv}件</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;text-align:center;font-weight:700;">{rate}</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;text-align:center;">{roas_chg}</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;text-align:center;">{cvr_chg}</td>'
            f'<td style="padding:7px 10px;border:1px solid #E2E8F0;text-align:center;">{cpc_chg}</td>'
            f'</tr>'
        )
    hd = "".join(
        f'<th style="padding:7px 10px;border:1px solid #E2E8F0;background:#EBF4FF;text-align:center;font-size:.8rem;">{c}</th>'
        for c in ["キャンペーン", "改善", "悪化", "変化なし", "改善率", "ROAS変化", "CVR変化", "CPC変化"])
    return (f'<table style="width:100%;border-collapse:collapse;font-size:.82rem;">'
            f'<thead><tr>{hd}</tr></thead><tbody>{rows_html}</tbody></table>')


def _anls_detail_html(row, id_col):
    metrics = [
        ("売上",    "sales_b",  "sales_a",  "¥{:,.0f}", True),
        ("広告費",  "cost_b",   "cost_a",   "¥{:,.0f}", False),
        ("ROAS",    "ROAS_b",   "ROAS_a",   "{:.2f}",   True),
        ("CVR",     "CVR_b",    "CVR_a",    "{:.1f}%",  True),
        ("注文数",  "orders_b", "orders_a", "{:.0f}件", True),
        ("クリック","clicks_b", "clicks_a", "{:.0f}",   True),
    ]
    cpc_b = (float(row.get("cost_b", 0) or 0) / float(row.get("clicks_b", 1) or 1)
             if float(row.get("clicks_b", 0) or 0) > 0 else None)
    cpc_a = (float(row.get("cost_a", 0) or 0) / float(row.get("clicks_a", 1) or 1)
             if float(row.get("clicks_a", 0) or 0) > 0 else None)
    def _fmt(val, fmt):
        try: return fmt.format(float(val))
        except Exception: return "ー"
    def _jclr(b, a, higher_ok=True):
        j = _anls_judge(float(b or 0), float(a or 0), higher_ok)
        return ("#276749" if "改善" in j else "#C53030" if "悪化" in j else "#718096"), j
    rows_h = ""
    for lbl, cb, ca, fmt, hok in metrics:
        bv = row.get(cb); av = row.get(ca)
        if bv is None and av is None: continue
        clr, jt = _jclr(bv or 0, av or 0, hok)
        rows_h += (
            f'<tr><td style="padding:6px 10px;border:1px solid #E2E8F0;font-weight:600;">{lbl}</td>'
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">{_fmt(bv, fmt)}</td>'
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">{_fmt(av, fmt)}</td>'
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:center;color:{clr};font-weight:700;">{jt}</td>'
            f'</tr>')
    if cpc_b is not None or cpc_a is not None:
        bvs = f"¥{cpc_b:.0f}" if cpc_b else "ー"
        avs = f"¥{cpc_a:.0f}" if cpc_a else "ー"
        clr, jt = _jclr(cpc_b or 0, cpc_a or 0, False)
        rows_h += (
            f'<tr><td style="padding:6px 10px;border:1px solid #E2E8F0;font-weight:600;">CPC</td>'
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">{bvs}</td>'
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">{avs}</td>'
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:center;color:{clr};font-weight:700;">{jt}</td>'
            f'</tr>')
    hd = "".join(f'<th style="padding:7px 10px;border:1px solid #E2E8F0;background:#EBF4FF;text-align:center;">{c}</th>'
                 for c in ["指標", "Before", "After", "判定"])
    return (f'<table style="width:100%;border-collapse:collapse;font-size:.82rem;margin-top:6px;">'
            f'<thead><tr>{hd}</tr></thead><tbody>{rows_h}</tbody></table>')


def _anls_normalize_entries(entries: list) -> list:
    """NaN/None/pd.NAを""に正規化したコピーを返す。
    比較用（既存History読込値の正規化）と、保存前のentries本体の正規化（非標準JSON出力防止）の両方で使用する。"""
    normalized = []
    for row in entries:
        new_row = {}
        for k, v in row.items():
            try:
                is_na = pd.isna(v)
            except (TypeError, ValueError):
                is_na = False
            new_row[k] = "" if is_na is True else v
        normalized.append(new_row)
    return normalized


def _anls_save_cpc_change_history(df_disp):
    save_cols = [c for c in ["campaign_name", "ad_group", "keyword",
                              "avg_cpc", "rec_cpc", "cpc_delta",
                              "cpc_rank", "cpc_action",
                              "sales", "cost", "ROAS", "orders"] if c in df_disp.columns]
    record = {
        "exported_at": _anls_dt.datetime.now().isoformat(),
        "entries": _anls_normalize_entries(df_disp[save_cols].to_dict(orient="records")),
    }
    existing = _anls_load("cpc_change_history.json")
    if existing and _anls_normalize_entries(existing[-1].get("entries") or []) == _anls_normalize_entries(record.get("entries") or []):
        return
    existing.append(record)
    _anls_save("cpc_change_history.json", existing)



def _anls_save_cpc_asin_history(df_disp, fname: str):
    save_cols = [c for c in ["campaign_name", "ad_group", "asin",
                              "avg_cpc", "rec_cpc", "cpc_delta",
                              "cpc_rank", "cpc_action",
                              "sales", "cost", "ROAS", "orders"] if c in df_disp.columns]
    record = {
        "exported_at": _anls_dt.datetime.now().isoformat(),
        "entries": _anls_normalize_entries(df_disp[save_cols].to_dict(orient="records")),
    }
    existing = _anls_load(fname)
    if existing and _anls_normalize_entries(existing[-1].get("entries") or []) == _anls_normalize_entries(record.get("entries") or []):
        return
    existing.append(record)
    _anls_save(fname, existing)


def _anls_save_kw_add_history(df_disp):
    save_cols = [c for c in ["campaign_name", "ad_group", "keyword",
                              "orders", "clicks", "cost", "sales", "ROAS"] if c in df_disp.columns]
    record = {
        "exported_at": _anls_dt.datetime.now().isoformat(),
        "entries": _anls_normalize_entries(df_disp[save_cols].to_dict(orient="records")),
    }
    existing = _anls_load("kw_add_history.json")
    if existing and _anls_normalize_entries(existing[-1].get("entries") or []) == _anls_normalize_entries(record.get("entries") or []):
        return
    existing.append(record)
    _anls_save("kw_add_history.json", existing)


def _anls_save_asin_add_history(df_disp, fname: str):
    save_cols = [c for c in ["campaign_name", "ad_group", "asin",
                              "orders", "clicks", "cost", "sales", "ROAS"] if c in df_disp.columns]
    record = {
        "exported_at": _anls_dt.datetime.now().isoformat(),
        "entries": _anls_normalize_entries(df_disp[save_cols].to_dict(orient="records")),
    }
    existing = _anls_load(fname)
    if existing and _anls_normalize_entries(existing[-1].get("entries") or []) == _anls_normalize_entries(record.get("entries") or []):
        return
    existing.append(record)
    _anls_save(fname, existing)


def _anls_render_list(merged, id_col):
    _ICON = {"改善": "🟢", "悪化": "🔴", "変化なし": "🟡"}
    _CLR  = {"改善": "#276749", "悪化": "#C53030", "変化なし": "#744210"}
    for _, row in merged.iterrows():
        j    = row.get("_判定", "変化なし")
        icon = _ICON.get(j, "🟡")
        clr  = _CLR.get(j, "#718096")
        kw   = str(row.get(id_col, ""))
        kw_disp = kw[:40] + ("…" if len(kw) > 40 else "")
        st.markdown("---")
        st.markdown(f"**{icon} {kw_disp}**")
        st.markdown(f'　<span style="color:{clr};font-weight:700;">{j}</span>', unsafe_allow_html=True)
        with st.expander("▶ 詳細", expanded=False):
            st.markdown(_anls_detail_html(row, id_col), unsafe_allow_html=True)
            _insight_reasons, _insight_actions = _anls_generate_insight(row)
            _reason_hdr = f"{j}理由" if j in ("改善", "悪化") else "理由"
            st.markdown(f"**{_reason_hdr}**\n" + "\n".join(_insight_reasons))
            st.markdown("**■広告運用で触るべき項目**\n" + "\n".join(_insight_actions))


def _anls_aggregate_before_after(merged: pd.DataFrame) -> dict:
    """「💾 分析結果を保存」時に、Before/After・理由・アクション表示に必要な
    最小限の集計値だけを計算する新規追加関数（読み取り専用・merged自体は変更しない）。
    ここで計算した値は、既存の _anls_generate_insight にそのまま渡せる形
    （ROAS_b/a, orders_b/a, sales_b/a, cost_b/a, avg_cpc_b/a）にすることで、
    理由・アクションの生成ロジック自体は既存関数を一切変更せず再利用する。

    agg_clicks_b/a, agg_rows は現時点のどの既存ロジック・表示からも参照されない
    「将来の分析レポート拡張のためだけ」の保存専用フィールド。
    """
    def _sum(col):
        return float(merged[col].sum()) if col in merged.columns else 0.0

    sales_b, sales_a   = _sum("sales_b"), _sum("sales_a")
    cost_b,  cost_a    = _sum("cost_b"),  _sum("cost_a")
    orders_b, orders_a = _sum("orders_b"), _sum("orders_a")
    clicks_b, clicks_a = _sum("clicks_b"), _sum("clicks_a")

    roas_b = round(sales_b / cost_b, 2) if cost_b > 0 else 0.0
    roas_a = round(sales_a / cost_a, 2) if cost_a > 0 else 0.0
    cpc_b  = round(cost_b / clicks_b, 0) if clicks_b > 0 else 0.0
    cpc_a  = round(cost_a / clicks_a, 0) if clicks_a > 0 else 0.0

    return {
        "agg_sales_b": sales_b, "agg_sales_a": sales_a,
        "agg_cost_b": cost_b, "agg_cost_a": cost_a,
        "agg_orders_b": orders_b, "agg_orders_a": orders_a,
        "agg_roas_b": roas_b, "agg_roas_a": roas_a,
        "agg_avg_cpc_b": cpc_b, "agg_avg_cpc_a": cpc_a,
        "agg_clicks_b": clicks_b, "agg_clicks_a": clicks_a,
        "agg_rows": int(len(merged)),
    }


def _anls_build_detail(merged: pd.DataFrame, id_col: str, mode: str = "") -> list:
    """保存用に、キャンペーン別・キーワード（ASIN）別のBefore/After・AI考察を
    保存後も再現できる最小限の形（campaign, keyword, before, after, judgement,
    reasons, actions のみ）で保存する新規追加関数（読み取り専用・mergedは変更しない）。

    判定（judgement）は既存の _判定（_anls_row_judge の出力）をそのまま転記し、
    理由・アクション（reasons/actions）は既存関数 _anls_generate_insight を
    そのまま呼び出すだけで、新しい判定ロジック・新しいAIロジックは一切追加しない。

    avg_cpc（before/after）は、CPC調整分析（mode="cpc_kw"/"cpc_asin"）の場合のみ
    追加する。kw_add/asin_add（30日キーワード追加機能側）は呼び出し時にmodeを
    渡さない限り従来のキー構成のまま（avg_cpcキー自体を持たない）で、JSON構造は
    一切変更されない。
    """
    _detail = []
    for _, row in merged.iterrows():
        _reasons, _actions = _anls_generate_insight(row)
        _before = {
            "sales": row.get("sales_b"), "cost": row.get("cost_b"),
            "ROAS": row.get("ROAS_b"), "CVR": row.get("CVR_b"),
            "orders": row.get("orders_b"), "clicks": row.get("clicks_b"),
        }
        _after = {
            "sales": row.get("sales_a"), "cost": row.get("cost_a"),
            "ROAS": row.get("ROAS_a"), "CVR": row.get("CVR_a"),
            "orders": row.get("orders_a"), "clicks": row.get("clicks_a"),
        }
        _CPC_MODES = {"cpc_kw", "cpc_asin"}
        _is_cpc = mode in _CPC_MODES
        if _is_cpc:
            _before["avg_cpc"] = row.get("avg_cpc_b")
            _after["avg_cpc"] = row.get("avg_cpc_a")
        _detail.append({
            "campaign": row.get("campaign_theme", ""),
            "keyword": row.get(id_col, ""),
            "before": _before,
            "after": _after,
            "judgement": row.get("_判定", "変化なし"),
            "reasons": _reasons,
            "actions": _actions,
        })
    return _detail


def _anls_save_memo(anls_hist_fname: str, rec_id: str, row_idx: int, memo_key: str):
    """保存済みレポートの「自分メモ」欄が編集されたときに、既存のJSON読み書き
    （_anls_load/_anls_save）だけを使って該当レコードのdetail[row_idx]へ
    memoを書き戻す新規追加関数。新しい保存ボタン・新しいJSONファイルは作らず、
    既存の保存処理（_anls_save）をそのまま流用する。他のキー・他のレコードは
    一切変更しない。"""
    _recs2 = _anls_load(anls_hist_fname)
    for _r in _recs2:
        if _r.get("id") == rec_id and isinstance(_r.get("detail"), list) and 0 <= row_idx < len(_r["detail"]):
            _r["detail"][row_idx]["memo"] = st.session_state.get(memo_key, "")
            break
    _anls_save(anls_hist_fname, _recs2)


def _anls_save_action_taken(anls_hist_fname: str, rec_id: str, row_idx: int, action_key: str):
    """保存済みレポートの「実施したこと」欄が編集されたときに、既存のJSON読み書き
    （_anls_load/_anls_save）だけを使って該当レコードのdetail[row_idx]へ
    action_takenを書き戻す新規追加関数。_anls_save_memo/_anls_save_next_eval と
    全く同じ仕組みをそのまま流用しており、新しい保存ボタン・新しいJSONファイルは
    作らない。他のキー・他のレコードは一切変更しない。"""
    _recs2 = _anls_load(anls_hist_fname)
    for _r in _recs2:
        if _r.get("id") == rec_id and isinstance(_r.get("detail"), list) and 0 <= row_idx < len(_r["detail"]):
            _r["detail"][row_idx]["action_taken"] = st.session_state.get(action_key, "")
            break
    _anls_save(anls_hist_fname, _recs2)


def _anls_save_next_eval(anls_hist_fname: str, rec_id: str, row_idx: int, next_eval_key: str):
    """保存済みレポートの「次回評価」欄が編集されたときに、既存のJSON読み書き
    （_anls_load/_anls_save）だけを使って該当レコードのdetail[row_idx]へ
    next_evalを書き戻す新規追加関数。_anls_save_memo と全く同じ仕組みを
    そのまま流用しており、新しい保存ボタン・新しいJSONファイルは作らない。
    他のキー・他のレコードは一切変更しない。"""
    _recs2 = _anls_load(anls_hist_fname)
    for _r in _recs2:
        if _r.get("id") == rec_id and isinstance(_r.get("detail"), list) and 0 <= row_idx < len(_r["detail"]):
            _r["detail"][row_idx]["next_eval"] = st.session_state.get(next_eval_key, "")
            break
    _anls_save(anls_hist_fname, _recs2)


def _anls_render_saved_detail(detail: list, anls_hist_fname: str = "", rec_id: str = "",
                              all_recs: list = None, rtype: str = ""):
    """保存済みdetail（_anls_build_detail の出力そのまま）から、
    ライブ画面（分析結果タブ）で使用している既存関数（_anls_camp_table_html,
    _anls_detail_html, _anls_generate_insight）をそのまま呼び出して
    サマリー→キャンペーン→キーワード（ASIN）→Before/After→AI考察の順に
    再現する新規追加関数。

    【Streamlit制約】この関数の呼び出し元はすでに
    「📂 保存済み分析履歴」expander → 各保存日カードexpander という
    2重のexpanderの内側であり、st.expanderをこれ以上ネストすると
    実行時エラー（Expanders may not be nested）になる。そのため
    _anls_render_list（内部でst.expanderを使う）は使わず、キャンペーン・
    キーワードとも見出し（st.markdown）と区切り線（---）のみで整理する。
    判定・理由・アクションの計算ロジックは既存の _anls_generate_insight を
    そのまま呼び出すだけで、新しい判定ロジック・新しいAIロジックは
    一切追加しない。

    all_recs（同じanls_hist_fnameの全レコード）を渡した場合、同一
    campaign+keywordの過去の保存回を _anls_load 済みデータの中から
    検索して時系列一覧を作るだけで、新しい集計・新しい計算は行わない。

    rtype（保存レコードの既存フィールド"type"の値）が、実際にコード上で
    使用されているCPC系のtype値（"キーワードCPC分析","商品CPC分析",
    "動画CPC分析"）のいずれかに完全一致する場合のみ、履歴一覧のラベルを
    既存の saved_at・period_days（どちらも保存済みの既存フィールド）から
    計算した比較対象期間（例: 6/9〜6/15）に置き換える。文字列の部分一致
    （"CPC" in rtype）ではなく、_anls_render_tab の実際の呼び出し箇所6件を
    確認して得た正確なtype値との完全一致（set内包）で判定する。
    period_daysはCPC系（cpc_kw/cpc_asin）では実際の比較日数と一致しているが、
    kw_add/asin_add（"KW追加分析","商品追加分析","動画追加分析"）は表示上30日
    固定運用のため保存値(7)と一致せず、誤った期間になる。そのためこの3つの
    type値は判定対象に含めず、従来通り「{saved_at} 保存」のまま変更しない。
    """
    if not isinstance(detail, list) or not detail:
        return

    def _anls_norm_detail_item(d):
        """保存済みdetailの1要素が想定外の形（None・非dict・before/after欠損等）
        でも安全に扱えるよう最小限に正規化するだけの防御用ヘルパー。判定・実数値
        等の中身は一切変更せず、型が壊れている場合の欠損補完のみ行う。"""
        if not isinstance(d, dict):
            d = {}
        _b, _a = d.get("before"), d.get("after")
        return {
            "campaign": d.get("campaign", ""),
            "keyword": d.get("keyword", ""),
            "judgement": d.get("judgement", "変化なし"),
            "before": _b if isinstance(_b, dict) else {},
            "after": _a if isinstance(_a, dict) else {},
            "reasons": d.get("reasons") or [],
            "actions": d.get("actions") or [],
            "memo": d.get("memo", ""),
            "action_taken": d.get("action_taken", ""),
            "next_eval": d.get("next_eval", ""),
        }

    _rows = []
    for _raw_d in detail:
        d = _anls_norm_detail_item(_raw_d)
        b, a = d["before"], d["after"]
        _rows.append({
            "campaign_theme": d.get("campaign", ""),
            "keyword": d.get("keyword", ""),
            "sales_b": b.get("sales"), "sales_a": a.get("sales"),
            "cost_b": b.get("cost"), "cost_a": a.get("cost"),
            "ROAS_b": b.get("ROAS"), "ROAS_a": a.get("ROAS"),
            "CVR_b": b.get("CVR"), "CVR_a": a.get("CVR"),
            "orders_b": b.get("orders"), "orders_a": a.get("orders"),
            "clicks_b": b.get("clicks"), "clicks_a": a.get("clicks"),
            "_判定": d.get("judgement", "変化なし"),
            "memo": d.get("memo", ""),
            "action_taken": d.get("action_taken", ""),
            "next_eval": d.get("next_eval", ""),
        })
    _df = pd.DataFrame(_rows)

    st.markdown("**サマリー ｜ キャンペーン別**")
    st.markdown(_anls_camp_table_html(_df), unsafe_allow_html=True)

    _ICON = {"改善": "🟢", "悪化": "🔴", "変化なし": "🟡"}
    _CLR  = {"改善": "#276749", "悪化": "#C53030", "変化なし": "#744210"}

    def _anls_hist_label(_period, _saved_at):
        """既存の期間ラベル計算（saved_atからの逆算ではなく、CSV由来の実period文字列を
        優先してM/D〜M/D形式に変換する）をそのまま関数化しただけ。ロジックは無変更、
        重複していた同一処理（4週間サマリー用・週別チェックボックス用）を1箇所に統合。"""
        if _period:
            try:
                _ps, _pe = str(_period).split(" - ")
                _psd = _anls_dt.datetime.strptime(_ps, "%Y/%m/%d").date()
                _ped = _anls_dt.datetime.strptime(_pe, "%Y/%m/%d").date()
                return f"{_psd.month}/{_psd.day}〜{_ped.month}/{_ped.day}"
            except Exception:
                pass
        return f"{_saved_at} 保存"

    def _anls_hist_sort_key(_t):
        """履歴の並び順を、保存順（saved_at/id）ではなく実際のレポート対象期間
        （period列の開始日）優先で並べるための新規ソートキー。periodが無い旧レコード
        は従来通りsaved_at/idで並べる（互換維持）。判定・集計等の分析ロジックには
        一切触れない、表示順序のみの変更。"""
        _s, _rid, _hi2, _hd2, _per = _t
        if _per:
            try:
                _ps = str(_per).split(" - ")[0]
                return (0, _anls_dt.datetime.strptime(_ps, "%Y/%m/%d").date().isoformat())
            except Exception:
                pass
        return (1, f"{_s}_{_rid}")

    _grp_col = "campaign_theme" if "campaign_theme" in _df.columns else None
    _groups = _df.groupby(_grp_col) if _grp_col else [("（未分類）", _df)]
    for _camp, _grp in _groups:
        st.markdown("---")
        st.markdown(f"**📁 キャンペーン：{_camp or '（未分類）'}（{len(_grp)}件）**")
        for _idx, row in _grp.iterrows():
            j = row.get("_判定", "変化なし")
            icon, clr = _ICON.get(j, "🟡"), _CLR.get(j, "#718096")
            kw = str(row.get("keyword", ""))
            _camp_name = row.get("campaign_theme", "")
            _hist = []
            if all_recs:
                for _r in all_recs:
                    if not isinstance(_r, dict):
                        continue
                    _r_detail = _r.get("detail")
                    if not isinstance(_r_detail, list):
                        continue
                    for _hi, _hd_raw in enumerate(_r_detail):
                        _hd = _anls_norm_detail_item(_hd_raw)
                        if _hd.get("campaign", "") == _camp_name and str(_hd.get("keyword", "")) == kw:
                            _hist.append((_r.get("saved_at", ""), _r.get("id", ""), _hi, _hd, _r.get("period")))
                _hist.sort(key=_anls_hist_sort_key)
            if not _hist:
                _hist = [("この保存", rec_id, int(_idx), _anls_norm_detail_item(detail[int(_idx)]), None)]
            if not _hist:
                continue  # UIガード：万一histが空のままでも後続のテーブル/チェックボックス描画へ進まない
            st.markdown(
                f"**{icon} {kw}**　"
                f'<span style="color:{clr};font-weight:700;">{j}</span>',
                unsafe_allow_html=True)
            if len(_hist) > 1:
                _wk_labels, _wk_j, _wk_roas, _wk_cpc = [], [], [], []
                _has_cpc = any("avg_cpc" in ((_h_d.get("after") or {})) for *_r, _h_d, _p in _hist)
                for _h_saved_at, _h_rec_id, _h_row_idx, _h_d, _h_period in _hist:
                    _wk_labels.append(_anls_hist_label(_h_period, _h_saved_at))
                    _sum_j = _h_d.get("judgement", "変化なし")
                    _wk_j.append(_ICON.get(_sum_j, "🟡"))
                    _sum_after = _h_d.get("after") or {}
                    _sum_roas = _sum_after.get("ROAS")
                    _wk_roas.append(f"{_sum_roas:.2f}" if isinstance(_sum_roas, (int, float)) else "―")
                    if _has_cpc:
                        _sum_cpc = _sum_after.get("avg_cpc")
                        _wk_cpc.append(f"¥{_sum_cpc:,.0f}" if isinstance(_sum_cpc, (int, float)) else "―")
                _tbl_rows = [
                    "| 判定 | " + " | ".join(_wk_j) + " |",
                    "| ROAS | " + " | ".join(_wk_roas) + " |",
                ]
                if _has_cpc:
                    _tbl_rows.append("| CPC | " + " | ".join(_wk_cpc) + " |")
                _tbl_md = (
                    "| 指標 | " + " | ".join(_wk_labels) + " |\n"
                    + "|---" * (len(_wk_labels) + 1) + "|\n"
                    + "\n".join(_tbl_rows)
                )
                st.markdown(_tbl_md)
            _open_key = f"_anls_kwopen_{rec_id}_{int(_idx)}"
            if st.checkbox("詳細を見る（Before/After・AI考察・自分メモ）", key=_open_key):
                st.markdown(f"**保存履歴（時系列・{len(_hist)}件）**")
                for _h_saved_at, _h_rec_id, _h_row_idx, _h_d, _h_period in _hist:
                    _hist_label = _anls_hist_label(_h_period, _h_saved_at)
                    _hist_key = f"_anls_histopen_{_h_rec_id}_{_h_row_idx}"
                    if st.checkbox(_hist_label, key=_hist_key):
                        _hb, _ha = (_h_d.get("before") or {}), (_h_d.get("after") or {})
                        _hrow = {
                            "keyword": kw,
                            "sales_b": _hb.get("sales"), "sales_a": _ha.get("sales"),
                            "cost_b": _hb.get("cost"), "cost_a": _ha.get("cost"),
                            "ROAS_b": _hb.get("ROAS"), "ROAS_a": _ha.get("ROAS"),
                            "CVR_b": _hb.get("CVR"), "CVR_a": _ha.get("CVR"),
                            "orders_b": _hb.get("orders"), "orders_a": _ha.get("orders"),
                            "clicks_b": _hb.get("clicks"), "clicks_a": _ha.get("clicks"),
                            "_判定": _h_d.get("judgement", "変化なし"),
                        }
                        st.markdown(_anls_detail_html(_hrow, "keyword"), unsafe_allow_html=True)
                        _h_j = _hrow["_判定"]
                        _h_reasons, _h_actions = _anls_generate_insight(_hrow)
                        _h_reason_hdr = f"{_h_j}理由" if _h_j in ("改善", "悪化") else "理由"
                        st.markdown(f"**{_h_reason_hdr}**\n" + "\n".join(_h_reasons))
                        st.markdown("**■ 広告運用で触るべき項目**\n" + "\n".join(_h_actions))
                        if anls_hist_fname:
                            _h_memo_key = f"_anls_memo_{_h_rec_id}_{_h_row_idx}"
                            if _h_memo_key not in st.session_state:
                                st.session_state[_h_memo_key] = _h_d.get("memo", "") or ""
                            st.text_area(
                                "自分メモ", key=_h_memo_key,
                                on_change=_anls_save_memo,
                                args=(anls_hist_fname, _h_rec_id, _h_row_idx, _h_memo_key))


def _anls_render_saved_report(recs: list, label: str, anls_hist_fname: str = ""):
    """保存済み分析履歴（recsは_anls_load()の戻り値そのまま）を、
    DataFrameの代わりにカード形式の「分析レポート」として表示する追加関数。

    【重要】既存のJSON構造・保存処理・分析ロジック・行単位の判定基準（_anls_row_judge）・
    既存のBefore/After実数値生成には一切触れない。新規追加した agg_* フィールド
    （agg_sales_b/a, agg_cost_b/a, agg_orders_b/a, agg_roas_b/a, agg_avg_cpc_b/a）が
    レコードに存在する場合は、既存関数 _anls_generate_insight にそのまま渡して
    実数値ベースの理由・アクションを表示する（判定/理由生成ロジック自体は既存のまま）。
    agg_* フィールドが無い旧履歴（このアップデート以前に保存された履歴）は、
    従来通り件数ベースの簡易表示にフォールバックし、互換性を維持する。
    """
    if not recs:
        st.info("保存済み分析はありません。")
        return

    def _sort_key(r):
        return (str(r.get("saved_at") or ""), str(r.get("id") or ""))

    recs_sorted = sorted(recs, key=_sort_key)
    enriched = []
    for pos, rec in enumerate(recs_sorted):
        prev = recs_sorted[pos - 1] if pos > 0 else None
        enriched.append((rec, prev))
    enriched_desc = list(reversed(enriched))

    st.markdown("##### 📈 履歴推移（改善率）")
    _trend_index = [r.get("saved_at", "") for r in recs_sorted]
    _trend_vals = [float(r.get("rate", 0) or 0) for r in recs_sorted]
    if len(recs_sorted) >= 2:
        _trend_df = pd.DataFrame({"改善率(%)": _trend_vals}, index=_trend_index)
        st.line_chart(_trend_df)
    _trend_txt = "　→　".join(f"{d}: {v:.1f}%" for d, v in zip(_trend_index, _trend_vals))
    st.caption(_trend_txt)
    st.markdown("---")

    for i, (rec, prev_rec) in enumerate(enriched_desc):
        n_total  = int(rec.get("n_matched", 0) or 0)
        n_kaizen = int(rec.get("n_kaizen", 0) or 0)
        n_akka   = int(rec.get("n_akka", 0) or 0)
        n_henko  = int(rec.get("n_henko", 0) or 0)
        rate     = float(rec.get("rate", 0) or 0)
        n_before = int(rec.get("n_before", 0) or 0)
        camps    = rec.get("camps") or []
        saved_at = rec.get("saved_at", "―")
        rtype    = rec.get("type", label)
        period_days_r = rec.get("period_days", "―")

        if n_kaizen > 0 and n_kaizen >= n_akka and n_kaizen >= n_henko:
            trend, color, emoji = "改善", "#1e8e3e", "🟢"
        elif n_akka > 0 and n_akka > n_kaizen and n_akka >= n_henko:
            trend, color, emoji = "悪化", "#d93025", "🔴"
        else:
            trend, color, emoji = "変化なし", "#f9a825", "🟡"

        stars_n = max(0, min(5, round(rate / 20)))
        stars = "★" * stars_n + "☆" * (5 - stars_n)

        header = f"{emoji} {saved_at}　{rtype}　改善率 {rate:.1f}%　{trend}"
        with st.expander(header, expanded=(i == 0)):
            st.markdown(
                f'<div style="border-bottom:2px solid #ddd;padding-bottom:8px;margin-bottom:12px;">'
                f'<div style="font-size:20px;font-weight:700;">📊 {rtype}</div>'
                f'<div style="color:#666;font-size:13px;margin-top:4px;">'
                f'分析日: {saved_at} ｜ 比較期間: {period_days_r}日固定 ｜ 対象キャンペーン数: {len(camps)}'
                f'</div></div>',
                unsafe_allow_html=True)

            st.markdown(
                f'<div style="background:{color}1a;border:1px solid {color};border-radius:10px;'
                f'padding:14px 18px;margin-bottom:14px;">'
                f'<span style="font-size:22px;">{emoji}</span>'
                f'<span style="font-size:20px;font-weight:700;color:{color};margin-left:6px;">{trend}</span>'
                f'<div style="margin-top:8px;font-size:22px;letter-spacing:2px;color:{color};">{stars}</div>'
                f'<div style="margin-top:4px;"><span style="color:#666;">改善率</span>'
                f'<span style="font-size:28px;font-weight:800;color:{color};margin-left:8px;">{rate:.1f}%</span>'
                f'</div></div>',
                unsafe_allow_html=True)

            _detail_keys = ("agg_sales_b", "agg_sales_a", "agg_cost_b", "agg_cost_a",
                            "agg_orders_b", "agg_orders_a", "agg_roas_b", "agg_roas_a",
                            "agg_avg_cpc_b", "agg_avg_cpc_a")
            has_detail = all(k in rec for k in _detail_keys)

            if has_detail:
                roas_b, roas_a   = float(rec["agg_roas_b"]), float(rec["agg_roas_a"])
                orders_b, orders_a = float(rec["agg_orders_b"]), float(rec["agg_orders_a"])
                sales_b, sales_a = float(rec["agg_sales_b"]), float(rec["agg_sales_a"])
                cost_b, cost_a   = float(rec["agg_cost_b"]), float(rec["agg_cost_a"])
                cpc_b, cpc_a     = float(rec["agg_avg_cpc_b"]), float(rec["agg_avg_cpc_a"])

                def _bf_card(m_label, b, a, higher_better, fmt):
                    if a > b:
                        clr = "#1e8e3e" if higher_better else "#d93025"
                    elif a < b:
                        clr = "#d93025" if higher_better else "#1e8e3e"
                    else:
                        clr = "#f9a825"
                    return (
                        f'<div style="background:#f5f5f5;border-radius:8px;padding:12px;'
                        f'text-align:center;margin-bottom:8px;">'
                        f'<div style="color:#888;font-size:12px;">{m_label}</div>'
                        f'<div style="font-size:20px;font-weight:800;">{fmt.format(b)}'
                        f'<span style="color:#999;font-size:14px;"> → </span>'
                        f'<span style="color:{clr};">{fmt.format(a)}</span></div></div>'
                    )

                st.markdown("**Before / After**")
                _bf_html = "".join([
                    _bf_card("ROAS", roas_b, roas_a, True, "{:.2f}"),
                    _bf_card("注文数", orders_b, orders_a, True, "{:.0f}件"),
                    _bf_card("売上", sales_b, sales_a, True, "¥{:,.0f}"),
                    _bf_card("広告費", cost_b, cost_a, False, "¥{:,.0f}"),
                    _bf_card("平均CPC", cpc_b, cpc_a, False, "¥{:.0f}"),
                ])
                st.markdown(_bf_html, unsafe_allow_html=True)

                _agg_row = {
                    "ROAS_b": roas_b, "ROAS_a": roas_a,
                    "orders_b": orders_b, "orders_a": orders_a,
                    "sales_b": sales_b, "sales_a": sales_a,
                    "cost_b": cost_b, "cost_a": cost_a,
                    "avg_cpc_b": cpc_b, "avg_cpc_a": cpc_a,
                    "_判定": trend,
                }
                _reasons, _actions = _anls_generate_insight(_agg_row)
                _reason_hdr = f"{trend}理由" if trend in ("改善", "悪化") else "理由"
                st.markdown(f"**{_reason_hdr}**\n" + "\n".join(_reasons))
                st.markdown("**■ 広告運用で触るべき項目**\n" + "\n".join(_actions))
            else:
                st.markdown("**Before / After（対象件数）**")
                _c1, _c2 = st.columns(2)
                with _c1:
                    st.markdown(
                        f'<div style="background:#f5f5f5;border-radius:8px;padding:14px;text-align:center;">'
                        f'<div style="color:#888;font-size:12px;">Before（抽出対象）</div>'
                        f'<div style="font-size:26px;font-weight:800;">{n_before}件</div></div>',
                        unsafe_allow_html=True)
                with _c2:
                    st.markdown(
                        f'<div style="background:#f5f5f5;border-radius:8px;padding:14px;text-align:center;">'
                        f'<div style="color:#888;font-size:12px;">After（CSV一致・分析対象）</div>'
                        f'<div style="font-size:26px;font-weight:800;">{n_total}件</div></div>',
                        unsafe_allow_html=True)
                st.caption("※ この履歴は本アップデート以前に保存されたため、実数値（ROAS等）が記録されていません。対象件数の比較のみ表示しています。")

                st.markdown("**なぜこうなったのか**")
                _pct = (lambda x: (x / n_total * 100) if n_total else 0.0)
                st.markdown(
                    f"・改善 {n_kaizen}件（{_pct(n_kaizen):.1f}%）\n\n"
                    f"・悪化 {n_akka}件（{_pct(n_akka):.1f}%）\n\n"
                    f"・変化なし {n_henko}件（{_pct(n_henko):.1f}%）"
                )
                st.caption("※ この履歴は本アップデート以前に保存されたため、詳細な理由データがありません。")

                st.markdown("**■ 広告運用で触るべき項目**")
                if trend == "改善":
                    _actions = ["・現状維持", "・予算増額を検討"]
                elif trend == "悪化":
                    _actions = ["・CPCを下げる", "・検索語句レポート確認", "・不要ターゲット停止候補"]
                else:
                    _actions = ["・現状維持", "・1週間様子を見る"]
                st.markdown("\n\n".join(_actions))
                st.caption("※ この履歴は本アップデート以前に保存されたため、集計傾向に基づく一般的な提案です。")

            st.markdown("**サマリー**")
            st.markdown(_anls_summary_html(n_total, n_kaizen, n_akka, n_henko, rate), unsafe_allow_html=True)

            if camps:
                st.markdown("**対象キャンペーン**")
                _tags = "".join(
                    f'<span style="display:inline-block;background:#eef1f5;border-radius:12px;'
                    f'padding:3px 10px;margin:2px;font-size:12px;">{c}</span>'
                    for c in camps)
                st.markdown(_tags, unsafe_allow_html=True)

            if prev_rec is not None:
                _prev_rate = float(prev_rec.get("rate", 0) or 0)
                _diff = rate - _prev_rate
                _arrow = "↑改善" if _diff > 0 else ("↓悪化" if _diff < 0 else "→変化なし")
                st.markdown(
                    f"**前回との比較**\n\n"
                    f"前回（{prev_rec.get('saved_at', '?')}）: {_prev_rate:.1f}%　→　今回: {rate:.1f}%　**{_arrow}**"
                )
            else:
                st.caption("前回の保存履歴はありません（初回保存）。")

            _row_detail = rec.get("detail")
            if _row_detail:
                st.markdown("---")
                _anls_render_saved_detail(_row_detail, anls_hist_fname, rec.get("id"), recs, rtype)


# ── 複数CSV比較表 用ヘルパー（新規・追加専用） ─────────────────
# 【重要】before/after・判定・ROAS等の値は既存の _anls_build_detail をそのまま
# 呼び出した結果のみを使い、新しい判定式・新しい集計式は一切追加しない。
# campaign_name × keyword(またはasin) の対応付けは、_results や
# _anls_build_detail の出力の並び順・インデックス位置には一切依存せず、
# 各ファイルの merged から作った (campaign_theme, id_col値) → campaign_name の
# 値ベース辞書と、_anls_build_detail の出力が持つ campaign（campaign_theme）・
# keyword値を突き合わせることで実現する（位置対応は不使用）。
def _anls_build_multi_period_table(_results: list, id_col: str, mode: str) -> list:
    def _period_end_date(period_str):
        if not period_str:
            return _anls_dt.date.min
        try:
            _pe = str(period_str).split(" - ")[1]
            return _anls_dt.datetime.strptime(_pe, "%Y/%m/%d").date()
        except Exception:
            return _anls_dt.date.min

    # ②：最新の判定はアップロード順ではなく、期間の終了日（既存の _period_str）で決める
    _sorted = sorted(_results, key=lambda r: _period_end_date(r.get("period")))

    # ③：campaign_name × id_col(keyword/asin) の値ベース辞書で対応付ける。
    # 【重要】_anls_build_detail の出力は campaign 値が campaign_theme であり
    # campaign_name を持たないため、campaign_theme×keyword を中継キーにすると、
    # 同一テーマ内で複数の campaign_name が同じキーワードを持つ場合に取り違えが
    # 起こり得る（テスト時に実際に検出）。そのため今回は _anls_build_detail を
    # 経由せず、merged 自身が持つ既存カラム（campaign_name / id_col / 既存の
    # 判定列 "_判定" / 既存のROAS_a）を直接読むだけにする。値はすべて既存の
    # _anls_row_judge・既存のbefore/after結合が計算済みのものをそのまま参照し、
    # 新しい判定式・新しい集計式は一切追加しない。リストの並び順・インデックス
    # 位置には一切依存しない。
    _per_file = []  # 期間の古い順: [(period_label, {(campaign_name, kw): {judgement, roas}}), ...]
    for _res in _sorted:
        _merged = _res["merged"]
        _by_key = {}
        if all(c in _merged.columns for c in ("campaign_name", id_col, "_判定")):
            for _, _row in _merged.iterrows():
                _key = (_row.get("campaign_name", ""), str(_row.get(id_col, "")))
                # 表示5項目(ROAS/平均CPC/CVR/クリック数/売上)はmergedの既存列
                # (ROAS_a/avg_cpc_a/CVR_a/clicks_a/sales_a)から.get()で読むだけ。
                # 新しい計算・再集計は行わない。
                _by_key[_key] = {
                    "judgement": _row.get("_判定", "変化なし"),
                    "roas": _row.get("ROAS_a"),
                    "avg_cpc": _row.get("avg_cpc_a"),
                    "clicks": _row.get("clicks_a"),
                    "sales": _row.get("sales_a"),
                }
        _label = _res.get("period") or "（期間不明）"
        _per_file.append((_label, _by_key))

    if not _per_file:
        return []

    # ③：比較表の行（軸）は最新CSV（_per_fileの末尾）のキー一覧のみを採用
    _latest_label, _latest_by_key = _per_file[-1]

    # ── 表示順の一元化（新しい並べ替え条件は追加しない） ──────────────
    # CPC調整画面（page_cpc）が最終的に決定した表示順を _cpc_apply_display_order
    # 経由でそのまま呼び出して使うだけで、分析画面独自のsort_valuesは行わない。
    # 対応するCPC調整画面が存在しないモード（kw_add/asin_add等）や、cpc_rank列が
    # 存在しない場合は、従来どおり_latest_by_keyの挿入順（変更なし）を用いる。
    _key_order = list(_latest_by_key.keys())
    if mode == "cpc_kw" and "cpc_rank" in dc_cpc.columns:
        _ordered_df = _cpc_apply_display_order(dc_cpc, _RANK_ORDER)
        _priority = [
            (str(_c), str(_k))
            for _c, _k in zip(_ordered_df.get("campaign_name", []), _ordered_df.get(id_col, []))
        ]
        _seen = set()
        _new_order = []
        for _k in _priority:
            if _k in _latest_by_key and _k not in _seen:
                _new_order.append(_k)
                _seen.add(_k)
        for _k in _key_order:
            if _k not in _seen:
                _new_order.append(_k)
                _seen.add(_k)
        _key_order = _new_order

    _rows = []
    for (_cname, _kw) in _key_order:
        _cells = []
        for _label, _by_key in _per_file:
            _v = _by_key.get((_cname, _kw))
            if _v is None:
                _cells.append({
                    "period_label": _label, "judgement": None, "roas": None,
                    "avg_cpc": None, "clicks": None, "sales": None,
                })
            else:
                _cells.append({
                    "period_label": _label,
                    "judgement": _v.get("judgement", "変化なし"),
                    "roas": _v.get("roas"),
                    "avg_cpc": _v.get("avg_cpc"),
                    "clicks": _v.get("clicks"),
                    "sales": _v.get("sales"),
                })
        _rows.append({"campaign_name": _cname, "keyword": _kw, "cells": _cells})
    return _rows


def _anls_render_multi_period_table(_rows: list) -> None:
    if not _rows:
        st.info("比較対象のキーワードがありません。")
        return

    # ① フォーマッタ関数をループ外(関数内トップレベル)へ移動。
    # 再定義防止・実行コスト削減が目的で、書式ロジック自体は無変更。
    def _fmt_roas(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else "データなし"

    def _fmt_avg_cpc(v):
        return f"{v:,.0f}円" if isinstance(v, (int, float)) else "データなし"

    def _fmt_clicks(v):
        return f"{int(v):,}" if isinstance(v, (int, float)) else "データなし"

    def _fmt_sales(v):
        return f"{v/10000:.1f}万" if isinstance(v, (int, float)) else "データなし"

    st.markdown("#### 📊 複数期間比較表")
    st.caption("最新CSVのキーワードを基準に表示しています。過去CSVに存在しない場合は「データなし」と表示されます。")
    # 表示4項目(ROAS/平均CPC/クリック数/売上)をWeek1〜N横並びで表示する。CVRは
    # 今回の4週間推移表示からは取得・表示ともに行わない(他画面のCVR処理は無変更)。
    # 判定・改善悪化アイコン等の新しい評価表示は行わない。値はcellsのmerged由来
    # データをそのまま文字列整形するだけで、再計算・再集計は行わない。
    for _row in _rows:
        st.markdown(f"**{_row['campaign_name']}｜{_row['keyword']}**")
        _cells = _row["cells"]
        if not _cells:
            st.info("表示できる期間データがありません。")
            continue
        _wk_labels = [f"Week{_i+1}" for _i in range(len(_cells))]
        _wk_cols = [f"Week{_i+1}" for _i in range(len(_cells))]
        _table_df = pd.DataFrame(
            [
                ["ROAS"]    + [_fmt_roas(c["roas"]) for c in _cells],
                ["平均CPC"]  + [_fmt_avg_cpc(c["avg_cpc"]) for c in _cells],
                ["クリック数"] + [_fmt_clicks(c["clicks"]) for c in _cells],
                ["売上"]    + [_fmt_sales(c["sales"]) for c in _cells],
            ],
            columns=["指標"] + _wk_cols,
        )
        st.table(_table_df)
        st.markdown("---")


def _anls_render_tab(before_df: pd.DataFrame, period_days: int,
                     anls_hist_fname: str, csv_key: str, label: str,
                     mode: str,
                     id_col: str = "keyword",
                     cpc_hist_fname: str = ""):
    _sk = f"_anls_{csv_key}"
    st.markdown(f"#### 📊 {label} 分析")
    _disp_days = 30 if mode in ("kw_add", "asin_add") else period_days
    if mode in ("kw_add", "asin_add"):
        st.info(f"📅 追加後の効果測定期間: **{_disp_days}日固定** — 追加候補を反映してから{_disp_days}日間のレポートCSVをアップロードしてください。")
    else:
        st.info(f"📅 分析期間: **{period_days}日固定** — {period_days}日レポートCSVをアップロードしてください。")
    if before_df is None or before_df.empty:
        st.warning("先に画面上部でCSVをアップロードして「分析開始」を行ってください。抽出対象が分析対象になります。")
        return
    camps = sorted(before_df["campaign_theme"].unique().tolist()) if "campaign_theme" in before_df.columns else []
    # ── CSV入力元: 分析画面内アップロード欄は廃止。CSV管理基盤（バケット）を参照する ──
    # 【重要】ここから下（_results=[] 以降）の比較・判定・集計・表示ロジックは無変更。
    # 変更対象は「af_filesの取得方法」のみ。
    _accept_multiple = mode in ("cpc_kw", "cpc_asin")
    _bucket_key   = "csv_bucket_30d" if _disp_days == 30 else "csv_bucket_7d"
    _bucket_label = "30日比較CSV"    if _disp_days == 30 else "7日比較CSV"
    _bucket_held  = st.session_state.get(_bucket_key, {})
    st.markdown(f"**比較用データ取得元: 📂 {_bucket_label}バケット（{_disp_days}日）**")
    if _bucket_held:
        if _accept_multiple:
            af_files = list(_bucket_held.values())
            st.caption(f"参照ファイル（{len(af_files)}件・保持分すべて）: " + "、".join(sorted(_bucket_held.keys())))
        else:
            _latest_name = list(_bucket_held.keys())[-1]
            af_files = [_bucket_held[_latest_name]]
            st.caption(f"参照ファイル（最新1件）: {_latest_name}")
    else:
        af_files = []
        st.caption(f"「{_bucket_label}」バケットにCSVが保持されていません。")
    # ── 案内表示のみ（既存ロジック・判定・DataFrameには一切影響しない） ──
    # 分析開始後、7日/30日比較CSVバケットに2件以上保持されている場合のみ、
    # 「分析開始の再実行は不要」であることをユーザーに案内する。
    if _accept_multiple and len(_bucket_held) >= 2:
        st.info(
            "📊 比較CSVを検出しました。\n"
            "分析開始は再度実行する必要はありません。\n"
            "「🔍 分析実行」を押すと、\n"
            "現在保持されている比較CSVで複数期間比較を開始します。"
        )
    run_btn = st.button("🔍 分析実行", key=f"{_sk}_run", type="primary")
    if run_btn:
        if not af_files:
            st.warning(f"「{_bucket_label}」バケットにCSVをアップロードしてください（画面上部のCSV管理基盤から）。"); return
        _results = []
        for af_file in af_files:
            with st.spinner("分析中..."):
                df_raw, kc, cc, sc, oc_, od, clk, imp, tkc, kwt, agn = _anls_parse_csv(af_file)
                _period_str = None
                if mode in ("cpc_kw", "cpc_asin"):
                    _pc = fcol(df_raw, ["期間"])
                    if _pc:
                        try:
                            _parts = df_raw[_pc].astype(str).str.split(" - ", expand=True)
                            _starts = pd.to_datetime(_parts[0], format="%Y/%m/%d", errors="coerce")
                            _ends = pd.to_datetime(_parts[1], format="%Y/%m/%d", errors="coerce") if _parts.shape[1] > 1 else _starts
                            if _starts.notna().any() and _ends.notna().any():
                                _period_str = f"{_starts.min().strftime('%Y/%m/%d')} - {_ends.max().strftime('%Y/%m/%d')}"
                        except Exception:
                            _period_str = None
                if not all([cc, sc, oc_]):
                    st.error("必要な列が見つかりません（キャンペーン名・売上・広告費）。"); return
                if mode == "kw_add":
                    if not kc: st.error("「検索用語」列が見つかりません。"); return
                    after_df = _anls_build_kw_after(df_raw, kc, cc, sc, oc_, od, clk)
                elif mode == "cpc_kw":
                    kw_col_cpc = kwt if kwt else fcol(df_raw, ["ターゲティング", "Targeting", "targeting"])
                    after_df = _anls_build_cpc_after(df_raw, cc, sc, oc_, od, clk, kw_col_cpc)
                else:  # cpc_asin or asin_add
                    camp_pat = None
                    if mode == "cpc_asin" and before_df is not None and not before_df.empty:
                        if "campaign_name" in before_df.columns:
                            camp_pat = "|".join(before_df["campaign_name"].dropna().unique().tolist())
                    after_df = _anls_build_asin_after(df_raw, cc, sc, oc_, od, clk, tkc, camp_pat)
                if after_df.empty:
                    st.warning("Afterデータが取得できませんでした。"); return
                bf = before_df.copy()
                n_history = None
                if mode == "cpc_kw":
                    _cpc_hist = _anls_load(cpc_hist_fname or "cpc_change_history.json")
                    if not _cpc_hist:
                        st.error("履歴がありません。先に「CPC調整タブ → 実行用CSVをダウンロード」してください。"); return
                    _last_entries = _cpc_hist[-1]["entries"]
                    _hist_df = pd.DataFrame(_last_entries)
                    def _cpc_key_fn(r):
                        cn_  = norm(str(r.get("campaign_name", "") or ""))
                        agn_ = norm(str(r.get("ad_group", "") or ""))
                        kw_  = norm(str(r.get("keyword", "") or ""))
                        return f"{cn_}|{agn_}|{kw_}"
                    _hist_df["_kn_key"] = _hist_df.apply(_cpc_key_fn, axis=1)
                    _hist_keys = set(_hist_df["_kn_key"])
                    n_history = len(_hist_df)
                    bf["_kn_key"] = bf.apply(_cpc_key_fn, axis=1)
                    bf = bf[bf["_kn_key"].isin(_hist_keys)].copy()
                elif mode == "cpc_asin":
                    if cpc_hist_fname:
                        _asin_hist = _anls_load(cpc_hist_fname)
                        if not _asin_hist:
                            st.error("履歴がありません。先に「CPC調整タブ → 実行用CSVをダウンロード」してください。"); return
                        _last_entries = _asin_hist[-1]["entries"]
                        _hist_df = pd.DataFrame(_last_entries)
                        if "asin" in _hist_df.columns:
                            _hist_keys = set(_hist_df["asin"].str.upper())
                            n_history = len(_hist_df)
                            bf["_kn_key"] = bf["asin"].str.upper() if "asin" in bf.columns else bf.index.astype(str)
                            bf = bf[bf["_kn_key"].isin(_hist_keys)].copy()
                        else:
                            bf["_kn_key"] = bf["asin"].str.upper() if "asin" in bf.columns else bf.index.astype(str)
                    else:
                        bf["_kn_key"] = bf["asin"].str.upper() if "asin" in bf.columns else bf.index.astype(str)
                elif mode == "asin_add":
                    if cpc_hist_fname:
                        _asin_add_hist = _anls_load(cpc_hist_fname)
                        if not _asin_add_hist:
                            st.error("履歴がありません。先に「追加候補タブ → CSVをダウンロード」してください。"); return
                        _last_entries = _asin_add_hist[-1]["entries"]
                        _hist_df = pd.DataFrame(_last_entries)
                        if "asin" in _hist_df.columns:
                            _hist_keys = set(_hist_df["asin"].str.upper())
                            n_history = len(_hist_df)
                            bf["_kn_key"] = bf["asin"].str.upper() if "asin" in bf.columns else bf.index.astype(str)
                            bf = bf[bf["_kn_key"].isin(_hist_keys)].copy()
                        else:
                            bf["_kn_key"] = bf["asin"].str.upper() if "asin" in bf.columns else bf.index.astype(str)
                    else:
                        bf["_kn_key"] = bf["asin"].str.upper() if "asin" in bf.columns else bf.index.astype(str)
                else:  # kw_add
                    if cpc_hist_fname:
                        _kw_add_hist = _anls_load(cpc_hist_fname)
                        if not _kw_add_hist:
                            st.error("履歴がありません。先に「追加候補タブ → CSVをダウンロード」してください。"); return
                        _last_entries = _kw_add_hist[-1]["entries"]
                        _hist_df = pd.DataFrame(_last_entries)
                        if "keyword" in _hist_df.columns:
                            _hist_df["_kn_key"] = _hist_df["keyword"].apply(norm)
                            _hist_keys = set(_hist_df["_kn_key"])
                            n_history = len(_hist_df)
                            bf["_kn_key"] = bf["keyword"].apply(norm) if "keyword" in bf.columns else bf.index.astype(str)
                            bf = bf[bf["_kn_key"].isin(_hist_keys)].copy()
                        else:
                            bf["_kn_key"] = bf["keyword"].apply(norm) if "keyword" in bf.columns else bf.index.astype(str)
                    else:
                        bf["_kn_key"] = bf["keyword"].apply(norm) if "keyword" in bf.columns else bf.index.astype(str)
                sfx = [c for c in ["sales", "cost", "ROAS", "orders", "clicks", "CVR", "avg_cpc"] if c in after_df.columns]
                merged = bf.merge(after_df[["_kn_key"] + sfx], on="_kn_key", how="inner", suffixes=("_b", "_a"))
            if merged.empty:
                st.info("マッチするデータが見つかりませんでした。"); return
            merged["_判定"] = merged.apply(_anls_row_judge, axis=1)
            n_total  = len(merged)
            n_kaizen = int((merged["_判定"] == "改善").sum())
            n_akka   = int((merged["_判定"] == "悪化").sum())
            n_henko  = int((merged["_判定"] == "変化なし").sum())
            rate     = n_kaizen / n_total * 100 if n_total > 0 else 0
            _results.append({
                "merged": merged, "camps": camps,
                "stats": (n_total, n_kaizen, n_akka, n_henko, rate),
                "anls_hist_fname": anls_hist_fname, "label": label,
                "period_days": period_days, "n_before": len(before_df),
                "n_history": n_history, "id_col": id_col,
                "period": _period_str,
            })
        st.session_state[f"{_sk}_result"] = _results
    if f"{_sk}_result" not in st.session_state:
        return
    _results_now = st.session_state[f"{_sk}_result"]
    if len(_results_now) >= 2:
        # 複数CSV時：①保存機能なし ②③は _anls_build_multi_period_table 側で対応。
        # ファイルごとの独立結果パネル（サマリー・対象一覧・保存ボタン）は表示しない。
        _cmp_rows = _anls_build_multi_period_table(_results_now, id_col, mode)
        _anls_render_multi_period_table(_cmp_rows)
    else:
        for _ri, res in enumerate(_results_now):
            merged   = res["merged"]
            camps    = res["camps"]
            n_total, n_kaizen, n_akka, n_henko, rate = res["stats"]
            anls_hist_fname = res["anls_hist_fname"]
            res_id_col = res.get("id_col", id_col)
            st.markdown(_anls_summary_html(n_total, n_kaizen, n_akka, n_henko, rate), unsafe_allow_html=True)
            n_history = res.get("n_history")
            if n_history is not None:
                n_unmatched = n_history - n_total
                st.info(f"変更履歴: {n_history}件 ｜ CSV一致: {n_total}件 ｜ 不一致: {n_unmatched}件 ｜ 分析対象: {n_total}件")
            sel_camp = st.selectbox("キャンペーン絞り込み", ["全キャンペーン"] + camps, key=f"{_sk}_{_ri}_camp")
            view = merged if sel_camp == "全キャンペーン" else merged[merged["campaign_theme"] == sel_camp].copy() if "campaign_theme" in merged.columns else merged
            with st.expander("📊 キャンペーン別サマリー", expanded=True):
                st.markdown(_anls_camp_table_html(view), unsafe_allow_html=True)
            st.markdown("#### 📋 対象一覧")
            _anls_render_list(view, res_id_col)
            if st.button("💾 分析結果を保存", key=f"{_sk}_{_ri}_save"):
                _recs = _anls_load(anls_hist_fname)
                _agg = _anls_aggregate_before_after(merged)
                _detail = _anls_build_detail(merged, res_id_col, mode)
                _recs.append({"id": _anls_dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
                              "saved_at": _anls_dt.date.today().isoformat(), "type": label,
                              "period_days": period_days, "n_before": res["n_before"],
                              "n_matched": n_total, "n_kaizen": n_kaizen, "n_akka": n_akka,
                              "n_henko": n_henko, "rate": round(rate, 1), "camps": camps,
                              "period": res.get("period"),
                              **_agg, "detail": _detail})
                _anls_save(anls_hist_fname, _recs)
                st.success("✅ 分析結果を保存しました。")
    with st.expander("📂 保存済み分析履歴", expanded=False):
        _recs = _anls_load(anls_hist_fname)
        _anls_render_saved_report(_recs, label, anls_hist_fname)


# ============================================================
# Observability Layer（app_v1_44で追加）
# ------------------------------------------------------------
# 【方式】完全な外側ラップ方式のみ。対象関数・_anls_render_tabの
# 定義（上のコード）には1文字も変更を加えていない。以下は全て
# 「元の関数を横取りして呼ぶだけの新規コード」であり、このブロックを
# まるごと削除すればv43と完全に同じ挙動に戻る（追加専用・可逆）。
# 各ラッパーは：
#   1) 引数を読むだけ（改変しない）で異常の事前スキャンログを出す
#   2) 元関数をそのまま呼ぶ（戻り値・例外は一切加工しない）
#   3) 戻り値を読むだけで正常系ログを出す
#   4) 例外はログ後に必ず re-raise する（挙動を変えない）
# ログ出力自体の失敗が本処理に影響しないよう、_anls_log内部で
# 例外を握りつぶす。
# ============================================================
import logging as _anls_logging
import json as _anls_json

_ANLS_OBS_ENABLED = True  # Falseにすればログのみ完全停止（挙動は不変）

_anls_obs_logger = _anls_logging.getLogger("aniha.analysis.observability")
if not _anls_obs_logger.handlers:
    _anls_obs_handler = _anls_logging.StreamHandler()
    _anls_obs_handler.setFormatter(_anls_logging.Formatter("%(message)s"))
    _anls_obs_logger.addHandler(_anls_obs_handler)
    _anls_obs_logger.setLevel(_anls_logging.INFO)
    _anls_obs_logger.propagate = False


def _anls_log(level: str, category: str, event: str, **fields):
    """観測ログ共通出力。既存の分析・判定・保存ロジックの戻り値・分岐には
    一切関与しない副作用専用関数。JSON化失敗・ロガー未設定等、ログ機構側の
    不具合は全てここで握りつぶし、本処理へは絶対に波及させない。
    category: "normal" | "anomaly" | "structural" 、level: INFO/WARN/ERROR"""
    if not _ANLS_OBS_ENABLED:
        return
    try:
        payload = {"ts": _anls_dt.datetime.now().isoformat(), "level": level,
                   "category": category, "event": event, **fields}
        msg = _anls_json.dumps(payload, ensure_ascii=False, default=str)
        getattr(_anls_obs_logger, level.lower(), _anls_obs_logger.info)(msg)
    except Exception:
        pass


_ANLS_KNOWN_MODES = {"cpc_kw", "cpc_asin", "kw_add", "asin_add"}
_ANLS_CPC_MODES = {"cpc_kw", "cpc_asin"}


# ---- ラップ対象①: _anls_build_detail（CPC判定処理を含む） ----
_anls_build_detail_orig = _anls_build_detail


def _anls_build_detail(*args, **kwargs):
    _mode = kwargs.get("mode", args[2] if len(args) > 2 else "")
    _merged_arg = kwargs.get("merged", args[0] if len(args) > 0 else None)
    try:
        if _mode and _mode not in _ANLS_KNOWN_MODES:
            _anls_log("WARN", "structural", "unexpected_mode_value",
                      fn="_anls_build_detail", mode=_mode)
        if _mode in _ANLS_CPC_MODES and _merged_arg is not None:
            _cols = list(getattr(_merged_arg, "columns", []))
            if "avg_cpc_b" not in _cols or "avg_cpc_a" not in _cols:
                _anls_log("ERROR", "structural", "cpc_key_missing_in_source",
                          fn="_anls_build_detail", mode=_mode)
    except Exception:
        pass

    try:
        _result = _anls_build_detail_orig(*args, **kwargs)
    except Exception as _e:
        _anls_log("ERROR", "anomaly", "build_detail_exception",
                  fn="_anls_build_detail", mode=_mode,
                  error_type=type(_e).__name__, error=str(_e))
        raise

    try:
        _is_cpc = _mode in _ANLS_CPC_MODES
        _has_avg_cpc = any(isinstance(_d, dict) and "avg_cpc" in (_d.get("after") or {})
                            for _d in _result)
        _roas_vals = [
            _d["after"].get("ROAS") for _d in _result
            if isinstance(_d, dict) and isinstance(_d.get("after"), dict)
            and isinstance(_d["after"].get("ROAS"), (int, float))
        ]
        _anls_log("INFO", "normal", "build_detail_done",
                  fn="_anls_build_detail", mode=_mode, is_cpc=_is_cpc,
                  has_avg_cpc=_has_avg_cpc, rows=len(_result),
                  roas_samples=len(_roas_vals),
                  roas_mean=(sum(_roas_vals) / len(_roas_vals) if _roas_vals else None))
    except Exception:
        pass
    return _result


# ---- ラップ対象②: _anls_render_saved_detail（4週間比較テーブル生成を含む） ----
_anls_render_saved_detail_orig = _anls_render_saved_detail


def _anls_render_saved_detail(*args, **kwargs):
    _detail_arg = kwargs.get("detail", args[0] if len(args) > 0 else None)
    _all_recs_arg = kwargs.get("all_recs", args[3] if len(args) > 3 else None)
    try:
        if isinstance(_detail_arg, list):
            _bad_items = sum(1 for _d in _detail_arg if not isinstance(_d, dict))
            if _bad_items:
                _anls_log("ERROR", "structural", "detail_items_not_dict",
                          fn="_anls_render_saved_detail", count=_bad_items)
            _bad_before = sum(1 for _d in _detail_arg
                               if isinstance(_d, dict) and not isinstance(_d.get("before"), dict))
            _bad_after = sum(1 for _d in _detail_arg
                              if isinstance(_d, dict) and not isinstance(_d.get("after"), dict))
            if _bad_before:
                _anls_log("WARN", "structural", "before_missing_or_invalid",
                          fn="_anls_render_saved_detail", count=_bad_before)
            if _bad_after:
                _anls_log("WARN", "structural", "after_missing_or_invalid",
                          fn="_anls_render_saved_detail", count=_bad_after)
        elif _detail_arg is not None:
            _anls_log("WARN", "structural", "detail_arg_not_list",
                      fn="_anls_render_saved_detail", original_type=type(_detail_arg).__name__)
        if _all_recs_arg:
            _bad_recs = sum(1 for _r in _all_recs_arg if not isinstance(_r, dict))
            if _bad_recs:
                _anls_log("ERROR", "structural", "hist_record_not_dict",
                          fn="_anls_render_saved_detail", count=_bad_recs)
        # v47: 参照のみで補完する追加観測（campaign/keyword走査・4週間ロジックの
        # 再実装・st.markdown解析は一切行わない。lenとキー存在確認のみ）。
        _hist_count = len(_all_recs_arg) if isinstance(_all_recs_arg, list) else 0
        _detail_list = _detail_arg if isinstance(_detail_arg, list) else []
        _detail_count = len(_detail_list)
        _cpc_present_count = sum(
            1 for _d in _detail_list
            if isinstance(_d, dict) and isinstance(_d.get("after"), dict) and "avg_cpc" in _d["after"]
        )
        _has_avg_cpc = _cpc_present_count > 0
        _avg_cpc_ratio = round(_cpc_present_count / _detail_count, 3) if _detail_count else 0.0
        # v48: 命名修正のみ（ロジック・計算式は無変更）。
        # 旧名 week_variation_flag は実態が「週」ではなく「今回保存分の行数が
        # 複数か」（len(detail) > 1）であったため、実態に即した名称へ変更。
        _row_count_flag = _detail_count > 1
        # hist_depth の計算式は無変更（各レコードのdetail長のlen合計のみ）。
        # 意味を明確化するため、ログにのみ説明文字列を追加する（計算結果には無関係）。
        _hist_depth = 0
        if isinstance(_all_recs_arg, list):
            _hist_depth = sum(
                len(_r["detail"]) for _r in _all_recs_arg
                if isinstance(_r, dict) and isinstance(_r.get("detail"), list)
            )
        _hist_depth_desc = "sum_of_detail_lengths_across_hist_records"
        _anls_log("INFO", "normal", "hist_reference_summary",
                  fn="_anls_render_saved_detail", hist_count=_hist_count,
                  detail_count=_detail_count, has_avg_cpc=_has_avg_cpc,
                  avg_cpc_ratio=_avg_cpc_ratio, row_count_flag=_row_count_flag,
                  hist_depth=_hist_depth, hist_depth_desc=_hist_depth_desc)
    except Exception:
        pass

    try:
        _result = _anls_render_saved_detail_orig(*args, **kwargs)
    except Exception as _e:
        _anls_log("ERROR", "anomaly", "render_saved_detail_exception",
                  fn="_anls_render_saved_detail", error_type=type(_e).__name__, error=str(_e))
        raise

    try:
        _anls_log("INFO", "normal", "render_saved_detail_done",
                  fn="_anls_render_saved_detail",
                  detail_rows=(len(_detail_arg) if isinstance(_detail_arg, list) else 0))
    except Exception:
        pass
    return _result


# ---- ラップ対象③（v46で純粋観測型に変更）: hist/detailの参照のみ ----
# v45では「同一campaign+keywordの保存回数」をラッパー内で再計算しており、
# これは_anls_render_saved_detail内部のhist構築アルゴリズム（マッチング
# ロジック）を観測レイヤ側に複製することに等しく、コアロジックとの二重化・
# 将来の仕様ズレリスクを生んでいた。v46ではこの再計算処理を完全に削除し、
# 観測は「渡された引数の件数・キー存在有無を参照するだけ」に限定する
# （フィルタリング・マッチング・集計は一切行わない）。
# 削除: _anls_count_week_history（本関数そのものを削除）
# 削除: four_week_table_generated / week_data_incomplete ログ
#       （campaign/keywordマッチングに基づく再計算だったため）
# 追加: hist_reference_summary（all_recsの件数・detailの件数・avg_cpc
#       キーの存在有無を、フィルタなしでそのまま参照するだけのログ）


# ---- ラップ対象④: NaN検知（_anls_pct_str / _anls_camp_table_html） ----
_anls_pct_str_orig = _anls_pct_str


def _anls_pct_str(b, a):
    try:
        if pd.isna(b) or pd.isna(a):
            _anls_log("WARN", "anomaly", "nan_detected_in_pct_calc",
                      fn="_anls_pct_str", raw_b=str(b), raw_a=str(a))
    except Exception:
        pass
    return _anls_pct_str_orig(b, a)


_anls_camp_table_html_orig = _anls_camp_table_html


def _anls_camp_table_html(*args, **kwargs):
    try:
        _merged_arg = kwargs.get("merged", args[0] if args else None)
        _cols = list(getattr(_merged_arg, "columns", []))
        if _merged_arg is not None and "campaign_theme" in _cols:
            for _ct, _grp in _merged_arg.groupby("campaign_theme"):
                for _col in ("ROAS_b", "ROAS_a", "CVR_b", "CVR_a"):
                    if _col in _grp.columns:
                        try:
                            if pd.isna(pd.to_numeric(_grp[_col], errors="coerce").mean()):
                                _anls_log("WARN", "anomaly", "nan_detected_in_camp_summary",
                                          fn="_anls_camp_table_html", campaign=str(_ct), column=_col)
                        except Exception:
                            pass
    except Exception:
        pass
    return _anls_camp_table_html_orig(*args, **kwargs)


# ---- ラップ対象⑤: _anls_render_tab（30日機能を含むUIレンダリング呼び出し部） ----
# 【重要】_anls_render_tab の定義（このファイルの上のコード）はv43から
# 1バイトも変更していない。以下は関数名の再代入のみで、内部コードには
# 一切触れていない。
_anls_render_tab_orig = _anls_render_tab


def _anls_render_tab(*args, **kwargs):
    _mode = kwargs.get("mode", args[5] if len(args) > 5 else "?")
    _anls_log("INFO", "normal", "render_tab_start", fn="_anls_render_tab", mode=_mode)
    try:
        _result = _anls_render_tab_orig(*args, **kwargs)
    except Exception as _e:
        _anls_log("ERROR", "anomaly", "render_tab_exception",
                  fn="_anls_render_tab", mode=_mode,
                  error_type=type(_e).__name__, error=str(_e))
        raise
    _anls_log("INFO", "normal", "render_tab_end", fn="_anls_render_tab", mode=_mode)
    return _result
# ============================================================
# Observability Layer ここまで
# ============================================================


# ── CPC調整タブ 階層表示UI用ヘルパー（新規・参照専用） ──────────
# 【重要】ここは表示専用の補助関数。判定・ROAS・CPC等の値は一切再計算せず、
# 「分析」タブが _anls_build_detail で既に保存した値（anls_hist_fname のJSON）を
# 読み取って画面表示用に整形するだけ。新しい判定ロジック・新しい計算式は含まない。
# 既存の _anls_render_tab / _anls_build_detail / _anls_save / _anls_load は無変更。
def _cpc_hier_lookup_trend(campaign_theme: str, keyword: str, anls_hist_fname: str) -> list:
    _recs = _anls_load(anls_hist_fname)
    if not _recs:
        return []
    _out = []
    for _r in _recs:
        if not isinstance(_r, dict):
            continue
        _detail = _r.get("detail")
        if not isinstance(_detail, list):
            continue
        for _hd in _detail:
            if not isinstance(_hd, dict):
                continue
            if _hd.get("campaign", "") != campaign_theme or str(_hd.get("keyword", "")) != str(keyword):
                continue
            _period = _r.get("period")
            _saved_at = _r.get("saved_at", "")
            _after = _hd.get("after") or {}
            _roas_v = _after.get("ROAS")
            _cpc_v = _after.get("avg_cpc")
            _cvr_v = _after.get("CVR")
            _clicks_v = _after.get("clicks")
            _sales_v = _after.get("sales")
            _out.append({
                "sort_key": _period or _saved_at,
                # 表示用文字列整形のみ（小数点／桁区切り／%表示／円表示／万円換算）。
                # 値そのもの（_roas_v等）はJSON保存済みの値をそのまま使用し、
                # 再計算・再集計は一切行わない。
                "roas_str": f"{_roas_v:.2f}" if isinstance(_roas_v, (int, float)) else "―",
                "avg_cpc_str": f"{_cpc_v:,.0f}円" if isinstance(_cpc_v, (int, float)) else "―",
                "cvr_str": f"{_cvr_v:.1f}%" if isinstance(_cvr_v, (int, float)) else "―",
                "clicks_str": f"{int(_clicks_v):,}" if isinstance(_clicks_v, (int, float)) else "―",
                "sales_str": f"{_sales_v/10000:.1f}万" if isinstance(_sales_v, (int, float)) else "―",
            })
    _out.sort(key=lambda x: x["sort_key"])
    return _out[-4:]


# ── CPC調整の表示順ロジックを一元化するための共通定数・共通関数 ──────────
# 【重要】以下はpage_cpc内に元々あった _RANK_ORDER と、tab1内にあった並べ替え
# 処理（cat_t → df_c["_r"] → sort_values → drop → reset_index）を、値・条件を
# 一切変更せずそのまま移しただけの共通化。新しい並べ替え基準は追加していない。
_RANK_ORDER = ["SS+", "SS", "S", "A", "B", "C", "D", "即削除", "判断保留"]

def _cpc_apply_display_order(df_c: pd.DataFrame, rank_order: list) -> pd.DataFrame:
    cat_t = pd.CategoricalDtype(categories=rank_order, ordered=True)
    df_c = df_c.copy()
    df_c["_r"] = df_c["cpc_rank"].astype(cat_t)
    df_c = df_c.sort_values(["_r", "ROAS"], ascending=[True, False]).drop(columns=["_r"]).reset_index(drop=True)
    return df_c



def page_cpc():
    _t_tab1, _t_tab2 = st.tabs(["CPC調整", "分析"])
    with _t_tab1:
        _RC = {
            "SS+": "#D69E2E", "SS": "#B7791F", "S": "#553C9A",
            "A":   "#2C7A7B", "B": "#2B6CB0", "C": "#C05621",
            "D":   "#C53030", "即削除": "#742A2A", "判断保留": "#4A5568",
        }
        _cond_bar([("CPC調整ルール", "適用"), ("最小クリック数", f"{sv['mc']}回")])
        render_logic_section(
            "📈 CPC調整ロジック",
            '''
<table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">
<thead>
  <tr style="background:#DBEAFE;">
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:15%;">ランク</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:45%;">判定条件</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:20%;">アクション</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:20%;">CPC変更幅</th>
  </tr>
</thead>
<tbody>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 1】 データ不足判定（最優先）
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#4A5568;">判断保留</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">広告費 &lt; ¥3,000 <b>かつ</b> 購入数 &lt; 4件</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">変更なし</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 2】 高実績ランク（購入数 ≥ 20件）
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#D69E2E;">SS+</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">購入数 ≥ 20件 <b>かつ</b> ROAS ≥ 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC上げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#276749;font-weight:700;">+5円</td>
  </tr>
  <tr style="background:#FFFBEB;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#B7791F;">SS</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">購入数 ≥ 20件 <b>かつ</b> 2.0 ≤ ROAS &lt; 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 3】 ROASベースランク
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#553C9A;">S</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">ROAS ≥ 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC上げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#276749;font-weight:700;">+5円</td>
  </tr>
  <tr style="background:#F0FFF4;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2C7A7B;">A</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">3.0 ≤ ROAS &lt; 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2B6CB0;">B</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">1.8 ≤ ROAS &lt; 3.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C05621;">C</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">1.5 ≤ ROAS &lt; 1.8</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC下げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#C53030;font-weight:700;">−5円</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C53030;">D</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">ROAS &lt; 1.5</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC下げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#C53030;font-weight:700;">−10円</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 4】 即削除判定（広告費過多 × 低ROAS）
    </td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#742A2A;">即削除</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">
      ROAS &lt; 0.8 <b>かつ</b> 広告費が閾値以上<br>
      <span style="font-size:.8rem;color:#718096;">
        売価 ≤¥1,500 → 広告費 ≥¥3,000 ／
        売価 ≤¥2,000 → 広告費 ≥¥4,000 ／
        売価 &gt;¥2,000 → 広告費 ≥¥5,000
      </span>
    </td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#742A2A;font-weight:700;">即削除</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">—</td>
  </tr>
</tbody>
</table>
<p style="font-size:.78rem;color:#718096;margin-top:10px;">
  ▶ 判定順序: STEP1（データ不足）→ STEP2（購入数優先） → STEP3（ROASベース） → STEP4（即削除）<br>
  ▶ 基本思想: ROASだけでなく、広告費と購入数を重視した複合判定
</p>''',
        )
        if dc_cpc.empty:
            st.info("分析を実行してください。")
            return
        sel_options = ["全商品"] + [c for c in CAMPAIGNS if not dc_cpc[dc_cpc["campaign_theme"] == c].empty]
        _sc, _ = st.columns([3, 2])
        with _sc:
            cpc_camp = st.selectbox("商品選択（キーワードCPC調整）", sel_options, label_visibility="visible", key="cpc_camp_sel")
        if cpc_camp == "全商品":
            df_c = dc_cpc.copy()
        else:
            df_c = dc_cpc[dc_cpc["campaign_theme"] == cpc_camp].copy()
        kpi_rks = ["SS+", "SS", "S", "A", "B", "C", "D", "即削除"]
        _bg_map_rank = {
            "SS+":"#FFFFF0","SS":"#FEFCBF","S":"#E9D8FD","A":"#C6F6D5",
            "B":"#BEE3F8","C":"#FEEBC8","D":"#FED7D7","即削除":"#FED7D7",
        }
        def _render_rank_cards(_df_for_cnt):
            _c = {r: int((_df_for_cnt["cpc_rank"] == r).sum()) for r in _RANK_ORDER}
            _kc = st.columns(len(kpi_rks))
            for _col, rk in zip(_kc, kpi_rks):
                _col.markdown(f'''<div class="kpi-card" style="background:{_bg_map_rank.get(rk,'#F4F6F8')};border-top:3px solid {_RC[rk]};">
                    <div class="kpi-label">{rk}</div>
                    <div class="kpi-value" style="color:{_RC[rk]};font-size:1.5rem;">{_c[rk]}</div>
                    <div class="kpi-sub">件</div></div>''', unsafe_allow_html=True)
            return _c
        # ① 全体サマリー（全商品横断・選択に関係なく常時表示。集計方法・色は変更禁止）
        cnt = _render_rank_cards(dc_cpc)
        if cnt["判断保留"] > 0:
            st.caption(f"⏸ 判断保留: {cnt['判断保留']}件（広告費¥3,000未満 かつ 購入数4件未満）")
        st.markdown("---")
        # ② キャンペーンサマリー（ローカル・選択キャンペーンに依存。表示専用・操作/フィルタ禁止。
        # 集計方法・色は①と同一の _render_rank_cards をそのまま再利用する。
        # 対象データは選択キャンペーンで既にフィルタ済みの df_c をそのまま使用し、
        # 独自のループ・再フィルタは行わない（グローバル①とローカル②③の意味を分離）。
        st.markdown(f"#### 🏢 キャンペーンサマリー　【{cpc_camp}】　総KW数：{len(df_c)}件")
        _render_rank_cards(df_c)
        st.markdown("---")
        disp_cols = [c for c in ["campaign_name","ad_group","keyword","ROAS","cost","sales","orders","avg_cpc","cpc_rank","cpc_action","cpc_delta","rec_cpc"] if c in df_c.columns]
        _rn = {"campaign_name":"キャンペーン名","ad_group":"広告グループ","keyword":"KWテキスト",
               "cost":"広告費","sales":"売上","orders":"購入数",
               "avg_cpc":"現在CPC","cpc_rank":"判定ランク","cpc_action":"推奨アクション",
               "cpc_delta":"変更幅","rec_cpc":"推奨CPC"}
        df_c = _cpc_apply_display_order(df_c, _RANK_ORDER)
        df_c.index = df_c.index + 1
        df_disp = df_c[df_c["cpc_delta"] != 0].copy()
        df_disp.index = range(1, len(df_disp) + 1)
        _d = df_disp[disp_cols].rename(columns=_rn).copy()
        if "広告費" in _d.columns: _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
        if "売上"   in _d.columns: _d["売上"]   = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
        if "ROAS"   in _d.columns: _d["ROAS"]   = _d["ROAS"].round(2)
        if "変更幅" in _d.columns: _d["変更幅"] = _d["変更幅"].apply(lambda x: f"+{x}円" if x > 0 else f"{x}円" if x < 0 else "±0円")
        if "現在CPC" in _d.columns: _d["現在CPC"] = _d["現在CPC"].apply(lambda x: f"¥{x:,.0f}" if x else "—")
        if "推奨CPC" in _d.columns: _d["推奨CPC"] = _d["推奨CPC"].apply(lambda x: f"¥{x:,.0f}" if x else "—")
        # ③ KW一覧（実行対象抽出のみ）。表示条件は以下2つを厳密AND評価する：
        # ①cpc_delta が数値としてnon-zero（NaN/文字列は数値0として扱い除外）
        # ②cpc_rank が判定保留・未確定・空ではない（確定済み状態のみ許可）
        # 文字列比較は行わず、フィルタは描画前に完結させる（描画後の除外は行わない）。
        # フィルタ結果が0件の場合も、ノイズ(±0・判定保留)を再度含める形での
        # 「完全開示」は行わない（安全な部分開示。対象なしの場合はその旨を表示する）。
        st.markdown("#### 📋 KW一覧（実行対象）")
        if df_c.empty:
            st.info("表示対象のキーワードがありません。")
        else:
            if "cpc_delta" in df_c.columns:
                _cd_num = pd.to_numeric(df_c["cpc_delta"], errors="coerce").fillna(0.0)
            else:
                _cd_num = pd.Series(0.0, index=df_c.index)
            _pending_vals = {"", "none", "nan", "n/a", "na", "pending", "保留", "判断保留"}
            def _cpc_is_pending(_v):
                if pd.isna(_v):
                    return True
                return str(_v).strip().lower() in _pending_vals
            if "cpc_rank" in df_c.columns:
                _rank_pending = df_c["cpc_rank"].apply(_cpc_is_pending)
            else:
                _rank_pending = pd.Series(False, index=df_c.index)
            _valid_mask = (_cd_num != 0) & (~_rank_pending)
            _kwl_target = df_c[_valid_mask].copy()
            if _kwl_target.empty:
                st.info("調整対象のキーワードはありません（すべて変更不要または判定保留）。")
            else:
                _kwl_cols = [c for c in ["keyword", "avg_cpc", "cpc_delta", "cpc_rank"] if c in _kwl_target.columns]
                _kwl = _kwl_target[_kwl_cols].rename(columns={
                    "keyword": "keyword", "avg_cpc": "CPC", "cpc_delta": "推奨調整額", "cpc_rank": "ランク",
                }).copy()
                if "CPC" in _kwl.columns:
                    _kwl["CPC"] = _kwl["CPC"].apply(lambda x: f"{x:,.0f}円" if x else "—")
                if "推奨調整額" in _kwl.columns:
                    _kwl["推奨調整額"] = pd.to_numeric(_kwl["推奨調整額"], errors="coerce").fillna(0.0).apply(
                        lambda x: f"+{int(x)}円" if x > 0 else f"{int(x)}円" if x < 0 else "±0円"
                    )
                _kwl.index = range(1, len(_kwl) + 1)
                st.dataframe(_kwl, use_container_width=True, height=460)
        st.markdown("---")
        _c1, _c2 = st.columns(2)
        with _c1:
            _dl_csv_adj = df_disp[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            _anls_save_cpc_change_history(df_disp[disp_cols].copy())
            st.download_button(f"📥 {cpc_camp}_CPC調整_実行用.csv", data=_dl_csv_adj,
                file_name=f"{cpc_camp}_CPC調整_実行用.csv", mime="text/csv", use_container_width=True)
        with _c2:
            _dl_csv_all = df_c[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(f"📥 {cpc_camp}_CPC調整表.csv", data=_dl_csv_all,
                file_name=f"{cpc_camp}_CPC調整表.csv", mime="text/csv", use_container_width=True)
    with _t_tab2:
        st.markdown("#### ⏱️ 期間別クイック分析")
        st.markdown("**📅 7日分析**")
        _anls_render_tab(dc_cpc, 7, "anls_cpc_kw_top7.json", "anls_cpc_kw_top7",
                          "キーワードCPC分析（7日窓）", "cpc_kw", "keyword", "cpc_change_history.json")
        st.markdown("---")
        st.markdown("**📅 30日分析**")
        _anls_render_tab(dc_cpc, 30, "anls_cpc_kw_top30.json", "anls_cpc_kw_top30",
                          "キーワードCPC分析（30日窓）", "cpc_kw", "keyword", "cpc_change_history.json")
        st.markdown("---")
        _anls_render_tab(dc_cpc, 7, "anls_cpc_kw.json", "anls_cpc_kw", "キーワードCPC分析", "cpc_kw", "keyword", "cpc_change_history.json")

def _render_pt_cpc_page(dc_pt, page_title: str, sel_key: str, hist_fname: str = ""):
    """商品ターゲ CPC調整ページ共通レンダラー（page_cpc()と同一ロジック・UI）"""
    _RANK_ORDER = ["SS+", "SS", "S", "A", "B", "C", "D", "即削除", "判断保留"]
    _RC = {
        "SS+": "#D69E2E", "SS": "#B7791F", "S": "#553C9A",
        "A":   "#2C7A7B", "B": "#2B6CB0", "C": "#C05621",
        "D":   "#C53030", "即削除": "#742A2A", "判断保留": "#4A5568",
    }
    _cond_bar([("CPC調整ルール", "適用"), ("対象", page_title)])
    render_logic_section(
        "📈 CPC調整ロジック",
        '''
<table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">
<thead>
  <tr style="background:#DBEAFE;">
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:15%;">ランク</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:45%;">判定条件</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:20%;">アクション</th>
    <th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:20%;">CPC変更幅</th>
  </tr>
</thead>
<tbody>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 1】 データ不足判定（最優先）
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#4A5568;">判断保留</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">広告費 &lt; ¥3,000 <b>かつ</b> 購入数 &lt; 4件</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">変更なし</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 2】 高実績ランク（購入数 ≥ 20件）
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#D69E2E;">SS+</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">購入数 ≥ 20件 <b>かつ</b> ROAS ≥ 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC上げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#276749;font-weight:700;">+5円</td>
  </tr>
  <tr style="background:#FFFBEB;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#B7791F;">SS</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">購入数 ≥ 20件 <b>かつ</b> 2.0 ≤ ROAS &lt; 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 3】 ROASベースランク
    </td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#553C9A;">S</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">ROAS ≥ 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC上げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#276749;font-weight:700;">+5円</td>
  </tr>
  <tr style="background:#F0FFF4;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2C7A7B;">A</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">3.0 ≤ ROAS &lt; 4.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2B6CB0;">B</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">1.8 ≤ ROAS &lt; 3.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C05621;">C</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">1.5 ≤ ROAS &lt; 1.8</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC下げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#C53030;font-weight:700;">−5円</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C53030;">D</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">ROAS &lt; 1.5</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC下げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#C53030;font-weight:700;">−10円</td>
  </tr>
  <tr style="background:#F1F5F9;">
    <td colspan="4" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">
      【STEP 4】 即削除判定（広告費過多 × 低ROAS）
    </td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#742A2A;">即削除</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">
      ROAS &lt; 0.8 <b>かつ</b> 広告費が閾値以上<br>
      <span style="font-size:.8rem;color:#718096;">
        売価 ≤¥1,500 → 広告費 ≥¥3,000 ／
        売価 ≤¥2,000 → 広告費 ≥¥4,000 ／
        売価 &gt;¥2,000 → 広告費 ≥¥5,000
      </span>
    </td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#742A2A;font-weight:700;">即削除</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">—</td>
  </tr>
</tbody>
</table>
<p style="font-size:.78rem;color:#718096;margin-top:10px;">
  ▶ 判定順序: STEP1（データ不足）→ STEP2（購入数優先） → STEP3（ROASベース） → STEP4（即削除）<br>
  ▶ 基本思想: ROASだけでなく、広告費と購入数を重視した複合判定
</p>''',
    )
    if dc_pt.empty:
        st.info("分析を実行してください。")
        return
    # ② 商品選択プルダウン（全商品 + データあり商品一覧）
    sel_options = ["全商品"] + [c for c in CAMPAIGNS if not dc_pt[dc_pt["campaign_theme"] == c].empty]
    _sc, _ = st.columns([3, 2])
    with _sc:
        cpc_camp = st.selectbox(f"商品選択（{page_title}）", sel_options,
                                label_visibility="visible", key=sel_key)
    if cpc_camp == "全商品":
        df_c = dc_pt.copy()
    else:
        df_c = dc_pt[dc_pt["campaign_theme"] == cpc_camp].copy()
    cnt = {r: int((df_c["cpc_rank"] == r).sum()) for r in _RANK_ORDER}
    st.markdown("---")
    kpi_rks = ["SS+", "SS", "S", "A", "B", "C", "D", "即削除"]
    kc_ = st.columns(len(kpi_rks))
    for _col, rk in zip(kc_, kpi_rks):
        bg_map = {
            "SS+":"#FFFFF0","SS":"#FEFCBF","S":"#E9D8FD","A":"#C6F6D5",
            "B":"#BEE3F8","C":"#FEEBC8","D":"#FED7D7","即削除":"#FED7D7",
        }
        _col.markdown(f'''<div class="kpi-card" style="background:{bg_map.get(rk,'#F4F6F8')};border-top:3px solid {_RC[rk]};">
            <div class="kpi-label">{rk}</div>
            <div class="kpi-value" style="color:{_RC[rk]};font-size:1.5rem;">{cnt[rk]}</div>
            <div class="kpi-sub">件</div></div>''', unsafe_allow_html=True)
    if cnt["判断保留"] > 0:
        st.caption(f"⏸ 判断保留: {cnt['判断保留']}件（広告費¥3,000未満 かつ 購入数4件未満）")
    # ── 本日調整対象ブロック ──────────────────────────────────
    _n_up   = int((df_c["cpc_delta"] > 0).sum())
    _n_down = int((df_c["cpc_delta"] < 0).sum())
    _n_adj  = _n_up + _n_down
    st.markdown("---")
    st.caption("📅 本日調整対象")
    _bc1, _bc2, _bc3 = st.columns(3)
    _bc1.markdown(f'''<div class="kpi-card" style="background:#E6FFFA;border-top:3px solid #276749;">
        <div class="kpi-label">🔺 CPC上げ</div>
        <div class="kpi-value" style="color:#276749;font-size:1.5rem;">{_n_up}</div>
        <div class="kpi-sub">件</div></div>''', unsafe_allow_html=True)
    _bc2.markdown(f'''<div class="kpi-card" style="background:#FFF5F5;border-top:3px solid #C53030;">
        <div class="kpi-label">🔻 CPC下げ</div>
        <div class="kpi-value" style="color:#C53030;font-size:1.5rem;">{_n_down}</div>
        <div class="kpi-sub">件</div></div>''', unsafe_allow_html=True)
    _bc3.markdown(f'''<div class="kpi-card" style="background:#EBF8FF;border-top:3px solid #2B6CB0;">
        <div class="kpi-label">📊 変更対象合計</div>
        <div class="kpi-value" style="color:#2B6CB0;font-size:1.5rem;">{_n_adj}</div>
        <div class="kpi-sub">件</div></div>''', unsafe_allow_html=True)
    st.markdown("---")
    disp_cols = [c for c in ["campaign_name","ad_group","asin","ROAS","cost","sales","orders","avg_cpc","cpc_rank","cpc_action","cpc_delta","rec_cpc"] if c in df_c.columns]
    _rn = {"campaign_name":"キャンペーン名","ad_group":"広告グループ","asin":"ASIN",
           "cost":"広告費","sales":"売上","orders":"購入数",
           "avg_cpc":"現在CPC","cpc_rank":"判定ランク","cpc_action":"推奨アクション",
           "cpc_delta":"変更幅","rec_cpc":"推奨CPC"}
    cat_t = pd.CategoricalDtype(categories=_RANK_ORDER, ordered=True)
    df_c["_r"] = df_c["cpc_rank"].astype(cat_t)
    df_c = df_c.sort_values(["_r","ROAS"], ascending=[True, False]).drop(columns=["_r"]).reset_index(drop=True)
    df_c.index = df_c.index + 1
    # ① 一覧テーブルは変更幅≠0（CPC上げ・CPC下げ）のみ表示
    df_disp = df_c[df_c["cpc_delta"] != 0].copy()
    df_disp.index = range(1, len(df_disp) + 1)
    _d = df_disp[disp_cols].rename(columns=_rn).copy()
    if "広告費" in _d.columns: _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
    if "売上"   in _d.columns: _d["売上"]   = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
    if "ROAS"   in _d.columns: _d["ROAS"]   = _d["ROAS"].round(2)
    if "変更幅" in _d.columns: _d["変更幅"] = _d["変更幅"].apply(lambda x: f"+{x}円" if x > 0 else f"{x}円" if x < 0 else "±0円")
    if "現在CPC" in _d.columns: _d["現在CPC"] = _d["現在CPC"].apply(lambda x: f"¥{x:,.0f}" if x else "—")
    if "推奨CPC" in _d.columns: _d["推奨CPC"] = _d["推奨CPC"].apply(lambda x: f"¥{x:,.0f}" if x else "—")
    def _cr(row):
        c = _RC.get(row.get("判定ランク", ""), "")
        return [f"color:{c};font-weight:700" if col == "判定ランク" else "" for col in row.index]
    if df_disp.empty:
        st.info("変更幅が発生するASINはありません（全件 現状維持 または 判断保留）。")
    else:
        st.dataframe(_d.style.apply(_cr, axis=1), use_container_width=True, height=460)
    # CSV: hist_fname指定時はHistory保存対象(df_disp)と完全同一DataFrameを出力
    _dl_fname = f"{cpc_camp}_{page_title}_CPC調整表.csv"
    if hist_fname:
        _dl_csv = df_disp[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        _anls_save_cpc_asin_history(df_disp[disp_cols].copy(), hist_fname)
        st.download_button(f"📥 {_dl_fname}", data=_dl_csv,
            file_name=_dl_fname, mime="text/csv")
    else:
        _dl_csv = df_c[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(f"📥 {_dl_fname}", data=_dl_csv,
            file_name=_dl_fname, mime="text/csv")


def page_cpc_product():
    _t1, _t2 = st.tabs(["CPC調整", "分析"])
    with _t1:
        _render_pt_cpc_page(dc_cpc_product, "商品CPC調整", "cpc_product_sel", "cpc_pt_m_history.json")
    with _t2:
        st.markdown("#### ⏱️ 期間別クイック分析")
        st.markdown("**📅 7日分析**")
        _anls_render_tab(
            st.session_state.get("df_cpc_product", pd.DataFrame()),
            7, "anls_cpc_pt_m_top7.json", "anls_cpc_pt_m_top7",
            "商品CPC分析（7日窓）", "cpc_asin", "asin", "cpc_pt_m_history.json")
        st.markdown("---")
        st.markdown("**📅 30日分析**")
        _anls_render_tab(
            st.session_state.get("df_cpc_product", pd.DataFrame()),
            30, "anls_cpc_pt_m_top30.json", "anls_cpc_pt_m_top30",
            "商品CPC分析（30日窓）", "cpc_asin", "asin", "cpc_pt_m_history.json")
        st.markdown("---")
        _anls_render_tab(
            st.session_state.get("df_cpc_product", pd.DataFrame()),
            7, "anls_cpc_pt_m.json", "anls_cpc_pt_m",
            "商品CPC分析", "cpc_asin", "asin", "cpc_pt_m_history.json")

def page_cpc_video():
    _t1, _t2 = st.tabs(["CPC調整", "分析"])
    with _t1:
        _render_pt_cpc_page(dc_cpc_video, "動画CPC調整", "cpc_video_sel", "cpc_pt_v_history.json")
    with _t2:
        st.markdown("#### ⏱️ 期間別クイック分析")
        st.markdown("**📅 7日分析**")
        _anls_render_tab(
            st.session_state.get("df_cpc_video", pd.DataFrame()),
            7, "anls_cpc_pt_v_top7.json", "anls_cpc_pt_v_top7",
            "動画CPC分析（7日窓）", "cpc_asin", "asin", "cpc_pt_v_history.json")
        st.markdown("---")
        st.markdown("**📅 30日分析**")
        _anls_render_tab(
            st.session_state.get("df_cpc_video", pd.DataFrame()),
            30, "anls_cpc_pt_v_top30.json", "anls_cpc_pt_v_top30",
            "動画CPC分析（30日窓）", "cpc_asin", "asin", "cpc_pt_v_history.json")
        st.markdown("---")
        _anls_render_tab(
            st.session_state.get("df_cpc_video", pd.DataFrame()),
            7, "anls_cpc_pt_v.json", "anls_cpc_pt_v",
            "動画CPC分析", "cpc_asin", "asin", "cpc_pt_v_history.json")


# ===================================================

# ============================================================
# 売れる予測KW TOP10 発見エンジン (_ddv4_) v5.1
# 需要(45) + 商品関連性(35) + 競争強度(15) + 未使用KWボーナス(5) = 100点
# 「今すぐAmazon検索語へ追加すべき有力KW」を抽出する実行ツール
# ============================================================

_DDV4_PRODUCTS = {
    "\u72ac\u7528\u4e73\u9178\u83cc (B0DJ8Q95XZ)": [
        "\u4e73\u9178\u83cc","\u8033\u6d3b","\u8033\u5185","\u5584\u7389\u83cc","\u4fbf","\u4fbf\u79d8","\u8edf\u4fbf","\u514d\u75ab","\u304a\u8179","\u6d88\u5316",
        "\u30d7\u30ed\u30d0\u30a4\u30aa\u30c6\u30a3\u30af\u30b9","\u8033\u5185\u74b0\u5883","\u6574\u8033","\u6d88\u5316\u5668"],
    "\u95a2\u7bc0\u30b5\u30dd\u30fc\u30c8 (B0DJ8QVCG1)": [
        "\u95a2\u7bc0","\u30b0\u30eb\u30b3\u30b5\u30df\u30f3","\u30b3\u30f3\u30c9\u30ed\u30a4\u30c1\u30f3","msm","\u8db3\u8170","\u30b7\u30cb\u30a2\u72ac","\u6b69\u884c",
        "\u8edf\u9aa8","\u95a2\u7bc0\u75db","\u8001\u72ac","\u8db3"],
    "\u30a2\u30a4\u30b1\u30a2 (B0DSP22H5G)": [
        "\u6d99\u3084\u3051","\u76ee","\u30eb\u30c6\u30a4\u30f3","\u30d6\u30eb\u30fc\u30d9\u30ea\u30fc","\u767d\u5185\u969c","\u8996\u529b",
        "\u76ee\u3084\u306b","\u773c","\u30a2\u30a4","\u6d99"],
    "\u30a2\u30df\u30ce\u9178\u30b7\u30e3\u30f3\u30d7\u30fc (B0GGGTYZTR)": [
        "\u30b7\u30e3\u30f3\u30d7\u30fc","\u654f\u611f\u808c","\u4f4e\u5c01\u6fc3","\u4fdd\u6e7f","\u304b\u3086\u307f","\u76ae\u819a","\u30a2\u30df\u30ce\u9178",
        "\u30d5\u30b1","\u6d88\u81ed","\u6bdb\u4e26\u307f","\u6d17\u6bdb","\u30b0\u30eb\u30fc\u30df\u30f3\u30b0","\u30dc\u30c7\u30a3"],
}

_DDV4_CATEGORY_TERMS = {
    "\u72ac\u7528\u4e73\u9178\u83cc (B0DJ8Q95XZ)":        ["\u72ac","\u30da\u30c3\u30c8","\u30b5\u30d7\u30ea","\u30b5\u30d7\u30ea\u30e1\u30f3\u30c8","\u5065\u5eb7","\u72ac\u7528"],
    "\u95a2\u7bc0\u30b5\u30dd\u30fc\u30c8 (B0DJ8QVCG1)":  ["\u72ac","\u30da\u30c3\u30c8","\u30b5\u30d7\u30ea","\u30b7\u30cb\u30a2","\u8001\u72ac","\u5065\u5eb7","\u72ac\u7528"],
    "\u30a2\u30a4\u30b1\u30a2 (B0DSP22H5G)":                ["\u72ac","\u30da\u30c3\u30c8","\u30b5\u30d7\u30ea","\u30b5\u30d7\u30ea\u30e1\u30f3\u30c8","\u5065\u5eb7","\u72ac\u7528"],
    "\u30a2\u30df\u30ce\u9178\u30b7\u30e3\u30f3\u30d7\u30fc (B0GGGTYZTR)": ["\u72ac","\u30da\u30c3\u30c8","\u30b7\u30e3\u30f3\u30d7\u30fc","\u30b0\u30eb\u30fc\u30df\u30f3\u30b0","\u30b1\u30a2","\u6d17","\u72ac\u7528"],
}

_DDV4_PRODUCT_ASINS = {
    "\u72ac\u7528\u4e73\u9178\u83cc (B0DJ8Q95XZ)":        "B0DJ8Q95XZ",
    "\u95a2\u7bc0\u30b5\u30dd\u30fc\u30c8 (B0DJ8QVCG1)": "B0DJ8QVCG1",
    "\u30a2\u30a4\u30b1\u30a2 (B0DSP22H5G)":               "B0DSP22H5G",
    "\u30a2\u30df\u30ce\u9178\u30b7\u30e3\u30f3\u30d7\u30fc (B0GGGTYZTR)": "B0GGGTYZTR",
}

_DDV4_PURCHASE_INTENT_WORDS = [
    "\u304a\u3059\u3059\u3081","\u4eba\u6c17","\u30e9\u30f3\u30ad\u30f3\u30b0","\u6bd4\u8f03","\u53e3\u30b3\u30df","\u52b9\u679c"]

# 競争強度ベーススコア (0-15スケール: 競争弱いほど高点)
_DDV4_COMP_BASE_V51 = {
    "very weak":   15,
    "weak":        12,
    "medium":       9,
    "strong":       5,
    "very strong":  2,
}


def _ddv4_norm_kw(x):
    if x is None: return ""
    import unicodedata
    x = unicodedata.normalize("NFKC", str(x)).lower()
    result = []
    for c in x:
        cp = ord(c)
        if 0x30A1 <= cp <= 0x30F6:
            result.append(chr(cp - 0x60))
        else:
            result.append(c)
    return " ".join("".join(result).split())

def _ddv4_compact_kw(kw):
    return "".join(_ddv4_norm_kw(kw).split())

def _ddv4_strip_particles(kw):
    import re
    return re.sub(r"(の|に|は|を|が|で|と|から|まで|より|へ|や|も)\s*", "", _ddv4_norm_kw(kw))

def _ddv4_is_excluded(kw, existing_set):
    if not existing_set: return False
    variants = {_ddv4_norm_kw(kw), _ddv4_compact_kw(kw), _ddv4_strip_particles(kw)}
    return bool(variants & existing_set)

def _ddv4_partial_used(kw, existing_set):
    if not existing_set: return False
    kn = _ddv4_norm_kw(kw)
    tokens = kn.split()
    if len(tokens) <= 1: return False
    for t in tokens:
        if len(t) >= 2 and any(t in e for e in existing_set):
            return True
    return False

def _ddv4_read_csv_bytes(raw):
    import pandas as pd, io
    for enc in ("utf-8-sig","utf-8","cp932","shift_jis"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except Exception:
            continue
    return pd.read_csv(io.BytesIO(raw), encoding="utf-8", errors="replace")

def _ddv4_find_col(df, cands):
    for c in df.columns:
        for cand in cands:
            if str(c).strip().lower() == cand.strip().lower():
                return c
    return None

def _ddv4_to_float(v):
    if v is None: return None
    try:
        return float(str(v).replace(",","").strip())
    except (ValueError, TypeError):
        return None

def _ddv4_load_keywords_csv(kw_file):
    try:
        raw = kw_file.read(); kw_file.seek(0)
        df = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]
        kw_col = _ddv4_find_col(df, [
            "Search Terms","SearchTerms","Keyword",
            "キーワード","検索語句","検索用語"])
        if kw_col is None and len(df.columns) > 0:
            kw_col = df.columns[0]
        if kw_col is None:
            return None, None, None, None, "Keyword列が見つかりません"
        sv_col  = _ddv4_find_col(df, ["SV","Search Volume","sv","search_volume"])
        rel_col = _ddv4_find_col(df, ["Relevancy","relevancy","Relevance","relevance","関連性"])
        df["_kw"] = df[kw_col].fillna("").astype(str)
        return df, kw_col, sv_col, rel_col, None
    except Exception as e:
        return None, None, None, None, str(e)

def _ddv4_load_amazon_search_csv(sf_file):
    if sf_file is None: return set()
    try:
        raw = sf_file.read(); sf_file.seek(0)
        df  = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]
        col = _ddv4_find_col(df, [
            "Customer Search Term","Search Term","検索用語",
            "Keyword","キーワード","SearchTerm"])
        if col is None and len(df.columns) > 0:
            col = df.columns[0]
        if col is None: return set()
        result = set()
        for kw in df[col].fillna("").astype(str).tolist():
            result.add(_ddv4_norm_kw(kw))
            result.add(_ddv4_compact_kw(kw))
            result.add(_ddv4_strip_particles(kw))
        return result
    except Exception:
        return set()

def _ddv4_load_asin_comp_dict(comp_file):
    try:
        raw = comp_file.read(); comp_file.seek(0)
        df  = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]
        asin_col = _ddv4_find_col(df, ["ASIN","asin","商品コード","ProductASIN"])
        if asin_col is None: asin_col = df.columns[0]
        str_col  = _ddv4_find_col(df, ["Strength","strength"])
        var_col  = _ddv4_find_col(df, ["Variations","variations","バリエーション"])
        rev_col  = _ddv4_find_col(df, ["Review Count","ReviewCount","レビュー数","reviewcount"])
        result = {}
        for _, row in df.iterrows():
            asin_raw = str(row[asin_col]).strip().upper()
            if not asin_raw or asin_raw.lower() in ("nan",""): continue
            s_raw = row[str_col] if str_col else None
            s_val = (str(s_raw).strip()
                     if s_raw is not None and str(s_raw).strip().lower() not in ("nan","")
                     else None)
            result[asin_raw] = {
                "strength":     s_val,
                "variations":   _ddv4_to_float(row[var_col]) if var_col else None,
                "review_count": _ddv4_to_float(row[rev_col]) if rev_col else None,
            }
        return result
    except Exception as e:
        return {"_error": str(e)}


# ─── スコア算出関数 v5.1 ─────────────────────────────────────

def _ddv4_sv_score_v51(sv):
    """① 需要スコア 0-45点"""
    try: v = float(str(sv).replace(",",""))
    except (ValueError, TypeError): return 10
    if v >= 10000: return 45
    if v >= 5000:  return 38
    if v >= 1000:  return 30
    if v >= 300:   return 20
    if v >= 100:   return 10
    return 3

def _ddv4_fit_score_v51(kw, product_label, relevancy_raw=None):
    """② 商品関連性 0-35点
    Relevancy(0-14) + 商品辞書(0-12) + カテゴリ語(0-7) + 購買意図(0-2)
    """
    kn = _ddv4_norm_kw(kw)
    score = 0
    # Relevancy (0-14)
    if relevancy_raw is not None:
        try:
            rel = float(str(relevancy_raw).replace("%","").strip())
            if rel > 1: rel = rel / 100.0
            score += min(14, round(rel * 14))
        except (ValueError, TypeError):
            pass
    # 商品辞書マッチ (0-12)
    prod_kws = _DDV4_PRODUCTS.get(product_label, [])
    matches  = sum(1 for w in prod_kws if _ddv4_norm_kw(w) in kn)
    score += min(12, matches * 6)
    # カテゴリ語マッチ (0-7)
    cat_kws     = _DDV4_CATEGORY_TERMS.get(product_label, [])
    cat_matches = sum(1 for w in cat_kws if _ddv4_norm_kw(w) in kn)
    score += min(7, cat_matches * 3)
    # 購買意図語 (0-2)
    for word in _DDV4_PURCHASE_INTENT_WORDS:
        if word in kn:
            score += 2
            break
    return min(35, score)

def _ddv4_comp_score_v51(asin_dict, product_asin):
    """③ 競争強度スコア 0-15点
    競争弱い=高点（参入しやすい） / 競争強い=低点（参入困難）
    RC: 単独加点禁止 / 単独減点禁止 / 参考表示のみ
    Keyword単位参照禁止 → ASIN単位で算出し全KWへ一律適用
    """
    if not asin_dict or not product_asin:
        return 9   # データなし=中程度
    entry = asin_dict.get(product_asin.upper())
    if not entry or not isinstance(entry, dict):
        return 9
    key  = str(entry.get("strength","")).strip().lower()
    base = _DDV4_COMP_BASE_V51.get(key, 9)
    # Variations補正 (±2): バリエーション少=競合多様化なし=余地あり
    var_adj = 0
    variations = entry.get("variations")
    if variations is not None:
        try:
            v = int(float(str(variations)))
            if   v <= 5:  var_adj =  2
            elif v <= 15: var_adj =  1
            elif v <= 30: var_adj =  0
            elif v <= 60: var_adj = -1
            else:         var_adj = -2
        except (ValueError, TypeError):
            pass
    # RC: 取得するがscoreには加算しない（表示用のみ）
    return max(0, min(15, base + var_adj))

def _ddv4_comp_label_v51(score):
    """競争強度ラベル"""
    if   score >= 13: return "低"
    elif score >= 7:  return "中"
    return "高"

def _ddv4_unused_bonus_v51(kw, existing_set):
    """④ 未使用KWボーナス 0-5点
    未使用: +5 / 部分使用: +2 / 使用中: 0
    """
    if not existing_set:
        return 5   # Amazon検索語CSV未投入=全KW未使用扱い
    if _ddv4_is_excluded(kw, existing_set):
        return 0   # 使用中
    if _ddv4_partial_used(kw, existing_set):
        return 2   # 部分使用
    return 5       # 未使用

def _ddv4_unused_label_v51(bonus):
    if bonus >= 5: return "未使用"
    if bonus >= 2: return "部分使用"
    return "使用中"

def _ddv4_make_reason_v51(s_demand, s_fit, s_comp, s_unused, u_label):
    """採用理由 最低3項目"""
    parts = []
    # 需要
    if   s_demand >= 38: parts.append("検索需要が非常に高い（SV>=5,000）")
    elif s_demand >= 30: parts.append("検索需要が高い（SV>=1,000）")
    elif s_demand >= 20: parts.append("検索需要がある（SV>=300）")
    elif s_demand >= 10: parts.append("検索需要が中程度（SV>=100）")
    else:                parts.append("検索需要が低い")
    # 商品関連性
    if   s_fit >= 28: parts.append("商品との関連性が非常に高い")
    elif s_fit >= 18: parts.append("商品との関連性が高い")
    elif s_fit >= 9:  parts.append("商品との関連性がある")
    else:             parts.append("商品との関連性が低い")
    # 競争強度
    lbl = _ddv4_comp_label_v51(s_comp)
    if   lbl == "低": parts.append("競争強度が比較的低い（参入しやすい）")
    elif lbl == "中": parts.append("競争強度は中程度")
    else:             parts.append("競争強度が高い（参入しにくい）")
    # 未使用
    if u_label == "未使用":
        parts.append("現在未使用KWである（今すぐ追加できる）")
    elif u_label == "部分使用":
        parts.append("部分的に使用中（拡張余地あり）")
    # 総合
    total = s_demand + s_fit + s_comp + s_unused
    if   total >= 80: parts.append("追加優先度が高い")
    elif total >= 65: parts.append("追加を検討すべきKWである")
    return " / ".join(parts)

def _ddv4_calculate_sellable_kw(cands_df, sv_col, rel_col, product_label,
                                  asin_dict, product_asin, existing_set):
    """売れる予測KW スコアリングエンジン v5.1
    需要(45)+商品関連性(35)+競争強度(15)+未使用ボーナス(5) = 100点
    RC: 単独加点禁止 / 単独減点禁止
    Keyword単位competitors参照禁止 → ASIN単位で全KW一律適用
    """
    import pandas as pd
    s_comp   = _ddv4_comp_score_v51(asin_dict, product_asin)
    comp_lbl = _ddv4_comp_label_v51(s_comp)
    results  = []
    for _, row in cands_df.iterrows():
        kw_raw  = str(row["_kw"])
        sv_val  = row.get(sv_col, 0)    if sv_col  and sv_col  in cands_df.columns else 0
        rel_val = row.get(rel_col, None) if rel_col and rel_col in cands_df.columns else None
        s_demand = _ddv4_sv_score_v51(sv_val)
        s_fit    = _ddv4_fit_score_v51(kw_raw, product_label, rel_val)
        s_unused = _ddv4_unused_bonus_v51(kw_raw, existing_set)
        u_label  = _ddv4_unused_label_v51(s_unused)
        final    = max(0, min(100, s_demand + s_fit + s_comp + s_unused))
        results.append({
            "_kw":        kw_raw,
            "_sv_raw":    sv_val,
            "_rel_raw":   rel_val,
            "_s_demand":  s_demand,
            "_s_fit":     s_fit,
            "_s_comp":    s_comp,
            "_s_unused":  s_unused,
            "未使用判定": u_label,
            "売れる予測スコア": final,
            "採用理由":   _ddv4_make_reason_v51(s_demand, s_fit, s_comp, s_unused, u_label),
        })
    return (
        pd.DataFrame(results)
        .sort_values("売れる予測スコア", ascending=False)
        .reset_index(drop=True)
    )



def _ddv4_render_sellable_top10():
    st.markdown("### \U0001f3af 売れる予測KW TOP10")
    st.caption("今すぐAmazon検索語へ追加すべき有力KWを抽出する実行ツール")
    st.markdown("---")

    st.markdown("##### \U0001f4cc 分析対象商品を選択")
    prod_opts  = ["― 選択してください ―"] + list(_DDV4_PRODUCTS.keys())
    prod_label = st.selectbox("商品選択", prod_opts, key="ddv4_prod",
                              label_visibility="collapsed")
    if prod_label == "― 選択してください ―":
        st.info("\U0001f4cc 分析対象商品を選択してください。")
        return
    st.success(f"✅ {prod_label}")
    st.markdown("")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### \U0001f4c4 DateDive Keywords CSV")
        ddv4_kw = st.file_uploader("keywords.csv", type="csv",
                                   key="ddv4_kw_csv", label_visibility="collapsed")
        if ddv4_kw: st.success(f"✅ {ddv4_kw.name}")
        else: st.caption("niche-XXXX-keywords.csv をアップロード（Search Terms / SV / Relevancy）")
    with c2:
        st.markdown("##### \U0001f4c4 DateDive Competitors CSV")
        ddv4_comp = st.file_uploader("competitors.csv", type="csv",
                                     key="ddv4_comp_csv", label_visibility="collapsed")
        if ddv4_comp: st.success(f"✅ {ddv4_comp.name}")
        else: st.caption("niche-XXXX-competitors.csv をアップロード（Strength / Variations / RC）")
    st.markdown("")
    st.markdown("##### \U0001f4c4 Amazon検索語CSV（現在使用中KW判定用）")
    ddv4_amz = st.file_uploader("Amazon検索語CSV", type="csv",
                                  key="ddv4_amz_csv", label_visibility="collapsed")
    if ddv4_amz: st.success(f"✅ {ddv4_amz.name}")
    else: st.caption("未投入の場合は全KWを未使用（+5点）で処理")
    st.markdown("")

    exec_btn = st.button("\U0001f3af 売れる予測KW TOP10を抽出", type="primary",
                         use_container_width=True, key="ddv4_exec_btn")
    if not exec_btn:
        return

    if ddv4_kw is None:
        st.error("❌ keywords.csv が未投入です"); return
    if ddv4_comp is None:
        st.error("❌ competitors.csv が未投入です"); return

    with st.spinner("keywords.csv 読み込み中..."):
        kw_df, kw_col, sv_col, rel_col, kw_err = _ddv4_load_keywords_csv(ddv4_kw)
    if kw_err: st.error(f"❌ {kw_err}"); return

    with st.spinner("Amazon検索語CSV 読み込み中..."):
        existing_set = _ddv4_load_amazon_search_csv(ddv4_amz)

    with st.spinner("competitors.csv 読み込み中..."):
        asin_dict = _ddv4_load_asin_comp_dict(ddv4_comp)
    if asin_dict.get("_error"):
        st.warning("competitors.csv 読み込みエラー（競争強度=中で続行）")
        asin_dict = {}

    product_asin = _DDV4_PRODUCT_ASINS.get(prod_label, "")

    with st.spinner("スコアリング中..."):
        scored = _ddv4_calculate_sellable_kw(
            kw_df, sv_col, rel_col, prod_label, asin_dict, product_asin, existing_set)

    top10     = scored.head(10).reset_index(drop=True)
    n_total   = len(scored)
    n_unused  = int((scored["未使用判定"] == "未使用").sum())
    avg_score = round(float(scored["売れる予測スコア"].mean()), 1) if not scored.empty else 0.0
    top_score = int(scored["売れる予測スコア"].max()) if not scored.empty else 0
    s_comp    = _ddv4_comp_score_v51(asin_dict, product_asin)
    comp_lbl  = _ddv4_comp_label_v51(s_comp)

    entry    = asin_dict.get(product_asin.upper(), {}) if product_asin else {}
    _str_disp= str(entry.get("strength","")).title() if isinstance(entry, dict) and entry.get("strength") else "取得不可"
    _var_disp= f"{int(entry.get('variations',0))}件" if isinstance(entry, dict) and entry.get("variations") is not None else "取得不可"
    _rc_disp = f"{int(entry.get('review_count',0)):,}" if isinstance(entry, dict) and entry.get("review_count") is not None else "取得不可"

    st.markdown("---")
    _k1,_k2,_k3,_k4,_k5 = st.columns(5)
    kpi(_k1,"\U0001f3af","売れる予測KW数", f"{n_total}件", "スコア算出済全KW","#F3ECFF","#6B46C1")
    kpi(_k2,"\U0001f4a1","未使用KW数",     f"{n_unused}件","今すぐ追加できるKW","#EAF7EF","#2F855A")
    kpi(_k3,"\U0001f3c6","最高スコア",     f"{top_score}点","TOP1のスコア","#EAF2FF","#3B82F6")
    kpi(_k4,"\U0001f4ca","平均スコア",     f"{avg_score}点","全KW平均","#F4F6F8","#718096")
    kpi(_k5,"\U0001f4a5","市場競争度",     comp_lbl,        f"Strength={_str_disp}","#FFFAF0","#D97706")
    st.markdown("")

    _pcol = "#C53030" if comp_lbl == "高" else "#2F855A" if comp_lbl == "低" else "#718096"
    st.markdown(
        f"<div style='background:#F0FFF4;border:1px solid #9AE6B4;"
        f"border-left:4px solid #38A169;border-radius:8px;padding:12px 16px;margin-bottom:12px;'>"
        f"<div style='font-weight:700;font-size:.88rem;color:#22543D;margin-bottom:6px;'>"
        f"\U0001f3ea 競合情報 — {prod_label}（ASIN: {product_asin}）</div>"
        f"<div style='display:flex;gap:24px;flex-wrap:wrap;font-size:.84rem;'>"
        f"<span><b style='color:#276749;'>Strength:</b> {_str_disp}</span>"
        f"<span><b style='color:#276749;'>Variations:</b> {_var_disp}</span>"
        f"<span><b style='color:#276749;'>Review Count:</b> {_rc_disp}"
        f"<span style='color:#718096;font-size:.78rem;'>（参考表示のみ・スコア非寄与）</span></span>"
        f"<span><b style='color:#276749;'>市場競争度:</b> "
        f"<b style='color:{_pcol};'>{comp_lbl}</b>（{s_comp}/15点）</span>"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    _logic = (
        "<table style='width:100%;border-collapse:collapse;font-size:.82rem;'>"
        "<thead><tr style='background:#DBEAFE;'>"
        "<th style='padding:6px 10px;border:1px solid #BFDBFE;width:22%;'>スコア軸</th>"
        "<th style='padding:6px 10px;border:1px solid #BFDBFE;width:9%;'>配点</th>"
        "<th style='padding:6px 10px;border:1px solid #BFDBFE;'>算出方法</th>"
        "</tr></thead><tbody>"
        "<tr style='background:#EAF2FF;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#3B82F6;'>① 需要</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-45点</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "SV&gt;=10k:45 / &gt;=5k:38 / &gt;=1k:30 / &gt;=300:20 / &gt;=100:10 / &lt;100:3</td></tr>"
        "<tr style='background:#EAF7EF;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2F855A;'>② 商品関連性</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-35点</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "Relevancy(0-14)+商品辞書(0-12)+カテゴリ(0-7)+購買意図(0-2)</td></tr>"
        "<tr style='background:#FFFAF0;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#D97706;'>③ 競争強度</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-15点</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "Strengthベース(VW15/W12/M9/S5/VS2)+Variations補正(±2) RC単独加減点禁止</td></tr>"
        "<tr style='background:#FFF0F5;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#9B2C2C;'>④ 未使用ボーナス</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-5点</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "未使用:+5 / 部分使用:+2 / 使用中:0</td></tr>"
        "<tr><td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>売れる予測スコア</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-100点</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "需要+商品関連性+競争強度+未使用ボーナス → スコア降順TOP10を出力</td></tr>"
        "</tbody></table>"
    )
    render_logic_section("\U0001f4ca 売れる予測KW スコアロジック（v5.1）", _logic)

    _cond_bar([
        ("対象商品", prod_label[:20]),
        ("ASIN",      product_asin or "未設定"),
        ("全KW数",    f"{n_total:,}件"),
        ("未使用KW",  f"{n_unused}件"),
        ("市場競争度", comp_lbl),
    ])
    st.markdown("---")

    st.markdown("**\U0001f4cb Amazon小分類広告 検索語登録用（コピーして貼り付け）**")
    kw_list_text = "\n".join(top10["_kw"].astype(str).tolist())
    st.code(kw_list_text, language=None)

    st.markdown("##### \U0001f4cb 売れる予測KW TOP10")
    disp = pd.DataFrame(index=range(1, len(top10)+1))
    disp["売れる予測KW"]    = top10["_kw"].astype(str).values
    disp["Search Volume"]   = top10["_sv_raw"].values
    disp["売れる予測スコア"] = top10["売れる予測スコア"].values
    disp["未使用判定"]      = top10["未使用判定"].values
    disp["採用理由"]        = top10["採用理由"].astype(str).values
    disp.index.name = "順位"
    st.dataframe(disp, use_container_width=True)

    dl_csv = (disp.reset_index()
                  .to_csv(index=False, encoding="utf-8-sig")
                  .encode("utf-8-sig"))
    st.download_button("\U0001f4e5 売れる予測KW_TOP10.csv", data=dl_csv,
                       file_name=f"売れる予測KW_{prod_label[:10]}.csv",
                       mime="text/csv", use_container_width=True)

    with st.expander("\U0001f50d スコア内訳（TOP10）", expanded=False):
        dbg = pd.DataFrame({
            "順位":           range(1, len(top10)+1),
            "Keyword":        top10["_kw"].astype(str).values,
            "SV":             top10["_sv_raw"].values,
            "需要(0-45)":     top10["_s_demand"].values,
            "関連性(0-35)":   top10["_s_fit"].values,
            "競争強度(0-15)": top10["_s_comp"].values,
            "未使用(0-5)":    top10["_s_unused"].values,
            "スコア":         top10["売れる予測スコア"].values,
            "未使用判定":     top10["未使用判定"].values,
        }).set_index("順位")
        st.dataframe(dbg, use_container_width=True)

    with st.expander(f"\U0001f4c3 全スコア一覧（{n_total}件）", expanded=False):
        all_disp = pd.DataFrame({
            "順位":          range(1, len(scored)+1),
            "売れる予測KW":  scored["_kw"].astype(str).values,
            "SV":            scored["_sv_raw"].values,
            "需要":          scored["_s_demand"].values,
            "関連性":        scored["_s_fit"].values,
            "競争強度":      scored["_s_comp"].values,
            "未使用":        scored["_s_unused"].values,
            "スコア":        scored["売れる予測スコア"].values,
            "未使用判定":    scored["未使用判定"].values,
        }).set_index("順位")
        st.dataframe(all_disp, use_container_width=True)
        dl_all = (all_disp.reset_index()
                          .to_csv(index=False, encoding="utf-8-sig")
                          .encode("utf-8-sig"))
        st.download_button("\U0001f4e5 全KWスコア.csv", data=dl_all,
                           file_name=f"全KWスコア_{prod_label[:10]}.csv",
                           mime="text/csv", use_container_width=True)



def _render_pt_page(session_key, is_add, camp_label, selectbox_key, hist_fname: str = ""):
    """商品ターゲ追加/削除 共通レンダラー"""
    df_all = st.session_state.get(session_key, pd.DataFrame())

    # ① 条件バー
    if is_add:
        _cond_bar([
            ("注文数",   "≥ 3"),
            ("クリック数", "≥ 5"),
            ("広告費",   "≥ ¥300"),
            ("売上",     "≥ 売価×2"),
            ("ROAS",     "≥ 2.0"),
            ("対象",     camp_label),
        ])
        render_logic_section(
            f"[+] {camp_label}追加 判定ロジック",
            '<table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">'
            '<thead><tr style="background:#DBEAFE;">'
            '<th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:30%;">項目</th>'
            '<th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:70%;">内容</th>'
            '</tr></thead><tbody>'
            '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">対象データ</td></tr>'
            f'<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">分析対象</td>'
            f'<td style="padding:6px 10px;border:1px solid #BFDBFE;">{camp_label}（ASINターゲティング）</td></tr>'
            '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">信頼度フィルター</td></tr>'
            '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">最低条件</td>'
            '<td style="padding:6px 10px;border:1px solid #BFDBFE;">注文数 ≥ 3件 / クリック数 ≥ 5回 / 広告費 ≥ ¥300</td></tr>'
            '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">採用条件</td></tr>'
            '<tr style="background:#EAF7EF;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2F855A;">✅ 追加対象</td>'
            '<td style="padding:6px 10px;border:1px solid #BFDBFE;">売上 ≥ 売価 × 2 <b>かつ</b> ROAS ≥ 2.0</td></tr>'
            '</tbody></table>'
            '<p style="font-size:.78rem;color:#718096;margin-top:10px;">▶ 売れているASINターゲに予算を集中して利益を最大化する</p>'
        )
    else:
        _cond_bar([
            ("広告費",   "≥ 売価×2"),
            ("ROAS",     "< 0.8"),
            ("対象",     camp_label),
        ])
        render_logic_section(
            f"[x] {camp_label}削除 判定ロジック",
            '<table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">'
            '<thead><tr style="background:#DBEAFE;">'
            '<th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:30%;">項目</th>'
            '<th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:70%;">内容</th>'
            '</tr></thead><tbody>'
            '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">対象データ</td></tr>'
            f'<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">分析対象</td>'
            f'<td style="padding:6px 10px;border:1px solid #BFDBFE;">{camp_label}（ASINターゲティング）</td></tr>'
            '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">削除条件</td></tr>'
            '<tr style="background:#FFF5F5;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C53030;">[x] 削除対象</td>'
            '<td style="padding:6px 10px;border:1px solid #BFDBFE;">広告費 ≥ 売価 × 2 <b>かつ</b> ROAS &lt; 0.8</td></tr>'
            '</tbody></table>'
            '<p style="font-size:.78rem;color:#718096;margin-top:10px;">▶ 売価の2倍以上広告費を使ってもROASが低いASINターゲは利益を生まない</p>'
        )
    st.markdown("")

    # ② 商品選択
    _c1, _c2 = st.columns([3, 2])
    with _c1:
        sel = st.selectbox(
            "商品選択",
            ["全商品"] + CAMPAIGNS,
            label_visibility="visible",
            key=selectbox_key,
        )
    df_view = df_all.copy()
    if sel != "全商品" and "campaign_theme" in df_view.columns:
        df_view = df_view[df_view["campaign_theme"] == sel].copy()

    n_rows  = len(df_view)
    avg_r   = round(df_view["ROAS"].mean(),   2) if n_rows > 0 and "ROAS"   in df_view.columns else 0.0
    avg_ord = round(df_view["orders"].mean(),  1) if n_rows > 0 and "orders" in df_view.columns else 0.0
    avg_cst = round(df_view["cost"].mean(),    0) if n_rows > 0 and "cost"   in df_view.columns else 0.0

    # ③ KPIカード（フィルタ後）
    if is_add:
        k1, k2, k3, k4 = st.columns(4)
        kpi(k1, "✅", "追加候補数",  f"{n_rows}件",          "ROAS≥2.0",   "#EAF7EF", "#2F855A")
        kpi(k2, "\U0001f4ca", "平均ROAS",   f"{avg_r}",      "追加候補",    "#EAF2FF", "#3B82F6")
        kpi(k3, "\U0001f4e6", "平均注文数", f"{avg_ord}件",   "追加候補",    "#FFFFF0", "#D69E2E")
        kpi(k4, "\U0001f4b8", "平均広告費", f"¥{int(avg_cst):,}", "追加候補", "#F0FFF4", "#276749")
    else:
        k1, k2, k3 = st.columns(3)
        kpi(k1, "\U0001f5d1", "削除候補数", f"{n_rows}件",          "ROAS<0.8",  "#FFF5F5", "#C53030")
        kpi(k2, "\U0001f4ca", "平均ROAS",   f"{avg_r}",              "削除候補",  "#FEE2E2", "#C53030")
        kpi(k3, "\U0001f4b8", "平均広告費", f"¥{int(avg_cst):,}",  "削除候補",  "#F4F6F8", "#718096")
    st.markdown("")

    # ④ 件数バッジ
    badge_color = "#2F855A" if is_add else "#C53030"

    badge_label = "追加対象件数" if is_add else "削除対象件数"
    st.markdown(
        f'<div class="count-badge" style="border-left-color:{badge_color};">'
        f'{badge_label}: <b style="font-size:1.1rem;color:{badge_color};">{n_rows}件</b>'
        f'　<span style="color:#718096;font-size:.8rem;">商品: {sel}</span></div>',
        unsafe_allow_html=True,
    )

    if df_view.empty:
        msg = (f"追加候補の{camp_label}はありません。（条件: 注文≥3 / クリック≥5 / 広告費≥¥300 / 売上≥売価×2 / ROAS≥2.0）"
               if is_add else
               f"削除候補の{camp_label}はありません。（条件: 広告費≥売価×2 かつ ROAS<0.8）")
        st.info(msg)
        return

    # ⑤ 詳細テーブル
    if is_add:
        def _reason(row):
            rs = []
            roas = row.get("ROAS", 0); sales = row.get("sales", 0)
            orders = row.get("orders", 0); clicks = row.get("clicks", 0)
            if roas >= 4.0: rs.append(f"ROASが高い({roas:.1f}倍)")
            elif roas >= 2.0: rs.append(f"ROAS良好({roas:.1f}倍)")
            if sales >= 10000: rs.append(f"売上実績が十分ある(¥{int(sales):,})")
            elif sales > 0: rs.append(f"売上あり(¥{int(sales):,})")
            if orders >= 10: rs.append(f"注文実績が十分ある({int(orders)}件)")
            elif orders >= 3: rs.append(f"注文実績あり({int(orders)}件)")
            if clicks >= 20: rs.append(f"クリック多数({int(clicks)}回)")
            rs += ["商品展開候補", "予算追加候補"]
            return " / ".join(rs[:4])
        reason_col = "採用理由"
        _disp_cols = ["campaign_name","ad_group","asin","orders","clicks","cost","sales","ROAS",reason_col]
        hdr = f"##### ✅ {camp_label}追加詳細テーブル"
    else:
        def _reason(row):
            rs = []
            cost = row.get("cost", 0); roas = row.get("ROAS", 0); orders = row.get("orders", 0)
            if cost >= 10000: rs.append(f"広告費消化が大きい(¥{int(cost):,})")
            else: rs.append(f"広告費≥売価×2(¥{int(cost):,})")
            if roas < 0.3: rs.append(f"ROASが著しく低い({roas:.2f}倍)")
            elif roas < 0.8: rs.append(f"ROASが低い({roas:.2f}倍)")
            if orders == 0: rs.append("注文0件")
            elif orders < 3: rs.append(f"注文{int(orders)}件のみ")
            rs += ["費用対効果が悪い", "利益貢献がない", "削除優先度が高い"]
            return " / ".join(rs[:4])
        reason_col = "削除理由"
        _disp_cols = ["campaign_name","ad_group","asin","cost","sales","ROAS",reason_col]
        hdr = f"##### \U0001f5d1 {camp_label}削除詳細テーブル"

    st.markdown(hdr)
    _df = df_view.copy()
    _df[reason_col] = _df.apply(_reason, axis=1)
    _disp = [c for c in _disp_cols if c in _df.columns or c == reason_col]
    _rn = {"campaign_name":"キャンペーン名","ad_group":"広告グループ","asin":"ASIN",
           "clicks":"クリック数","orders":"注文数","cost":"広告費","sales":"売上"}
    _show = _df[[c for c in _disp if c in _df.columns]].rename(columns=_rn).copy()
    _show.index = _show.index + 1
    if "広告費" in _show.columns: _show["広告費"] = _show["広告費"].apply(lambda x: f"¥{x:,.0f}")
    if "売上"   in _show.columns: _show["売上"]   = _show["売上"].apply(lambda x: f"¥{x:,.0f}")
    if "ROAS"   in _show.columns: _show["ROAS"]   = _show["ROAS"].round(2)
    st.dataframe(_show, use_container_width=True)

    # ⑥ CSV
    _action = "追加" if is_add else "削除"
    _ctype  = "商品" if "_m_" in selectbox_key else "動画"
    _fname  = f"{_ctype}{_action}_{sel}.csv"
    _dl = _df[[c for c in _disp if c in _df.columns]].rename(columns=_rn).to_csv(
        index=False, encoding="utf-8-sig").encode("utf-8-sig")
    if is_add and hist_fname:
        _asin_add_hist_cols = [c for c in ["campaign_name", "ad_group", "asin",
                                            "orders", "clicks", "cost", "sales", "ROAS"]
                                if c in _df.columns]
        _anls_save_asin_add_history(_df[_asin_add_hist_cols].copy(), hist_fname)
        st.download_button(f"\U0001f4e5 {_fname}", data=_dl,
            file_name=_fname, mime="text/csv", use_container_width=True)
    else:
        st.download_button(f"\U0001f4e5 {_fname}", data=_dl,
            file_name=_fname, mime="text/csv", use_container_width=True)


def page_pt_add_manual():
    _t1, _t2 = st.tabs(["追加候補", "分析"])
    with _t1:
        _render_pt_page("df_pt_add_m", True,  "商品", "pt_add_m_sel", "product_add_history.json")
    with _t2:
        _anls_render_tab(
            st.session_state.get("df_pt_add_m", pd.DataFrame()),
            7, "anls_pt_add_m.json", "anls_pt_add_m",
            "商品追加分析", "asin_add", "asin", "product_add_history.json")

def page_pt_del_manual():
    _render_pt_page("df_pt_del_m", False, "商品", "pt_del_m_sel")

def page_pt_add_video():
    _t1, _t2 = st.tabs(["追加候補", "分析"])
    with _t1:
        _render_pt_page("df_pt_add_v", True,  "動画", "pt_add_v_sel", "video_add_history.json")
    with _t2:
        _anls_render_tab(
            st.session_state.get("df_pt_add_v", pd.DataFrame()),
            7, "anls_pt_add_v.json", "anls_pt_add_v",
            "動画追加分析", "asin_add", "asin", "video_add_history.json")

def page_pt_del_video():
    _render_pt_page("df_pt_del_v", False, "動画", "pt_del_v_sel")



def page_dd_v4():
    _ddv4_render_sellable_top10()


def page_download():
    st.markdown("### 📥 ダウンロード")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**📦 全候補 勝ちKW（一括ZIP）**")
        st.caption(f"{nw}件 — ROAS≥2.0")
        if not dw.empty:
            st.download_button("📥 全候補_ZIP", data=all_zip(dw),
                file_name="all_win_kw.zip", mime="application/zip", use_container_width=True)
    with c2:
        st.empty()
    st.markdown("")
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**🚫 削除用KW（キャンペーン別ZIP）**")
        st.caption(f"{len(dd)}件 — 広告費≥売価×2 かつ ROAS<0.8")
        if not dd.empty:
            st.download_button("📥 削除用KW_ZIP", data=del_camp_zip(dd),
                file_name="del_kw.zip", mime="application/zip", use_container_width=True)
    with c4:
        st.markdown("**📈 キーワードCPC調整（全キャンペーン ZIP）**")
        st.caption("STEP1-4 判定ランク付きCSV")
        if not dc_cpc.empty:
            st.download_button("📥 キーワードCPC調整_ZIP", data=cpc_camp_zip(dc_cpc),
                file_name="cpc_adjust.zip", mime="application/zip", use_container_width=True)


def page_manual():
    st.markdown("### 📖 ANIHA Amazon広告分析ツール — 取扱説明書 Ver.71")

    # ── 概要 ──────────────────────────────────────────────────────────
    with st.expander("📌 概要", expanded=True):
        st.markdown("""
**ANIHA Amazon広告分析ツール** は、Amazon SP広告レポートを読み込み、
追加・削除・CPC調整の候補を自動抽出するANIHA専用ツールです。

| 機能 | 内容 |
|---|---|
| 📋 キーワード追加 | 成果KWを抽出しマニュアル広告追加候補を表示 |
| 🚫 キーワード削除 | 利益毀損KWを停止候補として抽出 |
| 📈 キーワードCPC調整 | 既存マニュアルKWの入札最適化 |
| ➕ 商品追加 | 成果ASINを商品広告へ追加候補として抽出 |
| 🗑️ 商品削除 | 成果の出ていない商品を停止候補として抽出 |
| 📹 動画追加 | 成果ASINを動画広告へ追加候補として抽出 |
| 📹 動画削除 | 成果の出ていない動画広告を停止候補として抽出 |
| 🎯 商品CPC調整 | 商品広告の入札最適化 |
| 📹 動画CPC調整 | 動画広告の入札最適化 |
| 🧹 オート除外KW（キーワード/商品/動画） | オート広告で利益毀損している項目を停止候補として抽出 |
| 📊 DateDive売れる予測KW | スコアリングによる有力KW抽出 |
""")

    # ── サイドバー構成 ─────────────────────────────────────────────────
    with st.expander("🗂️ サイドバー構成"):
        st.markdown("""
```
追加
├ キーワード   → キーワード追加
├ 商品         → 商品追加
└ 動画         → 動画追加

削除
├ キーワード   → キーワード削除
├ 商品         → 商品削除
└ 動画         → 動画削除

CPC調整
├ キーワード   → キーワードCPC調整
├ 商品         → 商品CPC調整
└ 動画         → 動画CPC調整

オート除外KW
├ キーワード   → オートKW削除
├ 商品         → オート商品削除
└ 動画         → オート動画削除

DateDive売れる予測KW
ダウンロード
取扱説明書
```
""")

    # ── キーワード追加 ────────────────────────────────────────────────
    with st.expander("📋 キーワード追加"):
        st.markdown("""
**① この機能の目的**

オート広告（自動ターゲティング）で既に成果が確認できている検索語句を、手動広告（マニュアルキャンペーン・部分一致）へ
追加するための候補として抽出します。抽出された語句はAmazon広告への手動登録用としてコピー・CSVダウンロードできます。

---

**② いつ使うか**

オート広告の運用実績が一定期間たまり、「オートで売れている検索語句を手動広告に昇格させたい」タイミングで使用します。
抽出後は、実際にAmazon広告側でキーワードを追加してから一定期間（後述）運用し、「分析」タブで効果測定を行う、という
2段階の使い方をする機能です。

---

**③ 必要なCSV**

**候補抽出用（アプリ上部でアップロード）**

| 項目 | 内容 |
|---|---|
| レポート名 | Amazon検索語句レポート（画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つに1件のみアップロード） |
| 必須列 | 検索用語（カスタマーの検索用語）／キャンペーン名／売上／広告費／ターゲティング |
| 任意列 | 商品購入数（注文数）／クリック数／インプレッション数（無いとROASのみで判定、CVRは計算されません） |
| 取得方法 | Amazon広告管理画面の「検索語句レポート」をCSV出力 |
| 注意点 | 「検索用語」列・「ターゲティング」列のいずれかが無いとエラーで停止します |

**効果測定用（「分析」タブでアップロード）**

| 項目 | 内容 |
|---|---|
| レポート名 | 同じく検索用語レポート（キーワード追加を反映した後の期間で再出力） |
| 必須列 | 検索用語／キャンペーン名／売上／広告費 |
| 任意列 | 注文数／クリック数（無いと注文数・CVR・平均CPCベースの理由やアクションは正しく算出されません） |
| 対象期間 | 画面の案内文の通り、追加候補を反映してから**30日間**のレポート |

---

**④ 操作手順**

1. 画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つにCSVを1件だけアップロードする
2. 「🚀 分析開始」ボタンを押す（サイドバーの各機能に共通する最初の処理です）
3. サイドバーの「➕ キーワード追加」→「キーワード」を開く
4. 「追加候補」タブで、必要であればキャンペーンを絞り込む（初期値: 全キャンペーン）
5. 「該当件数」「Amazon広告登録用KW一覧」「KW詳細テーブル」を確認する
6. KW一覧をコピーし、Amazon広告管理画面で手動キーワード（部分一致）として追加する
   （「📥 {キャンペーン}_キーワード追加候補.csv」ボタンからCSVとしてもダウンロード可能。このタブを開いた時点で
   候補リストが自動的に履歴として保存されます）
7. Amazon広告側でキーワードを追加した後、30日間運用する
8. 30日後、同じ形式の検索用語レポートを再度出力する
9. 「分析」タブに切り替え、「比較用 30日レポートCSVをアップロード」欄にアップロードする
10. 「🔍 分析実行」ボタンを押す

---

**⑤ 分析ロジック**

**（A）追加候補の抽出条件**

対象は「キャンペーン名にオート／autoを含む」行の検索用語のみです。以下の順で絞り込みます。

| 除外・抽出ステップ | 内容 |
|---|---|
| 除外① | 既にマニュアル広告へ完全一致で登録済みの検索語句 |
| 除外② | 既存の登録語句に部分一致で含まれる（カバーされる）検索語句 |
| 除外③ | ブランドワードを含む検索語句 |
| 除外④ | 商品コードのような文字列 |
| 除外⑤ | タイトル文字列のような長い語句 |
| 集計 | 残った検索語句を正規化キー単位でキャンペーンテーマごとに集計（売上・広告費・注文数・クリック数・インプレッションを合算、ROAS・CVRを算出） |
| 価格マスタ判定 | キャンペーンテーマが売価マスタに登録されていない場合は対象外 |

**採用条件**（両方を満たす場合のみ候補に採用）

| 条件 | 閾値 |
|---|---|
| 売上 | ≥ 売価 × 2 |
| ROAS | ≥ 2.0 |

**信頼度フィルター**（データ量が少ない語句を除外。採用条件を満たした後にさらに適用）

| 条件 | 閾値 |
|---|---|
| 注文数 | ≥ 3件（注文数列がある場合のみ） |
| クリック数 | ≥ 5回（クリック数列がある場合のみ） |
| 広告費 | ≥ ¥300 |

最後に「同一意図KW統合」を行い、語順・表記ゆれ（全角半角・カナひら等）が同じ検索語句は代表1件に集約します。

**（B）分析タブでの効果測定ロジック**

アップロードした効果測定用CSVを検索語句単位で集計（売上・広告費を合算し、注文数・クリック数があれば合算してROAS・CVR・
平均CPCを算出）し、候補抽出時点の実績（Before）と、正規化した検索語句をキーに突き合わせます（After）。

判定は原則ROASの変化率で行います。

| Before ROASの状態 | 判定 |
|---|---|
| Before・After ともに0 | 変化なし |
| Before が0（After はプラス） | 改善 |
| 変化率の絶対値が3%未満 | 変化なし |
| 変化率が+3%以上 | 改善 |
| 変化率が−3%以下 | 悪化 |

---

**⑥ 分析結果の見方**

分析実行後、以下が表示されます。

| 表示項目 | 内容 |
|---|---|
| サマリーカード | 分析対象件数／🟢改善件数／🔴悪化件数／🟡変化なし件数／改善率 |
| キャンペーン別サマリー | キャンペーンごとの改善・悪化・変化なし件数、改善率、ROAS変化、CVR変化、CPC変化 |
| 対象一覧 | 検索語句ごとに🟢🔴🟡の判定アイコンを表示。「▶ 詳細」を開くと売上・広告費・ROAS・CVR・注文数・クリックのBefore→After数値、判定理由、広告運用で触るべき項目が表示される |

「判定理由」は、ROASの上昇/低下、平均CPCの上昇/低下/維持、注文数の増減、広告費が増えたのに売上が伸びていない場合の
指摘、を実際の数値の大小比較のみで表示します。「広告運用で触るべき項目」は、改善時は「CPCを維持」「予算増額を検討」、
悪化時は「CPCを下げる」（広告費増加かつ注文数が増えていない場合はさらに「検索語句レポート確認」「不要ターゲット停止候補」）、
変化なし時は「現状維持」「1週間様子を見る」を、判定結果に応じて表示します。

---

**⑦ 分析結果を保存**

分析結果表示後に「💾 分析結果を保存」ボタンを押すと、以下が保存されます。

| 保存される値 | 内容 |
|---|---|
| 保存日時・種別・比較期間 | いつ・何の分析を・何日分で行ったか |
| Before件数／一致件数 | 抽出対象件数と、After CSVと突き合わせられた件数 |
| 改善／悪化／変化なし件数・改善率 | 判定結果の集計 |
| 対象キャンペーン一覧 | 分析対象に含まれるキャンペーンテーマ |
| 売上・広告費・注文数・ROAS・平均CPC・クリック数の合計（Before/After） | 実数値ベースでBefore/Afterを再現するための集計値 |

---

**⑧ 保存済み分析履歴**

「📂 保存済み分析履歴」を開くと、保存するたびに分析結果がカード形式で蓄積されていきます。

- 最新の保存結果が一番上に表示され、自動的に展開されます
- カードには、タイトル（分析日・比較期間・対象キャンペーン数）、総合評価（🟢🔴🟡＋改善率＋星評価）、Before/After
  （売上・広告費・注文数・ROAS・平均CPCの実数値）、なぜこうなったか、広告運用で触るべき項目、サマリー、対象キャンペーン、
  前回保存との比較、履歴推移（改善率の時系列グラフ）が表示されます
- 本アップデートより前に保存された履歴には実数値が含まれないため、その場合はBefore/After・理由・アクションが
  対象件数ベースの簡易表示になります

---

**⑨ 注意事項**

- 「分析」タブを実行するには、先に「追加候補」タブを一度開いて候補リストの履歴を保存しておく必要があります。
  履歴が無い状態で分析を実行すると「履歴がありません。先に「追加候補タブ → CSVをダウンロード」してください。」と表示されます
- 効果測定用CSVに検索用語・キャンペーン名・売上・広告費のいずれかの列が見つからない場合はエラーで停止します
- 効果測定用CSVの広告費が0円以下の行は集計対象から除外されます
- 信頼度フィルター（注文数≥3件・クリック数≥5回・広告費≥¥300）は、現在の実装ではコード内固定値であり、
  画面上に変更用の入力欄はありません
- キャンペーンテーマが売価マスタに未登録の場合、そのキャンペーンの検索語句は候補・分析のいずれにも含まれません

---

**⑩ FAQ**

**Q. 追加候補が0件になる**
A. 信頼度フィルター（注文数≥3件・クリック数≥5回・広告費≥¥300）と採用条件（売上≥売価×2 かつ ROAS≥2.0）を
同時に満たすオート広告の検索語句が無いか、対象キャンペーンのキャンペーンテーマが売価マスタに登録されていない
可能性があります。

**Q. 「分析」タブで「履歴がありません」と表示される**
A. 「追加候補」タブをまだ開いていません。「追加候補」タブを開くと候補リストが自動的に履歴として保存されます。

**Q. 「分析」タブで「Afterデータが取得できませんでした」と表示される**
A. アップロードした効果測定用CSVに検索用語・キャンペーン名・売上・広告費のいずれかの列が見つからないか、
該当行の広告費が0円以下です。
""")

    # ── キーワード削除 ────────────────────────────────────────────────
    with st.expander("🚫 キーワード削除"):
        st.markdown("""
**① この機能の目的**

利益を毀損している検索語句を抽出し、停止候補として表示します。マニュアルキャンペーンで広告費を使っているのに
成果が出ていない検索語句を検出し、除外キーワード登録の判断材料を提供する機能です。

---

**② いつ使うか**

マニュアルキャンペーンの運用実績が一定たまり、「広告費を使っているのに売上が伸びていない検索語句を止めたい」
タイミングで使用します。

---

**③ 必要なCSV**

| 項目 | 内容 |
|---|---|
| レポート名 | Amazon検索語句レポート（画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つ。キーワード追加など他機能と共通） |
| 必須列 | 検索用語（カスタマーの検索用語）／キャンペーン名／売上／広告費／ターゲティング |
| 任意列 | 商品購入数（注文数）／クリック数／インプレッション数 |
| 取得方法 | Amazon広告管理画面の「検索語句レポート」をCSV出力 |
| 注意点 | 「検索用語」列・「ターゲティング」列のいずれかが無いとエラーで停止します |

---

**④ 操作手順**

1. 画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つにCSVを1件だけアップロードする
2. 「🚀 分析開始」ボタンを押す（サイドバーの各機能に共通する最初の処理です）
3. サイドバーの「🚫 キーワード削除」→「キーワード」を開く
4. 必要であればキャンペーンを絞り込む（初期値: 全キャンペーン）
5. 「削除対象件数」「削除対象KW一覧」「削除KW詳細テーブル」を確認する
6. KW一覧をコピーし、Amazon広告管理画面で完全一致の除外キーワードとして登録する
   （CSVが必要な場合は、サイドバーの「📥 ダウンロード」ページにある「🚫 削除用KW（キャンペーン別ZIP）」から取得できます。
   このページ自体にはCSVダウンロードボタンはありません）

---

**⑤ 分析ロジック**

**対象データ**

マニュアルキャンペーン（キャンペーン名に「オート」「auto」を含まない行）の検索語句データが対象です。
ASIN形式・category形式の語句はあらかじめ除外され、通常の検索語句のみが対象になります
（「オート除外KW」「商品追加」「動画追加」とは別の集計です）。

**削除条件**（両方を同時に満たす場合に削除候補）

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ 売価 × 2 |
| ROAS | < 0.8 |

**除外ルール**

「キーワード追加」の追加候補（勝ちKW）と重複する検索語句は、削除対象から除外されます（有望なKWを誤って
削除しないための保護処理です）。また、キャンペーンテーマが売価マスタに未登録の場合、そのキャンペーンの
検索語句は削除候補の判定から除外されます。

この機能には効果測定（Before/After比較）の仕組みはありません。削除条件は抽出時点の実績のみで判定されます。

---

**⑥ 分析結果の見方**

| 表示項目 | 内容 |
|---|---|
| 削除対象件数 | 削除条件に一致した件数（画面上部のバッジ表示） |
| 削除対象KW一覧 | コピー用のテキストブロック（Amazon広告への除外キーワード登録用） |
| 削除KW詳細テーブル | KW／キャンペーン／ROAS／広告費／売上を、ROASの低い順に表示 |

---

**⑦ 分析結果を保存**

この機能には分析結果を保存するボタンや処理はありません。表示されるのはその時点の抽出結果のみで、
「キーワード追加」のような保存・履歴機能はありません。

---

**⑧ 保存済み分析履歴**

この機能には保存済み履歴を閲覧する機能はありません。削除候補のCSVが必要な場合は、サイドバーの
「📥 ダウンロード」ページにある「🚫 削除用KW（キャンペーン別ZIP）」から取得してください。

---

**⑨ 注意事項**

- 対象データはマニュアルキャンペーン（オートを含まない）の検索語句です。画面上のロジック説明パネルには
  「分析対象: オート広告の検索語句のみ」という記載がありますが、実際の処理ではオートキャンペーンを含む行は
  除外されており、マニュアルキャンペーンの検索語句が対象です
- 信頼度フィルター（注文数・クリック数などの最低件数条件）はこの機能には適用されません。広告費とROASの
  条件のみで判定されます
- 「キーワード追加」の追加候補と重複する検索語句は、削除対象から自動的に除外されます
- キャンペーンテーマが売価マスタに未登録の場合、そのキャンペーンの検索語句は削除候補に含まれません
- Amazon広告側での実際の除外キーワード登録は、この画面では行われません。表示された一覧を元に手動で
  操作してください

---

**⑩ FAQ**

**Q. 削除対象キーワードはありません、と表示される**
A. 広告費≥売価×2 かつ ROAS<0.8 を満たすマニュアルキャンペーンの検索語句が無いか、対象キャンペーンの
キャンペーンテーマが売価マスタに登録されていない可能性があります。

**Q. 「キーワード追加」で候補に挙がっている語句が削除候補にも出てくると思ったが出てこない**
A. 仕様です。「キーワード追加」の追加候補（勝ちKW）と重複する検索語句は、削除対象から自動的に除外されます。

**Q. この画面からCSVをダウンロードしたい**
A. このページ自体にはCSVダウンロードボタンはありません。サイドバーの「📥 ダウンロード」ページにある
「🚫 削除用KW（キャンペーン別ZIP）」から取得できます。
""")

    # ── キーワードCPC調整─────────────────────────────────────────
    with st.expander("📈 キーワードCPC調整"):
        st.markdown("""
**① この機能の目的**

既存マニュアルキーワードの入札額（CPC）を最適化します。ROAS・購入数・広告費の実績からランクを判定し、
CPCを上げる／下げる／現状維持／即削除のいずれかを提案します。

---

**② いつ使うか**

マニュアルキャンペーンのキーワードにある程度の運用実績がたまり、「入札額を今の実績に合わせて見直したい」
タイミングで使用します。

---

**③ 必要なCSV**

**CPC調整の判定用（アプリ上部でアップロード）**

| 項目 | 内容 |
|---|---|
| レポート名 | Amazon検索語句レポート（画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つ。キーワード追加・削除と共通） |
| 必須列 | キャンペーン名／売上／広告費／ターゲティング |
| 任意列 | 商品購入数（注文数）／クリック数／広告グループ名 |
| 取得方法 | Amazon広告管理画面の「検索語句レポート」をCSV出力 |

**効果測定用（「分析」タブでアップロード）**

| 項目 | 内容 |
|---|---|
| レポート名 | 同じく検索語句レポート（CPC変更を反映した後の期間で再出力） |
| 必須列 | キャンペーン名／売上／広告費 |
| 必須列（いずれか） | キーワードテキスト／ターゲティング（どちらも無いと「Afterデータが取得できませんでした」と表示されます） |
| 任意列 | 注文数／クリック数／広告グループ名（広告グループ名が無いとキャンペーン名＋キーワードのみで前後を突き合わせます） |
| 対象期間 | 画面の案内文の通り「7日固定」のレポート |

---

**④ 操作手順**

1. 画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つにCSVを1件だけアップロードする
2. 「🚀 分析開始」ボタンを押す
3. サイドバーの「📈 CPC調整」→「キーワード」を開く
4. 「CPC調整」タブで、必要であれば商品（キャンペーンテーマ）を選択する（初期値: 全商品）
5. ランク別件数カード（SS+／SS／S／A／B／C／D／即削除）と「本日調整対象」（CPC上げ／CPC下げ／変更対象合計）を確認する
6. 詳細テーブル（変更幅が±0円ではない行のみ表示）で、判定ランク・現在CPC・推奨CPCを確認する
7. 「📥 {商品}_CPC調整_実行用.csv」（変更対象のみ。ダウンロードボタンが表示された時点で履歴が自動保存されます）
   または「📥 {商品}_CPC調整表.csv」（全件）をダウンロードし、Amazon広告管理画面でCPCを更新する
8. CPC変更後、7日間運用する
9. 「分析」タブに切り替え、「比較用 7日レポートCSVをアップロード」欄に効果測定用CSVをアップロードする
10. 「🔍 分析実行」ボタンを押す

---

**⑤ 分析ロジック**

**（A）CPC調整の対象データとランク判定**

対象はキャンペーン名が「SP広告（マニュアル）」に該当し、「商品ターゲ」「動画ターゲ」を含まないキャンペーンの
キーワードデータです。さらに以下を除外して集計します。

| 除外ステップ | 内容 |
|---|---|
| オート除外 | キャンペーン名（またはターゲティングタイプ列）に「オート」「auto」を含む行 |
| Product Targeting除外 | キーワードテキストがASIN形式・category形式・complement・substituteに該当する行 |
| 空欄除外 | キーワードテキストが空欄の行 |
| ブランドKW除外 | 「アニハ」「あには」「アニは」を含む語句 |

残った行をキャンペーンテーマ×正規化キーワード単位で集計（売上・広告費を合算、注文数・クリック数があれば合算）し、
キャンペーンテーマに売価マスタの登録が無い場合は対象外にします。

ランクは以下の順序（STEP1→STEP2→STEP3→STEP4）で判定します。

| ランク | 条件 | アクション | 変更幅 |
|---|---|---|---|
| 判断保留 | 広告費 < ¥3,000 **かつ** 購入数 < 4件 | 変更なし | ±0円 |
| SS+ | 購入数 ≥ 20 **かつ** ROAS ≥ 4.0 | CPC上げ | +5円 |
| SS | 購入数 ≥ 20 **かつ** ROAS ≥ 2.0 | 現状維持 | ±0円 |
| S | ROAS ≥ 4.0 | CPC上げ | +5円 |
| A | ROAS ≥ 3.0 | 現状維持 | ±0円 |
| B | 1.8 ≤ ROAS < 3.0 | 現状維持 | ±0円 |
| C | 1.5 ≤ ROAS < 1.8 | CPC下げ | −5円 |
| D | ROAS < 1.5 | CPC下げ | −10円 |
| 即削除 | 広告費 ≥ 閾値 **かつ** ROAS < 0.8 | 即削除 | ±0円（削除のためCPC変更なし） |

**即削除閾値:** 売価 ≤¥1,500 → 広告費≥¥3,000 ／ 売価 ≤¥2,000 → 広告費≥¥4,000 ／ 売価 >¥2,000 → 広告費≥¥5,000

推奨CPCは「現在CPC（広告費÷クリック数）＋変更幅」で算出されます（最低¥1）。

**（B）分析タブでの効果測定ロジック**

アップロードした効果測定用CSVを、キャンペーン名＋広告グループ＋キーワード（広告グループ列が無い場合はキャンペーン名＋
キーワード）をキーに集計し、CPC調整タブで保存された実行用データ（Before）と突き合わせます（After）。

判定はROASの変化率で行います。

| Before ROASの状態 | 判定 |
|---|---|
| Before・After ともに0 | 変化なし |
| Before が0（After はプラス） | 改善 |
| 変化率の絶対値が3%未満 | 変化なし |
| 変化率が+3%以上 | 改善 |
| 変化率が−3%以下 | 悪化 |

---

**⑥ 分析結果の見方**

| 表示項目 | 内容 |
|---|---|
| サマリーカード | 分析対象件数／🟢改善件数／🔴悪化件数／🟡変化なし件数／改善率 |
| キャンペーン別サマリー | キャンペーンごとの改善・悪化・変化なし件数、改善率、ROAS変化、CVR変化、CPC変化 |
| 対象一覧 | キーワードごとに🟢🔴🟡の判定アイコンを表示。「▶ 詳細」で売上・広告費・ROAS・CVR・注文数・クリックの
Before→After数値、判定理由、広告運用で触るべき項目が確認できる |

「判定理由」「広告運用で触るべき項目」は、「③キーワード追加」と同じロジック（ROAS・平均CPC・注文数・広告費の
実際の数値の大小比較のみ）で表示されます。

---

**⑦ 分析結果を保存**

分析結果表示後に「💾 分析結果を保存」ボタンを押すと、以下が保存されます。

| 保存される値 | 内容 |
|---|---|
| 保存日時・種別・比較期間 | いつ・何の分析を・何日分で行ったか |
| Before件数／一致件数 | CPC調整対象件数と、After CSVと突き合わせられた件数 |
| 改善／悪化／変化なし件数・改善率 | 判定結果の集計 |
| 対象キャンペーン一覧 | 分析対象に含まれるキャンペーンテーマ |
| 売上・広告費・注文数・ROAS・平均CPC・クリック数の合計（Before/After） | 実数値ベースでBefore/Afterを再現するための集計値 |

---

**⑧ 保存済み分析履歴**

「📂 保存済み分析履歴」を開くと、保存するたびに分析結果がカード形式で蓄積されていきます。最新の保存結果が
一番上に表示され、自動的に展開されます。カードには、タイトル（分析日・比較期間・対象キャンペーン数）、総合評価
（🟢🔴🟡＋改善率＋星評価）、Before/After（売上・広告費・注文数・ROAS・平均CPCの実数値）、なぜこうなったか、
広告運用で触るべき項目、サマリー、対象キャンペーン、前回保存との比較、履歴推移（改善率の時系列グラフ）が
表示されます。本アップデートより前に保存された履歴には実数値が含まれないため、その場合は対象件数ベースの
簡易表示になります。

---

**⑨ 注意事項**

- 「分析」タブを実行するには、先に「CPC調整」タブを一度開いて実行用データの履歴を保存しておく必要があります。
  履歴が無い状態で分析を実行すると「履歴がありません。先に「CPC調整タブ → 実行用CSVをダウンロード」してください。」
  と表示されます
- 画面上部の条件バーには「最小クリック数: 5回」と表示されますが、実際のランク判定（判断保留・SS+〜D・即削除）は
  広告費・購入数・ROASのみで行われ、クリック数は判定条件として使用されていません
- 詳細テーブルには、変更幅が±0円（現状維持・判断保留）の行は表示されません。全ランクの件数はランク別カードで
  確認できます
- キャンペーンテーマが売価マスタに未登録の場合、そのキャンペーンのキーワードは対象に含まれません
- 分析タブでの前後の突き合わせは、広告グループ名の列がある場合はキャンペーン名＋広告グループ＋キーワードの
  組み合わせで行われます

---

**⑩ FAQ**

**Q. 「分析」タブで「履歴がありません」と表示される**
A. 「CPC調整」タブをまだ開いていません。「CPC調整」タブを開くと実行用データが自動的に履歴として保存されます。

**Q. 詳細テーブルに一部のキーワードしか表示されない**
A. 仕様です。詳細テーブルには変更幅が±0円ではない行（CPC上げ・CPC下げ・即削除）のみが表示されます。
現状維持・判断保留を含めた全件数はランク別カードで確認できます。

**Q. 「分析」タブで「Afterデータが取得できませんでした」と表示される**
A. アップロードした効果測定用CSVにキャンペーン名・売上・広告費のいずれかの列が見つからないか、
キーワードテキスト・ターゲティングのいずれの列も見つからない可能性があります。
""")

    # ── オート除外KW ──────────────────────────────────────────────────
    with st.expander("🧹 オート除外KW"):
        st.markdown("""
**① この機能の目的**

オート広告（自動ターゲティング）で利益を毀損している項目を検出し、停止候補として
「キーワード」「商品」「動画」の3ページに分けて表示します。

サイドバーの「🧹 オート除外KW」内に、キーワード・商品・動画の3つのボタンがあります。

---

**② いつ使うか**

オート広告の運用実績が一定たまり、「オートで広告費を使っているのに成果が出ていない検索語句・ASINを
除外したい」タイミングで使用します。

---

**③ 必要なCSV**

| 項目 | 内容 |
|---|---|
| レポート名 | Amazon検索語句レポート（画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つ。キーワード追加・削除・CPC調整と共通） |
| 必須列 | 検索用語（カスタマーの検索用語）／キャンペーン名／売上／広告費／ターゲティング |
| 任意列 | 商品購入数（注文数）／広告グループ名 |
| 取得方法 | Amazon広告管理画面の「検索語句レポート」をCSV出力 |
| 注意点 | 「検索用語」列・「ターゲティング」列のいずれかが無いとエラーで停止します |

---

**④ 操作手順**

1. 画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つにCSVを1件だけアップロードする
2. 「🚀 分析開始」ボタンを押す
3. サイドバーの「🧹 オート除外KW」から「キーワード」「商品」「動画」のいずれかを開く
4. 必要であればキャンペーンで絞り込む（初期値: 全キャンペーン）
5. 除外候補件数・除外対象一覧（コピー用）・詳細テーブルを確認する
6. 一覧をコピー、またはページ内の「📥」ダウンロードボタンでCSVを取得し、Amazon広告管理画面で
   除外キーワード／除外商品ターゲティングとして登録する

---

**⑤ 分析ロジック**

**集計の元データ**

「検索用語レポート」内のオートキャンペーン行から、「検索用語（カスタマーの検索用語）」列の
内容によって3種類に振り分けます。

| 検索用語の内容 | 振り分け先 |
|---|---|
| 通常の検索語句（日本語など） | 📄 キーワード |
| ASIN形式（例: B0XXXXXXXXX） | 🎯 商品 |
| category形式（例: category:〜） | 🎬 動画 |

商品・動画に振り分けられた項目は、商品ターゲティング広告・動画広告のオートキャンペーン自体から
集計したASIN実績（「🎯 オート商品削除」「🎥 オート動画削除」と同じ抽出元）とも合算されて表示されます。

**削除条件（両方を同時に満たす場合に削除候補）**

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ 売価 × 2 |
| ROAS | ≤ 0.8 |

**除外条件**

| 除外対象 | 内容 |
|---|---|
| マニュアル広告に完全一致登録済みの語句／ASIN | 重複のため除外 |
| 未分類キャンペーン | 売価が特定できないため除外 |

---

**⑥ 分析結果の見方**

**各ページの表示内容**

| ページ | 表示内容 |
|---|---|
| 📄 キーワード | 通常検索語句のみ（ASIN・category形式は含まれません） |
| 🎯 商品 | ASIN形式のみ |
| 🎬 動画 | category形式のみ |

各ページに「キャンペーン」フィルター（全キャンペーン＋各キャンペーン）があり、
件数カード・除外対象一覧（コピー用）・詳細テーブル・CSVダウンロードボタンを表示します。

---

**⑦ 分析結果を保存**

この機能には分析結果を保存するボタンや処理はありません。表示されるのはその時点の抽出結果のみです。

---

**⑧ 保存済み分析履歴**

この機能には保存済み履歴を閲覧する機能はありません。候補が必要な場合は、各ページ内のコピー用一覧または
CSVダウンロードボタンからその都度取得してください。

---

**⑨ 注意事項**

- 「キーワード」ページに表示されるのは通常の検索語句のみで、ASIN形式・category形式の語句は「商品」
  「動画」ページ側に振り分けられます
- 「商品」「動画」ページの候補は、検索語句がASIN／category形式だったものに加えて、商品ターゲティング広告・
  動画広告のオートキャンペーン自体のASIN実績も合算されています
- キャンペーンテーマが売価マスタに未登録の場合、そのキャンペーンの検索語句・ASINは候補に含まれません
- Amazon広告側での実際の除外登録は、この画面では行われません。表示された一覧を元に手動で操作してください
- サイドバーの「📥 ダウンロード」ページには、この機能専用のZIP出力はありません。CSVは各ページ内の
  ダウンロードボタンから個別に取得してください

---

**⑩ FAQ**

**Q. 除外候補がありません、と表示される**
A. オート広告の検索語句・ASINのうち、広告費≥売価×2 かつ ROAS≤0.8 を満たすものが無いか、対象キャンペーンの
キャンペーンテーマが売価マスタに登録されていない可能性があります。

**Q. 「キーワード」ページにASINのような文字列が出てくると思ったが出てこない**
A. 仕様です。ASIN形式・category形式の検索語句は自動的に「商品」「動画」ページ側に振り分けられます。

**Q. この機能の結果を後から見返したい**
A. この機能には保存・履歴機能がありません。必要なタイミングでその都度CSVをダウンロードしてください。
""")

    # ── 売れる予測KW ──────────────────────────────────────────────────
    with st.expander("📊 DateDive 売れる予測KW"):
        st.markdown("""
**① この機能の目的**

DateDive（外部キーワードリサーチツール）のデータをもとに、対象商品ごとに今後Amazon検索語へ追加すべき
有力キーワード候補をスコアリングし、TOP10として抽出します。アプリ上部の「7日比較CSV」「30日比較CSV」「その他CSV」を
使う他機能とは独立した、別系統のツールです。

---

**② いつ使うか**

DateDiveなどの外部リサーチデータを使って、新しく追加すべき検索キーワードの優先順位を付けたいタイミングで
使用します。

---

**③ 必要なCSV**

| CSV | 必須／任意 | 内容 |
|---|---|---|
| DateDive Keywords CSV（例: niche-XXXX-keywords.csv） | 必須 | DateDiveからエクスポート。Search Terms（またはKeyword／キーワード／検索語句／検索用語）／SV（Search Volume）／Relevancy列を使用。列名が見つからない場合、1列目がキーワード列として使われます |
| DateDive Competitors CSV（例: niche-XXXX-competitors.csv） | 必須 | DateDiveからエクスポート。ASIN／Strength／Variations／Review Count列を使用 |
| Amazon検索語CSV | 任意 | 他機能と同じ検索用語レポート。未使用キーワード判定にのみ使用します。未投入の場合は全キーワードが「未使用」として扱われます |

---

**④ 操作手順**

1. サイドバーの「📊 DateDive売れる予測KW」を開く
2. 「商品選択」で分析対象商品を選ぶ（選択できる商品は固定4種類）
3. 「DateDive Keywords CSV」欄にkeywords.csvをアップロードする
4. 「DateDive Competitors CSV」欄にcompetitors.csvをアップロードする
5. 必要であれば「Amazon検索語CSV」欄に検索用語レポートをアップロードする（未使用判定用・任意）
6. 「🎯 売れる予測KW TOP10を抽出」ボタンを押す
7. KPIカード・競合情報・スコアロジック表・条件バーを確認する
8. 「Amazon小分類広告 検索語登録用」欄のKW一覧をコピーし、Amazon広告管理画面へ登録する
9. 「売れる予測KW TOP10」テーブルでスコア・未使用判定・採用理由を確認する
10. 必要であれば「📥 売れる予測KW_TOP10.csv」、または「📄 全スコア一覧」を開いて「📥 全KWスコア.csv」をダウンロードする

---

**⑤ 分析ロジック**

**スコア配点（合計100点、内訳は以下の計算式で算出）**

| 項目 | 配点 | 算出方法 |
|---|---|---|
| ① 需要 | 0〜45点 | SV（検索ボリューム）に応じた段階評価: ≥10,000→45点／≥5,000→38点／≥1,000→30点／≥300→20点／≥100→10点／それ未満→3点 |
| ② 商品関連性 | 0〜35点 | Relevancy値（0〜14点）＋商品キーワード辞書との一致（1件一致ごとに+6点、最大12点）＋カテゴリ語との一致（1件ごとに+3点、最大7点）＋購買意図語（「おすすめ」「人気」等）が含まれれば+2点 |
| ③ 競争強度 | 0〜15点 | 対象商品のASIN1件についてCompetitors CSVのStrengthを基準点に変換（Very Weak=15／Weak=12／Medium=9／Strong=5／Very Strong=2）し、Variations件数で補正（≤5件:+2／≤15件:+1／≤30件:±0／≤60件:−1／61件以上:−2）。対象ASINのデータが無い場合は中間値9点 |
| ④ 未使用ボーナス | 0〜5点 | Amazon検索語CSVと照合し、完全一致（表記ゆれ・助詞の違いを吸収）すれば「使用中」で0点、単語の一部が既存語に含まれれば「部分使用」で+2点、どちらにも該当しなければ「未使用」で+5点。Amazon検索語CSV未投入の場合は全キーワードが+5点（未使用扱い） |

①〜④の合計（0〜100点）で「売れる予測スコア」を算出し、降順に並べ替えてTOP10を抽出します。競争強度スコアは
キーワードごとではなく、選択した商品のASIN1件に対して算出され、その値が全キーワードへ一律に適用されます。

---

**⑥ 分析結果の見方**

| 表示項目 | 内容 |
|---|---|
| KPIカード | 売れる予測KW数（全体）／未使用KW数／最高スコア／平均スコア／市場競争度 |
| 競合情報カード | Strength／Variations／Review Count（参考表示のみ・スコアには影響しません）／市場競争度とスコア |
| KW一覧（コピー用） | TOP10の検索語句のみのテキスト |
| 売れる予測KW TOP10テーブル | 検索語句／Search Volume／売れる予測スコア／未使用判定／採用理由 |
| スコア内訳（TOP10） | TOP10の需要／関連性／競争強度／未使用の内訳点数 |
| 全スコア一覧 | TOP10に入らなかったものを含む全キーワードのスコア内訳 |

「採用理由」は、需要・商品関連性・競争強度・未使用状況の各段階と、合計スコアに応じた優先度コメントを
組み合わせた自動生成テキストです。

---

**⑦ 分析結果を保存**

この機能には分析結果を保存するボタンや処理はありません。TOP10・全件スコアはその都度CSVでダウンロードして
ください。

---

**⑧ 保存済み分析履歴**

この機能には保存済み履歴を閲覧する機能はありません。

---

**⑨ 注意事項**

- 分析対象商品として選択できるのは「犬用乳酸菌」「関節サポート」「アイケア」「アミノ酸シャンプー」の
  4商品に固定されています。他機能で使われる売価マスタ・キャンペーン一覧とは別の商品リストであり、
  対象外の商品はこの機能では分析できません
- 競争強度スコアはキーワードごとではなく、選択した商品のASIN1件に対して算出され、全キーワードへ
  一律に適用されます
- Review Countは画面に表示されますが、スコア計算には使用されません（参考表示のみ）
- Amazon検索語CSVを投入しない場合、未使用ボーナスは全キーワードに一律+5点が付与されます
- この機能はアプリ上部の「7日比較CSV」「30日比較CSV」「その他CSV」「🚀 分析開始」とは独立した別系統の処理です
  （分析開始を行っていなくても利用できます）

---

**⑩ FAQ**

**Q. 「keywords.csv が未投入です」と表示される**
A. DateDive Keywords CSVは必須です。アップロードしてから「🎯 売れる予測KW TOP10を抽出」を押してください。

**Q. 分析したい商品がプルダウンにない**
A. 現在この機能で選択できる商品は「犬用乳酸菌」「関節サポート」「アイケア」「アミノ酸シャンプー」の
4商品に固定されています。

**Q. 未使用判定がすべて「未使用」になる**
A. Amazon検索語CSVを投入していない場合の仕様です（全キーワードが未使用扱いになります）。既に使用中の
キーワードを判定したい場合は、Amazon検索語CSVもアップロードしてください。
""")

    # ── ダウンロード ──────────────────────────────────────────────────
    with st.expander("📥 ダウンロード"):
        st.markdown("""
**① この機能の目的**

分析結果をキャンペーン単位でまとめてZIP形式でダウンロードできるページです。「キーワード追加」「キーワード削除」
「キーワードCPC調整」の3つの結果を、キャンペーンごとに分かれたCSVとしてまとめて取得できます。

---

**② いつ使うか**

キーワード追加候補・削除候補・CPC調整結果を、複数キャンペーン分まとめてファイルで欲しいタイミングで
使用します（各機能の画面内でも個別にダウンロードできますが、このページでは一括取得できます）。

---

**③ ダウンロードできるファイル**

| ボタン | ファイル名 | 内容 | 表示条件 |
|---|---|---|---|
| 📥 全候補_ZIP | all_win_kw.zip | キーワード追加候補（ROAS≥2.0）をキャンペーンごとに分けたCSV | 候補が1件以上ある場合のみ表示 |
| 📥 削除用KW_ZIP | del_kw.zip | キーワード削除候補（広告費≥売価×2 かつ ROAS<0.8）をキャンペーンごとに分けたCSV | 候補が1件以上ある場合のみ表示 |
| 📥 キーワードCPC調整_ZIP | cpc_adjust.zip | キーワードCPC調整表（STEP1〜STEP4判定ランク付き、全ランク収録）をキャンペーンごとに分けたCSV | 対象データが1件以上ある場合のみ表示 |

いずれも、対象データが0件のキャンペーンではボタン自体が表示されません。

---

**④ 操作手順**

1. 画面上部の「📅 7日比較CSV」「📅 30日比較CSV」「📊 その他CSV」のいずれか1つにCSVを1件だけアップロードする（他機能と共通）
2. 「🚀 分析開始」ボタンを押す
3. サイドバーの「📥 ダウンロード」を開く
4. 各ボタン上のキャプション（対象件数・条件）を確認する
5. 必要なZIPボタンを押してダウンロードする

---

**⑤ 各ファイルの内容**

| ZIP | 収録ファイル名 | 列 | 利用する機能 |
|---|---|---|---|
| all_win_kw.zip | winner_{キャンペーン名}.csv（キャンペーンごと） | 検索語句（ROAS降順） | 「キーワード追加」で抽出される候補と同じデータ |
| del_kw.zip | {キャンペーン名}_削除KW.csv（キャンペーンごと） | keyword（広告費降順） | 「キーワード削除」で抽出される候補と同じデータ |
| cpc_adjust.zip | {キャンペーン名}_CPC調整表.csv（キャンペーンごと） | キャンペーン名／広告グループ／KWテキスト／広告費／売上／購入数／現在CPC／判定ランク／推奨アクション／変更幅／推奨CPC | 「キーワードCPC調整」と同じ判定ロジックの全ランク（変更なし・判断保留を含む） |

---

**⑥ ダウンロード後の使い方**

全候補勝ちKW・削除用KWのCSVはキーワードのみのリストです。Amazon広告管理画面の「キーワード追加」または
「除外キーワード追加」の画面にそのままコピー＆ペーストして利用します。キーワードCPC調整のCSVは判定ランクと
推奨CPCの列を含むため、Amazon広告管理画面で該当キーワードの入札額を手動更新する際の一覧表として利用します。

---

**⑦ 注意事項**

- 各ボタンは対象データが0件の場合、ボタンごと表示されません（メッセージも表示されません）
- キーワードCPC調整ZIPは全件出力です。「キーワードCPC調整」タブの詳細テーブル（変更幅が±0円ではない行のみ表示）
  とは収録件数が異なります
- このページは画面上部の「7日比較CSV」「30日比較CSV」「その他CSV」「🚀 分析開始」の結果を元にしています。分析を実行していない
  場合、いずれのボタンも表示されません
- このページでダウンロードできるのはキーワード関連の3種類のみです。商品・動画に関するCSVはこのページには
  なく、それぞれの機能画面内のダウンロードボタンから取得します

---

**⑧ FAQ**

**Q. ダウンロードボタンが表示されない**
A. 対象データが0件の場合、ボタンごと非表示になります。まず画面上部でCSVをアップロードし
「🚀 分析開始」を押してください。

**Q. キーワードCPC調整ZIPの件数が「CPC調整」タブの詳細テーブルと合わない**
A. 仕様です。「CPC調整」タブの詳細テーブルは変更幅が±0円ではない行のみですが、このZIPは判断保留・現状維持を
含む全ランクを出力します。

**Q. 商品・動画のCPC調整や追加・削除候補もこのページからダウンロードできますか**
A. できません。このページでダウンロードできるのはキーワード追加候補・キーワード削除候補・キーワードCPC調整の
3種類のみです。商品・動画関連のCSVは、それぞれの機能画面内のダウンロードボタンから取得してください。
""")

# ─── Page Router ─────────────────────────────────────
_PAGE_FUNCS = {
    "📋 キーワード追加":              page_add_kw,
    "📊 DateDive売れる予測KW":        page_dd_v4,
    "🚫 キーワード削除":              page_del_kw,
    "📈 キーワードCPC調整":                   page_cpc,
    "🎯 商品CPC調整":                  page_cpc_product,
    "📹 動画CPC調整":                  page_cpc_video,
    "➕ 商品追加":                     page_pt_add_manual,
    "🗑️ 商品削除":                    page_pt_del_manual,
    "📹 動画追加":                     page_pt_add_video,
    "📹 動画削除":                     page_pt_del_video,
    "📄 オートKW削除":               page_auto_del_kw,
    "🎯 オート商品削除":             page_auto_del_product,
    "🎥 オート動画削除":             page_auto_del_video,
    "📥 ダウンロード":                 page_download,
    "📖 取扱説明書":                   page_manual,
}
_PAGE_FUNCS[current_page]()
