"""
ANIHA 勝ちKW抽出ツール v2.1
Amazon SP広告の勝ちKWを自動抽出する Streamlit アプリ

修正履歴:
  v2.0 - 完全リライト（ラッコ/Tier/DataDive削除、ROAS勝ちKW判定、売価マスタ）
  v2.1 - 採用理由表示、サマリー強化、選定ロジック説明更新
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

# 商品売価マスタ（税込定価）
PRICE_MASTER: dict[str, int] = {
    "ふりかけ犬": 2450,
    "お口周り":   1480,
    "ふりかけ猫": 2380,
    "アイケア":   1880,
    "イヤー":     1480,
    "グルーミング": 1980,
    "シャンプー": 1880,
    "ジェル":     1980,
    "ダニ捕り":   1480,
    "乳酸菌犬":   1880,
    "乳酸菌猫":   1880,
    "涙やけ":     1480,
    "液体":       1980,
    "肉球":       1450,
    "肉球S":      1480,
    "関節":       1880,
    "除菌消臭":   1980,
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


# ════════════════════════════════════════════════════════
# Streamlit UI
# ════════════════════════════════════════════════════════

st.set_page_config(
    page_title="ANIHA 勝ちKW抽出ツール",
    page_icon="🐾",
    layout="wide",
)

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
    st.caption("ANIHA 勝ちKW抽出ツール v2.1")

st.title("🐾 ANIHA 勝ちKW抽出ツール")


# ── ① 運用フロー ─────────────────────────────────────────
with st.expander("【ANIHA Amazon広告運用フロー】", expanded=False):
    st.markdown("""
**目的：属人化しないAmazon広告運用**

---

**毎週の運用手順**

① Amazon広告から検索語句レポート取得
↓
② Amazon広告からターゲットKWレポート取得
↓
③ 本ツールへアップロード
↓
④ 分析実行
↓
⑤ ZIPダウンロード
↓
⑥ キャンペーンへ追加
↓
⑦ 完了

---

**運用ルール**

- 担当者判断でのKW追加禁止
- ツール抽出KWのみ追加

---

**本ツールについて**

本ツールは利益最大化ツールではありません。
Amazon広告へ追加すべき **勝ちKWを発掘するためのツール** です。

判断基準は以下の2点です。

