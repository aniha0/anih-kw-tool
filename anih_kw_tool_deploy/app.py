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

RA = "A"; RBP = "B+"; RB = "B"
RLABEL = {
    RA:  "🏆 Aランク（高優先度追加候補KW）",
    RBP: "🚀 B+ランク（追加検討候補KW）",
    RB:  "👀 Bランク（監視候補KW）",
}
RENAME = {
    "campaign_theme": "キャンペーン名", "keyword": "検索語句",
    "rank": "ランク", "ROAS": "ROAS", "sales": "売上",
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

def assign_rank(r: float) -> str:
    return RA if r >= 5.0 else (RBP if r >= 3.5 else RB)

def tonum(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", "").str.replace("¥", ""),
        errors="coerce"
    ).fillna(0)

def clear():
    for k in ["has_results", "df_win", "df_a", "df_bp", "df_b", "df_del", "df_cpc", "stats", "dbg"]:
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

def a_zip(df_a: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in CAMPAIGNS:
            dc = df_a[df_a["campaign_theme"] == c]
            if not dc.empty:
                zf.writestr(f"{c}_A.csv", to_csv(dc, ["impressions"]))
    return buf.getvalue()

def a_camp_zip(df_a: pd.DataFrame) -> bytes:
    """Aランク キャンペーン別ZIP（keyword列のみ、改行区切り）"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in CAMPAIGNS:
            dc = df_a[df_a["campaign_theme"] == c]
            if dc.empty: continue
            kws = dc.sort_values("ROAS", ascending=False)["keyword"].tolist()
            csv_content = "keyword\n" + "\n".join(kws)
            zf.writestr(f"Aランク_{c}.csv", csv_content.encode("utf-8-sig"))
    return buf.getvalue()

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
            for rk, fn in [(RA, "A"), (RBP, "Bplus"), (RB, "B")]:
                dr = dc[dc["rank"] == rk]
                if not dr.empty:
                    zf.writestr(f"{c}_{fn}.csv", to_csv(dr))
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
CPC_RANK_ORDER = ["SS+", "SS", "S", "A", "B", "D", "E", "即削除", "判断保留"]

def assign_cpc_rank(cost: float, orders: float, roas: float, price: float):
    """STEP1→STEP2→STEP3→STEP4 の順で CPC ランクを返す。(rank, action, delta)"""
    orders = orders or 0
    if price <= 1500:
        del_thresh = 3000
    elif price <= 2000:
        del_thresh = 4000
    else:
        del_thresh = 5000
    if cost < 3000 or orders < 3:
        return ("判断保留", "変更なし", 0)
    if orders >= 20 and roas >= 4.0:
        rank, action, delta = "SS+", "CPC上げ", 5
    elif orders >= 20 and roas >= 2.0:
        rank, action, delta = "SS", "現状維持", 0
    elif roas >= 4.0:
        rank, action, delta = "S", "CPC上げ", 5
    elif roas >= 3.0:
        rank, action, delta = "A", "現状維持", 0
    elif roas >= 2.0:
        rank, action, delta = "B", "現状維持", 0
    elif roas >= 1.5:
        rank, action, delta = "D", "CPC下げ", -5
    else:
        rank, action, delta = "E", "CPC下げ", -10
    if cost >= del_thresh and roas < 0.5:
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
    cols_out = [c for c in ["keyword","campaign_theme","ROAS","cost","sales","orders",
                             "avg_cpc","cpc_rank","cpc_action","cpc_delta","rec_cpc"] if c in df_cpc.columns]
    rename_map = {"keyword":"検索語句","campaign_theme":"キャンペーン","cost":"広告費",
                  "sales":"売上","orders":"購入数","avg_cpc":"現在CPC","cpc_rank":"判定ランク",
                  "cpc_action":"推奨アクション","cpc_delta":"変更幅","rec_cpc":"推奨CPC"}
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

# ─── Sidebar ───────────────────────────────────────
with st.sidebar:
    NAV_PAGES = [
        "📋 Amazon追加用KW",
        "📊 DateDive売れる予測KW",
        "🚫 Amazon削除用KW",
        "📈 CPC調整表",
        "📥 ダウンロード",
        "📖 取扱説明書",
    ]
    current_page = st.radio("ページ選択", NAV_PAGES, label_visibility="collapsed")
    st.markdown("---")

    # 💲 売価マスタ
    st.markdown('<p class="section-header">💲 売価マスタ</p>', unsafe_allow_html=True)
    for _c, _p in PRICES.items(): st.caption(f"{_c}：¥{_p:,}")
    st.markdown("---")
    st.caption("ANIHA Command Center v2.0")

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
        dw["rank"] = dw["ROAS"].apply(assign_rank)
        win_kws = set(dw["keyword"].tolist())
        del_mask = (agg["cost"] >= agg["price"] * 2) & (agg["ROAS"] <= 0.5)
        df_del_ = agg[del_mask].copy()
        df_del_ = df_del_[~df_del_["keyword"].isin(win_kws)].copy()
        df_del_.drop(columns=["price"], inplace=True, errors="ignore")
        df_cpc_ = build_cpc_df(agg.copy())
        st.session_state.update({
            "has_results": True, "df_win": dw,
            "df_a": dw[dw["rank"]==RA].copy(),
            "df_bp": dw[dw["rank"]==RBP].copy(),
            "df_b": dw[dw["rank"]==RB].copy(),
            "df_del": df_del_, "df_cpc": df_cpc_,
            "stats": {
                "n_auto":n_auto,"n_ex":n_ex,"n_pt":n_pt,"n_ar":n_ar,
                "n_br":n_br,"n_cd":n_cd,"n_tl":n_tl,"n_ae":n_ae,
                "n_sl":n_sl,"n_ro":n_ro,"n_of":n_of,
                "n_clk_f":n_clk_f,"n_cost_f":n_cost_f,
                "n_pre":n_pre,"n_af":n_af,"nf":nf,
                "mo":int(min_ord),"mc":int(min_clk),"mco":int(min_cost),
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
da:  pd.DataFrame = st.session_state["df_a"]
dbp: pd.DataFrame = st.session_state["df_bp"]
db:  pd.DataFrame = st.session_state["df_b"]
dd:  pd.DataFrame = st.session_state.get("df_del", pd.DataFrame())
dc_cpc: pd.DataFrame = st.session_state.get("df_cpc", pd.DataFrame())
sv = st.session_state["stats"]
na = len(da); nbp = len(dbp); nb = len(db)

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
    title        : 表示タイトル（例: "📋 Amazon追加用KW判定ロジック"）
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
    # ① KPIカード（5枚）
    k1, k2, k3, k4, k5 = st.columns(5)
    kpi(k1, "🏆", "Aランク",  f"{na}件",            "高優先度追加候補",  "#EAF7EF", "#2F855A")
    kpi(k2, "🚀", "B+ランク", f"{nbp}件",            "追加検討候補",      "#EAF2FF", "#3B82F6")
    kpi(k3, "👀", "Bランク",  f"{nb}件",             "監視候補",          "#FFF9E8", "#F59E0B")
    kpi(k4, "📦", "抽出前",   f"{sv['n_pre']}件",    "フィルター適用前",  "#F4F6F8", "#718096")
    kpi(k5, "🎯", "抽出後",   f"{sv['nf']}件",       "同一意図KW統合後",  "#F3ECFF", "#9F5ACB")
    st.markdown("")

    # 分析フロー詳細（折りたたみ）
    with st.expander("📊 分析フロー詳細", expanded=False):
        st.markdown(f"""
| ステップ | 内容 | 件数 |
|---|---|---|
| オート広告 | 全検索語句 | **{sv["n_auto"]:,}件** |
| 登録済KW除外 | 完全一致−{sv["n_ex"]}・部分一致−{sv["n_pt"]} | **{sv["n_ar"]:,}件** |
| ブランド除外 | −{sv["n_br"]}件 | |
| コード・Title除外 | −{sv["n_cd"]+sv["n_tl"]}件 | **{sv["n_ae"]:,}件** |
| 売上条件（売価×2） | −{sv["n_pre"]-sv["n_sl"]}件 | **{sv["n_sl"]:,}件** |
| ROAS≥2.0 | −{sv["n_sl"]-sv["n_ro"]}件 | **{sv["n_ro"]:,}件** |
| 注文≥{sv["mo"]}・クリック≥{sv["mc"]}・広告費≥¥{sv["mco"]} | 信頼度フィルター | **{sv["n_af"]:,}件** |
| 同一意図KW統合 | 類似KW集約 | **{sv["nf"]:,}件** |
""")

    st.markdown("---")
    _cond_bar([
        ("最小注文数",  f'{sv["mo"]}件'),
        ("最小クリック数", f'{sv["mc"]}回'),
        ("最小広告費",  f'¥{sv["mco"]:,}'),
    ])

    render_logic_section(
        "📋 Amazon追加用KW判定ロジック",
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
      売上実績のある検索語句から、Amazonへ追加すべき <b>勝ちKW</b> を抽出します。<br>
      <span style="font-size:.8rem;color:#718096;">オート広告で成果が出た語句を手動・フレーズ・完全一致へ昇格するための候補抽出です。</span>
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
  <tr style="background:#F1F5F9;">
    <td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【ランク分類】</td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2F855A;">🏆 Aランク</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">ROAS ≥ 5.0 ／ 最優先追加候補</td>
  </tr>
  <tr style="background:#EAF2FF;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#3B82F6;">🚀 B+ランク</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">3.5 ≤ ROAS &lt; 5.0 ／ 追加推奨候補</td>
  </tr>
  <tr>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#F59E0B;">👀 Bランク</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">2.0 ≤ ROAS &lt; 3.5 ／ 監視候補</td>
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
    _c1, _c2, _c3 = st.columns([3, 3, 2])
    with _c1:
        kw_camp = st.selectbox(
            "キャンペーン",
            ["全キャンペーン"] + CAMPAIGNS,
            label_visibility="visible",
            key="add_camp_sel",
        )
    # ③ ランク選択
    with _c2:
        rank_options = {
            "全表示 (A+B++B)": "ALL",
            "🏆 Aランク":       RA,
            "🚀 B+ランク":      RBP,
            "👀 Bランク":       RB,
        }
        sel_rank_label = st.selectbox(
            "ランク絞込",
            list(rank_options.keys()),
            label_visibility="visible",
            key="add_rank_sel",
        )
    sel_rk = rank_options[sel_rank_label]

    # 絞込みデータ生成
    if sel_rk == "ALL":
        sel_df = dw.copy()
    elif sel_rk == RA:
        sel_df = da.copy()
    elif sel_rk == RBP:
        sel_df = dbp.copy()
    else:
        sel_df = db.copy()

    if kw_camp != "全キャンペーン":
        sel_df = sel_df[sel_df["campaign_theme"] == kw_camp].copy()

    n_sel = len(sel_df)

    # 件数表示
    st.markdown(
        f'<div class="count-badge">該当件数: <b style="font-size:1.1rem;">{n_sel}件</b>'
        f'　<span style="color:#718096;font-size:.8rem;">キャンペーン: {kw_camp} ／ ランク: {sel_rank_label}</span></div>',
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
    _cond_bar([("広告費", "≥ 商品売価×2"), ("ROAS", "≤ 0.5"), ("勝ちKW", "除外")])
    render_logic_section(
        "🚫 Amazon削除用KW判定ロジック",
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
      広告費 ≥ 売価 × 2 <b>かつ</b> ROAS &lt; 0.5<br>
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
    _del_camps = ["全キャンペーン"]
    if not dd.empty and "campaign_theme" in dd.columns:
        _del_camps += [c for c in CAMPAIGNS if c in dd["campaign_theme"].values]
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


def page_cpc():
    _RANK_ORDER = ["SS+", "SS", "S", "A", "B", "D", "E", "即削除", "判断保留"]
    _RC = {
        "SS+": "#D69E2E", "SS": "#B7791F", "S": "#553C9A",
        "A":   "#2C7A7B", "B": "#2B6CB0", "D": "#C05621",
        "E":   "#C53030", "即削除": "#742A2A", "判断保留": "#4A5568",
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
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">広告費 &lt; ¥3,000 <b>または</b> 購入数 &lt; 3件</td>
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
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">2.0 ≤ ROAS &lt; 3.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">現状維持</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">±0円</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C05621;">D</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">1.5 ≤ ROAS &lt; 2.0</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">CPC下げ</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;color:#C53030;font-weight:700;">−5円</td>
  </tr>
  <tr style="background:#FFF5F5;">
    <td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#C53030;">E</td>
    <td style="padding:6px 10px;border:1px solid #BFDBFE;">0.5 ≤ ROAS &lt; 1.5</td>
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
      ROAS &lt; 0.5 <b>かつ</b> 広告費が閾値以上<br>
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
    cpc_camps = [c for c in CAMPAIGNS if not dc_cpc[dc_cpc["campaign_theme"] == c].empty]
    _sc, _ = st.columns([3, 2])
    with _sc:
        cpc_camp = st.selectbox("キャンペーン（CPC）", cpc_camps, label_visibility="visible", key="cpc_camp_sel")
    df_c = dc_cpc[dc_cpc["campaign_theme"] == cpc_camp].copy()
    cnt = {r: int((df_c["cpc_rank"] == r).sum()) for r in _RANK_ORDER}
    st.markdown("---")
    kpi_rks = ["SS+", "SS", "S", "A", "B", "D", "E", "即削除"]
    kc_ = st.columns(len(kpi_rks))
    for _col, rk in zip(kc_, kpi_rks):
        bg_map = {
            "SS+":"#FFFFF0","SS":"#FEFCBF","S":"#E9D8FD","A":"#C6F6D5",
            "B":"#BEE3F8","D":"#FEEBC8","E":"#FED7D7","即削除":"#FED7D7",
        }
        _col.markdown(f'''<div class="kpi-card" style="background:{bg_map.get(rk,'#F4F6F8')};border-top:3px solid {_RC[rk]};">
            <div class="kpi-label">{rk}</div>
            <div class="kpi-value" style="color:{_RC[rk]};font-size:1.5rem;">{cnt[rk]}</div>
            <div class="kpi-sub">件</div></div>''', unsafe_allow_html=True)
    if cnt["判断保留"] > 0:
        st.caption(f"⏸ 判断保留: {cnt['判断保留']}件（広告費¥3,000未満 または 購入数3件未満）")
    st.markdown("---")
    disp_cols = [c for c in ["keyword","ROAS","cost","sales","orders","avg_cpc","cpc_rank","cpc_action","cpc_delta","rec_cpc"] if c in df_c.columns]
    _rn = {"keyword":"検索語句","cost":"広告費","sales":"売上","orders":"購入数",
           "avg_cpc":"現在CPC","cpc_rank":"判定ランク","cpc_action":"推奨アクション",
           "cpc_delta":"変更幅","rec_cpc":"推奨CPC"}
    cat_t = pd.CategoricalDtype(categories=_RANK_ORDER, ordered=True)
    df_c["_r"] = df_c["cpc_rank"].astype(cat_t)
    df_c = df_c.sort_values(["_r","ROAS"], ascending=[True, False]).drop(columns=["_r"]).reset_index(drop=True)
    df_c.index = df_c.index + 1
    _d = df_c[disp_cols].rename(columns=_rn).copy()
    if "広告費" in _d.columns: _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
    if "売上"   in _d.columns: _d["売上"]   = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
    if "ROAS"   in _d.columns: _d["ROAS"]   = _d["ROAS"].round(2)
    if "変更幅" in _d.columns: _d["変更幅"] = _d["変更幅"].apply(lambda x: f"+{x}円" if x > 0 else f"{x}円" if x < 0 else "±0円")
    if "現在CPC" in _d.columns: _d["現在CPC"] = _d["現在CPC"].apply(lambda x: f"¥{x:,.0f}" if x else "—")
    if "推奨CPC" in _d.columns: _d["推奨CPC"] = _d["推奨CPC"].apply(lambda x: f"¥{x:,.0f}" if x else "—")
    def _cr(row):
        c = _RC.get(row.get("判定ランク", ""), "")
        return [f"color:{c};font-weight:700" if col == "判定ランク" else "" for col in row.index]
    st.dataframe(_d.style.apply(_cr, axis=1), use_container_width=True, height=460)
    _dl_csv = df_c[disp_cols].rename(columns=_rn).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(f"📥 {cpc_camp}_CPC調整表.csv", data=_dl_csv,
        file_name=f"{cpc_camp}_CPC調整表.csv", mime="text/csv")


# ===================================================
# DateDive 売れる予測KW  (_ddv4_ プレフィックス)
# DateDive実データ仕様対応版
# competitors.csv = ASIN比較レポート（Keyword列なし）
# スコア式: SV×40% + 関連性×25% + 購買意図×15% + LT×10% + 市場難易度補正
# ===================================================

_DDV4_PRODUCTS = {
    "犬用乳酸菌 (B0DJ8Q95XZ)": [
        "乳酸菌","腸活","腸内","善玉菌","便","便秘","軟便","免疫","お腹","消化"],
    "関節サポート (B0DJ8QVCG1)": [
        "関節","グルコサミン","コンドロイチン","msm","足腰","シニア犬","歩行"],
    "アイケア (B0DSP22H5G)": [
        "涙やけ","目","ルテイン","ブルーベリー","白内障","視力"],
    "アミノ酸シャンプー (B0GGGTYZTR)": [
        "シャンプー","敏感肌","低刺激","保湿","かゆみ","皮膚","アミノ酸"],
}

# 商品ラベル → ASIN マッピング（市場難易度補正用）
_DDV4_PRODUCT_ASINS = {
    "犬用乳酸菌 (B0DJ8Q95XZ)":        "B0DJ8Q95XZ",
    "関節サポート (B0DJ8QVCG1)":       "B0DJ8QVCG1",
    "アイケア (B0DSP22H5G)":           "B0DSP22H5G",
    "アミノ酸シャンプー (B0GGGTYZTR)": "B0GGGTYZTR",
}

_DDV4_PURCHASE_INTENT_WORDS = ["おすすめ","人気","ランキング","比較","口コミ"]

_DDV4_STRENGTH_TABLE = {
    "very weak":   100,
    "weak":         80,
    "medium":       60,
    "strong":       30,
    "very strong":  10,
}

# Strength → 市場難易度補正値（全KW共通・商品単位）
_DDV4_MARKET_DIFF_TABLE = {
    "very weak":   +10,
    "weak":        +5,
    "medium":      0,
    "strong":      -5,
    "very strong": -10,
}


# ─── 正規化ユーティリティ ──────────────────────────────────────

def _ddv4_norm_kw(x: str) -> str:
    """NFKC正規化 + 小文字 + カタカナ→ひらがな + スペース正規化"""
    if x is None or (isinstance(x, float) and x != x):
        return ""
    import unicodedata as _ud
    t = _ud.normalize("NFKC", str(x)).lower().strip()
    t = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)
    import re as _re
    return _re.sub(r"\s+", " ", t).strip()


def _ddv4_compact_kw(kw: str) -> str:
    import re as _re
    return _re.sub(r"\s+", "", _ddv4_norm_kw(kw))


def _ddv4_strip_particles(kw: str) -> str:
    import re as _re
    t = _ddv4_norm_kw(kw)
    t = _re.sub(r"\s*(の|用|向け|専用|対応|ための?|への?|にも?|での?)\s*", " ", t)
    return _re.sub(r"\s+", " ", t).strip()


def _ddv4_is_excluded(kw: str, existing_set: set) -> bool:
    """既存運用KW除外: 完全一致→部分一致→助詞除去後部分一致"""
    kn = _ddv4_norm_kw(kw)
    kc = _ddv4_compact_kw(kw)
    ks = _ddv4_strip_particles(kw)
    for ex in existing_set:
        en = _ddv4_norm_kw(ex)
        ec = _ddv4_compact_kw(ex)
        es = _ddv4_strip_particles(ex)
        if kn == en:
            return True
        if kc and ec and (kc in ec or ec in kc):
            return True
        if ks and es and (ks in es or es in ks):
            return True
    return False


# ─── CSV 読み込みユーティリティ ───────────────────────────────

def _ddv4_read_csv_bytes(raw: bytes):
    import io as _io
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return pd.read_csv(_io.BytesIO(raw), encoding="utf-16", sep="\t")
        except Exception:
            pass
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(_io.BytesIO(raw), encoding=enc)
        except Exception:
            continue
    raise ValueError("CSVの読み込みに失敗しました（エンコーディング不明）")


def _ddv4_find_col(df, cands: list):
    import re as _re
    clean = {_re.sub(r"[\s_\-]", "", c).lower(): c for c in df.columns}
    for cand in cands:
        key = _re.sub(r"[\s_\-]", "", cand).lower()
        if key in clean:
            return clean[key]
    return None


def _ddv4_to_float(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# ─── keywords.csv 読み込み ─────────────────────────────────────

def _ddv4_load_keywords_csv(kw_file):
    """keywords.csv → (df, kw_col, sv_col, err)"""
    if kw_file is None:
        return None, None, None, "keywords.csv が未投入です"
    try:
        kw_file.seek(0)
        raw = kw_file.read(); kw_file.seek(0)
        df = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]
        # 全列を文字列化（float混入エラー防止）
        for col in df.columns:
            df[col] = df[col].fillna("").astype(str)
        kw_col = _ddv4_find_col(df, [
            "Keyword", "Search Terms", "SearchTerms", "SearchTerm", "キーワード"])
        if kw_col is None:
            kw_col = df.columns[0]
        sv_col = _ddv4_find_col(df, [
            "Search Volume", "SearchVolume", "SV", "検索ボリューム"])
        df["_kw"] = df[kw_col].astype(str).str.strip()
        df = df[(df["_kw"].str.len() > 0) &
                (df["_kw"].str.lower() != "nan")].reset_index(drop=True)
        return df, kw_col, sv_col, None
    except Exception as e:
        return None, None, None, f"keywords.csv 読み込みエラー: {e}"


# ─── Amazon検索用語 CSV 読み込み ───────────────────────────────

def _ddv4_load_amazon_search_csv(sf_file) -> set:
    if sf_file is None:
        return set()
    try:
        sf_file.seek(0)
        raw = sf_file.read(); sf_file.seek(0)
        df = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]
        for col in ["ターゲティング", "検索用語", "検索語句",
                    "Targeting", "targeting", "Keyword", "keyword"]:
            if col in df.columns:
                import re as _re
                vals = df[col].fillna("").astype(str).str.strip()
                return set(
                    v for v in vals
                    if v and not _re.match(r'^b0[a-z0-9]{8}$', v, _re.I)
                )
        return set()
    except Exception:
        return set()


# ─── competitors.csv → ASIN単位辞書 ──────────────────────────
# DateDive実データ仕様: competitors.csv にはKeyword列が存在しない。
# ASIN行 × 指標列 のフォーマット。

def _ddv4_load_asin_comp_dict(comp_file) -> dict:
    """
    DateDive competitors.csv (ASIN比較レポート) を読み込む。
    Keyword列なし、ASIN列あり。
    形式判定:
      Keyword/Search Terms 列あり → Keyword競合CSV（今回は非対象、警告のみ）
      なし                        → ASIN比較レポート（通常）
    戻り値: { "ASIN_UPPER": {"strength": str or None,
                              "review_count": float or None} }
    特殊キー: "_format" → "asin" or "keyword_unexpected"
              "_error"  → エラー文字列
    """
    if comp_file is None:
        return {}
    try:
        comp_file.seek(0)
        raw = comp_file.read(); comp_file.seek(0)
        df = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]

        # Keyword列が存在する場合は想定外フォーマット（警告用フラグを立てて続行）
        kw_col_check = _ddv4_find_col(df, [
            "Keyword", "Search Terms", "SearchTerms", "SearchTerm", "キーワード"])

        result = {"_format": "keyword_unexpected" if kw_col_check else "asin"}

        # ASIN列を探す
        asin_col = _ddv4_find_col(df, ["ASIN", "asin", "商品コード", "ProductASIN"])
        if asin_col is None:
            # ASINらしい列がなければ最初の列を使う
            asin_col = df.columns[0]

        str_col = _ddv4_find_col(df, ["Strength", "strength"])
        rev_col = _ddv4_find_col(df, [
            "Review Count", "ReviewCount", "レビュー数", "reviewcount", "Reviews"])

        for _, row in df.iterrows():
            asin_raw = str(row[asin_col]).strip().upper()
            if not asin_raw or asin_raw.lower() in ("nan", ""):
                continue

            strength_raw = row[str_col] if str_col else None
            strength_val = None
            if (strength_raw is not None
                    and str(strength_raw).strip().lower() not in ("nan", "")):
                strength_val = str(strength_raw).strip()

            result[asin_raw] = {
                "strength":     strength_val,
                "review_count": _ddv4_to_float(row[rev_col]) if rev_col else None,
            }

        return result

    except Exception as e:
        return {"_error": str(e)}


def _ddv4_get_market_diff_adj(asin_dict: dict, product_asin: str) -> int:
    """
    対象商品ASINのStrengthから市場難易度補正値を返す。
    Very Weak=+10 / Weak=+5 / Medium=0 / Strong=-5 / Very Strong=-10
    ASIN未発見またはStrength不明 → 0（中立）
    """
    if not asin_dict or not product_asin:
        return 0
    entry = asin_dict.get(product_asin.upper())
    if not entry or not isinstance(entry, dict):
        return 0
    strength_val = entry.get("strength")
    if not strength_val:
        return 0
    key = str(strength_val).strip().lower()
    return _DDV4_MARKET_DIFF_TABLE.get(key, 0)


# ─── スコア算出関数群 ──────────────────────────────────────────

def _ddv4_sv_score(sv) -> int:
    """需要評価: Search Volume → 0-100"""
    try:
        v = float(str(sv).replace(",", ""))
    except (ValueError, TypeError):
        return 30
    if v >= 10000: return 100
    if v >= 5000:  return 85
    if v >= 1000:  return 70
    if v >= 300:   return 50
    if v >= 100:   return 30
    return 10


def _ddv4_rel_score(kw: str, product_label: str) -> int:
    """商品関連性: 対象商品キーワード辞書とのマッチ数 → 0-100"""
    kn = _ddv4_norm_kw(kw)
    keywords = _DDV4_PRODUCTS.get(product_label, [])
    match_count = sum(1 for w in keywords if _ddv4_norm_kw(w) in kn)
    if match_count >= 2: return 100
    if match_count == 1: return 60
    return 0


def _ddv4_intent_score(kw: str) -> int:
    """購買意図: 購買意図ワード含有 → 0 or 100"""
    kn = _ddv4_norm_kw(kw)
    for word in _DDV4_PURCHASE_INTENT_WORDS:
        if word in kn:
            return 100
    return 0


def _ddv4_lt_score(kw: str) -> int:
    """ロングテール: 語数 → 0-100"""
    n = len(str(kw).strip().split())
    if n >= 5: return 100
    if n >= 4: return 75
    if n >= 3: return 50
    if n >= 2: return 25
    return 0


def _ddv4_calc_rank(score: int) -> str:
    """S>=90 / A>=80 / B>=70 / C>=60 / D<60"""
    if score >= 90: return "S"
    if score >= 80: return "A"
    if score >= 70: return "B"
    if score >= 60: return "C"
    return "D"


def _ddv4_make_reason(s_sv, s_rel, s_intent, s_lt, market_adj) -> str:
    """採用理由を詳細生成（5理由: SV / 関連性 / 購買意図 / ロングテール / 市場難易度）"""
    parts = []
    # 需要（Search Volume）
    if s_sv >= 85:   parts.append("高検索需要（SV>=5,000）")
    elif s_sv >= 70: parts.append("中〜高検索需要（SV>=1,000）")
    elif s_sv >= 50: parts.append("中程度の検索需要（SV>=300）")
    elif s_sv >= 30: parts.append("低〜中検索需要（SV>=100）")
    else:            parts.append("低検索需要（SV<100）")
    # 商品関連性
    if s_rel >= 100:  parts.append("高関連性（複数商品KW一致）")
    elif s_rel >= 60: parts.append("商品関連性あり（1KW一致）")
    else:             parts.append("商品関連性低（KW不一致）")
    # 購買意図
    if s_intent > 0: parts.append("強い購買意図語含む")
    else:            parts.append("購買意図語なし")
    # ロングテール
    if s_lt >= 100:  parts.append("ロングテール（5語以上）")
    elif s_lt >= 75: parts.append("ロングテール（4語）")
    elif s_lt >= 50: parts.append("ロングテール（3語）")
    elif s_lt >= 25: parts.append("短中尺KW（2語）")
    else:            parts.append("単語KW（1語）")
    # 市場難易度補正
    if market_adj >= 10:   parts.append("競争緩和市場（Very Weak / 加点+10）")
    elif market_adj >= 5:  parts.append("競争比較的低い市場（Weak / 加点+5）")
    elif market_adj == 0:  parts.append("中程度の競争市場（Medium）")
    elif market_adj >= -5: parts.append("競争やや強い市場（Strong / 減点-5）")
    else:                  parts.append("高競争市場（Very Strong / 減点-10）")
    return " / ".join(parts)



# -- メインスコアリング（keywords.csv中心・商品単位市場難易度補正）--

def _ddv4_calculate_sellable_keywords(
        cands_df, sv_col, product_label, market_diff_adj: int):
    """
    DateDive実データ仕様スコア式:
      base = sv*0.40 + rel*0.25 + intent*0.15 + lt*0.10  (0-90)
      final = max(0, round(base + market_diff_adj))        (0-100)
    市場難易度補正は全KW共通（商品ASIN単位）。
    Review Count: 参考表示のみ（スコア算出に使用しない）。
    """
    import pandas as pd
    df = cands_df.copy()
    results = []
    for _, row in df.iterrows():
        kw_raw = str(row["_kw"])
        if sv_col and sv_col in df.columns:
            sv_val = row.get(sv_col, 0)
            s_sv   = _ddv4_sv_score(sv_val)
        else:
            sv_val = 0
            s_sv   = 30
        s_rel    = _ddv4_rel_score(kw_raw, product_label)
        s_intent = _ddv4_intent_score(kw_raw)
        s_lt     = _ddv4_lt_score(kw_raw)
        base  = s_sv * 0.40 + s_rel * 0.25 + s_intent * 0.15 + s_lt * 0.10
        final = max(0, round(base + market_diff_adj))
        rank  = _ddv4_calc_rank(final)
        results.append({
            "_kw":           kw_raw,
            "_sv_raw":       sv_val,
            "_s_sv":         s_sv,
            "_s_rel":        s_rel,
            "_s_intent":     s_intent,
            "_s_lt":         s_lt,
            "_base":         round(base, 2),
            "_market_adj":   market_diff_adj,
            "売れる予測スコア": final,
            "ランク":          rank,
            "採用理由":        _ddv4_make_reason(s_sv, s_rel, s_intent, s_lt, market_diff_adj),
        })
    return pd.DataFrame(results)


def _ddv4_render_sellable_keywords():
    """DateDive 売れる予測KW -- Amazon追加KW候補と同等UI/品質で描画する。"""
    st.markdown("### 📊 DateDive 売れる予測KW")
    st.markdown("---")

    # == 1. 商品選択 ==================================================
    st.markdown("##### 📌 ① 分析対象商品を選択")
    prod_options = ["─ 選択してください ─"] + list(_DDV4_PRODUCTS.keys())
    prod_label = st.selectbox(
        "商品選択", prod_options, key="ddv4_prod", label_visibility="collapsed")
    if prod_label == "─ 選択してください ─":
        st.info("📌 分析対象商品を選択してください。")
        return
    st.success(f"✅ {prod_label}")
    st.markdown("")

    # == 2. CSV 投入 ==================================================
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📄 ② DateDive Keywords CSV")
        ddv4_kw = st.file_uploader(
            "keywords.csv", type="csv",
            key="ddv4_kw_csv", label_visibility="collapsed")
        if ddv4_kw: st.success(f"✅ {ddv4_kw.name}")
        else: st.caption("niche-XXXX-keywords.csv をアップロード")
    with c2:
        st.markdown("##### 📄 ③ DateDive Competitors CSV")
        ddv4_comp = st.file_uploader(
            "competitors.csv", type="csv",
            key="ddv4_comp_csv", label_visibility="collapsed")
        if ddv4_comp: st.success(f"✅ {ddv4_comp.name}")
        else: st.caption("niche-XXXX-competitors.csv をアップロード")

    st.markdown("")
    st.markdown("##### 📄 ④ Amazon検索用語 CSV（既存運用KW除外用）")
    ddv4_amz = st.file_uploader(
        "Amazon検索用語CSV", type="csv",
        key="ddv4_amz_csv", label_visibility="collapsed")
    if ddv4_amz: st.success(f"✅ {ddv4_amz.name}（既存運用KWを除外します）")
    else: st.caption("※ 未投入の場合はKW除外なしで実行します")
    st.markdown("")

    # == 3. 実行ボタン ================================================
    exec_btn = st.button(
        "🔍 売れる予測KW抽出",
        type="primary", use_container_width=True, key="ddv4_exec_btn")
    if not exec_btn:
        return

    errs = []
    if ddv4_kw   is None: errs.append("❌ DateDive keywords.csv が未投入です")
    if ddv4_comp is None: errs.append("❌ DateDive competitors.csv が未投入です")
    if errs:
        for e in errs: st.error(e)
        return

    # == 4. データ処理 ================================================
    with st.spinner("keywords.csv 読み込み中..."):
        kw_df, kw_col, sv_col, kw_err = _ddv4_load_keywords_csv(ddv4_kw)
    if kw_err:
        st.error(f"❌ {kw_err}"); return
    n_total = len(kw_df)

    with st.spinner("既存運用KW除外中..."):
        existing_set = _ddv4_load_amazon_search_csv(ddv4_amz)
        keep = ([not _ddv4_is_excluded(kw, existing_set) for kw in kw_df["_kw"]]
                if existing_set else [True] * len(kw_df))
    kw_df["_keep"] = keep
    exc_df   = kw_df[~kw_df["_keep"]].copy()
    cands_df = kw_df[kw_df["_keep"]].copy().reset_index(drop=True)
    n_excl   = len(exc_df)
    if cands_df.empty:
        st.warning("⚠️ 全KWが運用中KWと重複しています。"); return

    with st.spinner("competitors.csv 読み込み中（ASIN単位）..."):
        asin_dict = _ddv4_load_asin_comp_dict(ddv4_comp)

    fmt = asin_dict.get("_format", "asin")
    if asin_dict.get("_error"):
        st.warning(f"⚠️ competitors.csv 読み込みエラー: {asin_dict['_error']}（市場難易度補正=0で続行）")
        asin_dict = {}
    elif fmt == "keyword_unexpected":
        st.warning("⚠️ competitors.csv にKeyword列が検出されました。ASIN比較レポートとして処理します。")

    product_asin    = _DDV4_PRODUCT_ASINS.get(prod_label, "")
    market_diff_adj = _ddv4_get_market_diff_adj(asin_dict, product_asin)

    asin_entry    = asin_dict.get(product_asin.upper(), {}) if product_asin else {}
    asin_strength = asin_entry.get("strength")    if isinstance(asin_entry, dict) else None
    asin_reviews  = asin_entry.get("review_count") if isinstance(asin_entry, dict) else None
    n_asin_total  = len([k for k in asin_dict
                         if k not in ("_format", "_error") and not k.startswith("_")])

    with st.spinner("スコアリング中..."):
        scored = _ddv4_calculate_sellable_keywords(
            cands_df, sv_col, prod_label, market_diff_adj)

    scored_all    = scored.sort_values("売れる予測スコア", ascending=False).reset_index(drop=True)
    scored_ranked = scored_all[scored_all["ランク"] != "D"].copy().reset_index(drop=True)
    scored_d      = scored_all[scored_all["ランク"] == "D"].copy().reset_index(drop=True)

    n_s = int((scored_ranked["ランク"] == "S").sum())
    n_a = int((scored_ranked["ランク"] == "A").sum())
    n_b = int((scored_ranked["ランク"] == "B").sum())
    n_c = int((scored_ranked["ランク"] == "C").sum())
    n_d = len(scored_d)
    top_score = int(scored_all["売れる予測スコア"].max()) if not scored_all.empty else 0
    avg_score = round(float(scored_all["売れる予測スコア"].mean()), 1) if not scored_all.empty else 0.0

    # 市場難易度変数（KPIカード・市場難易度ブロックで共用）
    _strength_reason_map = {
        "very weak":   "競争圧力が非常に低いため大きく加点します",
        "weak":        "競争圧力が比較的低いため加点します",
        "medium":      "競争圧力は中程度のため補正なし",
        "strong":      "競争圧力が強いため減点します",
        "very strong": "競争圧力が非常に強いため大きく減点します",
    }
    _strength_display = asin_strength or "取得不可"
    _adj_display      = ("+" if market_diff_adj > 0 else "") + str(market_diff_adj) + "点"
    _adj_color        = "#2F855A" if market_diff_adj > 0 else "#C53030" if market_diff_adj < 0 else "#718096"
    _diff_reason      = _strength_reason_map.get(
        str(asin_strength).strip().lower() if asin_strength else "",
        "Strengthデータ未取得のため補正なし")
    _review_display   = f"{int(asin_reviews):,}" if asin_reviews else "取得不可"

    # == 5. KPIカード（6枚: 抽出KW数/Sランク/Aランク/最高/平均/市場難易度）========
    st.markdown("---")
    _k1, _k2, _k3, _k4, _k5, _k6 = st.columns(6)
    kpi(_k1, "📊", "抽出KW数",   f"{len(cands_df)}件",  "除外後の対象KW",   "#EAF2FF", "#3B82F6")
    kpi(_k2, "🥇", "Sランク数",  f"{n_s}件",             "スコア≥90",        "#F3ECFF", "#6B46C1")
    kpi(_k3, "🏆", "Aランク数",  f"{n_a}件",             "スコア≥80",        "#EAF7EF", "#2F855A")
    kpi(_k4, "⭐", "最高スコア", f"{top_score}点",       "スコア最上位",     "#FFF9E8", "#F59E0B")
    kpi(_k5, "📈", "平均スコア", f"{avg_score}点",       "採用候補平均",     "#F4F6F8", "#718096")
    kpi(_k6, "🏪", "市場難易度", _strength_display,      _adj_display,       "#F0FFF4", "#2F855A")
    st.markdown("")

    # == 6. 市場難易度ブロック ========================================

    st.markdown(
        '<div style="background:#F0FFF4;border:1px solid #9AE6B4;'
        'border-left:4px solid #38A169;border-radius:8px;padding:14px 18px;margin-bottom:12px;">'
        '<div style="font-weight:700;font-size:.92rem;color:#22543D;margin-bottom:8px;">'
        f'🏪 市場難易度情報 — {prod_label}（ASIN: {product_asin}）</div>'
        '<table style="width:100%;font-size:.85rem;color:#2D3748;border-collapse:collapse;">'
        '<tr>'
        '<td style="width:22%;padding:4px 8px;font-weight:600;color:#276749;">Strength</td>'
        f'<td style="padding:4px 8px;">{_strength_display}</td>'
        '<td style="width:22%;padding:4px 8px;font-weight:600;color:#276749;">市場難易度補正</td>'
        f'<td style="padding:4px 8px;font-weight:700;color:{_adj_color};">{_adj_display}（全KW共通）</td>'
        '</tr><tr>'
        '<td style="padding:4px 8px;font-weight:600;color:#276749;">Review Count</td>'
        f'<td style="padding:4px 8px;">{_review_display}（参考表示のみ）</td>'
        '<td style="padding:4px 8px;font-weight:600;color:#276749;">補正理由</td>'
        f'<td style="padding:4px 8px;color:#4A5568;">{_diff_reason}</td>'
        '</tr><tr>'
        '<td style="padding:4px 8px;font-weight:600;color:#276749;">competitors.csv</td>'
        f'<td style="padding:4px 8px;" colspan="3">{n_asin_total}件のASINデータ読み込み済</td>'
        '</tr></table></div>',
        unsafe_allow_html=True,
    )

    # == 7. 分析ロジック説明 ==========================================
    render_logic_section(
        "📊 DateDive 売れる予測KW 判定ロジック",
        '<table style="width:100%;border-collapse:collapse;font-size:.83rem;color:#2D3748;">'
        '<thead><tr style="background:#DBEAFE;">'
        '<th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:30%;">項目</th>'
        '<th style="padding:7px 10px;border:1px solid #BFDBFE;text-align:left;width:70%;">内容</th>'
        '</tr></thead><tbody>'
        '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【目的】</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;" colspan="2">'
        'DateDiveのキーワードデータから、実際に<b>売れる可能性が高いKW</b>を自動スコアリングして抽出します。<br>'
        '<span style="font-size:.8rem;color:#718096;">競合Strengthを商品単位で取得し、市場全体の難易度を補正に反映します。</span>'
        '</td></tr>'
        '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【スコア式】</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">基礎スコア（0-90点）</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">SV×<b>40%</b> ＋ 商品関連性×<b>25%</b> ＋ 購買意図×<b>15%</b> ＋ ロングテール×<b>10%</b></td></tr>'
        '<tr style="background:#F0FFF4;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">市場難易度補正（別枠）</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">'
        'Very Weak <b>+10</b> / Weak <b>+5</b> / Medium <b>0</b> / Strong <b>-5</b> / Very Strong <b>-10</b><br>'
        '<span style="font-size:.8rem;color:#718096;">対象商品ASINのStrengthを参照。全KW共通で適用</span></td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2F855A;">最終スコア</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">基礎スコア ＋ 市場難易度補正（0〜100点）</td></tr>'
        '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【各スコア算出方法】</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">SV（需要評価）</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">≥10,000→100 / ≥5,000→85 / ≥1,000→70 / ≥300→50 / ≥100→30 / &lt;100→10</td></tr>'
        '<tr style="background:#EAF2FF;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">商品関連性</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">2KW以上一致→100 / 1KW一致→60 / 不一致→0</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">購買意図</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">おすすめ/人気/ランキング/比較/口コミ 含む→100 / 含まない→0</td></tr>'
        '<tr style="background:#EAF2FF;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:600;">ロングテール性</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">5語以上→100 / 4語→75 / 3語→50 / 2語→25 / 1語→0</td></tr>'
        '<tr style="background:#F1F5F9;"><td colspan="2" style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#1E3A5F;">【ランク分類】</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#6B46C1;">🥇 Sランク</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">スコア ≥ 90 ／ 最優先追加候補</td></tr>'
        '<tr style="background:#EAF7EF;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2F855A;">🏆 Aランク</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">80 ≤ スコア &lt; 90 ／ 高優先度追加候補</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#3B82F6;">🚀 Bランク</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">70 ≤ スコア &lt; 80 ／ 追加検討候補</td></tr>'
        '<tr style="background:#FFF9E8;"><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#F59E0B;">👀 Cランク</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">60 ≤ スコア &lt; 70 ／ 監視候補</td></tr>'
        '<tr><td style="padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#718096;">📉 Dランク</td>'
        '<td style="padding:6px 10px;border:1px solid #BFDBFE;">スコア &lt; 60 ／ 今回は見送り</td></tr>'
        '</tbody></table>'
        '<p style="font-size:.78rem;color:#718096;margin-top:10px;">'
        '▶ Review Count はUI参考表示のみ（スコア計算に使用しない）<br>'
        '▶ 市場難易度補正は competitors.csv の対象ASIN Strength から自動取得</p>',
    )

    # == 8. 分析フロー詳細 ============================================
    with st.expander("📊 分析フロー詳細", expanded=False):
        st.markdown(
            f"| ステップ | 内容 | 件数 |\n|---|---|---|\n"
            f"| keywords.csv 読込 | 全KW数 | **{n_total:,}件** |\n"
            f"| 既存運用KW除外 | Amazon検索語CSVとの重複除去 | **{n_excl:,}件除外 → {len(cands_df):,}件** |\n"
            f"| competitors.csv | ASIN比較レポート読込 | **{n_asin_total}件のASIN** |\n"
            f"| 市場難易度補正取得 | {product_asin} Strength={_strength_display} | **補正{_adj_display}** |\n"
            f"| スコアリング | SV40%+関連性25%+意図15%+LT10%+補正 | **{len(scored_all):,}件** |\n"
            f"| S〜Cランク抽出 | スコア60点以上 | **{len(scored_ranked):,}件** |\n"
            f"| Dランク（見送り） | スコア60点未満 | **{n_d:,}件** |"
        )

    _cond_bar([
        ("対象商品", prod_label[:20]),
        ("対象ASIN", product_asin or "未設定"),
        ("Strength", _strength_display),
        ("市場難易度補正", _adj_display),
    ])
    st.markdown("---")

    # == 9. ランク絞込 ================================================
    _rc1, _rc2 = st.columns([3, 5])
    with _rc1:
        rank_options = {
            "全表示 (S+A+B+C)": "ALL",
            "🥇 Sランク": "S",
            "🏆 Aランク": "A",
            "🚀 Bランク": "B",
            "👀 Cランク": "C",
        }
        sel_rank_label = st.selectbox(
            "ランク絞込", list(rank_options.keys()),
            label_visibility="visible", key="ddv4_rank_sel")
    sel_rk = rank_options[sel_rank_label]

    if sel_rk == "ALL":
        view_df = scored_ranked.copy()
    else:
        view_df = scored_ranked[scored_ranked["ランク"] == sel_rk].copy()
    n_view = len(view_df)

    # 件数バッジ
    st.markdown(
        f'<div class="count-badge">該当件数: <b style="font-size:1.1rem;">{n_view}件</b>'
        f'&nbsp;&nbsp;<span style="color:#718096;font-size:.8rem;">'
        f'商品: {prod_label[:20]} ／ ランク: {sel_rank_label} ／ '
        f'平均スコア: {avg_score}点 ／ 最高スコア: {top_score}点</span></div>',
        unsafe_allow_html=True,
    )

    if view_df.empty:
        st.info("条件に合うキーワードはありません。")
    else:
        # コピー用KW一覧
        st.markdown("**📋 Amazon広告登録用KW一覧**（右上のコピーボタンでコピー）")
        st.code("\n".join(view_df["_kw"].astype(str).tolist()), language=None)

        # 詳細テーブル（全9列）
        st.markdown("##### 📋 売れる予測KW詳細テーブル")
        disp = pd.DataFrame()
        disp.index = range(1, len(view_df) + 1)
        disp["Keyword"]            = view_df["_kw"].astype(str).values
        disp["Search Volume"]      = view_df["_sv_raw"].values
        disp["商品関連性(0-100)"]  = view_df["_s_rel"].values
        disp["購買意図(0-100)"]    = view_df["_s_intent"].values
        disp["ロングテール(0-100)"]= view_df["_s_lt"].values
        disp["市場難易度補正"]     = view_df["_market_adj"].apply(
                                       lambda x: ("+" if x > 0 else "") + str(x)).values
        disp["基礎スコア"]         = view_df["_base"].values
        disp["売れる予測スコア"]   = view_df["売れる予測スコア"].values
        disp["Rank"]               = view_df["ランク"].values
        disp["採用理由"]           = view_df["採用理由"].astype(str).values
        st.dataframe(disp, use_container_width=True)

        # ダウンロード
        dl_csv = (disp.reset_index(names="順位")
                      .to_csv(index=False, encoding="utf-8-sig")
                      .encode("utf-8-sig"))
        st.download_button(
            "📥 売れる予測KW.csv", data=dl_csv,
            file_name=f"売れる予測KW_{prod_label[:10]}.csv",
            mime="text/csv", use_container_width=True)

        # スコア内訳デバッグ（上位20件）
        top20 = scored_all.head(20).copy().reset_index(drop=True)
        with st.expander("🔍 スコア内訳（上位20件）", expanded=False):
            dbg = pd.DataFrame()
            dbg["順位"]                  = range(1, len(top20) + 1)
            dbg["Keyword"]               = top20["_kw"].astype(str).values
            dbg["SV"]                    = top20["_sv_raw"].values
            dbg["需要スコア(x0.40)"]     = top20["_s_sv"].values
            dbg["需要貢献"]              = (top20["_s_sv"] * 0.40).round(1).values
            dbg["関連性スコア(x0.25)"]   = top20["_s_rel"].values
            dbg["関連性貢献"]            = (top20["_s_rel"] * 0.25).round(1).values
            dbg["意図スコア(x0.15)"]     = top20["_s_intent"].values
            dbg["意図貢献"]              = (top20["_s_intent"] * 0.15).round(1).values
            dbg["LTスコア(x0.10)"]       = top20["_s_lt"].values
            dbg["LT貢献"]                = (top20["_s_lt"] * 0.10).round(1).values
            dbg["基礎スコア(0-90)"]      = top20["_base"].values
            dbg["市場難易度補正"]         = top20["_market_adj"].apply(
                                              lambda x: ("+" if x > 0 else "") + str(x)).values
            dbg["最終スコア"]            = top20["売れる予測スコア"].values
            dbg["Rank"]                  = top20["ランク"].values
            dbg = dbg.set_index("順位")
            st.dataframe(dbg, use_container_width=True)

    # 除外KW expander
    if not exc_df.empty:
        with st.expander(f"🚫 既存運用KWと重複除外: {n_excl}件", expanded=False):
            et = (exc_df[["_kw"]]
                  .rename(columns={"_kw": "除外KW"})
                  .reset_index(drop=True))
            et.index = et.index + 1
            st.dataframe(et, use_container_width=True)

    # Dランク expander
    if not scored_d.empty:
        with st.expander(f"📉 Dランク（スコア60点未満）見送り候補: {n_d}件", expanded=False):
            d_disp = pd.DataFrame({
                "Keyword":          scored_d["_kw"].astype(str).values,
                "Search Volume":    scored_d["_sv_raw"].values,
                "商品関連性":       scored_d["_s_rel"].values,
                "購買意図":         scored_d["_s_intent"].values,
                "ロングテール":     scored_d["_s_lt"].values,
                "市場難易度補正":   scored_d["_market_adj"].apply(
                                      lambda x: ("+" if x > 0 else "") + str(x)).values,
                "基礎スコア":       scored_d["_base"].values,
                "Score":            scored_d["売れる予測スコア"].values,
                "採用理由":         scored_d["採用理由"].astype(str).values,
            })
            d_disp.index = range(1, len(d_disp) + 1)
            st.dataframe(d_disp, use_container_width=True)


def page_dd_v4():
    _ddv4_render_sellable_keywords()


def page_download():
    st.markdown("### 📥 ダウンロード")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🏆 Aランク 勝ちKW（キャンペーン別ZIP）**")
        st.caption(f"{na}件 — 高優先度追加候補")
        if not da.empty:
            st.download_button("📥 Aランク_ZIP", data=a_camp_zip(da),
                file_name="A_win_kw.zip", mime="application/zip", use_container_width=True)
    with c2:
        st.markdown("**📦 全ランク 勝ちKW（一括ZIP）**")
        st.caption(f"A{na}件 + B+{nbp}件 + B{nb}件")
        if not dw.empty:
            st.download_button("📥 全ランク_ZIP", data=all_zip(dw),
                file_name="all_win_kw.zip", mime="application/zip", use_container_width=True)
    st.markdown("")
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**🚫 削除用KW（キャンペーン別ZIP）**")
        st.caption(f"{len(dd)}件 — 広告費≥売価×2 かつ ROAS≤0.5")
        if not dd.empty:
            st.download_button("📥 削除用KW_ZIP", data=del_camp_zip(dd),
                file_name="del_kw.zip", mime="application/zip", use_container_width=True)
    with c4:
        st.markdown("**📈 CPC調整表（全キャンペーン ZIP）**")
        st.caption("STEP1-4 判定ランク付きCSV")
        if not dc_cpc.empty:
            st.download_button("📥 CPC調整表_ZIP", data=cpc_camp_zip(dc_cpc),
                file_name="cpc_adjust.zip", mime="application/zip", use_container_width=True)


def page_manual():
    st.markdown("### 📖 ANIHA Command Center — 取扱説明書")
    with st.expander("📌 このツールについて", expanded=True):
        st.markdown("""
**ANIHA Command Center** は、Amazon SP広告の検索用語レポートから**勝ちKW（追加候補）**・**削除KW**・**CPC調整案**を自動生成するANIHA専用ツールです。

| 機能 | 内容 |
|---|---|
| 📋 Amazon追加用KW | A/B+/B ランク別 勝ちKW一覧 |
| 📈 CPC調整表 | STEP1-4によるCPC判定ランク付き一覧 |
| 🚫 Amazon削除用KW | 広告費過多・低ROAS KW一覧 |
| 📥 ダウンロード | キャンペーン別ZIP出力 |
""")
    with st.expander("🔧 STEP1: CSVレポートの出力方法", expanded=True):
        st.markdown("""
**Amazon広告管理画面** → **レポート** → **レポートビルダー**

1. レポートタイプ: **スポンサープロダクト** → **検索用語レポート**
2. 列の選択で **必ず以下をチェック**:

| 必須列 | 用途 |
|---|---|
| ☑ 検索用語 | 分析対象KW |
| ☑ ターゲティング | 登録済KW除外に使用（必須） |
| ☑ キャンペーン名 | キャンペーン別集計 |
| ☑ 売上 / 広告費 / 注文数 / クリック数 | KPI算出 |

3. 期間: 任意（直近30〜90日推奨）
4. **CSVでダウンロード** → そのまま1ファイルをアップロード

> ⚠️ **ターゲティング列がないとエラーになります。** マニュアルキャンペーンの登録済KWをターゲティング列から自動除外します。
""")
    with st.expander("📤 STEP2: ANIHA Command Center へアップロード"):
        st.markdown("""
1. 画面上部の **「📊 Amazon検索用語レポート」** エリアにCSVをドロップ
2. ファイル名が表示されたら **「🔍 抽出実行」** を押す
3. 分析完了後、左サイドバーのページナビで各ページを確認

> 💡 アップロードは **1ファイルのみ** です。検索用語とターゲティングが同一CSVに含まれている必要があります。
""")
    with st.expander("📊 STEP3: 分析結果の確認"):
        st.markdown("""
| ページ | 確認内容 |
|---|---|
| 📋 Amazon追加用KW | A/B+/Bランク別 勝ちKW一覧 |
| 🚫 Amazon削除用KW | 広告費過多・低ROAS KW |
| 📈 CPC調整表 | STEP1-4 CPC判定ランク付き一覧 |
""")
    with st.expander("📥 STEP4: ダウンロードとAmazon登録"):
        st.markdown("""
1. **📥 ダウンロード** ページからZIPをダウンロード
2. ZIPを展開 → キャンペーン別CSVを確認
3. Amazon広告管理画面 → **ターゲティング** へ貼り付け
4. 削除用KWは **一時停止 or 入札削除** で対応
""")
    with st.expander("📊 利用条件一覧"):
        st.markdown("""
### 📋 Amazon追加用KW — 利用条件
| 条件 | 内容 |
|---|---|
| 最小注文数 | サイドバーで設定（デフォルト3件） |
| 最小クリック数 | サイドバーで設定（デフォルト5回） |
| 最小広告費 | サイドバーで設定（デフォルト¥300） |
| ROAS | ≥ 2.0（Bランク以上） |
| 売上 | ≥ 商品売価 × 2 |

### 🚫 Amazon削除用KW — 利用条件
| 条件 | 内容 |
|---|---|
| 広告費 | ≥ 商品売価 × 2 |
| ROAS | ≤ 0.5 |
| 除外 | 勝ちKW（追加候補KW）は対象外 |

### 📈 CPC調整表 — 利用条件
| 条件 | 内容 |
|---|---|
| 最小クリック数 | 平均CPC算出に使用 |
| 広告費 | ≥ ¥3,000 かつ 注文数 ≥ 3件（判断保留除外条件） |
""")
    with st.expander("🏆 STEP3: ランク判定基準"):
        st.markdown("""
| ランク | ROAS | 意味 |
|---|---|---|
| 🏆 Aランク | ≥ 5.0 | 高優先度追加候補 |
| 🚀 B+ランク | ≥ 3.5 | 追加検討候補 |
| 👀 Bランク | ≥ 2.0 | 監視候補 |

勝ちKWは以下の順で絞り込まれます:
1. オート広告キャンペーンの検索語句を抽出
2. マニュアルキャンペーン登録済KWを除外（完全一致・部分一致）
3. ブランドワード・商品コード・タイトル語句を除外
4. 売上≥売価×2 かつ ROAS≥2.0 を満たすものを抽出
5. 注文数・クリック数・広告費フィルターで信頼度確認
6. 同一意図KWを統合して重複除去
""")
    with st.expander("📈 STEP4: CPC調整ロジック"):
        st.markdown("""
| 判定 | 条件 | アクション |
|---|---|---|
| 判断保留 | 広告費<¥3,000 または 注文数<3 | 変更なし |
| SS+ | 注文≥20 かつ ROAS≥4.0 | CPC+5% |
| SS | 注文≥20 かつ ROAS≥2.0 | 現状維持 |
| S | ROAS≥4.0 | CPC+5% |
| A | ROAS≥3.0 | 現状維持 |
| B | ROAS≥2.0 | 現状維持 |
| D | ROAS≥1.5 | CPC−5% |
| E | ROAS<1.5 | CPC−10% |
| 即削除 | 広告費≥閾値 かつ ROAS<0.5 | 即削除 |

**即削除閾値:** 売価≤¥1,500→¥3,000 / 売価≤¥2,000→¥4,000 / 売価>¥2,000→¥5,000
""")
    with st.expander("ℹ️ デバッグ情報"):
        dbg = st.session_state.get("dbg", {})
        st.json(dbg)


# ─── Page Router ─────────────────────────────────────
_PAGE_FUNCS = {
    "📋 Amazon追加用KW":  page_add_kw,
    "📊 DateDive売れる予測KW":  page_dd_v4,
    "🚫 Amazon削除用KW":  page_del_kw,
    "📈 CPC調整表":        page_cpc,
    "📥 ダウンロード":     page_download,
    "📖 取扱説明書":       page_manual,
}
_PAGE_FUNCS[current_page]()
