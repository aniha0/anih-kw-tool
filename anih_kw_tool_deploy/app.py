"""
ANIHA 勝てるKW発掘ツール v1.8 最終版
- 分析フロー詳細表示（売上条件通過・ROAS条件通過 件数）
- Aランク上位10KW表示
- 全CSV ROAS降順ソート
- Aランク専用ZIP (A_only.zip)
- session_state 結果保持
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from typing import Optional

import pandas as pd
import streamlit as st

# DateDive module (独立実装 - 既存コード一切変更なし)
try:
    from modules.datedive.datedive_ui import render_datedive_page as _render_datedive
    _DATEDIVE_OK = True
except ImportError:
    _DATEDIVE_OK = False


# ════════════════════════════════════════════════════════
# 定数
# ════════════════════════════════════════════════════════

ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$", re.IGNORECASE)
CAMPAIGN_THEME_RE = re.compile(r"【(.*?)】")

DEFAULT_BRAND_EXCLUDES = "アニハ\nゾイック\nノルバサン\nマラセブ"

OFFICIAL_CAMPAIGNS = [
    "液体", "涙やけ", "イヤー", "ジェル", "ふりかけ犬",
    "グルーミング", "お口周り", "乳酸菌猫", "ダニ捕り", "肉球",
    "ふりかけ猫", "除菌消臭", "シャンプー", "アイケア", "関節",
    "乳酸菌犬", "肉球S",
]

PRICE_MASTER: dict[str, int] = {
    "ふりかけ犬": 2450, "お口周り": 1480, "ふりかけ猫": 2380,
    "アイケア":   1880, "イヤー":   1480, "グルーミング": 1980,
    "シャンプー": 1880, "ジェル":   1980, "ダニ捕り":   1480,
    "乳酸菌犬":  1880, "乳酸菌猫": 1880, "涙やけ":     1480,
    "液体":      1980, "肉球":     1450, "肉球S":      1480,
    "関節":      1880, "除菌消臭": 1980,
}


# ════════════════════════════════════════════════════════
# ユーティリティ
# ════════════════════════════════════════════════════════

def normalize_text(text) -> str:
    if text is None or (isinstance(text, float) and text != text):
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", text.strip().lower())


def extract_campaign_theme(campaign_name: str) -> str:
    m = CAMPAIGN_THEME_RE.search(str(campaign_name))
    return m.group(1) if m else ""


def is_asin(kw: str) -> bool:
    return bool(ASIN_RE.match(kw.replace(" ", "").upper()))


def read_csv_auto(file) -> pd.DataFrame:
    raw = file.read()
    file.seek(0)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return pd.read_csv(io.BytesIO(raw), encoding="utf-16", sep="\t")
    try:
        return pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(io.BytesIO(raw), encoding="cp932")


def assign_official_campaign(theme: str) -> str:
    if not theme:
        return "未分類"
    if theme in OFFICIAL_CAMPAIGNS:
        return theme
    for c in OFFICIAL_CAMPAIGNS:
        if c in theme:
            return c
    for c in OFFICIAL_CAMPAIGNS:
        if theme in c:
            return c
    return "未分類"


def find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    cols_lower = {col.lower(): col for col in df.columns}
    for c in candidates:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


def is_already_covered(kw_norm: str, registered_set: set[str]) -> bool:
    for reg in registered_set:
        if len(reg.split()) < 2:
            continue
        if reg in kw_norm:
            return True
    return False


def assign_rank(roas: float) -> str:
    return "A" if roas >= 3.0 else "B"


def clear_results() -> None:
    for key in ["has_results", "df_win", "df_a", "df_b", "flow_stats", "debug_info"]:
        st.session_state.pop(key, None)


# ════════════════════════════════════════════════════════
# CSV / ZIP 生成（ROAS降順）
# ════════════════════════════════════════════════════════

def _out_cols(df: pd.DataFrame) -> list[str]:
    base = ["campaign_theme", "keyword", "rank", "ROAS", "sales", "cost"]
    if "orders" in df.columns:
        base.append("orders")
    if "units" in df.columns:
        base.append("units")
    return [c for c in base if c in df.columns]


_COL_RENAME = {
    "campaign_theme": "キャンペーン名",
    "keyword":        "検索語句",
    "rank":           "ランク",
    "ROAS":           "ROAS",
    "sales":          "売上",
    "cost":           "広告費",
    "orders":         "商品購入数",
    "units":          "注文数",
}


def make_csv_bytes(df: pd.DataFrame) -> bytes:
    d = df[_out_cols(df)].rename(columns=_COL_RENAME).copy()
    d["ROAS"] = d["ROAS"].round(2)
    d = d.sort_values("ROAS", ascending=False)
    return d.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def make_zip_bytes(df_win: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for camp in OFFICIAL_CAMPAIGNS:
            df_c = df_win[df_win["campaign_theme"] == camp]
            if df_c.empty:
                continue
            kw_csv = (
                df_c.sort_values("ROAS", ascending=False)[["keyword"]]
                .rename(columns={"keyword": "Keyword"})
                .to_csv(index=False, encoding="utf-8-sig")
                .encode("utf-8-sig")
            )
            zf.writestr(f"winner_keywords_{camp}.csv", kw_csv)
            for rank_label in ("A", "B"):
                df_r = df_c[df_c["rank"] == rank_label].sort_values("ROAS", ascending=False)
                if not df_r.empty:
                    r_csv = (
                        df_r[["keyword"]]
                        .rename(columns={"keyword": "Keyword"})
                        .to_csv(index=False, encoding="utf-8-sig")
                        .encode("utf-8-sig")
                    )
                    zf.writestr(f"{camp}_{rank_label}.csv", r_csv)
    return buf.getvalue()


def make_a_only_zip_bytes(df_a: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for camp in OFFICIAL_CAMPAIGNS:
            df_c = df_a[df_a["campaign_theme"] == camp].sort_values("ROAS", ascending=False)
            if df_c.empty:
                continue
            r_csv = (
                df_c[["keyword"]]
                .rename(columns={"keyword": "Keyword"})
                .to_csv(index=False, encoding="utf-8-sig")
                .encode("utf-8-sig")
            )
            zf.writestr(f"{camp}_A.csv", r_csv)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# Streamlit UI
# ════════════════════════════════════════════════════════

st.set_page_config(
    page_title="ANIHA KWツール v1.9",
    page_icon="🐾",
    layout="wide",
)

with st.sidebar:
    # ── ページナビ ───────────────────────────────
    _page = st.radio(
        "ページ",
        ["🐾 Amazon KW発掘", "🔍 DateDive分析"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    st.header("フィルター設定")
    brand_text = st.text_area(
        "ブランド除外（改行区切り）",
        value=DEFAULT_BRAND_EXCLUDES,
        height=130,
    )
    brand_excludes = [
        normalize_text(b) for b in brand_text.strip().splitlines() if b.strip()
    ]
    st.markdown("---")
    st.markdown("**商品売価マスタ**")
    for camp, price in PRICE_MASTER.items():
        st.caption(f"{camp}：¥{price:,}")
    st.markdown("---")
    st.caption("ANIHA 勝てるKW発掘ツール v1.8")

# ── DateDive ページ切り替え ──────────────────────────────────────────
if _page == "🔍 DateDive分析":
    if _DATEDIVE_OK:
        _render_datedive()
    else:
        st.error(
            "DateDive モジュールが見つかりません。\n\n"
            "`modules/datedive/` フォルダが app.py と同じディレクトリに存在するか確認してください。"
        )
    st.stop()
# ──────────────────────────────────────────────────────────────────────

st.title("🐾 ANIHA 勝てるKW発掘ツール v1.8")
st.markdown("**売れたKWかつ利益が出ているKWのみを抽出し、A/Bランクで優先分類します。**")

with st.expander("【ANIHA広告運用手順】", expanded=True):
    st.markdown("""
