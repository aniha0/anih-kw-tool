"""
ANIHA 勝てるKW発掘ツール v2.1
- 同一意図KW統合（2段階 + deduplicate_keyword_intent / rank_keyword_cluster）
- Aランク: ROAS >= 5.0 / Bランク: ROAS >= 2.0
- 採用条件: 注文数>=3 AND ROAS>=2.0 AND 売上>=売価x2
- 必須関数: grouping_key / canonical_keyword / same_intent_keyword
              deduplicate_keyword_intent / rank_keyword_cluster
- session_state 結果保持 / A_only.zip / キャンペーン別出力
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

DEFAULT_BRAND_EXCLUDES = "アニハ\nANIHA\nゾイック\nノルバサン\nマラセブ"

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
# 同一意図KW統合ユーティリティ
# ════════════════════════════════════════════════════════

def kata_to_hira(text: str) -> str:
    """カタカナをひらがなに変換（カナ表記ゆれ吸収）。"""
    result = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            result.append(chr(code - 0x60))
        else:
            result.append(ch)
    return "".join(result)


def canonical_keyword(kw: str) -> str:
    """
    KWを比較用の正規化形式に変換。
    全角半角・大小文字・ひらがなカタカナ・記号・助詞の差異を吸収する。
    """
    text = unicodedata.normalize("NFKC", str(kw))
    text = text.lower()
    text = kata_to_hira(text)
    # 記号・中黒・スラッシュ・ハイフン → スペース
    text = re.sub(r"[-・/／\\|｜〜～·]", " ", text)
    # 助詞・接続詞 → スペース（前後の空白も吸収）
    text = re.sub(r"\s*(の|用|向け|専用|対応|ための?|への?|にも?|での?)\s*", " ", text)
    # 複数スペース → 1スペース
    text = re.sub(r"\s+", " ", text).strip()
    return text


def grouping_key_sorted(canonical: str) -> str:
    """語順無関係キー: トークンをソートして結合。「涙やけ 犬」↔「犬 涙やけ」を同一グループに。"""
    tokens = sorted(t for t in canonical.split() if t)
    return "".join(tokens)


def grouping_key_compact(canonical: str) -> str:
    """連結形キー: スペースを除去。「犬シャンプー」↔「犬 シャンプー」を同一グループに。"""
    return re.sub(r"\s+", "", canonical)


def grouping_key(kw_norm: str) -> str:
    """
    グルーピングキー（メイン）: canonical化 → 語順ソート結合。
    全角半角・カナひら・用/向け/記号・語順 を吸収する。
    """
    return grouping_key_sorted(canonical_keyword(kw_norm))


def same_intent_keyword(kw1: str, kw2: str) -> bool:
    """
    2つのKWが同一検索意図かどうか判定。
    sorted_key OR compact_key が一致すれば同一意図とみなす。
    """
    can1 = canonical_keyword(kw1)
    can2 = canonical_keyword(kw2)
    return (
        grouping_key_sorted(can1) == grouping_key_sorted(can2)
        or grouping_key_compact(can1) == grouping_key_compact(can2)
    )


def rank_keyword_cluster(cluster_df: pd.DataFrame) -> pd.Series:
    """
    同一意図KWクラスタから代表KW（1行）を選定する。
    優先順位: ① インプレッション（検索ボリューム代替）
              ② 売上  ③ 注文数  ④ ROAS
    """
    sort_cols: list[str] = []
    for col in ["impressions", "orders", "sales", "ROAS"]:
        if col in cluster_df.columns:
            sort_cols.append(col)
    if not sort_cols:
        sort_cols = ["ROAS"]
    ranked = cluster_df.sort_values(sort_cols, ascending=False)
    return ranked.iloc[0]


def deduplicate_keyword_intent(df: pd.DataFrame) -> pd.DataFrame:
    """
    同一意図KWを統合し、代表KW1件のみに絞り込む。
    2段階グループ化:
      第1段階: grouping_key_sorted  → 語順バリエーション統合
      第2段階: grouping_key_compact → 連結/スペース表記統合
    各クラスタから rank_keyword_cluster() で代表KWを選定する。
    """
    if df.empty:
        return df

    df = df.copy()
    df["_gkey_sort"]    = df["keyword"].apply(lambda k: grouping_key_sorted(canonical_keyword(k)))
    df["_gkey_compact"] = df["keyword"].apply(lambda k: grouping_key_compact(canonical_keyword(k)))

    # 第1段階: 語順正規化グループで代表選定
    representatives: list[pd.Series] = []
    for _, cluster in df.groupby("_gkey_sort", sort=False):
        representatives.append(rank_keyword_cluster(cluster))
    df_r1 = pd.DataFrame(representatives).reset_index(drop=True)

    # 第2段階: 連結形グループで重複を再統合
    df_r1["_gkey_compact2"] = df_r1["keyword"].apply(
        lambda k: grouping_key_compact(canonical_keyword(k))
    )
    representatives2: list[pd.Series] = []
    for _, cluster in df_r1.groupby("_gkey_compact2", sort=False):
        representatives2.append(rank_keyword_cluster(cluster))
    df_final = pd.DataFrame(representatives2).reset_index(drop=True)

    # 作業列除去
    df_final = df_final.drop(
        columns=[c for c in ["_gkey_sort", "_gkey_compact", "_gkey_compact2"] if c in df_final.columns],
        errors="ignore",
    )
    return df_final


# ════════════════════════════════════════════════════════
# その他ユーティリティ
# ════════════════════════════════════════════════════════

def normalize_text(text) -> str:
    if text is None or (isinstance(text, float) and text != text):
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", text.strip().lower())


def extract_campaign_theme(campaign_name: str) -> str:
    m = CAMPAIGN_THEME_RE.search(str(campaign_name))
    return m.group(1) if m else ""


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
    """登録済みKWの部分一致除外（2語以上のKWが候補に含まれていれば除外）。"""
    for reg in registered_set:
        if len(reg.split()) < 2:
            continue
        if reg in kw_norm:
            return True
    return False


def is_code_or_asin(kw: str) -> bool:
    """ASIN・JAN・型番・SKU・商品コードを判定。"""
    stripped = re.sub(r"[\s\-]", "", kw)
    if ASIN_RE.match(kw.strip()):
        return True
    if re.match(r"^\d{8,}$", stripped):
        return True
    if re.match(r"^[a-zA-Z0-9]{8,}$", stripped):
        return True
    return False


def is_product_title(kw: str) -> bool:
    """商品タイトル的な長文検索語を判定。"""
    if re.search(r"[【】（）()]", kw):
        return True
    if len(kw) >= 20:
        return True
    if kw.count(" ") >= 3:
        return True
    return False


def assign_rank(roas: float) -> str:
    """Aランク: ROAS >= 5.0 / Bランク: ROAS >= 2.0"""
    return "A" if roas >= 5.0 else "B"


def clear_results() -> None:
    for key in ["has_results", "df_win", "df_a", "df_b", "flow_stats", "debug_info"]:
        st.session_state.pop(key, None)


# ════════════════════════════════════════════════════════
# CSV / ZIP 生成
# ════════════════════════════════════════════════════════

_COL_RENAME_STD = {
    "campaign_theme": "キャンペーン名",
    "keyword":        "追加KW",
    "rank":           "ランク",
    "ROAS":           "ROAS",
    "sales":          "売上",
    "cost":           "広告費",
    "orders":         "商品購入数",
    "units":          "注文数",
}

_COL_RENAME_DETAIL = {
    "campaign_theme": "キャンペーン名",
    "keyword":        "追加KW",
    "ROAS":           "ROAS",
    "sales":          "売上",
    "cost":           "広告費",
    "orders":         "商品購入数",
    "CVR":            "CVR",
    "clicks":         "クリック数",
    "impressions":    "インプレッション",
}


def _out_cols(df: pd.DataFrame) -> list[str]:
    base = ["campaign_theme", "keyword", "rank", "ROAS", "sales", "cost"]
    for col in ["orders", "units"]:
        if col in df.columns:
            base.append(col)
    return [c for c in base if c in df.columns]


def make_csv_bytes(df: pd.DataFrame) -> bytes:
    """全件・Bランク用CSV（ROAS降順）。"""
    d = df[_out_cols(df)].rename(columns=_COL_RENAME_STD).copy()
    d["ROAS"] = d["ROAS"].round(2)
    d = d.sort_values("ROAS", ascending=False)
    return d.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def make_a_full_csv_bytes(df: pd.DataFrame) -> bytes:
    """Aランク用詳細CSV（CVR・クリック数・インプレッション含む）ROAS降順。"""
    base = ["campaign_theme", "keyword", "ROAS", "sales", "cost"]
    for col in ["orders", "CVR", "clicks", "impressions"]:
        if col in df.columns:
            base.append(col)
    d = df[[c for c in base if c in df.columns]].copy()
    d["ROAS"] = d["ROAS"].round(2)
    d = d.sort_values("ROAS", ascending=False)
    d = d.rename(columns=_COL_RENAME_DETAIL)
    if "CVR" in d.columns:
        d["CVR"] = d["CVR"].apply(lambda x: f"{x:.1f}%")
    return d.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def make_zip_bytes(df_win: pd.DataFrame) -> bytes:
    """全キャンペーン A/B 混在 ZIP（ROAS降順）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for camp in OFFICIAL_CAMPAIGNS:
            df_c = df_win[df_win["campaign_theme"] == camp]
            if df_c.empty:
                continue
            kw_csv = (
                df_c.sort_values("ROAS", ascending=False)[["keyword"]]
                .rename(columns={"keyword": "追加KW"})
                .to_csv(index=False, encoding="utf-8-sig")
                .encode("utf-8-sig")
            )
            zf.writestr(f"winner_keywords_{camp}.csv", kw_csv)
            for rank_label in ("A", "B"):
                df_r = df_c[df_c["rank"] == rank_label].sort_values("ROAS", ascending=False)
                if not df_r.empty:
                    r_csv = (
                        df_r[["keyword"]]
                        .rename(columns={"keyword": "追加KW"})
                        .to_csv(index=False, encoding="utf-8-sig")
                        .encode("utf-8-sig")
                    )
                    zf.writestr(f"{camp}_{rank_label}.csv", r_csv)
    return buf.getvalue()