- 売価2個分以上売れている
- ROAS2以上
""")


# ── ② ファイルアップロード ────────────────────────────────
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    st.subheader("① 検索語句レポート")
    search_file = st.file_uploader(
        "Amazon 検索語句レポートCSV", type="csv", key="search"
    )
with col2:
    st.subheader("② ターゲットKWレポート")
    target_file = st.file_uploader(
        "Amazon ターゲットKWレポートCSV", type="csv", key="target"
    )

st.markdown("---")


# ── ③ KW選定ロジック ─────────────────────────────────────
st.subheader("【KW選定ロジック】")
st.info(
    "本ツールは **売れたKW** かつ **利益が出ているKW** のみ抽出します。\n\n"
    "**判定条件**\n\n"
    "① 商品売価2個分以上の売上実績\n\n"
    "② ROAS2以上\n\n"
    "両方を満たしたKWのみ採用\n\n"
    "登録済KW除外  →  ブランドKW除外  →  売上判定（売価×2以上）  →  ROAS判定（2以上）  →  勝ちKW抽出"
)

run_btn = st.button("🔍 勝ちKW抽出", type="primary", use_container_width=True)


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

        # ── STEP1: 検索語句レポート読込 ──────────────────────
        df_search = read_csv_auto(search_file)

        kw_col       = find_col(df_search, ["検索用語", "Customer Search Term", "search term"])
        campaign_col = find_col(df_search, ["キャンペーン名", "Campaign Name", "campaign name"])
        sales_col    = find_col(df_search, ["売上", "広告費売上高", "7日間の総売上高", "Sales", "sales"])
        cost_col     = find_col(df_search, ["費用", "コスト", "Spend", "Cost", "spend"])

        missing = []
        if not kw_col:       missing.append("検索用語")
        if not campaign_col: missing.append("キャンペーン名")
        if not sales_col:    missing.append("売上")
        if not cost_col:     missing.append("費用/コスト")
        if missing:
            st.error(f"検索語句レポートに必要な列が見つかりません: {missing}")
            st.write("検出された列:", list(df_search.columns))
            st.stop()

        # ── STEP2: ターゲットKW読込 ──────────────────────────
        df_target = read_csv_auto(target_file)
        target_kw_col = find_col(
            df_target,
            ["ターゲティング", "キーワード", "Targeting", "Keyword", "keyword"],
        )
        if not target_kw_col:
            st.error("ターゲットKWレポートにターゲティング/キーワード列が見つかりません")
            st.write("検出された列:", list(df_target.columns))
            st.stop()

        total_before = len(df_search)

        # 数値変換
        df_search[sales_col] = pd.to_numeric(df_search[sales_col], errors="coerce").fillna(0)
        df_search[cost_col]  = pd.to_numeric(df_search[cost_col],  errors="coerce").fillna(0)
        df_search["kw_norm"] = df_search[kw_col].apply(normalize_text)
        df_search["campaign_theme"] = df_search[campaign_col].apply(
            lambda x: assign_official_campaign(extract_campaign_theme(str(x)))
        )

        # ── STEP3: 登録済KW除外 ──────────────────────────────
        registered_kws = set(df_target[target_kw_col].apply(normalize_text))
        registered_kws.discard("")
        mask_registered = df_search["kw_norm"].isin(registered_kws)
        n_registered = int(mask_registered.sum())
        df_search = df_search[~mask_registered].copy()

        # ── STEP4: ブランドKW除外 ────────────────────────────
        mask_brand = df_search["kw_norm"].apply(
            lambda k: any(b in k for b in brand_excludes)
        )
        n_brand = int(mask_brand.sum())
        df_search = df_search[~mask_brand].copy()

        # ASIN除外（サイレント）
        mask_asin = df_search["kw_norm"].apply(is_asin)
        df_search = df_search[~mask_asin].copy()

        # ── 集計（KW単位で売上・費用を合算）────────────────────
        agg = (
            df_search.groupby("kw_norm")
            .agg(
                keyword=(kw_col, "first"),
                campaign_theme=(
                    "campaign_theme",
                    lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類",
                ),
                sales=(sales_col, "sum"),
                cost=(cost_col, "sum"),
            )
            .reset_index(drop=True)
        )

        n_after_exclusion = len(agg)

        # ── STEP5: ROAS計算 ──────────────────────────────────
        agg["ROAS"] = agg.apply(
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0,
            axis=1,
        )

        # ── STEP6: 勝ちKW判定 ────────────────────────────────
        agg["price"] = agg["campaign_theme"].map(PRICE_MASTER)
        agg_with_price = agg[agg["price"].notna()].copy()

        mask_win = (
            (agg_with_price["sales"] >= agg_with_price["price"] * 2)
            & (agg_with_price["ROAS"] >= 2.0)
        )
        df_win = agg_with_price[mask_win].copy()
        n_win = len(df_win)

    # ════════════════════════════════════════════════════════
    # ④ 結果表示
    # ════════════════════════════════════════════════════════

    st.markdown("---")

    # 抽出フロー
    st.subheader("今回の抽出フロー")
    fc1, fc2, fc3, fc4, fc5, fc6, fc7 = st.columns([2, 1, 2, 1, 2, 1, 2])
    fc1.metric("検索語句", f"{total_before:,} 件")
    fc2.markdown(
        "<div style='text-align:center;font-size:24px;padding-top:12px'>→</div>",
        unsafe_allow_html=True,
    )
    fc3.metric("登録済KW除外", f"−{n_registered:,} 件")
    fc4.markdown(
        "<div style='text-align:center;font-size:24px;padding-top:12px'>→</div>",
        unsafe_allow_html=True,
    )
    fc5.metric("ブランドKW除外", f"−{n_brand:,} 件")
    fc6.markdown(
        "<div style='text-align:center;font-size:24px;padding-top:12px'>→</div>",
        unsafe_allow_html=True,
    )
    fc7.metric("勝ちKW（売価×2＆ROAS≥2）", f"{n_win:,} 件")

    if df_win.empty:
        st.warning(
            "勝ちKWが見つかりませんでした。\n\n"
            "・検索語句レポートの期間を広げてみてください\n"
            "・売上・費用の列名を確認してください"
        )
        with st.expander("デバッグ情報"):
            st.write(f"登録済みKW数: {len(registered_kws):,}")
            st.write(f"集計後KW数: {n_after_exclusion:,}")
            st.write(f"売価マッピング済み: {len(agg_with_price):,}")
        st.stop()

    # ── サマリー ────────────────────────────────────────────
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
    cols_per_row = 6
    items = list(active.items())
    for i in range(0, len(items), cols_per_row):
        chunk = items[i : i + cols_per_row]
        cols = st.columns(len(chunk))
        for col, (name, cnt) in zip(cols, chunk):
            col.metric(name, f"{int(cnt):,} 件")

    # ── ダウンロード ────────────────────────────────────────
    st.markdown("---")
    csv_out = df_win[["campaign_theme", "keyword", "sales", "cost", "ROAS"]].copy()
    csv_out.columns = ["Campaign", "Keyword", "Sales", "Cost", "ROAS"]
    csv_out = csv_out.sort_values(["Campaign", "Sales"], ascending=[True, False])
    csv_out["ROAS"] = csv_out["ROAS"].round(2)
    csv_bytes = csv_out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for camp in OFFICIAL_CAMPAIGNS:
            df_c = df_win[df_win["campaign_theme"] == camp]
            if df_c.empty:
                continue
            kw_csv = (
                df_c[["keyword"]]
                .rename(columns={"keyword": "Keyword"})
                .sort_values("Keyword")
                .to_csv(index=False, encoding="utf-8-sig")
                .encode("utf-8-sig")
            )
            zf.writestr(f"{camp}.csv", kw_csv)
    zip_bytes = zip_buf.getvalue()

    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            label="① campaign_kw_result.csv（全件）",
            data=csv_bytes,
            file_name="campaign_kw_result.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with dl2:
        st.download_button(
            label="② campaign_kw_zip.zip（Campaign別CSV）",
            data=zip_bytes,
            file_name="campaign_kw_zip.zip",
            mime="application/zip",
            use_container_width=True,
        )

    # ── キャンペーン別テーブル ──────────────────────────────
    st.markdown("---")
    st.subheader("キャンペーン別 勝ちKW")

    for camp in OFFICIAL_CAMPAIGNS:
        df_camp = df_win[df_win["campaign_theme"] == camp].copy()
        if df_camp.empty:
            continue
        df_camp = df_camp.sort_values("sales", ascending=False).reset_index(drop=True)
        cnt = len(df_camp)
        price = PRICE_MASTER.get(camp, 0)
        threshold = price * 2
        with st.expander(f"▼ {camp}（{cnt:,}件）", expanded=False):
            disp = df_camp[["keyword", "sales", "cost", "ROAS"]].copy()
            disp.columns = ["Keyword", "Sales", "Cost", "ROAS"]
            disp["売価判定"] = f"✓ 売価×2（¥{threshold:,}以上）"
            disp["ROAS判定"] = disp["ROAS"].apply(
                lambda r: f"✓ ROAS {r:.2f} ≥ 2.0"
            )
            disp["採否"] = "✅ 採用"
            disp["Sales"] = disp["Sales"].apply(lambda x: f"¥{x:,.0f}")
            disp["Cost"]  = disp["Cost"].apply(lambda x: f"¥{x:,.0f}")
            disp["ROAS"]  = disp["ROAS"].round(2)
            st.dataframe(disp, use_container_width=True, hide_index=True)

    # ── デバッグ ────────────────────────────────────────────
    with st.expander("デバッグ情報"):
        d1, d2, d3 = st.columns(3)
        d1.metric("登録済KW除外", f"{n_registered:,} 件")
        d2.metric("ブランドKW除外", f"{n_brand:,} 件")
        d3.metric("集計後KW（除外後）", f"{n_after_exclusion:,} 件")
        st.write(f"ブランド除外語: {brand_excludes}")
        st.write(f"登録済みKW数: {len(registered_kws):,}")
