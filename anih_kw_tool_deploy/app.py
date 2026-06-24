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

# ============================================================
# DateDive 攻略KW発掘エンジン (_ddv4_) v4.4 Final
# 需要(25) + 商品適合性(25) + 競争余地(15) + 攻略余地(15) + KWギャップ(20) = 100点
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

_DDV4_COMP_BASE = {
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
        kws = df[col].fillna("").astype(str).tolist()
        result = set()
        for kw in kws:
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
        result = {}
        asin_col = _ddv4_find_col(df, ["ASIN","asin","商品コード","ProductASIN"])
        if asin_col is None:
            asin_col = df.columns[0]
        str_col  = _ddv4_find_col(df, ["Strength","strength"])
        var_col  = _ddv4_find_col(df, ["Variations","variations","バリエーション"])
        rev_col  = _ddv4_find_col(df, ["Review Count","ReviewCount","レビュー数","reviewcount"])
        brand_col= _ddv4_find_col(df, ["Brand","brand","ブランド"])
        ctry_col = _ddv4_find_col(df, ["Seller Country","SellerCountry","国","Country"])
        for _, row in df.iterrows():
            asin_raw = str(row[asin_col]).strip().upper()
            if not asin_raw or asin_raw.lower() in ("nan",""):
                continue
            strength_raw = row[str_col] if str_col else None
            strength_val = (str(strength_raw).strip()
                            if strength_raw is not None
                               and str(strength_raw).strip().lower() not in ("nan","")
                            else None)
            brand_val  = str(row[brand_col]).strip()  if brand_col  else None
            country_val= str(row[ctry_col]).strip()   if ctry_col   else None
            result[asin_raw] = {
                "strength":     strength_val,
                "variations":   _ddv4_to_float(row[var_col]) if var_col else None,
                "review_count": _ddv4_to_float(row[rev_col]) if rev_col else None,
                "brand":        brand_val  if brand_val  and brand_val.lower()  != "nan" else None,
                "country":      country_val if country_val and country_val.lower()!= "nan" else None,
            }
        return result
    except Exception as e:
        return {"_error": str(e)}

def _ddv4_load_rank_map_csv(rank_file, product_asin):
    if rank_file is None:
        return {}
    try:
        raw = rank_file.read(); rank_file.seek(0)
        df  = _ddv4_read_csv_bytes(raw)
        df.columns = [str(c).strip() for c in df.columns]
        kw_col = _ddv4_find_col(df, [
            "Search Terms","SearchTerms","Keyword","キーワード","検索語句","検索用語"])
        if kw_col is None and len(df.columns) > 0:
            kw_col = df.columns[0]
        if kw_col is None:
            return {}
        result = {}
        asin_col = _ddv4_find_col(df, ["ASIN","asin"])
        rank_col = _ddv4_find_col(df, [
            "Rank","rank","Position","position","順位","Organic Rank"])
        if asin_col and rank_col:
            for kw, grp in df.groupby(kw_col):
                kw_n = _ddv4_norm_kw(str(kw))
                aniha_rank = None; best_comp_rank = None
                for _, row in grp.iterrows():
                    asin = str(row[asin_col]).strip().upper()
                    try: r = int(float(str(row[rank_col])))
                    except: continue
                    if r <= 0: continue
                    if asin == product_asin.upper():
                        if aniha_rank is None or r < aniha_rank: aniha_rank = r
                    else:
                        if best_comp_rank is None or r < best_comp_rank: best_comp_rank = r
                result[kw_n] = {"aniha_rank": aniha_rank, "best_comp_rank": best_comp_rank}
        else:
            import re
            asin_pat = re.compile(r"^B0[A-Z0-9]{8}$", re.IGNORECASE)
            asin_cols = [c for c in df.columns
                         if c != kw_col and asin_pat.match(str(c).strip())]
            if not asin_cols:
                asin_cols = [c for c in df.columns
                             if c != kw_col and str(c).strip() not in ("","nan")]
            for _, row in df.iterrows():
                kw_n = _ddv4_norm_kw(str(row[kw_col]))
                aniha_rank = None; best_comp_rank = None
                for col in asin_cols:
                    try: r = int(float(str(row[col])))
                    except: continue
                    if r <= 0: continue
                    if str(col).strip().upper() == product_asin.upper():
                        if aniha_rank is None or r < aniha_rank: aniha_rank = r
                    else:
                        if best_comp_rank is None or r < best_comp_rank: best_comp_rank = r
                result[kw_n] = {"aniha_rank": aniha_rank, "best_comp_rank": best_comp_rank}
        return result
    except Exception as e:
        return {"_error": str(e)}


# ─── スコア算出 ────────────────────────────────────────────────

def _ddv4_sv_score(sv):
    """需要スコア 0-25点"""
    try: v = float(str(sv).replace(",",""))
    except (ValueError, TypeError): return 7
    if v >= 10000: return 25
    if v >= 5000:  return 21
    if v >= 1000:  return 17
    if v >= 300:   return 12
    if v >= 100:   return 7
    return 2

def _ddv4_fit_score(kw, product_label, relevancy_raw=None):
    """商品適合性 0-25点: Relevancy(0-10)+辞書(0-8)+カテゴリ語(0-5)+購買意図(0-2)"""
    kn = _ddv4_norm_kw(kw)
    score = 0
    if relevancy_raw is not None:
        try:
            rel = float(str(relevancy_raw).replace("%","").strip())
            if rel > 1: rel = rel / 100.0
            score += min(10, round(rel * 10))
        except (ValueError, TypeError):
            pass
    prod_kws = _DDV4_PRODUCTS.get(product_label, [])
    matches  = sum(1 for w in prod_kws if _ddv4_norm_kw(w) in kn)
    score += min(8, matches * 4)
    cat_kws     = _DDV4_CATEGORY_TERMS.get(product_label, [])
    cat_matches = sum(1 for w in cat_kws if _ddv4_norm_kw(w) in kn)
    score += min(5, cat_matches * 2)
    for word in _DDV4_PURCHASE_INTENT_WORDS:
        if word in kn:
            score += 2
            break
    return min(25, score)

def _ddv4_comp_score(asin_dict, product_asin):
    """競争余地 0-15点: Strengthベース+Variations補正
    RC: 単独加点・単独減点禁止 — 競争圧力テキスト表示のみ利用
    """
    if not asin_dict or not product_asin:
        return 9
    entry = asin_dict.get(product_asin.upper())
    if not entry or not isinstance(entry, dict):
        return 9
    key  = str(entry.get("strength","")).strip().lower()
    base = _DDV4_COMP_BASE.get(key, 9)
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
    return max(0, min(15, base + var_adj))

def _ddv4_opportunity_score(s_demand, s_fit, asin_dict, product_asin):
    """攻略余地 0-15点: 需要×適合性×競争圧力×市場飽和
    RC: 単独加点・単独減点禁止
    """
    score = 0
    # 需要要素 (0-4)
    if   s_demand >= 21: score += 4
    elif s_demand >= 17: score += 3
    elif s_demand >= 12: score += 2
    elif s_demand >= 7:  score += 1
    # 適合性要素 (0-4)
    if   s_fit >= 20: score += 4
    elif s_fit >= 14: score += 3
    elif s_fit >= 8:  score += 2
    elif s_fit >= 4:  score += 1
    # 競争圧力低い → 加点 (Strengthのみ, RC非寄与) (0-4)
    entry = asin_dict.get(product_asin.upper(), {}) if asin_dict and product_asin else {}
    if isinstance(entry, dict):
        key = str(entry.get("strength","")).strip().lower()
        if   key == "very weak":   score += 4
        elif key == "weak":        score += 3
        elif key == "medium":      score += 2
        elif key == "strong":      score += 1
        elif key == "very strong": score += 0
        else:                      score += 2  # データなし
        # 市場飽和低い(Variations少ない) (0-3)
        variations = entry.get("variations")
        if variations is not None:
            try:
                v = int(float(str(variations)))
                if   v <= 5:  score += 3
                elif v <= 15: score += 2
                elif v <= 30: score += 1
                elif v <= 50: score += 0
                else:         score -= 1
            except (ValueError, TypeError):
                pass
    else:
        score += 2
    return max(0, min(15, score))

def _ddv4_gap_score(sv_raw, aniha_rank, best_comp_rank):
    """KWギャップ 0-20点: 競合が取っているがANIHAが取れていない市場を最大評価"""
    try: sv = float(str(sv_raw).replace(",",""))
    except: sv = 0
    sv_high = sv >= 1000
    sv_mid  = sv >= 300
    comp_top10  = best_comp_rank is not None and best_comp_rank <= 10
    comp_strong = best_comp_rank is not None and best_comp_rank <= 20
    aniha_top10 = aniha_rank is not None and aniha_rank <= 10
    aniha_weak  = aniha_rank is not None and 11 <= aniha_rank <= 30
    aniha_out   = aniha_rank is None or aniha_rank > 30
    if best_comp_rank is None and aniha_rank is None:
        return 7   # データなし=中立
    if aniha_top10:
        return 8 if sv_high else 5   # 守るKW: 加点小
    if sv_high and comp_top10 and aniha_out:   return 20  # 攻略KW最大
    if sv_high and comp_top10 and aniha_weak:  return 16  # 奪取KW
    if sv_high and comp_strong and aniha_out:  return 13
    if sv_high and comp_strong and aniha_weak: return 11
    if sv_mid  and comp_top10 and aniha_out:   return 12
    if sv_mid  and comp_strong and aniha_weak: return 9
    if sv_mid  and aniha_out:                  return 8
    if not sv_mid and comp_strong:             return 2   # 低需要+競合強
    return 5

def _ddv4_kw_category(sv_raw, aniha_rank, best_comp_rank):
    """KW分類: 攻略KW / 奪取KW / 守るKW / 放棄KW"""
    try: sv = float(str(sv_raw).replace(",",""))
    except: sv = 0
    sv_high      = sv >= 1000
    comp_top10   = best_comp_rank is not None and best_comp_rank <= 10
    aniha_top10  = aniha_rank is not None and aniha_rank <= 10
    aniha_11to30 = aniha_rank is not None and 11 <= aniha_rank <= 30
    aniha_out    = aniha_rank is None or aniha_rank > 30
    if aniha_top10:
        return "守るKW"
    if aniha_11to30 and comp_top10:
        return "奪取KW"
    if sv_high and comp_top10 and aniha_out:
        return "攻略KW"
    return "放棄KW"

def _ddv4_competition_pressure(asin_dict, product_asin):
    """競争圧力テキスト: Strength主体 (RC非寄与)"""
    if not asin_dict or not product_asin:
        return "中"
    entry = asin_dict.get(product_asin.upper())
    if not entry or not isinstance(entry, dict):
        return "中"
    key = str(entry.get("strength","")).strip().lower()
    if key in ("very strong","strong"):  return "高"
    if key in ("very weak","weak"):      return "低"
    return "中"

def _ddv4_entry_difficulty(s_comp, s_opp, s_gap):
    """参入難易度: 競争余地+攻略余地+KWギャップの合算"""
    total = s_comp + s_opp + s_gap
    if   total >= 42: return "容易"
    elif total >= 32: return "やや容易"
    elif total >= 22: return "中程度"
    elif total >= 14: return "やや困難"
    return "困難"

def _ddv4_calc_rank(score):
    if score >= 90: return "S"
    if score >= 80: return "A"
    if score >= 70: return "B"
    if score >= 60: return "C"
    return "D"

def _ddv4_make_reason(s_demand, s_fit, s_comp, s_opp, s_gap,
                       aniha_rank, best_comp_rank, kw_cat):
    """攻略理由 最低3理由生成"""
    parts = []
    # 需要
    if   s_demand >= 21: parts.append("検索需要が高い(SV>=5,000)")
    elif s_demand >= 17: parts.append("検索需要が中〜高(SV>=1,000)")
    elif s_demand >= 12: parts.append("需要が中程度(SV>=300)")
    elif s_demand >= 7:  parts.append("需要がある(SV>=100)")
    else:                parts.append("需要が低い")
    # 適合性
    if   s_fit >= 20: parts.append("商品適合性が非常に高い")
    elif s_fit >= 12: parts.append("商品適合性が高い")
    elif s_fit >= 6:  parts.append("商品適合性あり")
    else:             parts.append("商品適合性が低い")
    # 競争余地
    if   s_comp >= 13: parts.append("競争余地が大きい")
    elif s_comp >= 8:  parts.append("競争余地が中程度")
    else:              parts.append("競争余地が小さい")
    # 攻略余地
    if   s_opp >= 12: parts.append("市場攻略余地が大きい")
    elif s_opp >= 7:  parts.append("市場攻略余地がある")
    # 順位ギャップ
    if best_comp_rank is not None and best_comp_rank <= 10:
        parts.append(f"競合が上位表示中({best_comp_rank}位)")
    if aniha_rank is None or aniha_rank > 30:
        parts.append("ANIHA順位が低く改善余地が大きい")
    elif 11 <= aniha_rank <= 30:
        parts.append(f"ANIHA{aniha_rank}位→Top10を狙える")
    elif aniha_rank <= 10:
        parts.append(f"ANIHA{aniha_rank}位→維持が重要")
    # KW分類
    if   kw_cat == "攻略KW": parts.append("市場攻略余地がある")
    elif kw_cat == "奪取KW": parts.append("ランク上昇で売上獲得可能")
    elif kw_cat == "守るKW": parts.append("現在のポジション維持が重要")
    return " / ".join(parts)

def _ddv4_market_analysis(asin_dict):
    """市場攻略分析: competitors.csv全体をスキャン"""
    entries = {k: v for k, v in asin_dict.items()
               if not k.startswith("_") and isinstance(v, dict)}
    if not entries:
        return {
            "type": "データなし", "difficulty": "不明",
            "strength_counts": {k: 0 for k in
                ("very strong","strong","medium","weak","very weak")},
            "n_asins": 0, "brand_count": 0,
            "brand_concentration": 0.0, "avg_sku": 0.0,
        }
    sc = {k: 0 for k in ("very strong","strong","medium","weak","very weak")}
    brands = set()
    total_var = 0; n_with_var = 0
    n_asins = len(entries)
    for asin, data in entries.items():
        key = str(data.get("strength","")).strip().lower()
        if key in sc: sc[key] += 1
        brand = data.get("brand")
        if brand and str(brand).strip().lower() not in ("","nan"):
            brands.add(str(brand).strip().lower())
        v = data.get("variations")
        if v is not None:
            try:
                total_var += int(float(str(v)))
                n_with_var += 1
            except: pass
    n_strong     = sc["very strong"] + sc["strong"]
    strong_ratio = n_strong / n_asins if n_asins > 0 else 0
    if   strong_ratio >= 0.6: market_type = "レッドオーシャン"
    elif strong_ratio >= 0.3: market_type = "ライトレッド"
    else:                     market_type = "ブルーオーシャン"
    avg_sku = total_var / n_with_var if n_with_var > 0 else 0
    brand_conc = len(brands) / n_asins if n_asins > 0 else 0
    if market_type == "ブルーオーシャン" and avg_sku < 20:
        difficulty = "参入推奨"
    elif market_type == "レッドオーシャン" or avg_sku >= 50:
        difficulty = "非推奨"
    else:
        difficulty = "様子見"
    return {
        "type": market_type, "difficulty": difficulty,
        "strength_counts": sc, "n_asins": n_asins,
        "brand_count": len(brands),
        "brand_concentration": brand_conc, "avg_sku": avg_sku,
    }

def _ddv4_calculate_sellable_keywords(
        cands_df, sv_col, rel_col, product_label, asin_dict, product_asin, rank_map):
    """攻略KW発掘エンジン v4.4:
    需要(25)+商品適合性(25)+競争余地(15)+攻略余地(15)+KWギャップ(20) = 100点
    RC: 単独加点・単独減点禁止 / Keyword単位competitors参照禁止
    """
    import pandas as pd
    s_comp_base = _ddv4_comp_score(asin_dict, product_asin)
    comp_prs    = _ddv4_competition_pressure(asin_dict, product_asin)
    results = []
    for _, row in cands_df.iterrows():
        kw_raw  = str(row["_kw"])
        kw_n    = _ddv4_norm_kw(kw_raw)
        sv_val  = row.get(sv_col, 0)    if sv_col  and sv_col  in cands_df.columns else 0
        rel_val = row.get(rel_col, None) if rel_col and rel_col in cands_df.columns else None
        rank_data      = rank_map.get(kw_n, {}) if rank_map else {}
        aniha_rank     = rank_data.get("aniha_rank")
        best_comp_rank = rank_data.get("best_comp_rank")
        s_demand = _ddv4_sv_score(sv_val)
        s_fit    = _ddv4_fit_score(kw_raw, product_label, rel_val)
        s_comp   = s_comp_base
        s_opp    = _ddv4_opportunity_score(s_demand, s_fit, asin_dict, product_asin)
        s_gap    = _ddv4_gap_score(sv_val, aniha_rank, best_comp_rank)
        kw_cat   = _ddv4_kw_category(sv_val, aniha_rank, best_comp_rank)
        final    = max(0, min(100, s_demand + s_fit + s_comp + s_opp + s_gap))
        results.append({
            "_kw":             kw_raw,
            "_sv_raw":         sv_val,
            "_rel_raw":        rel_val,
            "_aniha_rank":     aniha_rank,
            "_best_comp_rank": best_comp_rank,
            "_s_demand":       s_demand,
            "_s_fit":          s_fit,
            "_s_comp":         s_comp,
            "_s_opp":          s_opp,
            "_s_gap":          s_gap,
            "売れる予測スコア": final,
            "ランク":           _ddv4_calc_rank(final),
            "KW分類":           kw_cat,
            "競争圧力":         comp_prs,
            "参入難易度":       _ddv4_entry_difficulty(s_comp, s_opp, s_gap),
            "攻略理由":         _ddv4_make_reason(
                                    s_demand, s_fit, s_comp, s_opp, s_gap,
                                    aniha_rank, best_comp_rank, kw_cat),
        })
    return pd.DataFrame(results)


def _ddv4_render_sellable_keywords():
    st.markdown("### 🎯 DateDive 攻略KW発掘エンジン v4.4")
    st.markdown("---")

    st.markdown("##### 📌 分析対象商品を選択")
    prod_opts  = ["― 選択してください ―"] + list(_DDV4_PRODUCTS.keys())
    prod_label = st.selectbox("商品選択", prod_opts, key="ddv4_prod",
                              label_visibility="collapsed")
    if prod_label == "― 選択してください ―":
        st.info("📌 分析対象商品を選択してください。")
        return
    st.success(f"✅ {prod_label}")
    st.markdown("")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📄 DateDive Keywords CSV")
        ddv4_kw = st.file_uploader("keywords.csv", type="csv",
                                   key="ddv4_kw_csv", label_visibility="collapsed")
        if ddv4_kw: st.success(f"✅ {ddv4_kw.name}")
        else: st.caption("niche-XXXX-keywords.csv をアップロード")
    with c2:
        st.markdown("##### 📄 DateDive Competitors CSV")
        ddv4_comp = st.file_uploader("competitors.csv", type="csv",
                                     key="ddv4_comp_csv", label_visibility="collapsed")
        if ddv4_comp: st.success(f"✅ {ddv4_comp.name}")
        else: st.caption("niche-XXXX-competitors.csv をアップロード")
    st.markdown("")
    st.markdown("##### 📄 ASIN順位マップCSV （最重要 - KWギャップ分析用）")
    ddv4_rank = st.file_uploader("ASIN順位マップCSV", type="csv",
                                  key="ddv4_rank_csv", label_visibility="collapsed")
    if ddv4_rank: st.success(f"✅ {ddv4_rank.name}")
    else: st.caption("投入なしの場合はKWギャップスコア=7点（中立）で動作。形式: Search Term, ASIN, Rank または横形式")
    st.markdown("")
    st.markdown("##### 📄 Amazon検索用語CSV（既存運用KW除外用）")
    ddv4_amz = st.file_uploader("Amazon検索用語CSV", type="csv",
                                  key="ddv4_amz_csv", label_visibility="collapsed")
    if ddv4_amz: st.success(f"✅ {ddv4_amz.name}")
    else: st.caption("未投入の場合はKW除外なしで実行")
    st.markdown("")

    exec_btn = st.button("🎯 攻略KW抽出", type="primary",
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
    n_total = len(kw_df)

    with st.spinner("既存運用KW除外中..."):
        existing_set = _ddv4_load_amazon_search_csv(ddv4_amz)
        keep = [not _ddv4_is_excluded(kw, existing_set)
                for kw in kw_df["_kw"]] if existing_set else [True]*len(kw_df)
    kw_df["_keep"] = keep
    exc_df   = kw_df[~kw_df["_keep"]].copy()
    cands_df = kw_df[kw_df["_keep"]].copy().reset_index(drop=True)
    n_excl   = len(exc_df)
    if cands_df.empty:
        st.warning("全KWが運用中KWと重複しています。"); return

    with st.spinner("competitors.csv 読み込み中..."):
        asin_dict = _ddv4_load_asin_comp_dict(ddv4_comp)
    if asin_dict.get("_error"):
        st.warning("competitors.csv 読み込みエラー（デフォルト値で続行）")
        asin_dict = {}

    product_asin = _DDV4_PRODUCT_ASINS.get(prod_label, "")

    with st.spinner("ASIN順位マップCSV 読み込み中..."):
        rank_map = _ddv4_load_rank_map_csv(ddv4_rank, product_asin)
    if rank_map.get("_error"):
        st.warning(f"ASIN順位マップエラー: {rank_map['_error']}（KWギャップ=7点で続行）")
        rank_map = {}
    n_rank_kws = len([k for k in rank_map if not k.startswith("_")])

    with st.spinner("攻略スコアリング中..."):
        scored = _ddv4_calculate_sellable_keywords(
            cands_df, sv_col, rel_col, prod_label, asin_dict, product_asin, rank_map)

    scored_all    = scored.sort_values("売れる予測スコア", ascending=False).reset_index(drop=True)
    scored_ranked = scored_all[scored_all["ランク"] != "D"].copy().reset_index(drop=True)
    scored_d      = scored_all[scored_all["ランク"] == "D"].copy().reset_index(drop=True)

    n_attack  = int((scored_all["KW分類"] == "攻略KW").sum())
    n_capture = int((scored_all["KW分類"] == "奪取KW").sum())
    n_defend  = int((scored_all["KW分類"] == "守るKW").sum())
    n_abandon = int((scored_all["KW分類"] == "放棄KW").sum())
    n_d       = len(scored_d)
    top_gap   = int(scored_all["_s_gap"].max())      if not scored_all.empty else 0
    avg_gap   = round(float(scored_all["_s_gap"].mean()), 1) if not scored_all.empty else 0.0
    top_score = int(scored_all["売れる予測スコア"].max())    if not scored_all.empty else 0
    avg_score = round(float(scored_all["売れる予測スコア"].mean()), 1) if not scored_all.empty else 0.0

    asin_entry  = asin_dict.get(product_asin.upper(), {}) if product_asin else {}
    asin_str    = asin_entry.get("strength")    if isinstance(asin_entry, dict) else None
    asin_var    = asin_entry.get("variations")  if isinstance(asin_entry, dict) else None
    asin_rc     = asin_entry.get("review_count")if isinstance(asin_entry, dict) else None
    n_asins_tot = len([k for k in asin_dict if not k.startswith("_")])
    _str_disp   = asin_str or "取得不可"
    _var_disp   = str(int(asin_var)) if asin_var is not None else "取得不可"
    _rc_disp    = f"{int(asin_rc):,}" if asin_rc else "取得不可"
    comp_prs    = _ddv4_competition_pressure(asin_dict, product_asin)
    _pcol       = "#C53030" if comp_prs == "高" else "#2F855A" if comp_prs == "低" else "#718096"

    # 市場攻略分析
    mkt = _ddv4_market_analysis(asin_dict)
    mkt_color = {"\u30d6\u30eb\u30fc\u30aa\u30fc\u30b7\u30e3\u30f3":"#38A169","\u30e9\u30a4\u30c8\u30ec\u30c3\u30c9":"#D97706","\u30ec\u30c3\u30c9\u30aa\u30fc\u30b7\u30e3\u30f3":"#C53030"}
    _mtype_col  = mkt_color.get(mkt["type"], "#718096")
    _diff_color = {"\u53c2\u5165\u63a8\u5968":"#38A169","\u69d8\u5b50\u898b":"#D97706","\u975e\u63a8\u5968":"#C53030"}
    _dif_col    = _diff_color.get(mkt["difficulty"], "#718096")
    sc = mkt["strength_counts"]

    # ─── KPIカード 8枚 (4+4) ─────────────────────────────────
    st.markdown("---")
    _k1,_k2,_k3,_k4 = st.columns(4)
    kpi(_k1,"🎯","攻略KW数",     f"{n_attack}件", "高需要·競合上位·ANIHA弱","#F3ECFF","#6B46C1")
    kpi(_k2,"💥","奪取KW数",     f"{n_capture}件","ANIHA11〜30·競合TOP10","#EAF7EF","#2F855A")
    kpi(_k3,"🛡","守るKW数",     f"{n_defend}件", "ANIHA Top10","#EAF2FF","#3B82F6")
    kpi(_k4,"🚫","放棄KW数",     f"{n_abandon}件","投資対効果低","#FEF2F2","#C53030")
    _k5,_k6,_k7,_k8 = st.columns(4)
    kpi(_k5,"⚡","最高KWギャップ", f"{top_gap}点",  "KWギャップ最上位","#FFF9E8","#F59E0B")
    kpi(_k6,"📈","平均KWギャップ", f"{avg_gap}点","KWギャップ平均","#F4F6F8","#718096")
    kpi(_k7,"🏆","最高スコア",   f"{top_score}点","売れる予測最上位","#EAF2FF","#3B82F6")
    kpi(_k8,"📊","平均スコア",   f"{avg_score}点","全KW平均","#F4F6F8","#718096")
    st.markdown("")

    # 市場攻略分析ブロック
    sc_rows = "".join([
        f"<tr><td style='padding:4px 8px;font-weight:600;color:#4A5568;'>{k.title()}</td>"
        f"<td style='padding:4px 8px;'>{v}件</td></tr>"
        for k, v in sc.items()
    ])
    st.markdown(
        "<div style='background:#EBF8FF;border:1px solid #90CDF4;"
        "border-left:4px solid #3182CE;border-radius:8px;padding:14px 18px;margin-bottom:12px;'>"
        "<div style='font-weight:700;font-size:.92rem;color:#1A365D;margin-bottom:8px;'>"
        "🌊 市場攻略分析</div>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap;'>"
        "<div>"
        f"<div style='font-size:.8rem;color:#4A5568;'>市場タイプ</div>"
        f"<div style='font-size:1.2rem;font-weight:800;color:{_mtype_col};'>{mkt['type']}</div>"
        "</div>"
        "<div>"
        f"<div style='font-size:.8rem;color:#4A5568;'>市場難易度</div>"
        f"<div style='font-size:1.2rem;font-weight:800;color:{_dif_col};'>{mkt['difficulty']}</div>"
        "</div>"
        "<div>"
        f"<div style='font-size:.8rem;color:#4A5568;'>ブランド集中度</div>"
        f"<div style='font-size:1rem;font-weight:700;'>{len([k for k in asin_dict if not k.startswith('_')])}ブランド/{mkt['n_asins']}アイテム</div>"
        "</div>"
        "<div>"
        f"<div style='font-size:.8rem;color:#4A5568;'>SKU飽和度</div>"
        f"<div style='font-size:1rem;font-weight:700;'>平均{mkt['avg_sku']:.1f}バリエーション</div>"
        "</div>"
        "</div>"
        f"<div style='margin-top:10px;font-size:.83rem;'>"
        "<table style='border-collapse:collapse;'>"
        + sc_rows +
        "</table></div></div>",
        unsafe_allow_html=True,
    )

    # 競合情報ブロック
    _str_reason_map = {
        "very weak":"\u7af6\u4e89\u5727\u529b\u304c\u975e\u5e38\u306b\u4f4e\u3044\uff08\u653b\u7565\u4f59\u5730\u304c\u975e\u5e38\u306b\u5927\u304d\u3044\uff09",
        "weak":"\u7af6\u4e89\u5727\u529b\u304c\u4f4e\u3044\uff08\u653b\u7565\u3057\u3084\u3059\u3044\uff09",
        "medium":"\u7af6\u4e89\u5727\u529b\u306f\u4e2d\u7a0b\u5ea6",
        "strong":"\u7af6\u4e89\u5727\u529b\u304c\u5f37\u3044\uff08\u653b\u7565\u306f\u96e3\u3057\u3044\uff09",
        "very strong":"\u7af6\u4e89\u5727\u529b\u304c\u975e\u5e38\u306b\u5f37\u3044\uff08\u653b\u7565\u56f0\u96e3\uff09",
    }
    _str_rsn = _str_reason_map.get(str(asin_str).strip().lower() if asin_str else "", "Strength\u30c7\u30fc\u30bf\u672a\u53d6\u5f97")
    st.markdown(
        f"<div style='background:#F0FFF4;border:1px solid #9AE6B4;"
        f"border-left:4px solid #38A169;border-radius:8px;padding:14px 18px;margin-bottom:12px;'>"
        f"<div style='font-weight:700;font-size:.92rem;color:#22543D;margin-bottom:8px;'>"
        f"\U0001f3ea \u7af6\u5408\u60c5\u5831 \u2014 {prod_label}\uff08ASIN: {product_asin}\uff09</div>"
        f"<table style='width:100%;font-size:.85rem;border-collapse:collapse;'>"
        f"<tr><td style='width:20%;padding:4px 8px;font-weight:600;color:#276749;'>Strength</td>"
        f"<td style='padding:4px 8px;'>{_str_disp}</td>"
        f"<td style='width:20%;padding:4px 8px;font-weight:600;color:#276749;'>\u7af6\u4e89\u5727\u529b</td>"
        f"<td style='padding:4px 8px;font-weight:700;color:{_pcol};'>{comp_prs}</td></tr>"
        f"<tr><td style='padding:4px 8px;font-weight:600;color:#276749;'>Variations</td>"
        f"<td style='padding:4px 8px;'>{_var_disp}\u4ef6</td>"
        f"<td style='padding:4px 8px;font-weight:600;color:#276749;'>Review Count</td>"
        f"<td style='padding:4px 8px;'>{_rc_disp}\uff08\u7af6\u4e89\u5727\u529b\u8868\u793a\u306e\u307f\xb7\u30b9\u30b3\u30a2\u975e\u5bc4\u4e0e\uff09</td></tr>"
        f"<tr><td style='padding:4px 8px;font-weight:600;color:#276749;'>\u7af6\u5408\u8a55\u4fa1</td>"
        f"<td style='padding:4px 8px;' colspan='3'>{_str_rsn}</td></tr>"
        f"</table>"
        f"<div style='margin-top:8px;font-size:.8rem;color:#4A5568;'>"
        f"ASIN\u9806\u4f4d\u30de\u30c3\u30d7: {n_rank_kws}\u4ef6KW\u8aad\u8fbc\u6e08 / "
        f"competitors.csv: {n_asins_tot}\u4ef6ASIN</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ロジック説明
    _logic = (
        "<table style='width:100%;border-collapse:collapse;font-size:.82rem;'>"
        "<thead><tr style='background:#DBEAFE;'>"
        "<th style='padding:6px 10px;border:1px solid #BFDBFE;width:20%;'>"
        "\u30b9\u30b3\u30a2\u8ef8</th>"
        "<th style='padding:6px 10px;border:1px solid #BFDBFE;width:9%;'>"
        "\u914d\u70b9</th>"
        "<th style='padding:6px 10px;border:1px solid #BFDBFE;'>\u7b97\u51fa\u65b9\u6cd5</th>"
        "</tr></thead><tbody>"
        "<tr style='background:#EAF2FF;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#3B82F6;'>"
        "\u2460 \u9700\u8981</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-25\u70b9</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "SV&gt;=10k:25/&gt;=5k:21/&gt;=1k:17/&gt;=300:12/&gt;=100:7/&lt;100:2</td></tr>"
        "<tr style='background:#EAF7EF;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#2F855A;'>"
        "\u2461 \u5546\u54c1\u9069\u5408\u6027</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-25\u70b9</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "Relevancy(0-10)+\u5546\u54c1\u8f9e\u66f8(0-8)+\u30ab\u30c6\u30b4\u30ea\u8a9e(0-5)+\u8cfc\u8cb7\u610f\u56f3(0-2)</td></tr>"
        "<tr style='background:#F0FFF4;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#38A169;'>"
        "\u2462 \u7af6\u4e89\u4f59\u5730</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-15\u70b9</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "Strength\u30d9\u30fc\u30b9(VW15/W12/M9/S5/VS2)+Variations\u88dc\u6b63(\xb12) "
        "\u300aRC\u5358\u72ec\u52a0\u6e1b\u70b9\u7981\u6b62\u300b</td></tr>"
        "<tr style='background:#FFFAF0;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#D97706;'>"
        "\u2463 \u653b\u7565\u4f59\u5730</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-15\u70b9</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "\u9700\u8981(0-4)+\u9069\u5408\u6027(0-4)+\u7af6\u4e89\u5727\u529b\u4f4e(0-4)+\u5e02\u5834\u98fd\u548c\u4f4e(0-3) "
        "\u300aRC\u5358\u72ec\u52a0\u6e1b\u70b9\u7981\u6b62\u300b</td></tr>"
        "<tr style='background:#FFF0F5;'>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;color:#9B2C2C;'>"
        "\u2464 KW\u30ae\u30e3\u30c3\u30d7 \u2605\u6700\u91cd\u8981</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-20\u70b9</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "\u653b\u7565KW(\u9ad8SV+\u7af6\u5408TOP10+ANIHA\u5708\u5916):20 / "
        "\u596a\u53d6KW(\u9ad8SV+\u7af6\u5408TOP10+ANIHA11-30):16 / "
        "\u5b88\u308bKW(ANIHA\u9806\u4f4dTop10):5-8 / "
        "\u653e\u68c4KW(\u4f4e\u9700\u8981+\u7af6\u5408\u5f37):2</td></tr>"
        "<tr><td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>\u6700\u7d42\u30b9\u30b3\u30a2</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;font-weight:700;'>0-100\u70b9</td>"
        "<td style='padding:6px 10px;border:1px solid #BFDBFE;'>"
        "\u9700\u8981+\u9069\u5408\u6027+\u7af6\u4e89\u4f59\u5730+\u653b\u7565\u4f59\u5730+KW\u30ae\u30e3\u30c3\u30d7  "
        "S(\u653b\u7565\u6700\u512a\u5148)&gt;=90 / A(\u5e83\u544a\u6295\u5165\u5019\u88dc)&gt;=80 / "
        "B(\u5c06\u6765\u5019\u88dc)&gt;=70 / C(\u4fdd\u7559)&gt;=60 / D(\u9664\u5916)&lt;60</td>"
        "</tr></tbody></table>"
    )
    render_logic_section("\U0001f4ca \u653b\u7565KW \u30b9\u30b3\u30a2\u30ed\u30b8\u30c3\u30af\uff08v4.4\uff09", _logic)

    with st.expander("\U0001f4ca \u5206\u6790\u30d5\u30ed\u30fc\u8a73\u7d30", expanded=False):
        rank_status = f"{n_rank_kws}\u4ef6KW\u8aad\u8fbc\u6e08" if rank_map else "\u672a\u6295\u5165\uff08KW\u30ae\u30e3\u30c3\u30d7=7\u70b9\uff09"
        st.markdown(
            f"| \u30b9\u30c6\u30c3\u30d7 | \u5185\u5bb9 | \u7d50\u679c |\n|---|---|---|\n"
            f"| keywords.csv \u8aad\u8fbc | \u5168KW | **{n_total:,}\u4ef6** |\n"
            f"| \u65e2\u5b58KW\u9664\u5916 | \u91cd\u8907KW\u9664\u53bb | **{n_excl:,}\u4ef6\u9664\u5916/{len(cands_df):,}\u4ef6\u6b8b** |\n"
            f"| competitors.csv | ASIN\u8aad\u8fbc | **{n_asins_tot}\u4ef6ASIN** |\n"
            f"| ASIN\u9806\u4f4d\u30de\u30c3\u30d7 | KW\u30ae\u30e3\u30c3\u30d7\u5206\u6790 | **{rank_status}** |\n"
            f"| \u653b\u7565KW | \u9ad8SV+\u7af6\u5408TOP10+ANIHA\u5708\u5916 | **{n_attack}\u4ef6** |\n"
            f"| \u596a\u53d6KW | ANIHA11\u301c30+\u7af6\u5408TOP10 | **{n_capture}\u4ef6** |\n"
            f"| \u5b88\u308bKW | ANIHA Top10 | **{n_defend}\u4ef6** |\n"
            f"| \u653e\u68c4KW | \u4f4e\u9700\u8981+\u7af6\u4e89\u6fc0\u5316 | **{n_abandon}\u4ef6** |"
        )

    _cond_bar([
        ("\u5bfe\u8c61\u5546\u54c1", prod_label[:20]),
        ("ASIN",     product_asin or "\u672a\u8a2d\u5b9a"),
        ("Strength", _str_disp),
        ("\u5e02\u5834\u30bf\u30a4\u30d7", mkt["type"]),
        ("KW\u30ae\u30e3\u30c3\u30d7\u6700\u9ad8", f"{top_gap}\u70b9"),
    ])
    st.markdown("---")

    # KW分類 + ランクフィルタ
    cat_opts = {
        "\u5168\u8868\u793a": "ALL",
        "\U0001f3af \u653b\u7565KW": "\u653b\u7565KW",
        "\U0001f4a5 \u596a\u53d6KW": "\u596a\u53d6KW",
        "\U0001f6e1 \u5b88\u308bKW": "\u5b88\u308bKW",
        "\U0001f6ab \u653e\u68c4KW": "\u653e\u68c4KW",
    }
    rank_opts = {
        "\u5168\u30e9\u30f3\u30af": "ALL",
        "\U0001f3af S: \u653b\u7565\u6700\u512a\u5148": "S",
        "\U0001f4e2 A: \u5e83\u544a\u6295\u5165\u5019\u88dc": "A",
        "\U0001f680 B: \u5c06\u6765\u5019\u88dc": "B",
        "\U0001f440 C: \u4fdd\u7559": "C",
    }
    _fc1, _fc2, _fc3 = st.columns([3, 3, 4])
    with _fc1:
        sel_cat_label  = st.selectbox("KW\u5206\u985e", list(cat_opts.keys()), key="ddv4_cat_sel")
    with _fc2:
        sel_rank_label = st.selectbox("\u30e9\u30f3\u30af\u7d5e\u8fbc", list(rank_opts.keys()), key="ddv4_rank_sel")
    sel_cat = cat_opts[sel_cat_label]
    sel_rk  = rank_opts[sel_rank_label]
    view_df = scored_ranked.copy()
    if sel_cat != "ALL":
        view_df = view_df[view_df["KW\u5206\u985e"] == sel_cat]
    if sel_rk != "ALL":
        view_df = view_df[view_df["\u30e9\u30f3\u30af"] == sel_rk]
    view_df = view_df.reset_index(drop=True)
    n_view  = len(view_df)

    st.markdown(
        f'<div class="count-badge">\u8a72\u5f53\u4ef6\u6570: <b style="font-size:1.1rem;">{n_view}\u4ef6</b>'
        f'&nbsp;<span style="color:#718096;font-size:.8rem;">'
        f'{sel_cat_label} / {sel_rank_label} / \u5e73\u5747{avg_score}\u70b9 / \u6700\u9ad8{top_score}\u70b9</span></div>',
        unsafe_allow_html=True,
    )

    if view_df.empty:
        st.info("\u6761\u4ef6\u306b\u5408\u3046\u30ad\u30fc\u30ef\u30fc\u30c9\u306f\u3042\u308a\u307e\u305b\u3093\u3002")
    else:
        st.markdown("**\U0001f4cb Amazon\u5e83\u544a\u767b\u9332\u7528KW\u4e00\u89a7**\uff08\u53f3\u4e0a\u30b3\u30d4\u30fc\u30dc\u30bf\u30f3\uff09")
        st.code("\n".join(view_df["_kw"].astype(str).tolist()), language=None)

        st.markdown("##### \U0001f4cb \u653b\u7565KW\u8a73\u7d30\u30c6\u30fc\u30d6\u30eb")
        disp = pd.DataFrame(index=range(1, len(view_df)+1))
        disp["Search Terms"]      = view_df["_kw"].astype(str).values
        disp["Search Volume"]     = view_df["_sv_raw"].values
        disp["ANIHA\u9806\u4f4d"]      = view_df["_aniha_rank"].apply(
            lambda x: f"{int(x)}\u4f4d" if x is not None and str(x) not in ("nan","None") else "\u5708\u5916").values
        disp["\u7af6\u5408\u6700\u9ad8\u9806\u4f4d"] = view_df["_best_comp_rank"].apply(
            lambda x: f"{int(x)}\u4f4d" if x is not None and str(x) not in ("nan","None") else "-").values
        disp["\u9700\u8981(0-25)"]      = view_df["_s_demand"].values
        disp["\u9069\u5408\u6027(0-25)"]   = view_df["_s_fit"].values
        disp["\u7af6\u4e89\u4f59\u5730(0-15)"] = view_df["_s_comp"].values
        disp["\u653b\u7565\u4f59\u5730(0-15)"] = view_df["_s_opp"].values
        disp["KW\u30ae\u30e3\u30c3\u30d7(0-20)"] = view_df["_s_gap"].values
        disp["\u58f2\u308c\u308b\u4e88\u6e2c\u30b9\u30b3\u30a2"] = view_df["\u58f2\u308c\u308b\u4e88\u6e2c\u30b9\u30b3\u30a2"].values
        disp["Rank"]              = view_df["\u30e9\u30f3\u30af"].values
        disp["KW\u5206\u985e"]       = view_df["KW\u5206\u985e"].values
        disp["\u7af6\u4e89\u5727\u529b"]     = view_df["\u7af6\u4e89\u5727\u529b"].values
        disp["\u53c2\u5165\u96e3\u6613\u5ea6"]   = view_df["\u53c2\u5165\u96e3\u6613\u5ea6"].values
        disp["\u653b\u7565\u7406\u7531"]     = view_df["\u653b\u7565\u7406\u7531"].astype(str).values
        st.dataframe(disp, use_container_width=True)

        dl_csv = (disp.reset_index(names="\u9806\u4f4d")
                      .to_csv(index=False, encoding="utf-8-sig")
                      .encode("utf-8-sig"))
        st.download_button("\U0001f4e5 \u653b\u7565KW.csv", data=dl_csv,
                           file_name=f"\u653b\u7565KW_{prod_label[:10]}.csv",
                           mime="text/csv", use_container_width=True)

        with st.expander("\U0001f50d \u30b9\u30b3\u30a2\u5185\u8a33\uff08\u4e0a\u4f4d20\u4ef6\uff09", expanded=False):
            top20 = scored_all.head(20).reset_index(drop=True)
            dbg = pd.DataFrame({
                "\u9806\u4f4d":           range(1, len(top20)+1),
                "Keyword":        top20["_kw"].astype(str).values,
                "SV":             top20["_sv_raw"].values,
                "ANIHA\u9806\u4f4d":     top20["_aniha_rank"].values,
                "\u7af6\u5408\u6700\u9ad8\u9806\u4f4d":  top20["_best_comp_rank"].values,
                "\u9700\u8981(0-25)":     top20["_s_demand"].values,
                "\u9069\u5408\u6027(0-25)":  top20["_s_fit"].values,
                "\u7af6\u4e89\u4f59\u5730(0-15)": top20["_s_comp"].values,
                "\u653b\u7565\u4f59\u5730(0-15)": top20["_s_opp"].values,
                "KW\u30ae\u30e3\u30c3\u30d7(0-20)": top20["_s_gap"].values,
                "\u6700\u7d42\u30b9\u30b3\u30a2":     top20["\u58f2\u308c\u308b\u4e88\u6e2c\u30b9\u30b3\u30a2"].values,
                "Rank":           top20["\u30e9\u30f3\u30af"].values,
                "KW\u5206\u985e":        top20["KW\u5206\u985e"].values,
            }).set_index("\u9806\u4f4d")
            st.dataframe(dbg, use_container_width=True)

    if not exc_df.empty:
        with st.expander(f"\U0001f6ab \u9664\u5916KW: {n_excl}\u4ef6", expanded=False):
            et = exc_df[["_kw"]].rename(columns={"_kw": "\u9664\u5916KW"}).reset_index(drop=True)
            et.index = et.index + 1
            st.dataframe(et, use_container_width=True)

    if not scored_d.empty:
        with st.expander(f"\U0001f4c9 D\uff08\u9664\u5916\uff09: {n_d}\u4ef6", expanded=False):
            dd = pd.DataFrame({
                "Keyword":   scored_d["_kw"].astype(str).values,
                "SV":        scored_d["_sv_raw"].values,
                "\u9700\u8981":     scored_d["_s_demand"].values,
                "\u9069\u5408\u6027":   scored_d["_s_fit"].values,
                "\u7af6\u4e89\u4f59\u5730": scored_d["_s_comp"].values,
                "\u653b\u7565\u4f59\u5730": scored_d["_s_opp"].values,
                "KW\u30ae\u30e3\u30c3\u30d7": scored_d["_s_gap"].values,
                "Score":     scored_d["\u58f2\u308c\u308b\u4e88\u6e2c\u30b9\u30b3\u30a2"].values,
                "KW\u5206\u985e":  scored_d["KW\u5206\u985e"].values,
            })
            dd.index = range(1, len(dd)+1)
            st.dataframe(dd, use_container_width=True)


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
