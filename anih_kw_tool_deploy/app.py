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
    for k in ["has_results", "df_win", "df_a", "df_bp", "df_b", "stats", "dbg"]:
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

# ===================================================
# ランク別表示
# ===================================================
def show_rank(df_r: pd.DataFrame, rk: str):
    n = len(df_r)
    st.markdown(f"### {RLABEL[rk]} — {n:,}件")
    if df_r.empty:
        st.info("候補はありません。")
        return
    d = df_r[bcols(df_r)].copy().sort_values("ROAS", ascending=False).reset_index(drop=True)
    d.index = d.index + 1
    d = d.rename(columns=RENAME)
    d["売上"] = d["売上"].apply(lambda x: f"¥{x:,.0f}")
    d["広告費"] = d["広告費"].apply(lambda x: f"¥{x:,.0f}")
    d["ROAS"] = d["ROAS"].round(2)
    if "CVR" in d.columns:
        d["CVR"] = d["CVR"].apply(lambda x: f"{x:.1f}%")
    st.dataframe(d, use_container_width=True)
    st.markdown("**📋 Amazon広告登録用KW一覧**（右上のコピーボタンでコピー）")
    st.code(
        "\n".join(df_r.sort_values("ROAS", ascending=False)["keyword"].tolist()),
        language=None
    )

# ===================================================
# Streamlit アプリ

st.set_page_config(
    page_title="ANIHA 勝ちKW抽出ツール",
    page_icon="🐾",
    layout="wide"
)

