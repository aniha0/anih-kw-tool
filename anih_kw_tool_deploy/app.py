"""ANIHA 勝ちKW抽出ツール 最終確定版"""
from __future__ import annotations
import io, re, unicodedata, zipfile
from typing import Optional
import pandas as pd
import streamlit as st


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
st.write("APP VERSION CHECK")
st.write(__file__)

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

# ─── File Upload ────────────────────────────────────
_u1, _u2 = st.columns([7, 2])
with _u1:
    st.markdown("""<div style="font-size:.88rem;font-weight:600;color:#4A5568;margin-bottom:4px;">
        📊 Amazon検索用語レポート
        <span style="font-weight:400;color:#718096;">※「検索用語」と「ターゲティング」列を含めて出力してください</span>
    </div>""", unsafe_allow_html=True)
    sf = st.file_uploader("検索用語レポート", type="csv", key="sf", on_change=clear, label_visibility="collapsed")
    if sf: st.success(f"✓ {sf.name}")
with _u2:
    st.markdown("**　**")
    run = st.button("🔍 抽出実行", type="primary", use_container_width=True)
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
            Amazon検索用語レポートをアップロードして「抽出実行」を押してください</p>
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

# ===================================================
# 分析機能 共通ヘルパー
# ===================================================
import json as _anls_json
import pathlib as _anls_plib
import datetime as _anls_dt

_ANLS_DIR = _anls_plib.Path("analysis_data")

def _anls_load(fname: str) -> list:
    p = _ANLS_DIR / fname
    if not p.exists(): return []
    try: return _anls_json.loads(p.read_text(encoding="utf-8")).get("records", [])
    except Exception: return []