def make_a_only_zip_bytes(df_a: pd.DataFrame) -> bytes:
    """Aランクのみ キャンペーン別 ZIP（詳細列付き）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for camp in OFFICIAL_CAMPAIGNS:
            df_c = df_a[df_a["campaign_theme"] == camp]
            if df_c.empty:
                continue
            zf.writestr(f"{camp}_A.csv", make_a_full_csv_bytes(df_c))
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# Streamlit UI
# ════════════════════════════════════════════════════════

st.set_page_config(
    page_title="ANIHA 勝てるKW発掘ツール v2.1",
    page_icon="🐾",
    layout="wide",
)

# ── サイドバー ────────────────────────────────────────
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
    min_orders = st.number_input(
        "最小注文数（必須）",
        min_value=1, max_value=20, value=3, step=1,
        help="この注文数未満のKWは除外されます。デフォルト3件。",
    )
    st.markdown("---")
    st.markdown("**商品売価マスタ**")
    for camp, price in PRICE_MASTER.items():
        st.caption(f"{camp}：¥{price:,}")
    st.markdown("---")
    st.caption("ANIHA 勝てるKW発掘ツール v2.1")

# ── タイトル ─────────────────────────────────────────
st.title("🐾 ANIHA 勝てるKW発掘ツール v2.1")
st.markdown("**目標：50〜100KW程度に絞り込み、実際に追加するKWのみ抽出します。**")

# ── 運用手順 ─────────────────────────────────────────
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

# ── ファイルアップロード ──────────────────────────────
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

# ── KW選定ロジック ────────────────────────────────────
st.markdown("---")
st.subheader("【KW選定ロジック】")
st.info(
    "登録済みKW除外（完全一致・部分一致） → ブランドKW除外 → コード・商品タイトル除外\n\n"
    "↓\n\n"
    "売上判定（売価×2以上） → ROAS判定（2.0以上） → **注文数判定（3件以上）**\n\n"
    "↓\n\n"
    "**同一意図KW統合**（全角半角・カナひら・用/向け/語順 → 代表KW1件に集約）\n\n"
    "↓\n\n"
    "**Aランク**：ROAS 5.0以上 ／ **Bランク**：ROAS 2.0以上 5.0未満"
)

ab1, ab2 = st.columns(2)
with ab1:
    st.success(
        "**🏆 Aランク**\n\n"
        "ROAS **5.0以上**\n\n"
        "最優先で広告追加するKW"
    )
with ab2:
    st.warning(
        "**📋 Bランク**\n\n"
        "ROAS **2.0以上 5.0未満**\n\n"
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

        # ── 列名自動検出 ──────────────────────────────────
        kw_col          = find_col(df_search, ["検索用語", "カスタマーの検索用語", "Customer Search Term", "search term"])
        campaign_col    = find_col(df_search, ["キャンペーン名", "Campaign Name", "campaign name"])
        sales_col       = find_col(df_search, ["売上", "売上額", "合計売上", "広告費売上高", "7日間の総売上高", "Attributed Sales", "Sales"])
        cost_col        = find_col(df_search, ["合計費用", "費用", "広告費", "コスト", "Cost", "Spend", "spend"])
        orders_col      = find_col(df_search, ["商品購入数", "注文数", "注文された商品点数", "Orders", "Purchases"])
        units_col       = find_col(df_search, ["注文された商品点数", "注文数", "商品購入数", "Units", "Orders"])
        clicks_col      = find_col(df_search, ["クリック数", "クリック", "Clicks", "clicks"])
        impressions_col = find_col(df_search, ["インプレッション数", "インプレッション", "Impressions", "impressions"])
        target_kw_col   = find_col(df_target, ["ターゲティング", "キーワード", "Targeting", "Keyword", "keyword"])

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

        # ── 数値変換 ──────────────────────────────────────
        for col in [sales_col, cost_col]:
            df_search[col] = pd.to_numeric(
                df_search[col].astype(str).str.replace(",", "").str.replace("¥", ""),
                errors="coerce",
            ).fillna(0)
        for col in [orders_col, units_col, clicks_col, impressions_col]:
            if col:
                df_search[col] = pd.to_numeric(
                    df_search[col].astype(str).str.replace(",", ""),
                    errors="coerce",
                ).fillna(0)

        df_search["kw_norm"] = df_search[kw_col].apply(normalize_text)
        df_search["campaign_theme"] = df_search[campaign_col].apply(
            lambda x: assign_official_campaign(extract_campaign_theme(str(x)))
        )

        # ── STEP1: オート広告のみ（ASINターゲティング除外） ─
        mask_auto = df_search[campaign_col].str.contains("オート|auto", case=False, na=False)
        n_auto_total = int(mask_auto.sum())
        df_auto = df_search[mask_auto].copy()

        # ── STEP2: 登録済みKW除外（完全一致 + 部分一致） ───
        registered_set: set[str] = set(df_target[target_kw_col].apply(normalize_text))
        registered_set.discard("")

        mask_exact = df_auto["kw_norm"].isin(registered_set)
        n_exact = int(mask_exact.sum())
        df_step = df_auto[~mask_exact].copy()

        mask_partial = df_step["kw_norm"].apply(lambda k: is_already_covered(k, registered_set))
        n_partial = int(mask_partial.sum())
        df_step = df_step[~mask_partial].copy()
        n_after_registered = len(df_step)

        # ── STEP3: ブランドKW除外 ─────────────────────────
        mask_brand = df_step["kw_norm"].apply(lambda k: any(b in k for b in brand_excludes))
        n_brand = int(mask_brand.sum())
        df_step = df_step[~mask_brand].copy()
        n_after_brand = len(df_step)

        # ── STEP4: コード・ASIN・型番除外 ──────────────────
        mask_code = df_step["kw_norm"].apply(is_code_or_asin)
        n_code = int(mask_code.sum())
        df_step = df_step[~mask_code].copy()

        # ── STEP5: 商品タイトル除外 ──────────────────────
        mask_title = df_step[kw_col].apply(is_product_title)
        n_title = int(mask_title.sum())
        df_step = df_step[~mask_title].copy()
        n_after_exclusions = len(df_step)

        # ── STEP6: 集計 ──────────────────────────────────
        agg_dict: dict = {
            "keyword":        (kw_col, "first"),
            "campaign_theme": ("campaign_theme", lambda x: x.mode().iloc[0] if len(x) > 0 else "未分類"),
            "sales":          (sales_col, "sum"),
            "cost":           (cost_col,  "sum"),
        }
        if orders_col:
            agg_dict["orders"] = (orders_col, "sum")
        if units_col and units_col != orders_col:
            agg_dict["units"] = (units_col, "sum")
        if clicks_col:
            agg_dict["clicks"] = (clicks_col, "sum")
        if impressions_col:
            agg_dict["impressions"] = (impressions_col, "sum")

        agg = (
            df_step.groupby("kw_norm")
            .agg(**agg_dict)
            .reset_index(drop=True)
        )

        agg["ROAS"] = agg.apply(
            lambda r: round(r["sales"] / r["cost"], 2) if r["cost"] > 0 else 0.0,
            axis=1,
        )

        if "clicks" in agg.columns and "orders" in agg.columns:
            agg["CVR"] = agg.apply(
                lambda r: round(r["orders"] / r["clicks"] * 100, 1) if r["clicks"] > 0 else 0.0,
                axis=1,
            )

        agg["price"] = agg["campaign_theme"].map(PRICE_MASTER)
        agg_priced = agg[agg["price"].notna()].copy()

        # ── STEP7: 売上条件（売価×2以上） ────────────────
        mask_sales = agg_priced["sales"] >= agg_priced["price"] * 2
        n_sales_pass = int(mask_sales.sum())
        df_cand = agg_priced[mask_sales].copy()

        # ── STEP8: ROAS条件（2.0以上） ───────────────────
        mask_roas = df_cand["ROAS"] >= 2.0
        n_roas_pass = int(mask_roas.sum())
        df_cand = df_cand[mask_roas].copy()

        # ── STEP9: 注文数条件（min_orders以上） ──────────
        if "orders" in df_cand.columns:
            mask_orders = df_cand["orders"] >= min_orders
            n_orders_fail = int((~mask_orders).sum())
            df_cand = df_cand[mask_orders].copy()
        else:
            n_orders_fail = 0
            st.warning("注文数列が見つからないため、注文数フィルタはスキップされました。")
        n_after_orders = len(df_cand)

        # price列除去
        df_cand = df_cand.drop(columns=["price"], errors="ignore")

        # ── STEP10: 同一意図KW統合（deduplicate_keyword_intent） ─
        df_win = deduplicate_keyword_intent(df_cand)
        n_after_grouping = len(df_win)

        df_win["rank"] = df_win["ROAS"].apply(assign_rank)

        # ── session_state へ保存 ──────────────────────────
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
            "n_code":             n_code,
            "n_title":            n_title,
            "n_after_exclusions": n_after_exclusions,
            "n_sales_pass":       n_sales_pass,
            "n_roas_pass":        n_roas_pass,
            "n_orders_fail":      n_orders_fail,
            "n_after_orders":     n_after_orders,
            "n_after_grouping":   n_after_grouping,
            "min_orders":         int(min_orders),
        }
        st.session_state["debug_info"] = {
            "cost_col":        cost_col,
            "sales_col":       sales_col,
            "orders_col":      orders_col,
            "clicks_col":      clicks_col,
            "impressions_col": impressions_col,
            "registered_len":  len(registered_set),
            "brand_excludes":  brand_excludes,
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

# ── 分析フロー ────────────────────────────────────────
st.markdown("---")
st.subheader("分析結果")

st.markdown(f"""
| ステップ | 内容 | 件数 |
|---|---|---|
| 検索語総数 | オート広告 | **{fs['n_auto_total']:,}件** |
| 登録済みKW除外後 | 完全一致 −{fs['n_exact']:,} ／ 部分一致 −{fs['n_partial']:,} | **{fs['n_after_registered']:,}件** |
| ブランド除外後 | −{fs['n_brand']:,}件 | **{fs['n_after_brand']:,}件** |
| コード・タイトル除外後 | コード −{fs['n_code']:,} ／ 商品タイトル −{fs['n_title']:,} | **{fs['n_after_exclusions']:,}件** |
| 売上条件通過 | 売価×2以上 | **{fs['n_sales_pass']:,}件** |
| ROAS条件通過 | ROAS≥2.0 | **{fs['n_roas_pass']:,}件** |
| 注文数条件通過 | 注文数≥{fs['min_orders']} | **{fs['n_after_orders']:,}件**（−{fs['n_orders_fail']:,}件） |
| **同一意図KW統合後** | 類似KW群を代表KW1件に集約 | **{fs['n_after_grouping']:,}件** |
| **Aランク** | ROAS≥5.0 | **{n_a:,}件** |
| **Bランク** | 2.0≤ROAS<5.0 | **{n_b:,}件** |
| **合計** | | **{n_win:,}件** |
""")

if df_win.empty:
    st.warning(
        "勝ちKWが見つかりませんでした。\n\n"
        "・検索語句レポートの期間を広げてください\n"
        "・サイドバーの「最小注文数」を下げてみてください\n"
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
    st.info("Aランク（ROAS 5.0以上）のキーワードはありません。")
else:
    top10_base = ["campaign_theme", "keyword", "ROAS", "sales", "cost"]
    for col in ["orders", "CVR", "clicks"]:
        if col in df_a.columns:
            top10_base.append(col)
    top10 = (
        df_a[[c for c in top10_base if c in df_a.columns]]
        .sort_values("ROAS", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )
    top10.index = top10.index + 1
    top10_disp = top10.rename(columns={
        "campaign_theme": "キャンペーン名",
        "keyword":        "追加KW",
        "ROAS":           "ROAS",
        "sales":          "売上",
        "cost":           "広告費",
        "orders":         "商品購入数",
        "CVR":            "CVR",
        "clicks":         "クリック数",
    }).copy()
    top10_disp["売上"]   = top10_disp["売上"].apply(lambda x: f"¥{x:,.0f}")
    top10_disp["広告費"] = top10_disp["広告費"].apply(lambda x: f"¥{x:,.0f}")
    top10_disp["ROAS"]  = top10_disp["ROAS"].round(2)
    if "CVR" in top10_disp.columns:
        top10_disp["CVR"] = top10_disp["CVR"].apply(lambda x: f"{x:.1f}%")
    st.dataframe(top10_disp, use_container_width=True)

# ── ダウンロード ──────────────────────────────────────
st.markdown("---")
st.subheader("ダウンロード")
st.caption("どれをダウンロードしても分析結果は消えません。複数CSV連続ダウンロード可能です。")

all_bytes    = make_csv_bytes(df_win)
a_bytes      = make_a_full_csv_bytes(df_a)
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
for col in ["orders", "CVR", "clicks"]:
    if col in df_win.columns:
        disp_base.append(col)

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
            "keyword": "追加KW",    "rank": "ランク",
            "ROAS":    "ROAS",      "sales": "売上",
            "cost":    "広告費",    "orders": "商品購入数",
            "CVR":     "CVR",       "clicks": "クリック数",
        })
        disp.index = disp.index + 1
        disp["売価判定"] = f"✓ ¥{threshold:,}以上"
        disp["ROAS判定"] = disp["ROAS"].apply(lambda r: f"✓ {r:.2f} ≥ 2.0")
        disp["売上"]   = disp["売上"].apply(lambda x: f"¥{x:,.0f}")
        disp["広告費"] = disp["広告費"].apply(lambda x: f"¥{x:,.0f}")
        disp["ROAS"]   = disp["ROAS"].round(2)
        if "CVR" in disp.columns:
            disp["CVR"] = disp["CVR"].apply(lambda x: f"{x:.1f}%")
        st.dataframe(disp, use_container_width=True)

# ── デバッグ ──────────────────────────────────────────
di = st.session_state.get("debug_info", {})
with st.expander("デバッグ情報"):
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("完全一致除外",  f"{fs['n_exact']:,}")
    d2.metric("部分一致除外",  f"{fs['n_partial']:,}")
    d3.metric("ブランド除外",  f"{fs['n_brand']:,}")
    d4.metric("コード除外",    f"{fs['n_code']:,}")
    st.write("商品タイトル除外:", fs.get("n_title", 0), "件")
    st.write("同一意図統合後:", fs.get("n_after_grouping", 0), "件")
    st.write("費用列:", di.get("cost_col"))
    st.write("売上列:", di.get("sales_col"))
    st.write("注文列:", di.get("orders_col"))
    st.write("クリック列:", di.get("clicks_col"))
    st.write("インプレッション列:", di.get("impressions_col"))
    st.write("ブランド除外語:", di.get("brand_excludes"))
    st.write(f"登録済みKW数: {di.get('registered_len', 0):,}")