# --- Custom CSS ---
st.markdown("""
<style>
.kpi-card {
    background: #1e2130;
    border-radius: 12px;
    padding: 20px 16px;
    text-align: center;
    border: 1px solid #2d3250;
}
.kpi-icon { font-size: 1.6rem; margin-bottom: 4px; }
.kpi-label { font-size: 0.72rem; color: #8b93a7; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 2px; }
.kpi-value { font-size: 2rem; font-weight: 700; color: #e8eaf0; line-height: 1.1; }
.kpi-sub { font-size: 0.7rem; color: #5a6380; margin-top: 3px; }
.section-header { font-size: 1rem; font-weight: 600; color: #a0a8c0; text-transform: uppercase;
    letter-spacing: .08em; padding: 8px 0 4px 0; border-bottom: 1px solid #2d3250; margin-bottom: 12px; }
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### ⚙️ 設定")
    st.markdown("---")
    st.markdown("<p class=\"section-header\">ブランド除外</p>", unsafe_allow_html=True)
    bt = st.text_area("ブランド除外（改行区切り）", value=DEFAULT_BRANDS, height=100, label_visibility="collapsed")
    brands = [norm(b) for b in bt.strip().splitlines() if b.strip()]
    st.markdown("---")
    st.markdown("<p class=\"section-header\">採用条件</p>", unsafe_allow_html=True)
    min_ord  = st.number_input("最小注文数",   min_value=1, max_value=20,    value=3,   step=1)
    min_clk  = st.number_input("最小クリック数", min_value=1, max_value=100,   value=5,   step=1)
    min_cost = st.number_input("最小広告費（¥）", min_value=0, max_value=10000, value=300, step=50)
    st.markdown("---")
    st.markdown("<p class=\"section-header\">売価マスタ</p>", unsafe_allow_html=True)
    for _c, _p in PRICES.items():
        st.caption(f"{_c}：¥{_p:,}")
    st.markdown("---")
    st.caption("ANIHA 勝ちKW抽出ツール 最終確定版")

# --- Header ---
st.markdown("## 🐾 ANIHA 勝ちKW抽出ツール")
st.markdown("<p style=\"color:#6b7280;font-size:.9rem;margin-top:-12px;\">Amazon SP広告 オート広告から勝ちKWを自動抽出 — DataDive連携対応版</p>", unsafe_allow_html=True)
st.markdown("---")

# --- File Upload ---
uc1, uc2, uc3 = st.columns([5, 5, 2])
with uc1:
    st.markdown("**① 検索語句レポート CSV**")
    sf = st.file_uploader("検索語句レポート", type="csv", key="sf", on_change=clear, label_visibility="collapsed")
    if sf: st.success(f"✓ {sf.name}")
with uc2:
    st.markdown("**② ターゲットKWレポート CSV**")
    tf = st.file_uploader("ターゲットKWレポート", type="csv", key="tf", on_change=clear, label_visibility="collapsed")
    if tf: st.success(f"✓ {tf.name}")
with uc3:
    st.markdown("**　**")
    run = st.button("🔍 抽出実行", type="primary", use_container_width=True)

if run:
    if not sf:
        st.error("検索語句レポートをアップロードしてください"); st.stop()
    if not tf:
        st.error("ターゲットKWレポートをアップロードしてください"); st.stop()

    with st.spinner("分析中..."):
        dfs = rcsv(sf); dft = rcsv(tf)
        kc  = fcol(dfs, ["検索用語", "カスタマーの検索用語", "Customer Search Term", "search term"])
        cc  = fcol(dfs, ["キャンペーン名", "Campaign Name", "campaign name"])
        sc  = fcol(dfs, ["売上", "売上額", "合計売上", "広告費売上高", "7日間の総売上高", "Attributed Sales", "Sales"])
        oc_ = fcol(dfs, ["合計費用", "費用", "広告費", "コスト", "Cost", "Spend", "spend"])
        od  = fcol(dfs, ["商品購入数", "注文数", "注文された商品点数", "Orders", "Purchases"])
        clk = fcol(dfs, ["クリック数", "クリック", "Clicks", "clicks"])
        imp = fcol(dfs, ["インプレッション数", "インプレッション", "Impressions", "impressions"])
        tkc = fcol(dft, ["ターゲティング", "ターゲッティング", "キーワード", "Targeting", "targeting", "Keyword", "keyword"])
        miss = [n for v, n in [(kc,"検索用語"),(cc,"キャンペーン名"),(sc,"売上"),(oc_,"広告費")] if not v]
        if miss: st.error(f"列が見つかりません: {miss}"); st.write(list(dfs.columns)); st.stop()
        if not tkc: st.error("ターゲットKWレポートの列が不明"); st.write(list(dft.columns)); st.stop()
        dfs[sc]  = tonum(dfs[sc])
        dfs[oc_] = tonum(dfs[oc_])
        for _col in [od, clk, imp]:
            if _col: dfs[_col] = tonum(dfs[_col])
        dfs["kn"] = dfs[kc].apply(norm)
        dfs["ct"] = dfs[cc].apply(lambda x: official(get_theme(str(x))))
        mask   = dfs[cc].str.contains("オート|auto", case=False, na=False)
        n_auto = int(mask.sum())
        d0     = dfs[mask].copy()
        reg  = set(dft[tkc].apply(norm)); reg.discard("")
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
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0, axis=1
        )
        if "clicks" in agg.columns and "orders" in agg.columns:
            agg["CVR"] = agg.apply(
                lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0, axis=1
            )
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
        st.session_state.update({
            "has_results": True, "df_win": dw,
            "df_a": dw[dw["rank"]==RA].copy(),
            "df_bp": dw[dw["rank"]==RBP].copy(),
            "df_b": dw[dw["rank"]==RB].copy(),
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

if not st.session_state.get("has_results"):
    st.markdown("""<div style="text-align:center;padding:80px 20px;color:#4a5568;">
    <div style="font-size:3.5rem;">📂</div>
    <p style="font-size:1.2rem;font-weight:600;margin-top:16px;color:#e8eaf0;">CSVをアップロードして「抽出実行」を押してください</p>
    <p style="color:#6b7280;">① 検索語句レポート &nbsp;＋&nbsp; ② ターゲットKWレポート</p>
    </div>""", unsafe_allow_html=True)
    st.stop()

# ===================================================
# KPI カード
# ===================================================
dw:  pd.DataFrame = st.session_state["df_win"]
da:  pd.DataFrame = st.session_state["df_a"]
dbp: pd.DataFrame = st.session_state["df_bp"]
db:  pd.DataFrame = st.session_state["df_b"]
sv = st.session_state["stats"]

na = len(da); nbp = len(dbp); nb = len(db)

st.markdown("---")
k1, k2, k3, k4, k5 = st.columns(5)
def kpi(col, icon, label, value, sub=""):
    col.markdown(f"""<div class="kpi-card">
        <div class="kpi-icon">{icon}</div>
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        <div class="kpi-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)

kpi(k1, "🏆", "A ランク", f"{na}件", "高優先度追加候補")
kpi(k2, "🚀", "B+ ランク", f"{nbp}件", "追加検討候補")
kpi(k3, "👀", "B ランク", f"{nb}件", "監視候補")
kpi(k4, "📦", "抽出前", "{}件".format(sv["n_pre"]), "フィルター適用前")
kpi(k5, "🎯", "抽出後", "{}件".format(sv["nf"]), "同一意図KW統合後")
st.markdown("---")
# ===================================================
# 4タブ レイアウト
# ===================================================
tab_res, tab_kw, tab_dl, tab_manual = st.tabs([
    "📊 分析結果",
    "📋 Amazon登録用KW",
    "📥 ダウンロード",
    "📖 取扱説明書",
])

# ===================================================
# TAB①: 分析結果
# ===================================================
with tab_res:
    # 分析フローサマリー
    with st.expander("📊 分析フロー詳細", expanded=False):
        st.markdown(f"""
| ステップ | 内容 | 件数 |
|---|---|---|
| オート広告検索語 | 全体 | **{sv["n_auto"]:,}件** |
| 登録済みKW除外 | 完全一致−{sv["n_ex"]}・部分一致−{sv["n_pt"]} | **{sv["n_ar"]:,}件** |
| ブランド除外 | −{sv["n_br"]}件 | **{sv["n_ar"]-sv["n_br"]:,}件** |
| コード・Title除外 | −{sv["n_cd"]+sv["n_tl"]}件 | **{sv["n_ae"]:,}件** |
| 売上条件（売価×2） | −{sv["n_pre"]-sv["n_sl"]}件 | **{sv["n_sl"]:,}件** |
| ROAS≥2.0 | −{sv["n_sl"]-sv["n_ro"]}件 | **{sv["n_ro"]:,}件** |
| 注文≥{sv["mo"]} | −{sv["n_of"]}件 | |
| クリック≥{sv["mc"]} | −{sv["n_clk_f"]}件 | |
| 広告費≥¥{sv["mco"]} | −{sv["n_cost_f"]}件 | **{sv["n_af"]:,}件** |
| **同一意図KW統合** | 類似KWを代表1件に集約 | **{sv["nf"]:,}件** |
""")

    def _show_rank_expander(df_r, rk, expanded=False):
        n = len(df_r)
        label_map = {RA: "🏆 Aランク", RBP: "🚀 B+ランク", RB: "👀 Bランク"}
        with st.expander(f"{label_map[rk]}（{n}件）", expanded=expanded):
            if df_r.empty:
                st.info("候補はありません。")
                return
            d = df_r[bcols(df_r)].copy().sort_values("ROAS", ascending=False).reset_index(drop=True)
            d.index = d.index + 1
            d = d.rename(columns=RENAME)
            d["売上"]  = d["売上"].apply(lambda x: f"¥{x:,.0f}")
            d["広告費"] = d["広告費"].apply(lambda x: f"¥{x:,.0f}")
            d["ROAS"]  = d["ROAS"].round(2)
            if "CVR" in d.columns: d["CVR"] = d["CVR"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(d, use_container_width=True)
            st.markdown("**📋 KW一覧**（右上コピーボタン）")
            st.code("\n".join(df_r.sort_values("ROAS", ascending=False)["keyword"].tolist()), language=None)

    _show_rank_expander(da,  RA,  expanded=True)
    _show_rank_expander(dbp, RBP, expanded=False)
    _show_rank_expander(db,  RB,  expanded=False)
# ===================================================
# TAB②: Amazon登録用KW
# ===================================================
with tab_kw:
    st.markdown("")
    st.markdown("""<div style="margin-bottom:8px;">
        <span style="font-size:1.25rem;font-weight:700;color:#e8eaf0;">📋 Amazon登録用KW</span>
        <span style="font-size:.85rem;color:#8b93a7;margin-left:12px;">部分一致（Broad）登録専用</span>
    </div>""", unsafe_allow_html=True)

    camps_info = [(c, da[da["campaign_theme"]==c]) for c in CAMPAIGNS if not da[da["campaign_theme"]==c].empty]
    if not camps_info:
        st.markdown("""<div style="text-align:center;padding:60px 20px;color:#4a5568;">
            <div style="font-size:2.5rem;">🔍</div>
            <p style="font-size:1rem;margin-top:12px;color:#e8eaf0;">Aランク候補KWがありません</p>
            <p style="color:#6b7280;font-size:.875rem;">分析期間を延ばして（90日）再実行してください。</p>
        </div>""", unsafe_allow_html=True)
    else:
        camp_labels = [f"{c}（{len(df_c)}件）" for c, df_c in camps_info]

        kw_col, _ = st.columns([3, 2])
        with kw_col:
            sel_label = st.selectbox(
                "キャンペーンを選択",
                camp_labels,
                index=0,
                label_visibility="collapsed",
                placeholder="キャンペーンを選択してください",
            )
        sel_idx = camp_labels.index(sel_label)
        sel_camp, sel_df = camps_info[sel_idx]
        kw_sorted = sel_df.sort_values("ROAS", ascending=False)["keyword"].tolist()
        kw_text = "\n".join(kw_sorted)

        st.markdown(f"""<div style="
            background:#1e2130;border-radius:12px;
            padding:20px 24px;margin:12px 0 4px;
            border-left:4px solid #6c63ff;">
            <div style="font-size:.78rem;color:#8b93a7;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">選択中キャンペーン</div>
            <div style="font-size:1.4rem;font-weight:700;color:#e8eaf0;">{sel_camp}</div>
            <div style="font-size:.9rem;color:#a0aec0;margin-top:4px;">🏆 追加候補KW &nbsp;
                <span style="font-size:1.3rem;font-weight:700;color:#6c63ff;">{len(kw_sorted)}</span>
                <span style="color:#8b93a7;"> 件</span>
            </div>
        </div>""", unsafe_allow_html=True)

        st.markdown("&nbsp;")
        st.markdown("""<div style="font-size:.78rem;color:#8b93a7;text-transform:uppercase;
            letter-spacing:.08em;margin-bottom:4px;">KW一覧（右上コピーボタン ＋ st.code組み込み）</div>""",
            unsafe_allow_html=True)
        st.code(kw_text, language=None)
# ===================================================
# TAB③: ダウンロード
# ===================================================
with tab_dl:
    st.markdown("#### 📥 ダウンロード")
    st.caption("ダウンロードしても結果は保持されます。複数回ダウンロード可能。")
    st.markdown("---")

    az      = a_zip(da)
    az_camp = a_camp_zip(da)
    allz    = all_zip(dw)
    ac_csv  = to_csv(da, ["impressions"])
    bpc_csv = to_csv(dbp)
    bc_csv  = to_csv(db)
    allc    = to_csv(dw)

    st.markdown("**📦 ZIP ファイル**")
    zc1, zc2, zc3 = st.columns(3)
    zc1.download_button(
        "🏆 A_only.zip\n（キャンペーン別Aランク全列）",
        data=az, file_name="A_only.zip", mime="application/zip",
        use_container_width=True, type="primary"
    )
    zc2.download_button(
        "📋 AランクKW_キャンペーン別.zip\n（keyword列のみ）",
        data=az_camp, file_name="AランクKW_キャンペーン別.zip", mime="application/zip",
        use_container_width=True,
    )
    zc3.download_button(
        "📦 winner_all.zip\n（全ランク・全キャンペーン）",
        data=allz, file_name="winner_all.zip", mime="application/zip",
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("**📄 CSV ファイル**")
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.download_button(
        "🏆 Aランク CSV",
        data=ac_csv, file_name="winner_A.csv", mime="text/csv",
        use_container_width=True,
    )
    cc2.download_button(
        "🚀 B+ランク CSV",
        data=bpc_csv, file_name="winner_Bplus.csv", mime="text/csv",
        use_container_width=True,
    )
    cc3.download_button(
        "👀 Bランク CSV",
        data=bc_csv, file_name="winner_B.csv", mime="text/csv",
        use_container_width=True,
    )
    cc4.download_button(
        "📋 全件 CSV",
        data=allc, file_name="winner_all.csv", mime="text/csv",
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("**📂 キャンペーン別 分析結果**")
    dbcols = ["keyword", "rank", "ROAS", "sales", "cost"]
    for _c in ["orders", "CVR", "clicks"]:
        if _c in dw.columns: dbcols.append(_c)
    for camp in CAMPAIGNS:
        dc = dw[dw["campaign_theme"] == camp]
        if dc.empty: continue
        dc = dc.sort_values("ROAS", ascending=False).reset_index(drop=True)
        na_ = int((dc["rank"]==RA).sum()); nbp_=int((dc["rank"]==RBP).sum()); nb_=int((dc["rank"]==RB).sum())
        with st.expander(f"▼ {camp}（A:{na_} B+:{nbp_} B:{nb_}）", expanded=False):
            _d = dc[[c for c in dbcols if c in dc.columns]].copy().rename(columns=RENAME)
            _d.index = range(1, len(_d)+1)
            _d["売上"]  = _d["売上"].apply(lambda x: f"¥{x:,.0f}")
            _d["広告費"] = _d["広告費"].apply(lambda x: f"¥{x:,.0f}")
            _d["ROAS"]  = _d["ROAS"].round(2)
            if "CVR" in _d.columns: _d["CVR"] = _d["CVR"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(_d, use_container_width=True)

    dbg = st.session_state.get("dbg", {})
    with st.expander("🔧 デバッグ情報", expanded=False):
        st.write("検索語句列:", dbg.get("kc")); st.write("売上列:", dbg.get("sc"))
        st.write("注文列:", dbg.get("od")); st.write("クリック列:", dbg.get("clk"))
        st.write("インプレ列:", dbg.get("imp")); st.write("登録KW数:", dbg.get("rn"))
        st.write("除外ブランド:", dbg.get("br"))
# ===================================================
# TAB④: 取扱説明書
# ===================================================
with tab_manual:
    st.header("📖 取扱説明書")
    st.markdown("---")
    st.subheader("📈 基本運用フロー")
    st.markdown("""
```
Amazon広告管理画面にログイン
         ↓
レポート → 検索語句レポート（期間指定）をダウンロード
         ↓
レポート → ターゲットKWレポートをダウンロード
         ↓
このアプリに2つのCSVをアップロード
         ↓
「抽出実行」ボタンを押す
         ↓
Aランク KWリストをコピー
         ↓
Amazon広告 → 手動キャンペーン → 部分一致で登録
```
""")
    st.markdown("---")
    st.subheader("📌 STEP別詳細手順")

    with st.expander("▶ STEP1：検索語句レポートをダウンロードする", expanded=False):
        st.markdown("""
**Amazon広告管理画面で検索語句レポートを取得します。**

1. Amazon広告管理画面にログイン
2. 左メニュー「測定」→「レポート」をクリック
3. 「スポンサープロダクト広告 検索語句」を選択
4. 期間を設定する

| タイミング | 分析期間 | 理由 |
|---|---|---|
| **初回** | 90日 | 十分なデータ量でKW傾向を把握 |
| **毎週（月曜）** | 7日 | 直近の成果で最新KWを追加 |
| **毎月末** | 90日 | B+ランクの再評価 |

5. CSV形式でダウンロード

> ℹ️ オート広告の検索語句のみ使用します。手動広告のデータは自動除外されます。
""")

    with st.expander("▶ STEP2：ターゲットKWレポートをダウンロードする", expanded=False):
        st.markdown("""
**登録済みKWを除外するために必須のファイルです。**

1. 同じレポート画面で「スポンサープロダクト広告」→「ターゲットコンバージョン」を選択
2. 期間は30日程度でOK（登録済みKWは全件含まれます）
3. CSV形式でダウンロード

**このデータの役割**

- 既に登録済みのKWを完全一致・部分一致で自動除外
- 重複登録を防ぐ

> ℹ️ 期間設定はどこでもOK。登録済みKWは常に全件含まれます。
""")

    with st.expander("▶ STEP3：アプリへCSVをアップロードして実行する", expanded=False):
        st.markdown("""
**画面上部に2つのCSVをアップロードし「抽出実行」を押します。**

| アップロード枠 | ファイル |
|---|---|
| ① 検索語句レポート | STEP1で取得したCSV |
| ② ターゲットKWレポート | STEP2で取得したCSV |

**採用条件（左サイドバーで変更可）**

| 条件 | デフォルト値 |
|---|---|
| 売上 | 売価 × 2 以上 |
| ROAS | 2.0 以上 |
| 注文数 | 3件以上 |
| クリック数 | 5以上 |
| 広告費 | ¥300以上 |

> ⚠️ 新しいファイルを入れ替えると、結果は自動リセットされます。
""")

    with st.expander("▶ STEP4：Amazon広告へ登録する", expanded=False):
        st.markdown("""
**「📋 Amazon登録用KW」タブでキャンペーンを選択→KWをコピー→Amazon広告に登録。**

| ランク | ROAS | 対応 |
|---|---|---|
| 🏆 Aランク | 5.0以上 | 高優先度追加候補KW |
| 🚀 B+ランク | 3.5以上 | 追加検討候補KW |
| 👀 Bランク | 2.0以上 | 監視候補KW |

**登録手順**

1. Amazon広告管理画面 → スポンサープロダクト広告
2. 手動ターゲッティングキャンペーンを選択
3. 「キーワード」タブ → 「キーワードを追加」
4. マッチタイプ：「**部分一致**」を選択
5. KW一覧をペースト → 保存

> ⚠️ 必ず「部分一致（Broad）」で登録してください。完全一致は使用しないこと。
""")

    st.markdown("---")
    st.subheader("📅 運用ルール")
    st.markdown("""
| タイミング | 分析期間 | 作業内容 |
|---|---|---|
| **初回起動時** | 90日 | Aランクを全キャンペーン一括追加 |
| **毎週（月曜）** | 7日 | Aランクを確認・順次追加 |
| **毎月末** | 90日 | B+ランクを再評価し追加判断 |

> 💡 毎週は**7日分析**のみ。初回・毎月末のみ90日分析を実施。
""")

    st.markdown("---")
    st.subheader("✅ よくあるミスと正解")
    st.markdown("""
| ❌ ミス | ⭕ 正解 |
|---|---|
| 完全一致で登録する | **部分一致（Broad）**で登録する |
| Bランクを全部追加 | **Aランクを優先**して追加 |
| 毎週90日分析する | 毎週は**7日分析**、毎月末のみ90日 |
| 全候補KWを登録する | **50〜100件程度**に絞り追加する |
| Aランクが0件で諦める | 期間を延ばして（90日）再分析する |
""")
