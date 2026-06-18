"""
ANIHA 勝てるKW発掘ツール v1.5
売れたKWかつ利益が出ているKWのみを抽出します。
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from typing import Optional

import pandas as pd
import streamlit as st


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
    """列名候補を優先順で検索。大文字小文字無視のフォールバックあり。"""
    for c in candidates:
        if c in df.columns:
            return c
    cols_lower = {col.lower(): col for col in df.columns}
    for c in candidates:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


def is_already_covered(kw_norm: str, registered_set: set[str]) -> bool:
    """
    登録済みKW（2語以上）が候補KWに部分一致で含まれていれば True を返す。
    例: 登録済み「犬 涙やけ」→ 候補「犬 涙やけ サプリ」は除外
    """
    for reg in registered_set:
        if len(reg.split()) < 2:
            continue
        if reg in kw_norm:
            return True
    return False


# ════════════════════════════════════════════════════════
# Streamlit UI
# ════════════════════════════════════════════════════════

st.set_page_config(
    page_title="ANIHA 勝てるKW発掘ツール v1.5",
    page_icon="🐾",
    layout="wide",
)

# ── サイドバー ────────────────────────────────────────────
with st.sidebar:
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
    st.caption("ANIHA 勝てるKW発掘ツール v1.5")

# ── タイトル ──────────────────────────────────────────────
st.title("🐾 ANIHA 勝てるKW発掘ツール v1.5")
st.markdown("**売れたKWかつ利益が出ているKWのみを抽出します。**")

# ── ① ツール運用手順 ─────────────────────────────────────
with st.expander("【ツール運用手順】", expanded=True):
    st.markdown("""
**目的：属人化しないAmazon広告運用**

① Amazon検索語句レポートCSVをダウンロード  
② AmazonターゲットKWレポートCSVをダウンロード  
③ ツールへ投入  
④ 勝てるKW抽出を実行  
⑤ キャンペーン別CSVをダウンロード  
⑥ Amazon広告へ追加  
""")

# ── ② ファイルアップロード ────────────────────────────────
st.markdown("---")
c1, c2 = st.columns(2)
with c1:
    st.subheader("① 検索語句レポート")
    search_file = st.file_uploader(
        "Amazon 検索語句レポートCSV", type="csv", key="search"
    )
with c2:
    st.subheader("② ターゲットKWレポート")
    target_file = st.file_uploader(
        "Amazon ターゲットKWレポートCSV", type="csv", key="target"
    )

# ── ③ KW選定ロジック ─────────────────────────────────────
st.markdown("---")
st.subheader("【KW選定ロジック】")
st.info(
    "本ツールは **売れたKW** かつ **利益が出ているKW** のみ抽出します。\n\n"
    "**判定条件**\n\n"
    "① 商品売価2個分以上の売上\n\n"
    "② ROAS 2以上\n\n"
    "両方を満たしたKWのみ採用\n\n"
    "登録済KW除外 → 部分一致除外 → ブランドKW除外 → ASIN除外 → "
    "売上判定（売価×2以上） → ROAS判定（2以上） → 勝ちKW抽出"
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

        # ── ファイル読込 ──────────────────────────────────
        df_search = read_csv_auto(search_file)
        df_target = read_csv_auto(target_file)

        # ── 列名自動判定（検索語句レポート）────────────────
        kw_col = find_col(df_search, [
            "検索用語", "カスタマーの検索用語",
            "Customer Search Term", "search term",
        ])
        campaign_col = find_col(df_search, [
            "キャンペーン名", "Campaign Name", "campaign name",
        ])
        sales_col = find_col(df_search, [
            "売上", "売上額", "合計売上", "広告費売上高",
            "7日間の総売上高", "Attributed Sales", "Sales",
        ])
        cost_col = find_col(df_search, [
            "合計費用", "費用", "広告費", "コスト",
            "Cost", "Spend", "spend",
        ])
        orders_col = find_col(df_search, [
            "商品購入数", "注文数", "注文された商品点数",
            "Orders", "Purchases",
        ])
        units_col = find_col(df_search, [
            "注文された商品点数", "注文数", "商品購入数",
            "Units", "Orders",
        ])

        # ── 列名自動判定（ターゲットKWレポート）────────────
        target_kw_col = find_col(df_target, [
            "ターゲティング", "キーワード",
            "Targeting", "Keyword", "keyword",
        ])

        # ── 必須列チェック ────────────────────────────────
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

        total_before = len(df_search)

        # ── 数値変換 ──────────────────────────────────────
        for col in [sales_col, cost_col]:
            df_search[col] = pd.to_numeric(
                df_search[col].astype(str).str.replace(",", "").str.replace("¥", ""),
                errors="coerce",
            ).fillna(0)
        if orders_col:
            df_search[orders_col] = pd.to_numeric(
                df_search[orders_col], errors="coerce"
            ).fillna(0)
        if units_col and units_col != orders_col:
            df_search[units_col] = pd.to_numeric(
                df_search[units_col], errors="coerce"
            ).fillna(0)

        # ── キャンペーンテーマを付与 ──────────────────────
        df_search["kw_norm"] = df_search[kw_col].apply(normalize_text)
        df_search["campaign_theme"] = df_search[campaign_col].apply(
            lambda x: assign_official_campaign(extract_campaign_theme(str(x)))
        )

        # ── オート広告のみに絞り込み ──────────────────────
        mask_auto = df_search[campaign_col].str.contains(
            "オート|auto", case=False, na=False
        )
        n_non_auto = int((~mask_auto).sum())
        df_auto = df_search[mask_auto].copy()
        n_auto_rows = len(df_auto)

        # ── 登録済みKW（完全一致 + 部分一致除外用）─────────
        registered_set: set[str] = set(
            df_target[target_kw_col].apply(normalize_text)
        )
        registered_set.discard("")

        # ── STEP3: 登録済KW除外（完全一致）──────────────────
        mask_exact = df_auto["kw_norm"].isin(registered_set)
        n_exact = int(mask_exact.sum())
        df_step = df_auto[~mask_exact].copy()

        # ── 部分一致除外 ──────────────────────────────────
        mask_partial = df_step["kw_norm"].apply(
            lambda k: is_already_covered(k, registered_set)
        )
        n_partial = int(mask_partial.sum())
        df_step = df_step[~mask_partial].copy()

        # ── STEP4: ブランドKW除外 ────────────────────────
        mask_brand = df_step["kw_norm"].apply(
            lambda k: any(b in k for b in brand_excludes)
        )
        n_brand = int(mask_brand.sum())
        df_step = df_step[~mask_brand].copy()

        # ── STEP5: ASIN除外 ──────────────────────────────
        mask_asin = df_step["kw_norm"].apply(is_asin)
        n_asin = int(mask_asin.sum())
        df_step = df_step[~mask_asin].copy()

        # ── 集計（KW単位） ────────────────────────────────
        agg_dict = {
            "keyword":        (kw_col, "first"),
            "campaign_theme": ("campaign_theme",
                               lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
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

        n_after_exclusion = len(agg)

        # ── ROAS計算 ──────────────────────────────────────
        agg["ROAS"] = agg.apply(
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0,
            axis=1,
        )

        # ── 勝ちKW判定 ────────────────────────────────────
        agg["price"] = agg["campaign_theme"].map(PRICE_MASTER)
        agg_priced = agg[agg["price"].notna()].copy()

        mask_win = (
            (agg_priced["sales"] >= agg_priced["price"] * 2)
            & (agg_priced["ROAS"] >= 2.0)
        )
        df_win = agg_priced[mask_win].copy()
        n_win = len(df_win)

    # ════════════════════════════════════════════════════════
    # 結果表示
    # ════════════════════════════════════════════════════════

    st.markdown("---")

    # 抽出フロー
    st.subheader("今回の抽出フロー")
    st.markdown(f"""