def _anls_save(fname: str, records: list):
    _ANLS_DIR.mkdir(exist_ok=True)
    (_ANLS_DIR / fname).write_text(
        _anls_json.dumps({"records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8")

def _anls_parse_csv(csv_file):
    """分析用CSVを読み込んで主要カラム名を返す (df, kc, cc, sc, oc_, od, clk, imp, tkc, kwt, agn)"""
    df = rcsv(csv_file)
    kc  = fcol(df, ["検索用語","カスタマーの検索用語","Customer Search Term","search term"])
    cc  = fcol(df, ["キャンペーン名","Campaign Name","campaign name"])
    sc  = fcol(df, ["売上","売上額","合計売上","広告費売上高","7日間の総売上高","Attributed Sales","Sales"])
    oc_ = fcol(df, ["合計費用","費用","広告費","コスト","Cost","Spend","spend"])
    od  = fcol(df, ["商品購入数","注文数","注文された商品点数","Orders","Purchases"])
    clk = fcol(df, ["クリック数","クリック","Clicks","clicks"])
    imp = fcol(df, ["インプレッション数","インプレッション","Impressions","impressions"])
    tkc = fcol(df, ["ターゲティング","ターゲッティング","キーワード","Targeting","targeting","Keyword","keyword"])
    kwt = fcol(df, ["Keyword Text","Keyword text","keyword text","キーワードテキスト"])
    agn = fcol(df, ["Ad Group Name","広告グループ名","Ad Group","広告グループ","ad group"])
    for col in [sc, oc_]:
        if col: df[col] = tonum(df[col])
    for col in [od, clk, imp]:
        if col: df[col] = tonum(df[col])
    return df, kc, cc, sc, oc_, od, clk, imp, tkc, kwt, agn

def _anls_build_kw_after(df, kc, cc, sc, oc_, od, clk) -> pd.DataFrame:
    """キーワード追加分析用: オート行を kw_norm 単位で集計して返す"""
    if not all([kc, cc, sc, oc_]): return pd.DataFrame()
    df = df.copy()
    df["kn"] = df[kc].apply(norm)
    df["ct"] = df[cc].apply(lambda x: official(get_theme(str(x))))
    df = df[df[cc].str.contains("オート|auto", case=False, na=False)].copy()
    if df.empty: return pd.DataFrame()
    agg_d = {
        "keyword": (kc, "first"),
        "campaign_theme": ("ct", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
        "sales": (sc, "sum"), "cost": (oc_, "sum"),
    }
    if od:  agg_d["orders"] = (od, "sum")
    if clk: agg_d["clicks"] = (clk, "sum")
    agg = df.groupby("kn").agg(**agg_d).reset_index(drop=True)
    agg["ROAS"] = agg.apply(lambda r: round(r["sales"]/r["cost"],2) if r["cost"]>0 else 0.0, axis=1)
    if "clicks" in agg.columns and "orders" in agg.columns:
        agg["CVR"] = agg.apply(lambda r: round(r["orders"]/r["clicks"]*100,1) if r["clicks"]>0 else 0.0, axis=1)
    agg["_kn_key"] = agg["keyword"].apply(norm)
    return agg

def _anls_build_cpc_after(df, cc, sc, oc_, od, clk, kwt_col) -> pd.DataFrame:
    """CPC調整分析用: SP広告マニュアル行を Keyword Text 単位で集計して返す"""
    cpc_col = kwt_col
    if not all([cc, sc, oc_, cpc_col]): return pd.DataFrame()
    df = df.copy()
    df["ct"] = df[cc].apply(lambda x: official(get_theme(str(x))))
    mask = (df[cc].str.contains("SP広告.*マニュアル|SP.*manual", case=False, na=False)
            & ~df[cc].str.contains("商品ターゲ|動画ターゲ|オート|auto", case=False, na=False))
    df = df[mask].copy()
    if df.empty: return pd.DataFrame()
    df["_kn"] = df[cpc_col].apply(norm)
    df = df[df["_kn"].str.strip() != ""].copy()
    if df.empty: return pd.DataFrame()
    agg_d = {
        "keyword": (cpc_col, "first"),
        "campaign_theme": ("ct", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
        "sales": (sc, "sum"), "cost": (oc_, "sum"),
    }
    if od:  agg_d["orders"] = (od, "sum")
    if clk: agg_d["clicks"] = (clk, "sum")
    agg = df.groupby(["ct","_kn"]).agg(**agg_d).reset_index(drop=True)
    agg["ROAS"] = agg.apply(lambda r: round(r["sales"]/r["cost"],2) if r["cost"]>0 else 0.0, axis=1)
    if "clicks" in agg.columns:
        agg["avg_cpc"] = (agg["cost"]/agg["clicks"].replace(0, float("nan"))).round(0).fillna(0).astype(int)
    agg["_kn_key"] = agg["keyword"].apply(norm)
    return agg

def _anls_build_asin_after(df, cc, sc, oc_, od, clk, tkc, camp_pat) -> pd.DataFrame:
    """商品/動画 ASIN 単位で集計して返す"""
    import re as _re_ax
    def _ex(s):
        m = _re_ax.search(r'B0[A-Z0-9]{8}', str(s), _re_ax.IGNORECASE)
        return m.group(0).upper() if m else ""
    if not all([cc, sc, oc_, tkc]): return pd.DataFrame()
    df = df.copy()
    df["ct"] = df[cc].apply(lambda x: official(get_theme(str(x))))
    df = df[df[cc].str.contains(camp_pat, na=False)].copy()
    if df.empty: return pd.DataFrame()
    df["_asin"] = df[tkc].apply(_ex)
    df = df[df["_asin"] != ""].copy()
    if df.empty: return pd.DataFrame()
    agg_d = {
        "asin": ("_asin","first"),
        "campaign_theme": ("ct", lambda x: x.mode().iloc[0] if len(x)>0 else "未分類"),
        "sales": (sc,"sum"), "cost": (oc_,"sum"),
    }
    if od:  agg_d["orders"] = (od, "sum")
    if clk: agg_d["clicks"] = (clk, "sum")
    agg = df.groupby("_asin").agg(**agg_d).reset_index(drop=True)
    agg["ROAS"] = agg.apply(lambda r: round(r["sales"]/r["cost"],2) if r["cost"]>0 else 0.0, axis=1)
    if "clicks" in agg.columns:
        agg["avg_cpc"] = (agg["cost"]/agg["clicks"].replace(0, float("nan"))).round(0).fillna(0).astype(int)
    return agg

def _anls_judge(b, a, higher_ok=True):
    """改善判定文字列を返す"""
    if b == 0: return "ー"
    pct = (a - b) / b * 100
    if abs(pct) < 2: return "→ 変化なし"
    if higher_ok: return f"↑ 改善 ({pct:+.1f}%)" if pct > 0 else f"↓ 悪化 ({pct:+.1f}%)"
    return f"↓ 改善 ({pct:+.1f}%)" if pct < 0 else f"↑ 悪化 ({pct:+.1f}%)"

def _anls_html_table(rows):
    """list[dict] → HTML テーブル文字列"""
    if not rows: return ""
    cols = list(rows[0].keys())
    hd = "".join(
        f'<th style="padding:7px 10px;border:1px solid #E2E8F0;background:#EBF4FF;'
        f'text-align:center;font-size:.82rem;">{c}</th>' for c in cols)
    bd = ""
    for r in rows:
        bd += "<tr>" + "".join(
            f'<td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:center;'
            f'font-size:.82rem;">{v}</td>' for v in r.values()) + "</tr>"
    return (f'<table style="width:100%;border-collapse:collapse;margin-top:8px;">'
            f'<thead><tr>{hd}</tr></thead><tbody>{bd}</tbody></table>')

def _anls_render_kw_tab(before_df: pd.DataFrame, period_days: int,
                        hist_fname: str, csv_key: str, label: str,
                        after_builder_type: str):
    """
    キーワード系 分析タブ 共通レンダラー。
    after_builder_type: "kw_add" | "cpc_kw"
    """
    st.markdown(f"#### 📊 {label} 分析")
    st.info(f"📅 分析期間: **{period_days}日固定** — {period_days}日レポートCSVをアップロードしてください。")
    if before_df is None or before_df.empty:
        st.warning("先に「改善」タブで抽出実行を行ってください。抽出対象が分析対象になります。")
        return
    n_before = len(before_df)
    kw_col = "keyword"
    camps = sorted(before_df["campaign_theme"].unique().tolist()) if "campaign_theme" in before_df.columns else []
    st.markdown(f"**改善対象: {n_before}件　キャンペーン: {len(camps)}件**")

    # ① キャンペーン一覧
    with st.expander("① キャンペーン一覧（改善対象）", expanded=False):
        if "campaign_theme" in before_df.columns:
            _g = before_df.groupby("campaign_theme").size().reset_index(name="件数")
            st.dataframe(_g, use_container_width=True)

    # ② キャンペーン選択
    sel_camp = st.selectbox("② キャンペーン選択", ["全キャンペーン"] + camps, key=f"_anls_{csv_key}_camp")

    # CSV アップロード + 分析実行
    st.markdown(f"**③ 比較用 {period_days}日レポートCSVをアップロード**")
    af_file = st.file_uploader(f"{period_days}日レポートCSV", type="csv", key=csv_key)
    run_btn = st.button("🔍 分析実行", key=f"_anls_{csv_key}_run", type="primary")

    if run_btn and af_file is None:
        st.warning("CSVをアップロードしてください。")
        return
    if not run_btn:
        return

    with st.spinner("分析中..."):
        df_raw, kc, cc, sc, oc_, od, clk, imp, tkc, kwt, agn = _anls_parse_csv(af_file)
        if not all([cc, sc, oc_]):
            st.error("必要な列が見つかりません（キャンペーン名・売上・広告費）。")
            return
        if after_builder_type == "kw_add":
            if not kc:
                st.error("「検索用語」列が見つかりません。")
                return
            after_df = _anls_build_kw_after(df_raw, kc, cc, sc, oc_, od, clk)
        else:  # cpc_kw
            kw_col_cpc = kwt if kwt else fcol(df_raw, ["ターゲティング","Targeting","targeting"])
            after_df = _anls_build_cpc_after(df_raw, cc, sc, oc_, od, clk, kw_col_cpc)
        if after_df.empty:
            st.warning("Afterデータが取得できませんでした。キャンペーン構成を確認してください。")
            return

        # Before に _kn_key を付与してマッチング
        bf = before_df.copy()
        bf["_kn_key"] = bf[kw_col].apply(norm) if kw_col in bf.columns else bf.index.astype(str)
        sfx_cols = [c for c in ["sales","cost","ROAS","orders","clicks","CVR","avg_cpc"] if c in after_df.columns]
        merged = bf.merge(after_df[["_kn_key"] + sfx_cols], on="_kn_key", how="inner", suffixes=("_b","_a"))
        if sel_camp != "全キャンペーン" and "campaign_theme" in merged.columns:
            merged = merged[merged["campaign_theme"] == sel_camp].copy()

    n_matched = len(merged)
    st.markdown(f"**マッチ件数: {n_matched}件 / 改善対象: {n_before}件**")
    if merged.empty:
        st.info("マッチするキーワードが見つかりませんでした。同キャンペーンのCSVを確認してください。")
        return

    # ③ キャンペーン比較
    st.markdown("##### ③ キャンペーン比較")
    has_b_sales = "sales_b" in merged.columns; has_a_sales = "sales_a" in merged.columns
    if has_b_sales and has_a_sales:
        _bc = merged.groupby("campaign_theme")[["sales_b","cost_b","ROAS_b"]].sum() if "cost_b" in merged.columns else pd.DataFrame()
        if not _bc.empty:
            _ac = merged.groupby("campaign_theme")[["sales_a","cost_a","ROAS_a"]].sum()
            rows_c = []
            for ct in _bc.index:
                bs, ba = float(_bc.loc[ct,"sales_b"]), float(_ac.loc[ct,"sales_a"])
                bc, ac_ = float(_bc.loc[ct,"cost_b"]), float(_ac.loc[ct,"cost_a"])
                br, ar = float(_bc.loc[ct,"ROAS_b"]) if "ROAS_b" in _bc.columns else 0, float(_ac.loc[ct,"ROAS_a"]) if "ROAS_a" in _ac.columns else 0
                rows_c.append({
                    "キャンペーン": ct,
                    "売上 Before": f"¥{bs:,.0f}", "売上 After": f"¥{ba:,.0f}", "売上 判定": _anls_judge(bs, ba),
                    "広告費 Before": f"¥{bc:,.0f}", "広告費 After": f"¥{ac_:,.0f}", "広告費 判定": _anls_judge(bc, ac_, higher_ok=False),
                    "ROAS Before": f"{br:.2f}", "ROAS After": f"{ar:.2f}", "ROAS 判定": _anls_judge(br, ar),
                })
            st.markdown(_anls_html_table(rows_c), unsafe_allow_html=True)

    # ④⑤ 対象一覧 + Before/After
    st.markdown("##### ④⑤ 対象一覧 Before / After")
    _dcols_b = [c for c in [kw_col,"campaign_theme","sales_b","cost_b","ROAS_b","orders_b","CVR_b","avg_cpc_b"] if c in merged.columns]
    _dcols_a = [c for c in ["sales_a","cost_a","ROAS_a","orders_a","CVR_a","avg_cpc_a"] if c in merged.columns]
    _disp = merged[_dcols_b + _dcols_a].copy()
    _ren = {kw_col:"キーワード","campaign_theme":"キャンペーン",
            "sales_b":"売上_Before","cost_b":"広告費_Before","ROAS_b":"ROAS_Before",
            "orders_b":"注文_Before","CVR_b":"CVR_Before","avg_cpc_b":"CPC_Before",
            "sales_a":"売上_After","cost_a":"広告費_After","ROAS_a":"ROAS_After",
            "orders_a":"注文_After","CVR_a":"CVR_After","avg_cpc_a":"CPC_After"}
    _disp = _disp.rename(columns=_ren)
    for c in [c for c in _disp.columns if "売上" in c or "広告費" in c]:
        _disp[c] = _disp[c].apply(lambda x: f"¥{x:,.0f}" if isinstance(x,(int,float)) else x)
    _disp.index = range(1, len(_disp)+1)
    st.dataframe(_disp, use_container_width=True)

    # 履歴保存
    if st.button("💾 分析結果を保存", key=f"_anls_{csv_key}_save"):
        _recs = _anls_load(hist_fname)
        _recs.append({
            "id": _anls_dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
            "saved_at": _anls_dt.date.today().isoformat(),
            "type": label, "period_days": period_days,
            "n_before": n_before, "n_matched": n_matched,
            "camps": camps,
        })
        _anls_save(hist_fname, _recs)
        st.success("✅ 分析結果を保存しました。")

    # 保存済み履歴
    with st.expander("📂 保存済み分析履歴", expanded=False):
        _recs = _anls_load(hist_fname)
        if not _recs:
            st.info("保存済み分析はありません。")
        else:
            st.dataframe(pd.DataFrame(_recs[::-1]), use_container_width=True)


def _anls_render_asin_tab(before_df: pd.DataFrame, period_days: int,
                          hist_fname: str, csv_key: str, label: str, camp_pat: str):
    """商品/動画 ASIN 系 分析タブ 共通レンダラー"""
    st.markdown(f"#### 📊 {label} 分析")
    st.info(f"📅 分析期間: **{period_days}日固定** — {period_days}日レポートCSVをアップロードしてください。")
    if before_df is None or before_df.empty:
        st.warning("先に「改善」タブで抽出実行を行ってください。")
        return
    n_before = len(before_df)
    id_col = "asin" if "asin" in before_df.columns else "keyword"
    camps = sorted(before_df["campaign_theme"].unique().tolist()) if "campaign_theme" in before_df.columns else []
    st.markdown(f"**改善対象: {n_before}件　キャンペーン: {len(camps)}件**")

    with st.expander("① キャンペーン一覧（改善対象）", expanded=False):
        if "campaign_theme" in before_df.columns:
            _g = before_df.groupby("campaign_theme").size().reset_index(name="件数")
            st.dataframe(_g, use_container_width=True)

    sel_camp = st.selectbox("② キャンペーン選択", ["全キャンペーン"] + camps, key=f"_anls_{csv_key}_camp")

    st.markdown(f"**③ 比較用 {period_days}日レポートCSVをアップロード**")
    af_file = st.file_uploader(f"{period_days}日レポートCSV", type="csv", key=csv_key)
    run_btn = st.button("🔍 分析実行", key=f"_anls_{csv_key}_run", type="primary")

    if run_btn and af_file is None:
        st.warning("CSVをアップロードしてください。")
        return
    if not run_btn:
        return

    with st.spinner("分析中..."):
        df_raw, kc, cc, sc, oc_, od, clk, imp, tkc, kwt, agn = _anls_parse_csv(af_file)
        if not all([cc, sc, oc_, tkc]):
            st.error("必要な列が見つかりません（キャンペーン名・売上・広告費・ターゲティング）。")
            return
        after_df = _anls_build_asin_after(df_raw, cc, sc, oc_, od, clk, tkc, camp_pat)
        if after_df.empty:
            st.warning("Afterデータが取得できませんでした。")
            return

        bf = before_df.copy()
        sfx_cols = [c for c in ["sales","cost","ROAS","orders","clicks","avg_cpc"] if c in after_df.columns]
        if id_col not in after_df.columns:
            st.error(f"After データに '{id_col}' 列がありません。")
            return
        merged = bf.merge(after_df[[id_col] + sfx_cols], on=id_col, how="inner", suffixes=("_b","_a"))
        if sel_camp != "全キャンペーン" and "campaign_theme" in merged.columns:
            merged = merged[merged["campaign_theme"] == sel_camp].copy()

    n_matched = len(merged)
    st.markdown(f"**マッチ件数: {n_matched}件 / 改善対象: {n_before}件**")
    if merged.empty:
        st.info("マッチするASINが見つかりませんでした。")
        return

    # ③ キャンペーン比較
    st.markdown("##### ③ キャンペーン比較")
    if "sales_b" in merged.columns and "sales_a" in merged.columns:
        _bc = merged.groupby("campaign_theme")[["sales_b","cost_b","ROAS_b"]].sum()
        _ac = merged.groupby("campaign_theme")[["sales_a","cost_a","ROAS_a"]].sum()
        rows_c = []
        for ct in _bc.index:
            bs, ba = float(_bc.loc[ct,"sales_b"]), float(_ac.loc[ct,"sales_a"])
            bc, ac_ = float(_bc.loc[ct,"cost_b"]), float(_ac.loc[ct,"cost_a"])
            br = float(_bc.loc[ct,"ROAS_b"]) if "ROAS_b" in _bc.columns else 0
            ar = float(_ac.loc[ct,"ROAS_a"]) if "ROAS_a" in _ac.columns else 0
            rows_c.append({
                "キャンペーン": ct,
                "売上 Before": f"¥{bs:,.0f}", "売上 After": f"¥{ba:,.0f}", "売上 判定": _anls_judge(bs, ba),
                "広告費 Before": f"¥{bc:,.0f}", "広告費 After": f"¥{ac_:,.0f}", "広告費 判定": _anls_judge(bc, ac_, higher_ok=False),
                "ROAS Before": f"{br:.2f}", "ROAS After": f"{ar:.2f}", "ROAS 判定": _anls_judge(br, ar),
            })
        st.markdown(_anls_html_table(rows_c), unsafe_allow_html=True)

    # ④⑤ 対象別 Before/After
    st.markdown("##### ④⑤ 対象別 Before / After")
    _dcols_b = [c for c in [id_col,"campaign_theme","sales_b","cost_b","ROAS_b","orders_b","avg_cpc_b"] if c in merged.columns]
    _dcols_a = [c for c in ["sales_a","cost_a","ROAS_a","orders_a","avg_cpc_a"] if c in merged.columns]
    _disp = merged[_dcols_b + _dcols_a].copy()
    _ren = {id_col:"ASIN","campaign_theme":"キャンペーン",
            "sales_b":"売上_Before","cost_b":"広告費_Before","ROAS_b":"ROAS_Before",
            "orders_b":"注文_Before","avg_cpc_b":"CPC_Before",
            "sales_a":"売上_After","cost_a":"広告費_After","ROAS_a":"ROAS_After",
            "orders_a":"注文_After","avg_cpc_a":"CPC_After"}
    _disp = _disp.rename(columns=_ren)
    for c in [c for c in _disp.columns if "売上" in c or "広告費" in c]:
        _disp[c] = _disp[c].apply(lambda x: f"¥{x:,.0f}" if isinstance(x,(int,float)) else x)
    _disp.index = range(1, len(_disp)+1)
    st.dataframe(_disp, use_container_width=True)

    if st.button("💾 分析結果を保存", key=f"_anls_{csv_key}_save"):
        _recs = _anls_load(hist_fname)
        _recs.append({
            "id": _anls_dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
            "saved_at": _anls_dt.date.today().isoformat(),
            "type": label, "period_days": period_days,
            "n_before": n_before, "n_matched": n_matched, "camps": camps,
        })
        _anls_save(hist_fname, _recs)
        st.success("✅ 分析結果を保存しました。")

    with st.expander("📂 保存済み分析履歴", expanded=False):
        _recs = _anls_load(hist_fname)
        if not _recs:
            st.info("保存済み分析はありません。")
        else:
            st.dataframe(pd.DataFrame(_recs[::-1]), use_container_width=True)


def page_add_kw():
    _t_tab1, _t_tab2 = st.tabs(["改善", "分析"])
    with _t_tab1:
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
    with _t_tab2:
        _anls_render_kw_tab(
            before_df=dw,
            period_days=30,
            hist_fname="kw_add_analysis.json",
            csv_key="anls_kw_add_csv",
            label="キーワード追加",
            after_builder_type="kw_add",
        )


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

def page_cpc():
    _t_tab1, _t_tab2 = st.tabs(["CPC調整", "分析"])
    with _t_tab1:
        _RANK_ORDER = ["SS+", "SS", "S", "A", "B", "C", "D", "即削除", "判断保留"]
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
        disp_cols = [c for c in ["campaign_name","ad_group","keyword","ROAS","cost","sales","orders","avg_cpc","cpc_rank","cpc_action","cpc_delta","rec_cpc"] if c in df_c.columns]
        _rn = {"campaign_name":"キャンペーン名","ad_group":"広告グループ","keyword":"KWテキスト",
               "cost":"広告費","sales":"売上","orders":"購入数",
               "avg_cpc":"現在CPC","cpc_rank":"判定ランク","cpc_action":"推奨アクション",
               "cpc_delta":"変更幅","rec_cpc":"推奨CPC"}
        cat_t = pd.CategoricalDtype(categories=_RANK_ORDER, ordered=True)
        df_c["_r"] = df_c["cpc_rank"].astype(cat_t)
        df_c = df_c.sort_values(["_r","ROAS"], ascending=[True, False]).drop(columns=["_r"]).reset_index(drop=True)
        df_c.index = df_c.index + 1
        # ③ 変更対象のみ表示（cpc_delta != 0）
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
            st.info("変更幅が発生するキーワードはありません（全件 現状維持 または 判断保留）。")
        else:
            st.dataframe(_d.style.apply(_cr, axis=1), use_container_width=True, height=460)
        # ④ CSV: 実行用（変更対象のみ）+ 全件
        _c1, _c2 = st.columns(2)
        with _c1:
            _dl_csv_adj = df_disp[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(f"📥 {cpc_camp}_CPC調整_実行用.csv", data=_dl_csv_adj,
                file_name=f"{cpc_camp}_CPC調整_実行用.csv", mime="text/csv", use_container_width=True)
        with _c2:
            _dl_csv_all = df_c[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(f"📥 {cpc_camp}_CPC調整表.csv", data=_dl_csv_all,
                file_name=f"{cpc_camp}_CPC調整表.csv", mime="text/csv", use_container_width=True)
    with _t_tab2:
        _anls_render_kw_tab(
            before_df=dc_cpc,
            period_days=7,
            hist_fname="cpc_kw_analysis.json",
            csv_key="anls_cpc_kw_csv",
            label="キーワードCPC調整",
            after_builder_type="cpc_kw",
        )


def _render_pt_cpc_page(dc_pt, page_title: str, sel_key: str):
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
    # CSV は全件出力（±0含む）
    _dl_fname = f"{cpc_camp}_{page_title}_CPC調整表.csv"
    _dl_csv = df_c[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(f"📥 {_dl_fname}", data=_dl_csv,
        file_name=_dl_fname, mime="text/csv")


def page_cpc_product():
    _t_tab1, _t_tab2 = st.tabs(["CPC調整", "分析"])
    with _t_tab1:
        _render_pt_cpc_page(dc_cpc_product, "商品CPC調整", "cpc_product_sel")
    with _t_tab2:
        _anls_render_asin_tab(
            before_df=dc_cpc_product,
            period_days=7,
            hist_fname="cpc_product_analysis.json",
            csv_key="anls_cpc_pt_csv",
            label="商品CPC調整",
            camp_pat="商品ターゲ",
        )

def page_cpc_video():
    _t_tab1, _t_tab2 = st.tabs(["CPC調整", "分析"])
    with _t_tab1:
        _render_pt_cpc_page(dc_cpc_video, "動画CPC調整", "cpc_video_sel")
    with _t_tab2:
        _anls_render_asin_tab(
            before_df=dc_cpc_video,
            period_days=7,
            hist_fname="cpc_video_analysis.json",
            csv_key="anls_cpc_vid_csv",
            label="動画CPC調整",
            camp_pat="動画",
        )


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



def _render_pt_page(session_key, is_add, camp_label, selectbox_key):
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
    st.download_button(f"\U0001f4e5 {_fname}", data=_dl,
        file_name=_fname, mime="text/csv", use_container_width=True)


def page_pt_add_manual():
    _t_tab1, _t_tab2 = st.tabs(["改善", "分析"])
    with _t_tab1:
        _render_pt_page("df_pt_add_m", True,  "商品", "pt_add_m_sel")
    with _t_tab2:
        _anls_render_asin_tab(
            before_df=st.session_state.get("df_pt_add_m", pd.DataFrame()),
            period_days=30,
            hist_fname="pt_add_m_analysis.json",
            csv_key="anls_pt_add_m_csv",
            label="商品追加",
            camp_pat="商品ターゲ",
        )

def page_pt_del_manual():
    _render_pt_page("df_pt_del_m", False, "商品", "pt_del_m_sel")

def page_pt_add_video():
    _t_tab1, _t_tab2 = st.tabs(["改善", "分析"])
    with _t_tab1:
        _render_pt_page("df_pt_add_v", True,  "動画", "pt_add_v_sel")
    with _t_tab2:
        _anls_render_asin_tab(
            before_df=st.session_state.get("df_pt_add_v", pd.DataFrame()),
            period_days=30,
            hist_fname="pt_add_v_analysis.json",
            csv_key="anls_pt_add_v_csv",
            label="動画追加",
            camp_pat="動画",
        )

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
| 📊 分析 | 改善施策の前後比較・自動評価・AI傾向分析 |
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
追加分析（分析機能）
CPC分析（分析機能）
```
""")

    # ── キーワード追加 ────────────────────────────────────────────────
    with st.expander("📋 キーワード追加"):
        st.markdown("""
**目的**

オート広告（自動ターゲティング）で成果が出た検索語句を、
手動広告（部分一致）のマニュアルキャンペーンへ追加するための候補を抽出します。
サイドバーの「➕ キーワード追加」を選択するとこのページが表示されます。

---

**集計元データ**

「検索用語レポート」内のオートキャンペーン行の「カスタマーの検索用語」列を集計します。
キャンペーン名に「オート」または「auto」が含まれる行がオートキャンペーン行です。

| 項目 | 内容 |
|---|---|
| 集計対象キャンペーン | キャンペーン名に「オート」「auto」を含むもの |
| 集計単位 | 正規化後の検索語句（kn） |
| 集計項目 | 売上・広告費・注文数・クリック数の合算 |

---

**信頼度フィルター（両方を同時に満たす必要があります）**

| 条件 | 閾値 |
|---|---|
| 注文数 | ≥ 3件 |
| クリック数 | ≥ 5回 |
| 広告費 | ≥ ¥300 |

> サイドバーで閾値を変更できます。

---

**採用条件（両方を同時に満たす必要があります）**

| 条件 | 閾値 |
|---|---|
| 売上 | ≥ 売価 × 2 |
| ROAS | ≥ 2.0 |

---

**除外条件**

| 除外対象 | 内容 |
|---|---|
| ASIN形式（例: B0XXXXXXXXX） | オート除外KW（商品）ページで管理するため除外 |
| category形式（例: category:〜） | オート除外KW（動画）ページで管理するため除外 |
| 同一意図KW（語順・表記ゆれ） | 代表1件に統合して重複を排除 |

---

**画面の表示内容**

| 表示エリア | 内容 |
|---|---|
| キャンペーン選択 | 全キャンペーン / 個別キャンペーン |
| 件数バッジ | 該当件数: N件 |
| Amazon広告登録用KW一覧 | コピー用コードブロック |
| KW詳細テーブル | keyword / キャンペーン / ROAS / 広告費 / 売上 / 注文数 / クリック数（ROAS降順） |

CSV出力は「ダウンロードページ」の「全候補 勝ちKW ZIP」から行います。

---

### ■ 目的

オート広告で成果が確認できた検索語句を、手動広告（部分一致）へ追加するための候補を抽出します。
オート広告は自動で検索語句を探索しますが、入札コントロールができません。
成果が出た語句を手動広告（部分一致）に登録することで入札を最適化し、
同一語句への広告費を効率的に配分できます。

---

### ■ 集計元データ

「検索用語レポート」内のオートキャンペーン行が集計対象です。
キャンペーン名に「オート」または「auto」（大文字・小文字不問）を含む行がオートキャンペーン行です。
マニュアルキャンペーンの行は集計対象外です。
検索語句は正規化（norm()）処理を経て、語順・表記ゆれを統一したうえで集計します。

---

### ■ 使用するCSV

Amazon広告管理画面の **「検索用語レポート」** を使用します。
集計期間は **14〜30日** を推奨します。
期間が短いと信頼度フィルターを通過できる語句が少なくなり、抽出件数が減少します。

---

### ■ 対象

以下の手順ですべての条件を通過した検索語句が追加候補になります。

| ステップ | 内容 |
|---|---|
| ① オートキャンペーン行の抽出 | キャンペーン名に「オート」「auto」を含む行のみ残す |
| ② 語句の種別除外 | ASIN形式・category形式を除外し、通常検索語句のみ残す |
| ③ 信頼度フィルター | 注文数≥3 AND クリック数≥5 AND 広告費≥¥300 |
| ④ 採用条件 | 売上≥売価×2 AND ROAS≥2.0 |
| ⑤ 同一意図KW統合 | 語順・表記ゆれが同じ語句を代表1件に統合 |

---

### ■ 処理ロジック

```
オートキャンペーン行を抽出
        ↓
ASIN形式・category形式の語句を除外
（通常の日本語・英語の検索語句のみ残す）
        ↓
信頼度フィルター適用
（注文数≥3件 AND クリック数≥5回 AND 広告費≥¥300）
        ↓
採用条件チェック
（売上≥売価×2 AND ROAS≥2.0）
        ↓
同一意図KW統合
（語順・表記ゆれが同じ語句は代表1件に集約）
        ↓
追加候補として件数バッジ・一覧・詳細テーブルに表示
```

信頼度フィルターは、データ不足による偶発的な成果を排除するための前処理です。
採用条件の売上・ROASはどちらか一方だけでは不十分で、**両方を同時に満たす**必要があります。
同一意図KW統合後の件数が、統合前の件数より少なくなることがあります。これは正常動作です。

---

### ■ 判定条件

| 条件名 | 項目 | 閾値 | 意味 |
|---|---|---|---|
| 信頼度フィルター | 注文数 | ≥ 3件 | 偶発的な注文を排除 |
| 信頼度フィルター | クリック数 | ≥ 5回 | データ不足を排除 |
| 信頼度フィルター | 広告費 | ≥ ¥300 | データ不足を排除 |
| 採用条件 | 売上 | ≥ 売価 × 2 | 売価の2倍以上の売上が必要 |
| 採用条件 | ROAS | ≥ 2.0 | 広告効率の最低基準 |

すべての条件を**同時に**満たす語句のみが追加候補になります。

---

### ■ 対象になるケース

```
例: 注文数5件 / クリック数10回 / 広告費¥500 / 売上¥3,000 / 売価¥1,000 / ROAS 6.0
  → 信頼度フィルター: ○（注文5≥3 / クリック10≥5 / 広告費500≥300）
  → 採用条件: ○（売上3,000≥売価×2=2,000 / ROAS 6.0≥2.0）
  → 追加候補に採用
```

- 注文数・クリック数・広告費の信頼度フィルターをすべて通過している
- 売上が売価の2倍以上ある
- ROASが2.0以上ある
- ASIN形式・category形式ではない通常の検索語句である

---

### ■ 対象にならないケース

| ケース | 理由 |
|---|---|
| 注文数 < 3件 | 信頼度フィルター未通過（データ不足） |
| クリック数 < 5回 | 信頼度フィルター未通過（データ不足） |
| 広告費 < ¥300 | 信頼度フィルター未通過（データ不足） |
| 売上 < 売価 × 2 | 採用条件未通過（売上が不十分） |
| ROAS < 2.0 | 採用条件未通過（広告効率が基準未満） |
| ASIN形式の語句 | オート除外KW（商品）ページで管理 |
| category形式の語句 | オート除外KW（動画）ページで管理 |
| 売価マスタ未登録の商品のKW | 売価が特定できず採用条件を判定できない |

---

### ■ 処理の流れ

① 検索用語レポート（CSV）をアップロードする
② 「🔍 抽出実行」ボタンを押す
③ オートキャンペーン行を自動抽出する
④ ASIN形式・category形式の語句を除外する
⑤ 信頼度フィルターを適用する（注文数・クリック数・広告費）
⑥ 採用条件をチェックする（売上・ROAS）
⑦ 同一意図KWを統合する
⑧ キャンペーン選択で絞り込む（任意）
⑨ 件数バッジ・KW一覧・詳細テーブルに結果を表示する

---

### ■ 実際の操作手順

① Amazon広告管理画面にログインする
② 「レポート」→「広告レポート」→「検索用語レポート」を選択してCSVをダウンロードする
③ ツール画面の上部「CSVアップロード」からファイルを選択する
④ 「🔍 抽出実行」ボタンを押す
⑤ サイドバーの「📋 キーワード追加」を選択してこのページを開く
⑥ 「キャンペーン」プルダウンで対象キャンペーンを選択する（または「全キャンペーン」）
⑦ 件数バッジで追加候補件数を確認する
⑧ 詳細テーブルで各語句のROAS・売上・注文数を確認する
⑨ 「Amazon広告登録用KW一覧」のコードブロック右上のコピーボタンで語句をコピーする
⑩ Amazon広告管理画面の手動キャンペーン→広告グループ→「キーワードターゲティング」→「部分一致」へ貼り付けて登録する

※ CSVで一括登録する場合
⑨′ ダウンロードページの「全候補 勝ちKW ZIP」からCSVを取得する
⑩′ Amazonバルクシートに貼り付けてアップロードする

---

### ■ 実行後の確認方法

```
[キャンペーン選択プルダウン: 全キャンペーン / 個別キャンペーン]
[件数バッジ: 該当件数: N件  キャンペーン: 〇〇]
[Amazon広告登録用KW一覧（コピー用コードブロック）]
[KW詳細テーブル: keyword / キャンペーン / ROAS / 広告費 / 売上 / 注文数 / クリック数]
                 ← ROAS降順（最も効率の高いKWが上位）
```

件数が **0件** の場合、信頼度フィルターまたは採用条件を満たす語句がありません。
→ 集計期間を延ばす、またはサイドバーで閾値を調整してください。

件数が **統合前より少ない** 場合、同一意図KWが統合されています。これは正常動作です。

---

### ■ 注意事項

- 信頼度フィルター（注文数・クリック数・広告費）は**3つすべて同時に**満たす必要があります。
- 採用条件（売上・ROAS）も**両方同時に**満たす必要があります。
- CSVはこのページではなく「ダウンロードページ」の「全候補 勝ちKW ZIP」から出力します。
- 追加はツールで自動実行しません。必ず手動でAmazon広告管理画面へ登録してください。
- 同一意図KW統合後の件数が、統合前より少なくなる場合があります。これは正常動作です。
- 売価マスタ（PRICES辞書）に未登録の商品のKWは集計から除外されます。

---

### ■ おすすめ運用

- 毎週CSVを更新し、追加候補を確認する。
- 新規追加したキーワードは「改善履歴」へ登録し、分析機能でBefore / After比較を実施する。
- 集計期間は14〜30日を基本とし、データ量が少ない場合は期間を延ばす。
- 追加後7日経過したら分析機能で効果を確認する。
- 追加候補が0件の場合は、集計期間を30日に延ばすかサイドバーで閾値を下げてみる。
""")

    with st.expander("🚫 キーワード削除"):
        st.markdown("""
**目的**

マニュアル広告（手動ターゲティング）のキーワードのうち、
広告費を消費しているにもかかわらず成果が出ていない語句を停止候補として抽出します。
サイドバーの「🚫 キーワード削除」を選択するとこのページが表示されます。

---

**集計元データ**

「検索用語レポート」内のマニュアルキャンペーン行の「カスタマーの検索用語」列を集計します。
キャンペーン名に「オート」「auto」を含まない行がマニュアルキャンペーン行です。

| 項目 | 内容 |
|---|---|
| 集計対象キャンペーン | キャンペーン名に「オート」「auto」を含まないもの |
| 集計単位 | 正規化後の検索語句（kn） |
| 集計項目 | 売上・広告費・注文数・クリック数の合算 |

---

**削除条件（両方を同時に満たす場合に削除候補）**

| 条件 | 閾値 | 意味 |
|---|---|---|
| 広告費 | ≥ 売価 × 2 | 売価の2倍以上の広告費を消費している |
| ROAS | < 0.8 | 広告効率が基準を大きく下回っている |

> ⚠️ 広告費とROASの**両方**を同時に満たす場合のみ削除候補になります。

---

**除外条件**

| 除外対象 | 内容 |
|---|---|
| ASIN形式（例: B0XXXXXXXXX） | 検索語句ではなくターゲティング指定のため除外 |
| category形式（例: category:〜） | 同上 |
| 勝ちKW（追加候補と重複する語句） | 成果の出ているKWを誤削除しないための保護 |
| 売価マスタ未登録の商品 | 売価が特定できず削除条件を判定できないため除外 |

---

**画面の表示内容**

| 表示エリア | 内容 |
|---|---|
| キャンペーン選択 | 全キャンペーン / 個別キャンペーン |
| 件数バッジ | 削除対象件数: N件（赤色） |
| 削除対象KW一覧 | コピー用コードブロック |
| 削除KW詳細テーブル | keyword / キャンペーン / ROAS / 広告費 / 売上（ROAS昇順） |

CSV出力は「ダウンロードページ」の「削除用KW ZIP」から行います。

---

### ■ 目的

広告費を消費しているにもかかわらず成果が出ていないキーワードを停止し、
費用対効果の高いキーワードへ広告費を集中させます。
感覚や主観ではなく「広告費とROAS」という客観的な数値基準で停止候補を判定します。

---

### ■ 集計元データ

「検索用語レポート」内のマニュアルキャンペーン行が集計対象です。
キャンペーン名に「オート」「auto」を含まない行がマニュアルキャンペーン行です。
オートキャンペーンの行は除外されます（オート除外KWページで管理します）。
検索語句は正規化（norm()）処理を経て、語順・表記ゆれを統一したうえで集計します。

---

### ■ 使用するCSV

Amazon広告管理画面の **「検索用語レポート」** を使用します。
集計期間は **14〜30日** を推奨します。
期間が短すぎると広告費が閾値（売価×2）に達しない語句が多くなり、
データ不足として削除候補に上がらない語句が増えます。

---

### ■ 対象

以下の手順ですべての条件を通過した検索語句が削除候補になります。

| ステップ | 内容 |
|---|---|
| ① マニュアルキャンペーン行の抽出 | キャンペーン名に「オート」「auto」を含まない行のみ残す |
| ② 語句の種別除外 | ASIN形式・category形式を除外し、通常検索語句のみ残す |
| ③ 削除条件チェック | 広告費≥売価×2 AND ROAS<0.8 |
| ④ 勝ちKW除外 | キーワード追加候補（勝ちKW）と重複する語句を除外 |
| ⑤ 売価チェック | 売価マスタ未登録の商品を除外 |

---

### ■ 処理ロジック

```
マニュアルキャンペーン行を抽出
（キャンペーン名に「オート」「auto」を含まない行）
        ↓
ASIN形式・category形式の語句を除外
（通常の検索語句のみ残す）
        ↓
正規化後の語句単位でキャンペーン別に集計
（売上・広告費・注文数・クリック数を合算）
        ↓
売価マスタを参照して売価を付与
（未登録商品は除外）
        ↓
削除条件チェック
（広告費 ≥ 売価 × 2  AND  ROAS < 0.8）
        ↓
勝ちKW除外
（キーワード追加候補と重複する語句を削除対象から除外）
        ↓
削除候補として件数バッジ・一覧・詳細テーブルに表示
```

削除条件は広告費とROASの**両方**を同時に満たす必要があります。
どちらか一方だけでは削除候補になりません（データ不足・経過観察扱い）。
勝ちKW保護処理により、成果の出ているKWを誤って削除するリスクを防いでいます。

---

### ■ 判定条件

| 条件名 | 項目 | 閾値 | 意味 |
|---|---|---|---|
| 削除条件 | 広告費 | ≥ 売価 × 2 | 売価の2倍以上の広告費を消費 |
| 削除条件 | ROAS | < 0.8 | 広告効率が基準を大きく下回る |

削除条件の2つを**同時に**満たす場合にのみ削除候補になります。

---

### ■ 対象になるケース

```
例: 広告費¥2,500 / 売価¥1,000 / ROAS 0.5
  → 削除条件①: ○（広告費2,500 ≥ 売価×2=2,000）
  → 削除条件②: ○（ROAS 0.5 < 0.8）
  → 削除候補に採用
```

- 広告費が売価の2倍以上に達している
- ROASが0.8未満である
- ASIN形式・category形式ではない通常の検索語句である
- 勝ちKW（追加候補）と重複していない

---

### ■ 対象にならないケース

| ケース | 理由 |
|---|---|
| 広告費 < 売価 × 2 | データ不足（判定に十分なデータがない）・経過観察 |
| ROAS ≥ 0.8 | 一定の広告効率が出ているため停止不要 |
| ASIN形式の語句 | 検索語句ではなくターゲティング指定のため対象外 |
| category形式の語句 | 同上 |
| 勝ちKWと重複する語句 | 成果の出ているKWを誤削除しないための保護 |
| 売価マスタ未登録の商品 | 売価が判定できないため除外 |

---

### ■ 処理の流れ

① 検索用語レポート（CSV）をアップロードする
② 「🔍 抽出実行」ボタンを押す
③ マニュアルキャンペーン行を自動抽出する
④ ASIN形式・category形式の語句を除外する
⑤ 語句単位でキャンペーン別に集計する
⑥ 売価マスタを参照して売価を付与する
⑦ 削除条件をチェックする（広告費・ROAS）
⑧ 勝ちKWと重複する語句を除外する
⑨ キャンペーン選択で絞り込む（任意）
⑩ 件数バッジ・KW一覧・詳細テーブルに結果を表示する

---

### ■ 実際の操作手順

① Amazon広告管理画面にログインする
② 「レポート」→「広告レポート」→「検索用語レポート」を選択してCSVをダウンロードする
③ ツール画面の上部「CSVアップロード」からファイルを選択する
④ 「🔍 抽出実行」ボタンを押す
⑤ サイドバーの「🚫 キーワード削除」を選択してこのページを開く
⑥ 「キャンペーン（削除用KW）」プルダウンで対象を選択する（または「全キャンペーン」）
⑦ 件数バッジで削除対象件数を確認する
⑧ 詳細テーブルで削除候補の語句・ROAS・広告費を確認する（ROAS昇順で最も低いものが上位）
⑨ 削除対象KW一覧をコピーするか、ダウンロードページの「削除用KW ZIP」からCSVを取得する
⑩ Amazon広告管理画面で対象キーワードを「一時停止」する

---

### ■ 実行後の確認方法

```
[キャンペーン選択プルダウン: 全キャンペーン / 個別キャンペーン（削除用KW）]
[件数バッジ: 削除対象件数: N件（赤色）]
[削除対象KW一覧（コピー用コードブロック）]
[削除KW詳細テーブル: keyword / キャンペーン / ROAS / 広告費 / 売上]
                      ← ROAS昇順（最も効率の悪い語句が上位）
```

件数が **0件** の場合、削除条件（広告費≥売価×2 AND ROAS<0.8）を満たす語句がありません。
→ データ蓄積期間を延ばすと判定できる語句が増えます。

---

### ■ 注意事項

- 広告費とROASの**両方**が条件を満たした場合のみ削除候補になります。
- ROAS ≥ 0.8 の語句は成果が出ているため削除候補になりません。
- 広告費が売価×2未満の語句はデータ不足として削除候補になりません。
- 勝ちKW（追加候補）と重複する語句は削除候補から自動除外されています。
- CSVはこのページではなく「ダウンロードページ」の「削除用KW ZIP」から出力します。
- 削除はツールで自動実行しません。必ず手動でAmazon広告管理画面で停止してください。
- **「一時停止」を推奨します。**完全削除すると元に戻せません。

---

### ■ おすすめ運用

- 毎週CSVを更新し、削除候補を確認する。
- 停止前に詳細テーブルを確認し、意図しない語句が含まれていないか確かめる。
- 停止後は「改善履歴」へ登録し、分析機能でBefore / After比較を実施する。
- 一時停止後1〜2週間様子を見てから完全削除を判断する。
- 停止後7日経過したら分析機能で広告費削減効果を確認する。
""")

    with st.expander("📈 キーワードCPC調整"):
        st.markdown("""
**目的**

SP広告マニュアルキャンペーンのキーワード入札額（CPC）を最適化します。
ROASと購入数の実績に基づいてランクを判定し、
入札引き上げ・現状維持・引き下げ・即削除の推奨アクションと推奨CPCを表示します。
サイドバーの「📈 キーワードCPC調整」を選択するとこのページが表示されます。

---

**集計元データ**

「検索用語レポート」内のSP広告マニュアルキャンペーン行の
「キーワードテキスト（Keyword Text）」列をキャンペーン単位で集計します。

| 項目 | 内容 |
|---|---|
| 集計対象キャンペーン | 「SP広告.*マニュアル」「SP.*manual」を含むキャンペーン |
| 集計単位 | キャンペーン × キーワードテキスト（正規化後） |
| 集計項目 | 広告費・売上・購入数・クリック数の合算 |

---

**ランク判定（STEP1 → STEP2 → STEP3 → STEP4 の順で判定）**

| ランク | 判定条件 | アクション | CPC変更幅 |
|---|---|---|---|
| 判断保留 | 広告費 < ¥3,000 **かつ** 購入数 < 4件 | 変更なし | ±0円 |
| SS+ | 購入数 ≥ 20件 **かつ** ROAS ≥ 4.0 | CPC上げ | +5円 |
| SS | 購入数 ≥ 20件 **かつ** 2.0 ≤ ROAS < 4.0 | 現状維持 | ±0円 |
| S | ROAS ≥ 4.0 | CPC上げ | +5円 |
| A | 3.0 ≤ ROAS < 4.0 | 現状維持 | ±0円 |
| B | 1.8 ≤ ROAS < 3.0 | 現状維持 | ±0円 |
| C | 1.5 ≤ ROAS < 1.8 | CPC下げ | −5円 |
| D | ROAS < 1.5 | CPC下げ | −10円 |
| 即削除 | ROAS < 0.8 **かつ** 広告費 ≥ 閾値 | 即削除 | — |

**即削除閾値**

| 売価 | 広告費閾値 |
|---|---|
| ≤ ¥1,500 | ≥ ¥3,000 |
| ≤ ¥2,000 | ≥ ¥4,000 |
| > ¥2,000 | ≥ ¥5,000 |

---

**除外条件**

| 除外対象 | 内容 |
|---|---|
| オートキャンペーン行（「オート」「auto」） | 手動入札が設定されていないため除外 |
| 商品ターゲティング行（「商品ターゲ」「動画ターゲ」） | 商品CPC・動画CPC調整ページで管理 |
| ASIN形式・category形式・complement・substitute | キーワードではなくターゲティング指定 |
| Keyword Text（キーワードテキスト）が空欄の行 | キーワードが特定できないため除外 |
| ブランドキーワード（アニハ・あには・アニは） | 自社ブランドKWは除外対象 |
| 売価マスタ未登録の商品 | 即削除閾値が計算できないため除外 |

---

**画面の表示内容**

| 表示エリア | 内容 |
|---|---|
| 商品選択プルダウン | 全商品（初期値）/ 個別商品 |
| 件数カード | SS+ / SS / S / A / B / C / D / 即削除 ← **全件数**（判断保留・現状維持を含む） |
| 本日調整対象 | CPC上げN件 / CPC下げN件 |
| 詳細テーブル | **変更幅 ≠ 0のみ**（CPC上げ・下げ対象のみ） |

> ⚠️ 件数カードの合計とテーブルの件数は一致しません。SS / A / B / 判断保留はカードのみに表示されます。

---

### ■ 目的

SP広告マニュアルキャンペーンのキーワード入札額（CPC）を最適化します。
成果の高いキーワードの入札を引き上げて露出を拡大し、
成果の低いキーワードの入札を下げてコスト効率を改善します。
感覚ではなく ROASと購入数の実績という客観的な指標でランクを判定します。

---

### ■ 集計元データ

「検索用語レポート」内のSP広告マニュアルキャンペーン行が集計対象です。
「SP広告.*マニュアル」または「SP.*manual」を含むキャンペーン名の行を抽出します。
「キーワードテキスト（Keyword Text）」列を集計の基準列とします。
同一キャンペーン内で同一キーワードが複数行ある場合は、
広告費・売上・購入数・クリック数を合算（sum）します。

---

### ■ 使用するCSV

Amazon広告管理画面の **「検索用語レポート」** を使用します。
集計期間は **14〜30日** を推奨します。
期間が短いと「判断保留」（データ不足）になるキーワードが増えます。

---

### ■ 対象

以下の手順ですべての条件を通過したキーワードがCPC調整の対象になります。

| ステップ | 内容 |
|---|---|
| ① SP広告マニュアルキャンペーン行の抽出 | 「SP広告.*マニュアル」「SP.*manual」を含む行のみ残す |
| ② オートキャンペーン除外 | キャンペーン名に「オート」「auto」を含む行を除外 |
| ③ 商品・動画ターゲ除外 | 「商品ターゲ」「動画ターゲ」を含む行を除外 |
| ④ KW種別除外 | ASIN形式・category形式・complement・substituteを除外 |
| ⑤ ブランドKW除外 | 「アニハ」「あには」「アニは」を含む語句を除外 |
| ⑥ Keyword Text空欄除外 | Keyword Text列が空欄の行を除外 |
| ⑦ キャンペーン×KWテキスト単位で集計 | 広告費・売上・購入数・クリック数を合算 |
| ⑧ 売価マスタを参照 | 未登録商品は除外 |
| ⑨ ランク判定 | STEP1→STEP2→STEP3→STEP4の順で判定 |

---

### ■ 処理ロジック

```
SP広告マニュアルキャンペーン行を抽出
        ↓
除外処理
（オート / 商品ターゲ / 動画ターゲ / ASIN形式 / category形式
 / complement / substitute / ブランドKW / Keyword Text空欄）
        ↓
キャンペーン × キーワードテキスト（正規化後）の単位で集計
（広告費・売上・購入数・クリック数を合算）
        ↓
現在CPC算出（広告費 ÷ クリック数）
        ↓
ランク判定（STEP1→STEP2→STEP3→STEP4）
  STEP1: 判断保留（広告費<¥3,000 AND 購入数<4件）
  STEP2: SS+・SS（購入数≥20件での高実績判定）
  STEP3: S・A・B・C・D（ROASベース判定）
  STEP4: 即削除（ROAS<0.8 AND 広告費≥即削除閾値）
        ↓
変更幅算出（+5 / 0 / -5 / -10円）
推奨CPC算出（現在CPC + 変更幅、最低1円）
        ↓
商品選択で絞り込み（任意）
        ↓
件数カード（全件数）・本日調整対象・詳細テーブル（変更幅≠0のみ）に表示
```

---

### ■ 判定条件

**STEP1（最優先）：データ不足判定**

| 条件 | 閾値 | 結果 |
|---|---|---|
| 広告費 < ¥3,000 **かつ** 購入数 < 4件 | 両方 | 判断保留（変更なし） |

> STEP1を通過したKWのみ STEP2以降で判定します。
> 広告費≥¥3,000 **または** 購入数≥4件の場合、STEP1は通過します。

**STEP2：高実績ランク（購入数優先）**

| ランク | 条件 | 変更幅 |
|---|---|---|
| SS+ | 購入数 ≥ 20件 **かつ** ROAS ≥ 4.0 | +5円 |
| SS | 購入数 ≥ 20件 **かつ** 2.0 ≤ ROAS < 4.0 | ±0円 |

**STEP3：ROASベースランク**

| ランク | 条件 | 変更幅 |
|---|---|---|
| S | ROAS ≥ 4.0 | +5円 |
| A | 3.0 ≤ ROAS < 4.0 | ±0円 |
| B | 1.8 ≤ ROAS < 3.0 | ±0円 |
| C | 1.5 ≤ ROAS < 1.8 | −5円 |
| D | ROAS < 1.5 | −10円 |

**STEP4：即削除判定**

| 条件 | 閾値 | 結果 |
|---|---|---|
| ROAS < 0.8 **かつ** 広告費 ≥ 即削除閾値 | 両方 | 即削除 |

---

### ■ 対象になるケース

```
例（SS+）: 広告費¥5,000 / 購入数25件 / ROAS 5.0 / 現在CPC ¥100
  → STEP1通過（広告費≥¥3,000）
  → STEP2: SS+（購入数25≥20 AND ROAS 5.0≥4.0）
  → 変更幅 +5円 / 推奨CPC ¥105

例（D）: 広告費¥4,000 / 購入数2件 / ROAS 1.2 / 現在CPC ¥200
  → STEP1通過（広告費≥¥3,000）
  → STEP2非該当（購入数2<20）
  → STEP3: D（ROAS 1.2 < 1.5）
  → 変更幅 −10円 / 推奨CPC ¥190
```

---

### ■ 対象にならないケース

| ケース | ランク / 理由 |
|---|---|
| 広告費 < ¥3,000 かつ 購入数 < 4件 | 判断保留（データ不足） |
| ROAS ≥ 1.8 かつ ランクA・B・SS | 現状維持（変更幅 ±0円）→ テーブル非表示 |
| オートキャンペーン行 | 除外（手動入札が設定されていない） |
| 商品ターゲ・動画ターゲ行 | 除外（商品CPC・動画CPCページで管理） |
| ASIN形式・category形式 | 除外（キーワードではない） |
| ブランドKW | 除外（自社ブランドKWは対象外） |
| Keyword Text 空欄 | 除外（KWが特定できない） |
| 売価マスタ未登録商品 | 除外（即削除閾値が計算できない） |

---

### ■ 処理の流れ

① 検索用語レポート（CSV）をアップロードする
② 「🔍 抽出実行」ボタンを押す
③ SP広告マニュアルキャンペーン行を自動抽出する
④ オート・商品ターゲ・ASIN形式・ブランドKW・空欄を除外する
⑤ キャンペーン×キーワードテキスト単位で広告費・売上・購入数・クリック数を集計する
⑥ 現在CPCを算出する（広告費 ÷ クリック数）
⑦ ランクを判定する（STEP1→STEP2→STEP3→STEP4）
⑧ 変更幅・推奨CPCを算出する
⑨ 商品選択で絞り込む（任意）
⑩ 件数カード・本日調整対象・詳細テーブルに結果を表示する

---

### ■ 実際の操作手順

① Amazon広告管理画面にログインする
② 「レポート」→「広告レポート」→「検索用語レポート」を選択してCSVをダウンロードする
③ ツール画面の上部「CSVアップロード」からファイルを選択する
④ 「🔍 抽出実行」ボタンを押す
⑤ サイドバーの「📈 キーワードCPC調整」を選択してこのページを開く
⑥ 「商品選択」プルダウンで対象商品を選択する（または「全商品」）
⑦ 件数カードでランク別件数を確認する（全件数が表示されます）
⑧ 「本日調整対象」でCPC上げ・CPC下げの件数を確認する
⑨ 詳細テーブルで各キーワードのランク・変更幅・推奨CPCを確認する（変更幅≠0のみ表示）
⑩ CSVをダウンロードする（ダウンロードボタン）
⑪ Amazon広告管理画面でSP広告マニュアルキャンペーンを開く
⑫ 各キーワードの入札額を推奨CPCに変更する
⑬ 「即削除」ランクのキーワードは一時停止または削除する

---

### ■ 実行後の確認方法

```
[商品選択プルダウン: 全商品（初期値）/ 個別商品]
[件数カード: SS+ / SS / S / A / B / C / D / 即削除]
              ← 全件数（SS/A/B/判断保留を含む）
[本日調整対象: CPC上げ N件 / CPC下げ N件]
[詳細テーブル: keyword / キャンペーン名 / 広告グループ / ROAS / 広告費 / 売上 /
              購入数 / 現在CPC / 判定ランク / 推奨アクション / 変更幅 / 推奨CPC]
              ← 変更幅≠0（SS+・S・C・D・即削除）のみ表示
[CSVダウンロードボタン]
```

件数カードは変更なし（SS / A / B）・判断保留を含む**全件数**を表示します。
詳細テーブルは変更幅≠0のみを表示するため、件数カードの合計とテーブルの件数は一致しません。

---

### ■ 注意事項

- 件数カードの合計とテーブルの件数は一致しません。SS / A / B / 判断保留はテーブルに表示されません。
- 「判断保留」はデータ不足です。集計期間を延ばすと判定対象になります。
- 「即削除」ランクはツールで自動停止しません。Amazon広告管理画面で手動停止してください。
- 推奨CPCは「現在CPC ＋ 変更幅」で算出された参考値です。広告管理画面の入札額は自動更新されません。
- CPC変更後は入札額が反映されるまで数時間かかる場合があります。
- 変更後は1〜2週間データを蓄積してから再分析することを推奨します。
- 売価マスタ（PRICES辞書）に未登録の商品は集計から除外されます。

---

### ■ おすすめ運用

- 毎週CSVを更新し、ランク変動を確認する。
- CPC変更後は「改善履歴」に登録し、分析機能でBefore / After比較を実施する。
- 判断保留が多い場合は集計期間を30日に延ばす。
- 即削除ランクは優先的に処理し、広告費の無駄を削減する。
- 変更後7日経過したら分析機能でCPC変更の効果を確認する。
""")

    # ── オート除外KW ──────────────────────────────────────────────────
    with st.expander("🧹 オート除外KW"):
        st.markdown("""
**目的**

オート広告（自動ターゲティング）で利益を毀損している項目を検出し、停止候補として
「キーワード」「商品」「動画」の3ページに分けて表示します。

サイドバーの「🧹 オート除外KW」内に、キーワード・商品・動画の3つのボタンがあります。

---

**集計の元データ**

「検索用語レポート」内のオートキャンペーン行から、「検索用語（カスタマーの検索用語）」列の
内容によって3種類に振り分けます。

| 検索用語の内容 | 振り分け先 |
|---|---|
| 通常の検索語句（日本語など） | 📄 キーワード |
| ASIN形式（例: B0XXXXXXXXX） | 🎯 商品 |
| category形式（例: category:〜） | 🎬 動画 |

商品・動画に振り分けられた項目は、既存の「🎯 オート商品削除」「🎥 オート動画削除」
ページ（商品ターゲティング・動画キャンペーン由来のASIN削除候補）と合流して表示されます。

---

**削除条件（両方を同時に満たす場合に削除候補）**

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ 売価 × 2 |
| ROAS | ≤ 0.8 |

---

**除外条件**

| 除外対象 | 内容 |
|---|---|
| マニュアル広告に完全一致登録済みの語句／ASIN | 重複のため除外 |
| 未分類キャンペーン | 売価が特定できないため除外 |

---

**各ページの表示内容**

| ページ | 表示内容 |
|---|---|
| 📄 キーワード | 通常検索語句のみ（ASIN・category形式は含まれません） |
| 🎯 商品 | ASIN形式のみ |
| 🎬 動画 | category形式のみ |

各ページに「キャンペーン」フィルター（全キャンペーン＋各キャンペーン）があり、
件数カード・除外対象一覧（コピー用）・詳細テーブル・CSVダウンロードを表示します。

---

### ■ 対象データ

オートキャンペーン（自動ターゲティング）から発生した検索語句・ASIN・categoryが対象です。
マニュアルキャンペーンの行は除外されます。

---

### ■ 使用するCSV

Amazon広告管理画面の **「検索用語レポート」** を使用します。
集計期間は14〜30日を推奨します。

---

### ■ 処理ロジック

```
オートキャンペーン行を抽出
        ↓
検索語句の内容で振り分け
  ├ 通常語句 → キーワードページ
  ├ ASIN形式 → 商品ページ（オート商品削除と合流）
  └ category形式 → 動画ページ（オート動画削除と合流）
        ↓
削除条件チェック（広告費≥売価×2 AND ROAS≤0.8）
        ↓
除外処理（マニュアル完全一致登録済み・未分類を除外）
        ↓
各ページに除外候補を表示
```

---

### ■ 処理の流れ

① CSVをアップロードし「抽出実行」を押す
② オートキャンペーン行を自動抽出
③ 検索語句の形式によってキーワード・商品・動画に振り分け
④ 削除条件（広告費・ROAS）を両方満たす項目を選別
⑤ 除外条件に該当する項目を除外
⑥ 各ページに除外候補を表示

---

### ■ 実際の操作手順

① Amazon広告管理画面から「検索用語レポート」をダウンロードする
② ツール画面の上部「CSVアップロード」からファイルを選択する
③ 「🔍 抽出実行」ボタンを押す
④ サイドバーの「🧹 オート除外KW」から「キーワード」「商品」「動画」のいずれかを選択する
⑤ キャンペーンフィルターで対象キャンペーンを選択する（または「全キャンペーン」）
⑥ 件数カードと一覧で除外候補を確認する
⑦ 除外対象一覧（コピー用）をコピーするか、CSVをダウンロードする
⑧ Amazon広告管理画面のオートキャンペーンのネガティブターゲティングへ登録する

---

### ■ 実行後の確認方法

```
[キャンペーンフィルター: 全キャンペーン / 個別キャンペーン]
[件数カード: 除外候補件数]
[除外対象一覧（コピー用）]
[詳細テーブル: 語句 / キャンペーン / ROAS / 広告費 / 売上]
[CSVダウンロードボタン]
```

---

### ■ 注意事項

- キーワード・商品・動画は別ページに表示されます。サイドバーから切り替えてください。
- 除外はツールで自動実行しません。Amazon広告管理画面で手動登録してください。
- マニュアル広告に登録済みの語句は除外候補から自動除外されています。

---

### ■ おすすめ運用

- 毎週CSVを更新し、除外候補を確認する。
- 除外後は「改善履歴」に登録し、分析機能でオート広告費削減効果を確認する。
- 商品・動画ページは「オート商品削除」「オート動画削除」ページと合流しているため、両方から確認する。
""")

    # ── 分析 ─────────────────────────────────────────────────────────
    with st.expander("📊 分析"):
        st.markdown("""
## 📊 分析機能

分析機能は、**改善で抽出した対象**の成果変化を確認する機能です。
新しい抽出ロジックは使いません。改善対象だけを分析します。

---

### ■ 目的

改善タブ（キーワード追加・商品追加・動画追加・CPC調整）で抽出した対象が、
その後の実績でどう変化したかをCSV比較で確認します。

---

### ■ 分析対象

| 分析タブ | 分析対象 |
|---------|---------|
| キーワード追加 / 分析 | 改善タブで追加候補となったキーワード |
| 商品追加 / 分析 | 改善タブで追加候補となったASIN |
| 動画追加 / 分析 | 改善タブで追加候補となった動画ASIN |
| キーワードCPC調整 / 分析 | 改善タブでCPC調整対象となったキーワード |
| 商品CPC調整 / 分析 | 改善タブでCPC調整対象となった商品ASIN |
| 動画CPC調整 / 分析 | 改善タブでCPC調整対象となった動画ASIN |

---

### ■ 分析期間

| 対象 | 期間 |
|------|------|
| キーワード追加・商品追加・動画追加 | **30日固定** |
| キーワードCPC調整・商品CPC調整・動画CPC調整 | **7日固定** |

期間変更機能はありません。30日 / 7日のみ使用してください。

---

### ■ 分析フロー

```
① 改善タブで「抽出実行」
        ↓
② 改善対象が抽出される
（キーワード追加: 30日後に確認 / CPC調整: 7日後に確認）
        ↓
③ 分析タブへ移動
        ↓
④ 比較用CSVをアップロード
（キーワード追加 → 30日レポート / CPC調整 → 7日レポート）
        ↓
⑤ 「分析実行」ボタンを押す
        ↓
⑥ 改善対象を新CSVから自動検索
（同一キャンペーン + キーワード/ASIN で照合）
        ↓
⑦ Before（改善時） / After（新CSV）を比較表示
        ↓
⑧ 「分析結果を保存」ボタンで履歴に保存
```

---

### ■ キーワード追加 分析

- **使用レポート**: 30日レポートCSV
- **分析対象**: 改善タブで追加候補となったキーワードのみ
- **Before**: 改善実行時のデータ（改善タブで抽出した時点の実績）
- **After**: 新しくアップロードした30日レポートの実績
- **照合キー**: キャンペーン + 正規化キーワード（表記ゆれを吸収）

---

### ■ CPC調整 分析

- **使用レポート**: 7日レポートCSV
- **分析対象**: 改善タブでCPC調整対象となったキーワード/ASINのみ
- **Before**: 改善実行時のデータ（CPC調整前の実績）
- **After**: 新しくアップロードした7日レポートの実績
- **照合キー**: キャンペーン + Keyword Text / ASIN

---

### ■ 比較項目

売上 / 広告費 / ROAS / CTR / CVR / CPC / クリック数 / 注文数

各項目に対して「↑ 改善」「↓ 悪化」「→ 変化なし」を自動判定します（変化率 ±2% 未満は「変化なし」）。

---

### ■ 画面構成

```
① キャンペーン一覧（改善対象）
② キャンペーン選択
③ 比較用CSVをアップロード（30日 or 7日）
④ 分析実行ボタン
⑤ キャンペーン比較（Before / After）
⑥ 対象別 Before / After 一覧
⑦ 分析結果を保存ボタン
⑧ 保存済み分析履歴
```

---

### ■ 操作手順

**① 改善タブで抽出実行**
キーワード追加 or CPC調整の「改善」タブでCSVをアップロードし「抽出実行」を押します。
この時の抽出結果が分析の「Before」データになります。

**② 改善を実施（KW追加 / CPC変更）**
抽出結果を使ってAmazon広告の改善を実施します。

**③ 期間経過後にCSVを取得**
- キーワード追加 → 30日後に30日レポートを取得
- CPC調整 → 7日後に7日レポートを取得

**④ 分析タブへ移動**
同じページの「分析」タブをクリックします。

**⑤ 比較用CSVをアップロード**
新しいレポートCSVをアップロードします。

**⑥ 分析実行**
「🔍 分析実行」ボタンを押します。
改善対象と新CSV内の同一キーワード/ASINを自動照合します。

**⑦ 結果確認**
Before / After 比較表とキャンペーン別集計が表示されます。

**⑧ 分析結果を保存**
「💾 分析結果を保存」ボタンで履歴に追記されます。

---

### ■ 注意事項

- 「改善」タブで抽出実行してから「分析」タブを開いてください。先に分析タブを開いても対象データがありません。
- キーワード追加分析は30日レポートを使用してください。7日レポートでは比較対象が異なります。
- CPC調整分析は7日レポートを使用してください。
- 新旧CSVで同一キャンペーン名 / キーワードが含まれていないとマッチしません。
- 分析結果の保存先: `analysis_data/` フォルダ（JSONファイル）

---

### ■ おすすめ運用

**キーワード追加**
① 改善タブで抽出実行 → KW追加を実施
② 30日後に新しい30日レポートを取得
③ 分析タブで比較 → 売上・ROAS改善を確認
④ 分析結果を保存

**CPC調整**
① 改善タブで抽出実行 → CPC変更を実施
② 7日後に新しい7日レポートを取得
③ 分析タブで比較 → ROAS・CPC変化を確認
④ 分析結果を保存
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
