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
# ===================================================
st.set_page_config(
    page_title="ANIHA 勝ちKW抽出ツール",
    page_icon="🐾",
    layout="wide"
)

with st.sidebar:
    st.header("⚙️ 設定")
    bt = st.text_area("ブランド除外（改行区切り）", value=DEFAULT_BRANDS, height=120)
    brands = [norm(b) for b in bt.strip().splitlines() if b.strip()]
    st.markdown("---")
    min_ord = st.number_input("最小注文数", min_value=1, max_value=20, value=3, step=1)
    st.markdown("---")
    st.markdown("**売価マスタ**")
    for c, p in PRICES.items():
        st.caption(f"{c}：¥{p:,}")
    st.markdown("---")
    st.caption("ANIHA 勝ちKW抽出ツール 最終確定版")

st.title("🐾 ANIHA 勝ちKW抽出ツール 最終確定版")

tab1, tab2 = st.tabs(["🔍 KW抽出", "📖 取扱説明書"])

# ===================================================
# TAB2: 取扱説明書
# ===================================================
with tab2:
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
「勝ちKW抽出を実行」ボタンを押す
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

    with st.expander("▶ STEP3：アプリへCSVをアップロードする", expanded=False):
        st.markdown("""
**「KW抽出」タブに2つのCSVをアップロードします。**

| アップロード枠 | ファイル |
|---|---|
| ① 検索語句レポート | STEP1で取得したCSV |
| ② ターゲットKWレポート | STEP2で取得したCSV |

**対応エンコーディング（自動判別）**

- UTF-8 / UTF-8 BOM
- UTF-16（タブ区切り）
- Shift-JIS

> ⚠️ 新しいファイルを入れ替えると、結果は自動リセットされます。
""")

    with st.expander("▶ STEP4：分析を実行する", expanded=False):
        st.markdown("""
**「KW抽出」タブの「勝ちKW抽出を実行」ボタンを押します。**

内部処理フロー：

```
オート広告のみ抽出
      ↓
登録済みKW除外（完全一致・部分一致）
      ↓
ブランドKW除外
      ↓
コード・ASIN除外
      ↓
商品タイトル文字列除外
      ↓
KW別集計（売上・広告費・注文数）
      ↓
採用条件フィルタ（売上≥売価×2・ROAS≥2.0・注文数≥3）
      ↓
同一意図KW統合（2段階グループ化）
      ↓
ランク分け（Aランク・B+ランク・Bランク）
```

**採用条件**

| 条件 | 基準値 |
|---|---|
| 売上 | 売価 × 2 以上 |
| ROAS | 2.0 以上 |
| 注文数 | 3件以上（左サイドバーで変更可） |
""")

    with st.expander("▶ STEP5：Amazon広告へ登録する", expanded=False):
        st.markdown("""
**ランク別にKWを確認し、Amazon広告に登録します。**

| ランク | ROAS | 対応 |
|---|---|---|
| 🏆 Aランク | 5.0以上 | 高優先度追加候補KW |
| 🚀 B+ランク | 3.5以上 | 追加検討候補KW |
| 👀 Bランク | 2.0以上 | 監視候補KW |

**Amazon広告への登録手順**

1. Amazon広告管理画面 → スポンサープロダクト広告
2. 手動ターゲッティングキャンペーンを選択
3. 「キーワード」タブ → 「キーワードを追加」
4. マッチタイプ：「**部分一致**」を選択
5. KW一覧をペースト（各ランクのコピーボタンを使用）
6. 保存

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

# ===================================================
# TAB1: KW抽出
# ===================================================
with tab1:
    st.markdown("**目標：50〜100KW程度に絞り追加。実際に広告追加できるKWのみ抽出。**")

    ra_c, rbp_c, rb_c = st.columns(3)
    ra_c.success("🏆 **Aランク**\n\nROAS **5.0以上**\n\n高優先度追加候補")
    rbp_c.info("🚀 **B+ランク**\n\nROAS **3.5〜5.0未満**\n\n追加検討候補")
    rb_c.warning("👀 **Bランク**\n\nROAS **2.0〜3.5未満**\n\n監視候補")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("① 検索語句レポート")
        sf = st.file_uploader("検索語句レポートCSV", type="csv", key="sf", on_change=clear)
    with c2:
        st.subheader("② ターゲットKWレポート")
        tf = st.file_uploader("ターゲットKWレポートCSV", type="csv", key="tf", on_change=clear)

    st.markdown("---")
    run = st.button("🔍 勝ちKW抽出を実行", type="primary", use_container_width=True)

    if run:
        if not sf:
            st.error("検索語句レポートをアップロードしてください"); st.stop()
        if not tf:
            st.error("ターゲットKWレポートをアップロードしてください"); st.stop()

        with st.spinner("処理中..."):
            dfs = rcsv(sf); dft = rcsv(tf)

            kc  = fcol(dfs, ["検索用語", "カスタマーの検索用語", "Customer Search Term", "search term"])
            cc  = fcol(dfs, ["キャンペーン名", "Campaign Name", "campaign name"])
            sc  = fcol(dfs, ["売上", "売上額", "合計売上", "広告費売上高", "7日間の総売上高", "Attributed Sales", "Sales"])
            oc_ = fcol(dfs, ["合計費用", "費用", "広告費", "コスト", "Cost", "Spend", "spend"])
            od  = fcol(dfs, ["商品購入数", "注文数", "注文された商品点数", "Orders", "Purchases"])
            clk = fcol(dfs, ["クリック数", "クリック", "Clicks", "clicks"])
            imp = fcol(dfs, ["インプレッション数", "インプレッション", "Impressions", "impressions"])
            tkc = fcol(dft, ["ターゲティング", "ターゲッティング", "キーワード", "Targeting", "targeting", "Keyword", "keyword"])

            miss = [n for v, n in [(kc, "検索用語"), (cc, "キャンペーン名"), (sc, "売上"), (oc_, "広告費")] if not v]
            if miss:
                st.error(f"列が見つかりません: {miss}")
                st.write(list(dfs.columns)); st.stop()
            if not tkc:
                st.error("ターゲットKWレポートの列が不明")
                st.write(list(dft.columns)); st.stop()

            dfs[sc]  = tonum(dfs[sc])
            dfs[oc_] = tonum(dfs[oc_])
            for c in [od, clk, imp]:
                if c: dfs[c] = tonum(dfs[c])
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

            n_sl = int((agg["sales"] >= agg["price"] * 2).sum())
            d1   = agg[agg["sales"] >= agg["price"] * 2].copy()
            n_ro = int((d1["ROAS"] >= 2.0).sum())
            d1   = d1[d1["ROAS"] >= 2.0].copy()
            if "orders" in d1.columns:
                n_of = int((d1["orders"] < min_ord).sum())
                d1   = d1[d1["orders"] >= min_ord].copy()
            else:
                n_of = 0
            n_af = len(d1)
            d1.drop(columns=["price"], inplace=True, errors="ignore")

            dw = deduplicate_keyword_intent(d1)
            nf = len(dw)
            dw["rank"] = dw["ROAS"].apply(assign_rank)

            st.session_state.update({
                "has_results": True,
                "df_win": dw,
                "df_a":   dw[dw["rank"] == RA].copy(),
                "df_bp":  dw[dw["rank"] == RBP].copy(),
                "df_b":   dw[dw["rank"] == RB].copy(),
                "stats": {
                    "n_auto": n_auto, "n_ex": n_ex, "n_pt": n_pt, "n_ar": n_ar,
                    "n_br": n_br, "n_cd": n_cd, "n_tl": n_tl, "n_ae": n_ae,
                    "n_sl": n_sl, "n_ro": n_ro, "n_of": n_of, "n_af": n_af,
                    "nf": nf, "mo": int(min_ord),
                },
                "dbg": {
                    "kc": kc, "sc": sc, "oc_": oc_, "od": od,
                    "clk": clk, "imp": imp, "rn": len(reg), "br": brands,
                },
            })

    if not st.session_state.get("has_results"):
        st.stop()

    st.success("✅ 分析結果を保持中。新ファイルをアップロードするまで結果は消えません。")

    dw:  pd.DataFrame = st.session_state["df_win"]
    da:  pd.DataFrame = st.session_state["df_a"]
    dbp: pd.DataFrame = st.session_state["df_bp"]
    db:  pd.DataFrame = st.session_state["df_b"]
    sv = st.session_state["stats"]

    st.markdown("---")
    st.subheader("📊 分析フロー")
    st.markdown(f"""
| ステップ | 内容 | 件数 |
|---|---|---|
| オート広告検索語 | 全体 | **{sv["n_auto"]:,}件** |
| 登録済みKW除外後 | 完全一致−{sv["n_ex"]}・部分一致−{sv["n_pt"]} | **{sv["n_ar"]:,}件** |
| ブランド除外後 | −{sv["n_br"]}件 | **{sv["n_ar"]-sv["n_br"]:,}件** |
| コード・Title除外後 | コード−{sv["n_cd"]} / タイトル−{sv["n_tl"]} | **{sv["n_ae"]:,}件** |
| 売上条件通過 | 売価×2以上 | **{sv["n_sl"]:,}件** |
| ROAS条件通過 | ROAS≥2.0 | **{sv["n_ro"]:,}件** |
| 注文数条件通過 | 注文数≥{sv["mo"]} | **{sv["n_af"]:,}件**（−{sv["n_of"]}） |
| **同一意図KW統合後** | 類似KWを代表1件に集約 | **{sv["nf"]:,}件** |
""")

    na = len(da); nbp = len(dbp); nb = len(db)

    def mts(df):
        s = df["sales"].sum(); c = df["cost"].sum()
        return s, c, round(s / c, 2) if c > 0 else 0.0

    as_, ac_, ar_ = mts(da)
    bps_, bpc_, bpr_ = mts(dbp)
    bs_, bc_, br_ = mts(db)

    ca2, cb2, cc2 = st.columns(3)
    with ca2:
        st.success(f"🏆 **Aランク {na:,}件**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("件数", f"{na:,}"); c2.metric("売上", f"¥{as_:,.0f}")
        c3.metric("広告費", f"¥{ac_:,.0f}"); c4.metric("ROAS", f"{ar_:.2f}")
    with cb2:
        st.info(f"🚀 **B+ランク {nbp:,}件**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("件数", f"{nbp:,}"); c2.metric("売上", f"¥{bps_:,.0f}")
        c3.metric("広告費", f"¥{bpc_:,.0f}"); c4.metric("ROAS", f"{bpr_:.2f}")
    with cc2:
        st.warning(f"👀 **Bランク {nb:,}件**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("件数", f"{nb:,}"); c2.metric("売上", f"¥{bs_:,.0f}")
        c3.metric("広告費", f"¥{bc_:,.0f}"); c4.metric("ROAS", f"{br_:.2f}")

    st.markdown("**キャンペーン別件数**")
    cc_cnt = dw["campaign_theme"].value_counts().reindex(CAMPAIGNS, fill_value=0)
    act = [(k, v) for k, v in cc_cnt.items() if v > 0]
    for i in range(0, len(act), 6):
        ch = act[i:i+6]; cols = st.columns(len(ch))
        for col, (name, cnt) in zip(cols, ch):
            col.metric(name, f"{int(cnt):,}件")

    st.markdown("---")
    st.subheader("ランク別 勝ちKW")
    show_rank(da,  RA);  st.markdown("---")
    show_rank(dbp, RBP); st.markdown("---")
    show_rank(db,  RB)

    st.markdown("---")
    st.subheader("⬇️ ダウンロード")
    st.caption("ダウンロードしても結果は保持されます。複数回ダウンロード可能。")

    az   = a_zip(da)
    allz = all_zip(dw)
    ac_csv  = to_csv(da, ["impressions"])
    bpc_csv = to_csv(dbp)
    bc_csv  = to_csv(db)
    allc    = to_csv(dw)

    st.download_button(
        "🏆 Aランク一括ZIP（A_only.zip）",
        data=az, file_name="A_only.zip", mime="application/zip",
        use_container_width=True, type="primary"
    )
    d1, d2, d3, d4, d5 = st.columns(5)
    d1.download_button("全件CSV",      allc,    "winner_all.csv",   "text/csv", use_container_width=True)
    d2.download_button("AランクCSV",   ac_csv,  "winner_A.csv",     "text/csv", use_container_width=True)
    d3.download_button("B+ランクCSV",  bpc_csv, "winner_Bplus.csv", "text/csv", use_container_width=True)
    d4.download_button("BランクCSV",   bc_csv,  "winner_B.csv",     "text/csv", use_container_width=True)
    d5.download_button("キャンペーン別ZIP", allz, "winner_all.zip", "application/zip", use_container_width=True)

    st.markdown("---")
    st.subheader("キャンペーン別 勝ちKW（ROAS降順）")
    dbcols = ["keyword", "rank", "ROAS", "sales", "cost"]
    for c in ["orders", "CVR", "clicks"]:
        if c in dw.columns: dbcols.append(c)

    for camp in CAMPAIGNS:
        dc = dw[dw["campaign_theme"] == camp]
        if dc.empty: continue
        dc  = dc.sort_values("ROAS", ascending=False).reset_index(drop=True)
        na_ = int((dc["rank"] == RA).sum())
        nbp_ = int((dc["rank"] == RBP).sum())
        nb_  = int((dc["rank"] == RB).sum())
        with st.expander(f"▼ {camp}（A:{na_} B+:{nbp_} B:{nb_}）", expanded=False):
            d = dc[[c for c in dbcols if c in dc.columns]].copy()
            d = d.rename(columns=RENAME)
            d.index = range(1, len(d) + 1)
            d["売上"]  = d["売上"].apply(lambda x: f"¥{x:,.0f}")
            d["広告費"] = d["広告費"].apply(lambda x: f"¥{x:,.0f}")
            d["ROAS"] = d["ROAS"].round(2)
            if "CVR" in d.columns:
                d["CVR"] = d["CVR"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(d, use_container_width=True)

    dbg = st.session_state.get("dbg", {})
    with st.expander("デバッグ情報"):
        st.write("検索語句列:", dbg.get("kc"))
        st.write("売上列:", dbg.get("sc"))
        st.write("注文列:", dbg.get("od"))
        st.write("クリック列:", dbg.get("clk"))
        st.write("インプレ列:", dbg.get("imp"))
        st.write("登録KW数:", dbg.get("rn"))
        st.write("除外ブランド:", dbg.get("br"))