| ステップ | 内容 | 件数 |
|---|---|---|
| 検索語句レポート | 読込 | **{total_before:,}件** |
| オート広告絞込 | マニュアル除外 | −{n_non_auto:,}件 |
| 登録済KW除外 | 完全一致 | −{n_exact:,}件 |
| 部分一致除外 | 部分包含 | −{n_partial:,}件 |
| ブランドKW除外 | | −{n_brand:,}件 |
| ASIN除外 | | −{n_asin:,}件 |
| **勝ちKW** | 売価×2 & ROAS≥2 | **{n_win:,}件** |
""")

    if df_win.empty:
        st.warning(
            "勝ちKWが見つかりませんでした。\n\n"
            "・検索語句レポートの期間を広げてください\n"
            "・オート広告キャンペーンが含まれているか確認してください"
        )
        with st.expander("デバッグ情報"):
            st.write(f"登録済みKW数: {len(registered_set):,}")
            st.write(f"集計後KW数: {n_after_exclusion:,}")
            st.write(f"売価マッピング済み: {len(agg_priced):,}")
        st.stop()

    # ── サマリー ─────────────────────────────────────────
    st.markdown("---")
    total_sales = df_win["sales"].sum()
    total_cost  = df_win["cost"].sum()
    avg_roas    = round(total_sales / total_cost, 2) if total_cost > 0 else 0.0

    st.subheader("分析結果")
    sm1, sm2, sm3, sm4 = st.columns(4)
    sm1.metric("勝ちKW", f"{n_win:,} 件")
    sm2.metric("総売上", f"¥{total_sales:,.0f}")
    sm3.metric("総広告費", f"¥{total_cost:,.0f}")
    sm4.metric("平均ROAS", f"{avg_roas:.2f}")

    # キャンペーン別件数
    camp_counts = (
        df_win["campaign_theme"]
        .value_counts()
        .reindex(OFFICIAL_CAMPAIGNS, fill_value=0)
    )
    active = camp_counts[camp_counts > 0]

    st.markdown("**キャンペーン別件数**")
    items = list(active.items())
    for i in range(0, len(items), 6):
        chunk = items[i:i + 6]
        cols = st.columns(len(chunk))
        for col, (name, cnt) in zip(cols, chunk):
            col.metric(name, f"{int(cnt):,} 件")

    # ── ダウンロード ──────────────────────────────────────
    st.markdown("---")

    # 出力列を整形
    out_cols_map = {
        "campaign_theme": "キャンペーン名",
        "keyword":        "検索語句",
        "sales":          "売上",
        "cost":           "広告費",
        "ROAS":           "ROAS",
    }
    if "orders" in df_win.columns:
        out_cols_map["orders"] = "商品購入数"
    if "units" in df_win.columns:
        out_cols_map["units"] = "注文された商品点数"

    csv_out = df_win[list(out_cols_map.keys())].rename(columns=out_cols_map).copy()
    csv_out = csv_out.sort_values(["キャンペーン名", "売上"], ascending=[True, False])
    csv_out["ROAS"] = csv_out["ROAS"].round(2)

    # ① 全体CSV
    all_csv_bytes = csv_out.to_csv(
        index=False, encoding="utf-8-sig"
    ).encode("utf-8-sig")

    # ② キャンペーン別ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for camp in OFFICIAL_CAMPAIGNS:
            df_c = df_win[df_win["campaign_theme"] == camp]
            if df_c.empty:
                continue
            camp_csv = (
                df_c[["keyword"]]
                .rename(columns={"keyword": "Keyword"})
                .sort_values("Keyword")
                .to_csv(index=False, encoding="utf-8-sig")
                .encode("utf-8-sig")
            )
            fname = f"winner_keywords_{camp}.csv"
            zf.writestr(fname, camp_csv)
    zip_bytes = zip_buf.getvalue()

    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            label="① winner_keywords_all.csv（全件）",
            data=all_csv_bytes,
            file_name="winner_keywords_all.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with dl2:
        st.download_button(
            label="② winner_keywords_zip.zip（キャンペーン別）",
            data=zip_bytes,
            file_name="winner_keywords_zip.zip",
            mime="application/zip",
            use_container_width=True,
        )

    # ── キャンペーン別テーブル ──────────────────────────
    st.markdown("---")
    st.subheader("キャンペーン別 勝てるKW")

    disp_base = ["keyword", "sales", "cost", "ROAS"]
    if "orders" in df_win.columns:
        disp_base.append("orders")
    if "units" in df_win.columns:
        disp_base.append("units")

    for camp in OFFICIAL_CAMPAIGNS:
        df_camp = df_win[df_win["campaign_theme"] == camp].copy()
        if df_camp.empty:
            continue
        df_camp = df_camp.sort_values("sales", ascending=False).reset_index(drop=True)
        cnt = len(df_camp)
        price = PRICE_MASTER.get(camp, 0)
        threshold = price * 2
        with st.expander(f"▼ {camp}（{cnt:,}件）", expanded=False):
            disp = df_camp[[c for c in disp_base if c in df_camp.columns]].copy()
            col_rename = {
                "keyword": "検索語句", "sales": "売上",
                "cost": "広告費", "ROAS": "ROAS",
                "orders": "商品購入数", "units": "注文数",
            }
            disp = disp.rename(columns=col_rename)
            disp["売価判定"] = f"✓ ¥{threshold:,}以上"
            disp["ROAS判定"] = disp["ROAS"].apply(
                lambda r: f"✓ {r:.2f} ≥ 2.0"
            )
            disp["採否"] = "✅ 採用"
            disp["売上"] = disp["売上"].apply(lambda x: f"¥{x:,.0f}")
            disp["広告費"] = disp["広告費"].apply(lambda x: f"¥{x:,.0f}")
            disp["ROAS"] = disp["ROAS"].round(2)
            st.dataframe(disp, use_container_width=True, hide_index=True)

    # ── デバッグ ─────────────────────────────────────────
    with st.expander("デバッグ情報"):
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("登録済KW除外", f"{n_exact:,}")
        d2.metric("部分一致除外", f"{n_partial:,}")
        d3.metric("ブランド除外", f"{n_brand:,}")
        d4.metric("ASIN除外", f"{n_asin:,}")
        d5.metric("集計後KW数", f"{n_after_exclusion:,}")
        st.write("費用列:", cost_col)
        st.write("売上列:", sales_col)
        st.write("注文列:", orders_col)
        st.write(f"ブランド除外語: {brand_excludes}")
        st.write(f"登録済みKW数: {len(registered_set):,}")
