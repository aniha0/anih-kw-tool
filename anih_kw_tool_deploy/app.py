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
              "df_auto_del_kw", "df_auto_del_product", "df_auto_del_video"]:
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
        del_mask = (agg["cost"] >= agg["price"] * 2) & (agg["ROAS"] < 0.8)
        df_del_ = agg[del_mask].copy()
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
            s = str(s).strip()
            return (bool(_re.match(r"^[Bb]0[A-Za-z0-9]{8}", s))
                    or s.lower().startswith("category:")
                    or s.lower().startswith("asin:")
                    or "complement" in s.lower()
                    or "substitute" in s.lower())
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
        # キーワード: オートKW中、マニュアルKWと重複しない出血KW
        if kc and tkc:
            _auto_kw_base = dfs[dfs[cc].str.contains("オート|auto", case=False, na=False)].copy()
            _auto_kw_base["ct"] = _auto_kw_base[cc].apply(lambda x: official(get_theme(str(x))))
            _auto_kw_base["kn"] = _auto_kw_base[kc].apply(norm)
            _manual_mask_kw = ~dfs[cc].str.contains("オート|auto", case=False, na=False)
            _manual_reg_kw = set(dfs[_manual_mask_kw][tkc].apply(norm)); _manual_reg_kw.discard("")
            _dup_kw = _auto_kw_base["kn"].isin(_manual_reg_kw)
            _auto_kw_base = _auto_kw_base[~_dup_kw].copy()
            _agg_akw_d = {
                "keyword":        (kc,    "first"),
                "campaign_theme": ("ct",  lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else "未分類"),
                "sales":          (sc,    "sum"),
                "cost":           (oc_,   "sum"),
            }
            if od:  _agg_akw_d["orders"]   = (od,  "sum")
            if agn: _agg_akw_d["ad_group"] = (agn, "first")
            _agg_akw = _auto_kw_base.groupby("kn").agg(**_agg_akw_d).reset_index(drop=True)
            _agg_akw["ROAS"]  = _agg_akw.apply(
                lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
            _agg_akw["price"] = _agg_akw["campaign_theme"].map(PRICES)
            _agg_akw = _agg_akw[_agg_akw["price"].notna()].copy()
            df_auto_del_kw_ = _agg_akw[
                (_agg_akw["cost"] >= _agg_akw["price"] * 2) & (_agg_akw["ROAS"] <= 0.5)
            ].copy()
            df_auto_del_kw_.drop(columns=["price"], errors="ignore", inplace=True)
        else:
            df_auto_del_kw_ = pd.DataFrame()

        # 商品/動画: オートASIN中、マニュアルASINと重複しない出血ASIN
        def _build_auto_asin_del(camp_mask, manual_mask):
            _d = _mpt_base[camp_mask].copy()
            if _d.empty or not tkc: return pd.DataFrame()
            _d["_asin_clean"] = _d[tkc].apply(_extract_asin)
            _d = _d[_d["_asin_clean"] != ""].copy()
            if _d.empty: return pd.DataFrame()
            _manual_asins = set(_mpt_base[manual_mask][tkc].apply(_extract_asin)); _manual_asins.discard("")
            _d = _d[~_d["_asin_clean"].isin(_manual_asins)].copy()
            if _d.empty: return pd.DataFrame()
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
            _agg3["ROAS"]  = _agg3.apply(
                lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1)
            _agg3["price"] = _agg3["campaign_theme"].map(PRICES)
            _agg3 = _agg3[_agg3["price"].notna()].copy()
            _result = _agg3[
                (_agg3["cost"] >= _agg3["price"] * 2) & (_agg3["ROAS"] <= 0.5)
            ].copy()
            _result.drop(columns=["price"], errors="ignore", inplace=True)
            return _result

        _mask_auto_pt_del = (
            _mpt_base[cc].str.contains("商品ターゲ", na=False) &
            _mpt_base[cc].str.contains("オート|auto", case=False, na=False) &
            ~_mpt_base[cc].str.contains("動画", na=False)
        )
        _mask_auto_vid_del = (
            _mpt_base[cc].str.contains("動画", na=False) &
            _mpt_base[cc].str.contains("オート|auto", case=False, na=False)
        )
        df_auto_del_product_ = _build_auto_asin_del(_mask_auto_pt_del, _mask_m)
        df_auto_del_video_   = _build_auto_asin_del(_mask_auto_vid_del, _mask_v)
        # ────────────────────────────────────────────────────────────

        st.session_state.update({
            "has_results": True, "df_win": dw,
            "df_del": df_del_, "df_cpc": df_cpc_,
            "df_pt_add_m": df_pt_add_m_, "df_pt_del_m": df_pt_del_m_,
            "df_pt_add_v": df_pt_add_v_, "df_pt_del_v": df_pt_del_v_,
            "df_cpc_product": df_cpc_product_, "df_cpc_video": df_cpc_video_,
            "df_auto_del_kw": df_auto_del_kw_,
            "df_auto_del_product": df_auto_del_product_,
            "df_auto_del_video": df_auto_del_video_,
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
df_auto_del_kw:      pd.DataFrame = st.session_state.get("df_auto_del_kw",      pd.DataFrame())
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



def page_auto_del_kw():
    df = st.session_state.get("df_auto_del_kw", pd.DataFrame())
    if df.empty:
        st.info("除外候補のキーワードはありません。（オートKWで出血中かつマニュアル未登録のものなし）")
        return
    st.markdown(f"**除外候補: {len(df)}件** — 広告費 ≥ 売価×2 かつ ROAS ≤ 0.5 / マニュアルKW重複除外済み")
    _dcols = [c for c in ["keyword","campaign_theme","cost","ROAS","sales","orders","ad_group"] if c in df.columns]
    _rn = {"keyword":"検索語句","campaign_theme":"キャンペーン","cost":"広告費",
           "sales":"売上","orders":"購入数","ad_group":"広告グループ"}
    _d = df[_dcols].rename(columns=_rn).copy()
    if "広告費" in _d.columns: _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
    if "売上"   in _d.columns: _d["売上"]   = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
    if "ROAS"   in _d.columns: _d["ROAS"]   = _d["ROAS"].round(2)
    st.dataframe(_d, use_container_width=True)
    _csv = df[_dcols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 除外KW候補.csv", data=_csv, file_name="除外KW候補.csv", mime="text/csv")

def page_auto_del_product():
    df = st.session_state.get("df_auto_del_product", pd.DataFrame())
    if df.empty:
        st.info("除外候補の商品ASINはありません。（オート商品広告で出血中かつマニュアル未登録のものなし）")
        return
    st.markdown(f"**除外候補: {len(df)}件** — 広告費 ≥ 売価×2 かつ ROAS ≤ 0.5 / マニュアル商品重複除外済み")
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
    st.markdown(f"**除外候補: {len(df)}件** — 広告費 ≥ 売価×2 かつ ROAS ≤ 0.5 / マニュアル動画重複除外済み")
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
    _render_pt_cpc_page(dc_cpc_product, "商品CPC調整", "cpc_product_sel")

def page_cpc_video():
    _render_pt_cpc_page(dc_cpc_video, "動画CPC調整", "cpc_video_sel")


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
    _render_pt_page("df_pt_add_m", True,  "商品", "pt_add_m_sel")

def page_pt_del_manual():
    _render_pt_page("df_pt_del_m", False, "商品", "pt_del_m_sel")

def page_pt_add_video():
    _render_pt_page("df_pt_add_v", True,  "動画", "pt_add_v_sel")

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

DateDive売れる予測KW
ダウンロード
取扱説明書
```
""")

    # ── キーワード追加 ────────────────────────────────────────────────
    with st.expander("📋 キーワード追加"):
        st.markdown("""
**目的**
オート広告で成果が確認できた検索語句を、手動広告（部分一致）へ追加するための候補抽出です。

---

**信頼度フィルター（データ量が少ない語句を除外）**

| 条件 | 閾値 |
|---|---|
| 注文数 | ≥ 3件 |
| クリック数 | ≥ 5回 |
| 広告費 | ≥ ¥300 |

**採用条件**

| 条件 | 閾値 |
|---|---|
| 売上 | ≥ 売価 × 2 |
| ROAS | ≥ 2.0 |

**除外条件**

| 除外対象 | 内容 |
|---|---|
| 既存マニュアルKW（完全一致） | 登録済のため除外 |
| 既存マニュアルKW（部分一致） | 登録済のため除外 |
| 重複検索語 | 同一キャンペーン内での重複を除外 |
""")

    # ── キーワード削除 ────────────────────────────────────────────────
    with st.expander("🚫 キーワード削除"):
        st.markdown("""
**目的**
利益を毀損している検索語を抽出し、停止候補として表示します。

---

**削除条件（両方を同時に満たす場合に削除候補）**

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ 売価 × 2 |
| ROAS | < 0.8 |
""")

    # ── キーワードCPC調整─────────────────────────────────────────
    with st.expander("📈 キーワードCPC調整"):
        st.markdown("""
**目的** — 既存マニュアルKWの入札額を最適化します。

---

**判定対象条件**（いずれか一方を満たせば判定対象）

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ ¥3,000 |
| 購入数 | ≥ 4件 |

> **判断保留になるのは「広告費 < ¥3,000 かつ 購入数 < 4件」の場合のみです。**
>
> 例: 広告費¥1,000・購入数7件 → **判定対象**（購入数4件以上のため）
>
> 例: 広告費¥1,500・購入数2件 → **判断保留**（両方とも閾値未満）

---

**ランク判定（判定順序: STEP1 → STEP2 → STEP3 → STEP4）**

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
| 即削除 | 広告費 ≥ 閾値 **かつ** ROAS < 0.8 | 即削除 | — |

**即削除閾値:** 売価 ≤¥1,500 → ¥3,000 / 売価 ≤¥2,000 → ¥4,000 / 売価 >¥2,000 → ¥5,000

---

**画面表示仕様**

| 表示エリア | 表示内容 |
|---|---|
| SS〜E 件数カード | **全件数**を表示（変更なし・判断保留を含む） |
| 詳細テーブル | **全件表示**（変更なし・判断保留を含む） |
""")

    # ── 商品追加 ──────────────────────────────────────────────────────
    with st.expander("➕ 商品追加"):
        st.markdown("""
**目的** — 商品広告で成果が出ているASINを追加候補として表示します。

---

**対象キャンペーン**

| 条件 | 内容 |
|---|---|
| 含む | 「商品ターゲ」を含むキャンペーン |
| 除外 | 「動画」を含むキャンペーン |
| 除外 | 「オート」「auto」を含むキャンペーン |

**信頼度フィルター**

| 条件 | 閾値 |
|---|---|
| 注文数 | ≥ 3件 |
| クリック数 | ≥ 5回 |
| 広告費 | ≥ ¥300 |

**採用条件**

| 条件 | 閾値 |
|---|---|
| 売上 | ≥ 売価 × 2 |
| ROAS | ≥ 2.0 |
""")

    # ── 商品削除 ──────────────────────────────────────────────────────
    with st.expander("🗑️ 商品削除"):
        st.markdown("""
**目的** — 成果の出ていない商品を停止候補として表示します。

---

**対象キャンペーン** — 商品追加と同一（動画・オート除外）

**削除条件（両方を同時に満たす場合に削除候補）**

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ 売価 × 2 |
| ROAS | < 0.8 |
""")

    # ── 動画追加 ──────────────────────────────────────────────────────
    with st.expander("📹 動画追加"):
        st.markdown("""
**目的** — 動画広告で成果の出ているASINを追加候補として表示します。

---

**対象キャンペーン**

| 条件 | 内容 |
|---|---|
| 含む | 「動画」を含むキャンペーン |

**信頼度フィルター**

| 条件 | 閾値 |
|---|---|
| 注文数 | ≥ 3件 |
| クリック数 | ≥ 5回 |
| 広告費 | ≥ ¥300 |

**採用条件**

| 条件 | 閾値 |
|---|---|
| 売上 | ≥ 売価 × 2 |
| ROAS | ≥ 2.0 |
""")

    # ── 動画削除 ──────────────────────────────────────────────────────
    with st.expander("📹 動画削除"):
        st.markdown("""
**目的** — 成果の出ていない動画広告を停止候補として表示します。

---

**対象キャンペーン** — 動画追加と同一（動画キャンペーンが対象）

**削除条件（両方を同時に満たす場合に削除候補）**

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ 売価 × 2 |
| ROAS | < 0.8 |
""")

    # ── 商品CPC調整 ───────────────────────────────────────────────────
    with st.expander("🎯 商品CPC調整"):
        st.markdown("""
**目的** — 商品広告の入札額を最適化します。

---

**判定対象条件**（いずれか一方を満たせば判定対象）

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ ¥3,000 |
| 購入数 | ≥ 4件 |

> **判断保留になるのは「広告費 < ¥3,000 かつ 購入数 < 4件」の場合のみです。**
>
> 例: 広告費¥1,000・購入数7件 → **判定対象**（購入数4件以上のため）
>
> 例: 広告費¥1,500・購入数2件 → **判断保留**（両方とも閾値未満）

---

**ランク判定**

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
| 即削除 | 広告費 ≥ 閾値 **かつ** ROAS < 0.8 | 即削除 | — |

**即削除閾値:** 売価 ≤¥1,500 → ¥3,000 / 売価 ≤¥2,000 → ¥4,000 / 売価 >¥2,000 → ¥5,000

---

**画面表示仕様**

| 表示エリア | 表示内容 |
|---|---|
| SS〜E 件数カード | **全件数**を表示（変更なし・判断保留を含む） |
| 詳細テーブル | **変更幅 ≠ 0円のみ**表示（CPC上げ・CPC下げのみ） |
| 非表示 | 変更なし（SS / A / B）・判断保留 |

> ⚠️ 件数カードの合計とテーブルの件数は一致しない場合があります。これは正常動作です。

---

**全商品フィルター**

初期値は **全商品** です。

| 選択 | 動作 |
|---|---|
| 全商品（初期値） | すべての対象キャンペーンを集計して表示する |
| 個別商品名 | 選択商品のキャンペーンのみ表示する |
""")

    # ── 動画CPC調整 ───────────────────────────────────────────────────
    with st.expander("📹 動画CPC調整"):
        st.markdown("""
**目的** — 動画広告の入札額を最適化します。

---

**判定対象条件**（いずれか一方を満たせば判定対象）

| 条件 | 閾値 |
|---|---|
| 広告費 | ≥ ¥3,000 |
| 購入数 | ≥ 4件 |

> **判断保留になるのは「広告費 < ¥3,000 かつ 購入数 < 4件」の場合のみです。**
>
> 例: 広告費¥1,000・購入数7件 → **判定対象**（購入数4件以上のため）
>
> 例: 広告費¥1,500・購入数2件 → **判断保留**（両方とも閾値未満）

---

**ランク判定**

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
| 即削除 | 広告費 ≥ 閾値 **かつ** ROAS < 0.8 | 即削除 | — |

**即削除閾値:** 売価 ≤¥1,500 → ¥3,000 / 売価 ≤¥2,000 → ¥4,000 / 売価 >¥2,000 → ¥5,000

---

**画面表示仕様**

| 表示エリア | 表示内容 |
|---|---|
| SS〜E 件数カード | **全件数**を表示（変更なし・判断保留を含む） |
| 詳細テーブル | **変更幅 ≠ 0円のみ**表示（CPC上げ・CPC下げのみ） |
| 非表示 | 変更なし（SS / A / B）・判断保留 |

> ⚠️ 件数カードの合計とテーブルの件数は一致しない場合があります。これは正常動作です。

---

**全商品フィルター**

初期値は **全商品** です。

| 選択 | 動作 |
|---|---|
| 全商品（初期値） | すべての動画キャンペーンを集計して表示する |
| 個別商品名 | 選択商品の動画キャンペーンのみ表示する |
""")

    # ── 売れる予測KW ──────────────────────────────────────────────────
    with st.expander("📊 DateDive 売れる予測KW"):
        st.markdown("""
**目的** — 今後Amazon検索語へ追加すべき有力キーワード候補をスコアリングして抽出します。

---

**スコア配点（合計100点）**

| 項目 | 配点 | 説明 |
|---|---|---|
| 需要 | 45点 | 検索ボリューム・トレンドを評価 |
| 関連性 | 35点 | 商品との関連度を評価 |
| 競争強度 | 15点 | 競合の少なさを評価 |
| 未使用KW | 5点 | 既存KWに未登録であれば加点 |

需要と関連性を最重視します。
競争強度や未使用ボーナスのみで上位表示されることはありません。

上位 **TOP10** を表示します。
""")

    # ── ダウンロード ──────────────────────────────────────────────────
    with st.expander("📥 ダウンロード"):
        st.markdown("""
各分析結果はCSVでダウンロード可能です。

**各ページ内のCSVダウンロードボタン**

| 機能 | CSVファイル名 |
|---|---|
| 商品追加 | 商品追加_{商品名}.csv |
| 商品削除 | 商品削除_{商品名}.csv |
| 動画追加 | 動画追加_{商品名}.csv |
| 動画削除 | 動画削除_{商品名}.csv |
| キーワードCPC調整 | {キャンペーン名}_CPC調整表.csv |
| 商品CPC調整 | {キャンペーン名}_商品CPC調整_CPC調整表.csv |
| 動画CPC調整 | {キャンペーン名}_動画CPC調整_CPC調整表.csv |

**ダウンロードページ（ZIPファイル）**

| ZIP名 | 内容 |
|---|---|
| 全候補 勝ちKW ZIP | キーワード追加 — 追加KW_{商品名}.csv |
| 削除用KW ZIP | キーワード削除 — 削除KW_{商品名}.csv |
| キーワードCPC調整 ZIP | キーワードCPC調整 — {キャンペーン名}_CPC調整表.csv |

> ⚠️ CSVは全件出力です（KW CPC調整テーブルの±0円行も含みます）。
""")

    # ── ASIN抽出 ──────────────────────────────────────────────────────
    with st.expander("🔍 ASIN抽出 対応形式"):
        st.markdown("""
商品広告・動画広告のASINは以下3形式をすべて自動認識します。

| 形式 | 例 |
|---|---|
| `asin="B0XXXXXXXX"` | TargetingExpression 標準形式 |
| `asin-expanded="B0XXXXXXXX"` | 拡張ターゲティング形式 |
| 裸ASIN `B0XXXXXXXX` | 直接記述形式 |

> ASINは `B0` で始まる10文字（`B0[A-Z0-9]{8}`）で識別します。
""")

    # ── 画面サンプル ──────────────────────────────────────────────────
    with st.expander("🖼️ 画面サンプル"):
        st.markdown("""
各ページの主な表示構成は以下の通りです。

---

**【追加】キーワード追加**
```
[条件バー: 最小注文数 / 最小クリック数 / 最小広告費]
[商品選択プルダウン]
[KPIカード: 抽出前件数 / 抽出後件数（同一意図KW統合後）]
[一覧テーブル: keyword / キャンペーン名 / ROAS / 広告費 / 売上 / 注文数 / クリック数]
※ CSVはダウンロードページの「全候補 勝ちKW ZIP」から取得
```

---

**【追加】商品（商品追加）**
```
[条件バー: 売上≥売価×2 / ROAS≥2.0 / 商品]
[商品選択プルダウン]
[KPIカード: 追加候補数 / 平均ROAS / 平均注文数 / 平均広告費]
[一覧テーブル: キャンペーン名 / 広告グループ / ASIN / 注文数 / クリック数 / 広告費 / 売上 / ROAS / 採用理由]
[CSVダウンロードボタン: 商品追加_{商品名}.csv]
```

---

**【追加】動画（動画追加）**
```
[条件バー: 売上≥売価×2 / ROAS≥2.0 / 動画]
[商品選択プルダウン]
[KPIカード: 追加候補数 / 平均ROAS / 平均注文数 / 平均広告費]
[一覧テーブル: キャンペーン名 / 広告グループ / ASIN / 注文数 / クリック数 / 広告費 / 売上 / ROAS / 採用理由]
[CSVダウンロードボタン: 動画追加_{商品名}.csv]
```

---

**【削除】キーワード削除**
```
[条件バー: 広告費≥売価×2 / ROAS<0.8 / 勝ちKW除外]
[商品選択プルダウン]
[件数バッジ: 削除対象件数: N件]
[一覧テーブル: keyword / キャンペーン名 / ROAS / 広告費 / 売上]
※ CSVはダウンロードページの「削除用KW ZIP」から取得
```

---

**【削除】商品（商品削除）**
```
[条件バー: 広告費≥売価×2 / ROAS<0.8 / 商品]
[商品選択プルダウン]
[KPIカード: 削除候補数 / 平均ROAS / 平均広告費]
[一覧テーブル: ASIN / campaign / ROAS / cost / 削除理由]
[CSVダウンロードボタン]
```

---

**【削除】動画（動画削除）**
```
[条件バー: 広告費≥売価×2 / ROAS<0.8 / 動画]
[商品選択プルダウン]
[KPIカード: 削除候補数 / 平均ROAS / 平均広告費]
[一覧テーブル: ASIN / campaign / ROAS / cost / 削除理由]
[CSVダウンロードボタン]
```

---

**【CPC調整】キーワードCPC調整**
```
[条件バー: CPC調整ルール適用]
[ロジックテーブル expander]
[キャンペーン選択プルダウン]
[件数カード: SS+ / SS / S / A / B / C / D / 即削除]  ← 全件表示
[一覧テーブル: keyword / ROAS / cost / 現在CPC / ランク / 変更幅 / 推奨CPC]
                                                      ← 全件表示（±0含む）
[CSVダウンロードボタン]
```

---

**【CPC調整】商品CPC調整**
```
[条件バー: CPC調整ルール適用 / 商品]
[ロジックテーブル expander]
[商品選択プルダウン: 全商品 / 液体 / 涙やけ / ...]  ← 全商品が初期値
[件数カード: SS+ / SS / S / A / B / C / D / 即削除]  ← 全件表示
[一覧テーブル: ASIN / ROAS / cost / 現在CPC / ランク / 変更幅 / 推奨CPC]
                                                       ← 変更幅≠0のみ
[CSVダウンロードボタン]
```

---

**【CPC調整】動画CPC調整**
```
[条件バー: CPC調整ルール適用 / 動画]
[ロジックテーブル expander]
[商品選択プルダウン: 全商品 / 液体 / 涙やけ / ...]  ← 全商品が初期値
[件数カード: SS+ / SS / S / A / B / C / D / 即削除]  ← 全件表示
[一覧テーブル: ASIN / ROAS / cost / 現在CPC / ランク / 変更幅 / 推奨CPC]
                                                       ← 変更幅≠0のみ
[CSVダウンロードボタン]
```
""")

    # ── トラブルシューティング ────────────────────────────────────────
    with st.expander("🛠️ トラブルシューティング"):
        st.markdown("""
**Q. データが表示されない**
→ 画面上部でCSVをアップロードし「🔍 抽出実行」ボタンを押してください。
ボタンを押すまで分析は実行されません。

---

**Q. 全商品が表示されない（商品が一部しか出ない）**
→ 売価マスタ（PRICES辞書）に登録されていない商品は除外されます。
campaign_theme が PRICES のキーと一致しているか確認してください。

---

**Q. 商品追加・動画追加が0件になる**
→ 以下を確認してください。
- 信頼度フィルター（注文≥3 / クリック≥5 / 広告費≥¥300）を満たす語句があるか
- 売上 ≥ 売価×2 かつ ROAS ≥ 2.0 を満たすASINがあるか
- 対象キャンペーン名に「商品ターゲ」が含まれているか

---

**Q. CPC調整（商品/動画）のテーブルが空になる**
→ 商品CPC / 動画CPCの詳細テーブルは「変更幅 ≠ 0円」のみ表示する仕様です。
SS / A / B（現状維持）と判断保留は表示されません。
件数カードで各ランクの件数を確認してください。
なお、キーワードCPC調整の詳細テーブルは**全件表示**です。

---

**Q. CSVが出力できない**
→ 分析結果が0件の場合もダウンロードボタンは表示されますが、
CSVは空ファイルになる場合があります。
データがあるページでダウンロードしてください。

---

**Q. 売価マスタ未登録時の動作**
→ campaign_theme が PRICES に存在しない商品は、
追加・削除・CPC調整のすべての判定から除外されます。
該当商品を利用するにはPRICES辞書への登録が必要です。
""")

    # ── デバッグ情報 ──────────────────────────────────────────────────
    with st.expander("ℹ️ デバッグ情報"):
        dbg = st.session_state.get("dbg", {})
        st.json(dbg)



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