**目的：属人化しないAmazon広告運用**

① Amazon広告管理画面から検索語句レポートをダウンロード
② Amazon広告管理画面からターゲットKWレポートをダウンロード
③ ANIHAツールへアップロード
④ 勝ちKW抽出を実行
⑤ Aランク一括ZIPをダウンロード
⑥ Amazon広告へ追加
""")

st.markdown("---")
c1, c2 = st.columns(2)
with c1:
    st.subheader("① 検索語句レポート")
    search_file = st.file_uploader(
        "Amazon 検索語句レポートCSV", type="csv",
        key="search", on_change=clear_results,
    )
with c2:
    st.subheader("② ターゲットKWレポート")
    target_file = st.file_uploader(
        "Amazon ターゲットKWレポートCSV", type="csv",
        key="target", on_change=clear_results,
    )

st.markdown("---")
st.subheader("【KW選定ロジック】")
st.info(
    "登録済みKW除外 → 部分一致除外 → ブランドKW除外 → ASIN除外\n\n"
    "↓\n\n"
    "売上判定（売価×2以上） → ROAS判定（2.0以上）\n\n"
    "↓\n\n"
    "**Aランク**：ROAS 3.0以上 ／ **Bランク**：ROAS 2.0以上 3.0未満"
)

ab1, ab2 = st.columns(2)
with ab1:
    st.success(
        "**🏆 Aランク**\n\n"
        "ROAS **3.0以上**\n\n"
        "最優先で広告追加するKW"
    )
with ab2:
    st.warning(
        "**📋 Bランク**\n\n"
        "ROAS **2.0以上 3.0未満**\n\n"
        "検討用KW"
    )

st.markdown("---")
run_btn = st.button("🔍 勝てるKW抽出", type="primary", use_container_width=True)


# ════════════════════════════════════════════════════════
# 分析処理
# ════════════════════════════════════════════════════════

if run_btn:
    if not search_file:
        st.error("検索語句レポートがアップロードされていません")
        st.stop()
    if not target_file:
        st.error("ターゲットKWレポートがアップロードされていません")
        st.stop()

    with st.spinner("処理中..."):

        df_search = read_csv_auto(search_file)
        df_target = read_csv_auto(target_file)

        kw_col        = find_col(df_search, ["検索用語", "カスタマーの検索用語", "Customer Search Term", "search term"])
        campaign_col  = find_col(df_search, ["キャンペーン名", "Campaign Name", "campaign name"])
        sales_col     = find_col(df_search, ["売上", "売上額", "合計売上", "広告費売上高", "7日間の総売上高", "Attributed Sales", "Sales"])
        cost_col      = find_col(df_search, ["合計費用", "費用", "広告費", "コスト", "Cost", "Spend", "spend"])
        orders_col    = find_col(df_search, ["商品購入数", "注文数", "注文された商品点数", "Orders", "Purchases"])
        units_col     = find_col(df_search, ["注文された商品点数", "注文数", "商品購入数", "Units", "Orders"])
        target_kw_col = find_col(df_target, ["ターゲティング", "キーワード", "Targeting", "Keyword", "keyword"])

        missing = []
        if not kw_col:       missing.append("検索用語")
        if not campaign_col: missing.append("キャンペーン名")
        if not sales_col:    missing.append("売上")
        if not cost_col:     missing.append("広告費/合計費用")
        if missing:
            st.error(f"検索語句レポートに必要な列が見つかりません: {missing}")
            st.write("検出列:", list(df_search.columns))
            st.stop()
        if not target_kw_col:
            st.error("ターゲットKWレポートにターゲティング列が見つかりません")
            st.write("検出列:", list(df_target.columns))
            st.stop()

        for col in [sales_col, cost_col]:
            df_search[col] = pd.to_numeric(
                df_search[col].astype(str).str.replace(",", "").str.replace("¥", ""),
                errors="coerce",
            ).fillna(0)
        if orders_col:
            df_search[orders_col] = pd.to_numeric(df_search[orders_col], errors="coerce").fillna(0)
        if units_col and units_col != orders_col:
            df_search[units_col] = pd.to_numeric(df_search[units_col], errors="coerce").fillna(0)

        df_search["kw_norm"] = df_search[kw_col].apply(normalize_text)
        df_search["campaign_theme"] = df_search[campaign_col].apply(
            lambda x: assign_official_campaign(extract_campaign_theme(str(x)))
        )

        # STEP1: オート広告のみ
        mask_auto = df_search[campaign_col].str.contains("オート|auto", case=False, na=False)
        n_auto_total = int(mask_auto.sum())
        df_auto = df_search[mask_auto].copy()

        # STEP2: 登録済みKW除外（完全一致 + 部分一致）
        registered_set: set[str] = set(df_target[target_kw_col].apply(normalize_text))
        registered_set.discard("")

        mask_exact = df_auto["kw_norm"].isin(registered_set)
        n_exact = int(mask_exact.sum())
        df_step = df_auto[~mask_exact].copy()

        mask_partial = df_step["kw_norm"].apply(lambda k: is_already_covered(k, registered_set))
        n_partial = int(mask_partial.sum())
        df_step = df_step[~mask_partial].copy()
        n_after_registered = len(df_step)

        # STEP3: ブランドKW除外
        mask_brand = df_step["kw_norm"].apply(lambda k: any(b in k for b in brand_excludes))
        n_brand = int(mask_brand.sum())
        df_step = df_step[~mask_brand].copy()
        n_after_brand = len(df_step)

        # STEP4: ASIN除外
        mask_asin = df_step["kw_norm"].apply(is_asin)
        n_asin = int(mask_asin.sum())
        df_step = df_step[~mask_asin].copy()
        n_after_asin = len(df_step)

        # STEP5: 集計
        agg_dict = {
            "keyword":        (kw_col, "first"),
            "campaign_theme": ("campaign_theme", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
            "sales":          (sales_col, "sum"),
            "cost":           (cost_col,  "sum"),
        }
        if orders_col:
            agg_dict["orders"] = (orders_col, "sum")
        if units_col and units_col != orders_col:
            agg_dict["units"] = (units_col, "sum")

        agg = (
            df_step.groupby("kw_norm")
            .agg(**agg_dict)
            .reset_index(drop=True)
        )

        agg["ROAS"] = agg.apply(
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0,
            axis=1,
        )
        agg["price"] = agg["campaign_theme"].map(PRICE_MASTER)
        agg_priced = agg[agg["price"].notna()].copy()

        # 売上条件（売価×2以上）
        mask_sales = agg_priced["sales"] >= agg_priced["price"] * 2
        n_sales_pass = int(mask_sales.sum())
        agg_sales = agg_priced[mask_sales].copy()

        # ROAS条件（2.0以上）
        mask_roas = agg_sales["ROAS"] >= 2.0
        n_roas_pass = int(mask_roas.sum())
        df_win = agg_sales[mask_roas].copy()
        df_win["rank"] = df_win["ROAS"].apply(assign_rank)

        # session_state へ保存
        st.session_state["has_results"] = True
        st.session_state["df_win"]      = df_win
        st.session_state["df_a"]        = df_win[df_win["rank"] == "A"].copy()
        st.session_state["df_b"]        = df_win[df_win["rank"] == "B"].copy()
        st.session_state["flow_stats"]  = {
            "n_auto_total":       n_auto_total,
            "n_exact":            n_exact,
            "n_partial":          n_partial,
            "n_after_registered": n_after_registered,
            "n_brand":            n_brand,
            "n_after_brand":      n_after_brand,
            "n_asin":             n_asin,
            "n_after_asin":       n_after_asin,
            "n_sales_pass":       n_sales_pass,
            "n_roas_pass":        n_roas_pass,
        }
        st.session_state["debug_info"] = {
            "cost_col":       cost_col,
            "sales_col":      sales_col,
            "orders_col":     orders_col,
            "registered_len": len(registered_set),
            "brand_excludes": brand_excludes,
        }


# ════════════════════════════════════════════════════════
# 結果表示（session_state から描画）
# ════════════════════════════════════════════════════════

if not st.session_state.get("has_results", False):
    st.stop()

st.success("✅ 分析結果は保持されています。再抽出は不要です。複数CSVを連続ダウンロードできます。")

df_win: pd.DataFrame = st.session_state["df_win"]
df_a:   pd.DataFrame = st.session_state["df_a"]
df_b:   pd.DataFrame = st.session_state["df_b"]
fs = st.session_state["flow_stats"]

n_win = len(df_win)
n_a   = len(df_a)
n_b   = len(df_b)

# ── 分析結果フロー ────────────────────────────────────
st.markdown("---")
st.subheader("分析結果")

st.markdown(f"""
| ステップ | 内容 | 件数 |
|---|---|---|
| 検索語総数 | オート広告 | **{fs['n_auto_total']:,}件** |
| 登録済みKW除外後 | 完全一致 −{fs['n_exact']:,} ／ 部分一致 −{fs['n_partial']:,} | **{fs['n_after_registered']:,}件** |
| ブランド除外後 | −{fs['n_brand']:,}件 | **{fs['n_after_brand']:,}件** |
| ASIN除外後 | −{fs['n_asin']:,}件 | **{fs['n_after_asin']:,}件** |
| 売上条件通過 | 売価×2以上 | **{fs['n_sales_pass']:,}件** |
| ROAS条件通過 | ROAS≥2.0 | **{fs['n_roas_pass']:,}件** |
| **Aランク** | ROAS≥3.0 | **{n_a:,}件** |
| **Bランク** | 2.0≤ROAS<3.0 | **{n_b:,}件** |
| **合計** | | **{n_win:,}件** |
""")

if df_win.empty:
    st.warning(
        "勝ちKWが見つかりませんでした。\n\n"
        "・検索語句レポートの期間を広げてください\n"
        "・オート広告キャンペーンが含まれているか確認してください"
    )
    st.stop()

# ── A/B サマリー ──────────────────────────────────────
a_sales = df_a["sales"].sum()
a_cost  = df_a["cost"].sum()
a_roas  = round(a_sales / a_cost, 2) if a_cost > 0 else 0.0
b_sales = df_b["sales"].sum()
b_cost  = df_b["cost"].sum()
b_roas  = round(b_sales / b_cost, 2) if b_cost > 0 else 0.0

col_a, col_b = st.columns(2)
with col_a:
    st.success(f"**🏆 Aランク：{n_a:,}件**")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("件数",     f"{n_a:,}")
    a2.metric("総売上",   f"¥{a_sales:,.0f}")
    a3.metric("総広告費", f"¥{a_cost:,.0f}")
    a4.metric("平均ROAS", f"{a_roas:.2f}")
with col_b:
    st.warning(f"**📋 Bランク：{n_b:,}件**")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("件数",     f"{n_b:,}")
    b2.metric("総売上",   f"¥{b_sales:,.0f}")
    b3.metric("総広告費", f"¥{b_cost:,.0f}")
    b4.metric("平均ROAS", f"{b_roas:.2f}")

# ── キャンペーン別件数 ────────────────────────────────
st.markdown("**キャンペーン別件数**")
camp_counts = (
    df_win["campaign_theme"]
    .value_counts()
    .reindex(OFFICIAL_CAMPAIGNS, fill_value=0)
)
active_items = [(k, v) for k, v in camp_counts.items() if v > 0]
for i in range(0, len(active_items), 6):
    chunk = active_items[i:i + 6]
    cols  = st.columns(len(chunk))
    for col, (name, cnt) in zip(cols, chunk):
        col.metric(name, f"{int(cnt):,} 件")

# ── Aランク上位10KW ───────────────────────────────────
st.markdown("---")
st.subheader("🏆 Aランク上位10KW")

if df_a.empty:
    st.info("Aランクのキーワードはありません。")
else:
    top10_base = ["campaign_theme", "keyword", "ROAS", "sales", "cost"]
    if "orders" in df_a.columns:
        top10_base.append("orders")
    top10 = (
        df_a[top10_base]
        .sort_values("ROAS", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )
    top10.index = top10.index + 1
    top10_disp = top10.rename(columns={
        "campaign_theme": "キャンペーン名",
        "keyword":        "検索語",
        "ROAS":           "ROAS",
        "sales":          "売上",
        "cost":           "広告費",
        "orders":         "注文数",
    }).copy()
    top10_disp["売上"]  = top10_disp["売上"].apply(lambda x: f"¥{x:,.0f}")
    top10_disp["広告費"] = top10_disp["広告費"].apply(lambda x: f"¥{x:,.0f}")
    top10_disp["ROAS"]  = top10_disp["ROAS"].round(2)
    st.dataframe(top10_disp, use_container_width=True)

# ── ダウンロード ──────────────────────────────────────
st.markdown("---")
st.subheader("ダウンロード")
st.caption("どれをダウンロードしても分析結果は消えません。複数CSV連続ダウンロード可能です。")

all_bytes    = make_csv_bytes(df_win)
a_bytes      = make_csv_bytes(df_a)
b_bytes      = make_csv_bytes(df_b)
zip_bytes    = make_zip_bytes(df_win)
a_only_bytes = make_a_only_zip_bytes(df_a)

st.download_button(
    "🏆 Aランク一括ZIPダウンロード (A_only.zip)",
    data=a_only_bytes,
    file_name="A_only.zip",
    mime="application/zip",
    use_container_width=True,
    type="primary",
)

st.markdown("**個別ダウンロード**")
dl1, dl2, dl3, dl4 = st.columns(4)
with dl1:
    st.download_button(
        "① 全件CSV",
        data=all_bytes,
        file_name="winner_keywords_all.csv",
        mime="text/csv",
        use_container_width=True,
    )
with dl2:
    st.download_button(
        "② Aランク CSV",
        data=a_bytes,
        file_name="winner_keywords_A.csv",
        mime="text/csv",
        use_container_width=True,
    )
with dl3:
    st.download_button(
        "③ Bランク CSV",
        data=b_bytes,
        file_name="winner_keywords_B.csv",
        mime="text/csv",
        use_container_width=True,
    )
with dl4:
    st.download_button(
        "④ キャンペーン別ZIP",
        data=zip_bytes,
        file_name="winner_keywords_zip.zip",
        mime="application/zip",
        use_container_width=True,
    )

# ── キャンペーン別テーブル ────────────────────────────
st.markdown("---")
st.subheader("キャンペーン別 勝てるKW（ROAS降順）")

disp_base = ["keyword", "rank", "ROAS", "sales", "cost"]
if "orders" in df_win.columns:
    disp_base.append("orders")

for camp in OFFICIAL_CAMPAIGNS:
    df_camp = df_win[df_win["campaign_theme"] == camp].copy()
    if df_camp.empty:
        continue
    df_camp = df_camp.sort_values("ROAS", ascending=False).reset_index(drop=True)
    cnt   = len(df_camp)
    cnt_a = int((df_camp["rank"] == "A").sum())
    cnt_b = int((df_camp["rank"] == "B").sum())
    price     = PRICE_MASTER.get(camp, 0)
    threshold = price * 2
    label = f"▼ {camp}（合計{cnt:,}件 ／ A:{cnt_a}件 B:{cnt_b}件）"
    with st.expander(label, expanded=False):
        disp = df_camp[[c for c in disp_base if c in df_camp.columns]].copy()
        disp = disp.rename(columns={
            "keyword": "検索語句", "rank": "ランク",
            "ROAS": "ROAS", "sales": "売上", "cost": "広告費",
            "orders": "商品購入数",
        })
        disp.index = disp.index + 1
        disp["売価判定"] = f"✓ ¥{threshold:,}以上"
        disp["ROAS判定"] = disp["ROAS"].apply(lambda r: f"✓ {r:.2f} ≥ 2.0")
        disp["売上"]  = disp["売上"].apply(lambda x: f"¥{x:,.0f}")
        disp["広告費"] = disp["広告費"].apply(lambda x: f"¥{x:,.0f}")
        disp["ROAS"]  = disp["ROAS"].round(2)
        st.dataframe(disp, use_container_width=True)

# ── デバッグ ──────────────────────────────────────────
di = st.session_state.get("debug_info", {})
with st.expander("デバッグ情報"):
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("完全一致除外",  f"{fs['n_exact']:,}")
    d2.metric("部分一致除外",  f"{fs['n_partial']:,}")
    d3.metric("ブランド除外",  f"{fs['n_brand']:,}")
    d4.metric("ASIN除外",     f"{fs['n_asin']:,}")
    st.write("費用列:", di.get("cost_col"))
    st.write("売上列:", di.get("sales_col"))
    st.write("注文列:", di.get("orders_col"))
    st.write("ブランド除外語:", di.get("brand_excludes"))
    st.write(f"登録済みKW数: {di.get('registered_len', 0):,}")
