"""
ANIH 勝てるKW発掘ツール v1.2
Amazon SP広告の追加キーワード発掘を自動化する Streamlit アプリ

修正履歴:
  v1.0 - 初期実装
  v1.1 - 部分一致除外、ラッコKWテーマ振り分け、スコア改善、クラス構造化
  v1.2 - 部分一致除外を「2語以上のKWのみ」に限定、デバッグ画面に除外件数表示
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import streamlit as st


# ════════════════════════════════════════════════════════
# 定数
# ════════════════════════════════════════════════════════

ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$", re.IGNORECASE)
CAMPAIGN_THEME_RE = re.compile(r"【(.*?)】")

DEFAULT_BRAND_EXCLUDES = "アニハ\nゾイック\nノルバサン\nマラセブ"
DEFAULT_STOP_WORDS = (
    "近く\n店舗\n店\n料金\nサロン\n出張\n"
    "東京\n大阪\n埼玉\n神奈川\nコーナン\nカインズ"
)


# ════════════════════════════════════════════════════════
# ユーティリティ（共通）
# ════════════════════════════════════════════════════════

def normalize_text(text) -> str:
    """全角→半角(NFKC)、小文字化、前後空白・連続スペース削除"""
    if text is None or (isinstance(text, float) and text != text):
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_campaign_theme(campaign_name: str) -> str:
    """【テーマ名】SP広告... -> テーマ名"""
    m = CAMPAIGN_THEME_RE.search(str(campaign_name))
    return m.group(1) if m else ""


def is_asin(kw: str) -> bool:
    return bool(ASIN_RE.match(kw.replace(" ", "").upper()))


def read_csv_auto(file) -> pd.DataFrame:
    """utf-8-sig / utf-16(tab区切り) を自動判別して読み込む"""
    raw = file.read()
    file.seek(0)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return pd.read_csv(io.BytesIO(raw), encoding="utf-16", sep="\t")
    try:
        return pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(io.BytesIO(raw), encoding="cp932")


def check_required_columns(df: pd.DataFrame, required: list) -> list:
    return [c for c in required if c not in df.columns]


# ════════════════════════════════════════════════════════
# データクラス
# ════════════════════════════════════════════════════════

@dataclass
class KWCandidate:
    tier: str
    keyword: str
    campaign_theme: str
    score: float
    reason: str
    source: str
    clicks: Optional[float] = None
    orders: Optional[float] = None
    sales: Optional[float] = None
    search_volume: Optional[float] = None


@dataclass
class ExclusionStats:
    asin: int = 0
    brand: int = 0
    stop_word: int = 0
    exact_match: int = 0
    partial_match: int = 0


# ════════════════════════════════════════════════════════
# 部分一致除外（v1.2: 2語以上のみ）
# ════════════════════════════════════════════════════════

def is_already_covered(keyword: str, registered_keywords: set) -> bool:
    """
    登録済みKW（2語以上）が候補KWに含まれていれば True。

    v1.2: 1語のみの登録済みKW（例: 「犬」）はスキップ。
    例:
      登録済み「犬 シャンプー」(2語) ⊆ 「犬 シャンプー 敏感肌」 -> 除外
      登録済み「犬」(1語) ⊆ 「犬 シャンプー」 -> 除外しない
    """
    kw = normalize_text(keyword)
    for reg_kw in registered_keywords:
        reg_kw = normalize_text(reg_kw)
        if len(reg_kw.split()) < 2:
            continue
        if reg_kw in kw:
            return True
    return False


# ════════════════════════════════════════════════════════
# クラス構造（DataDive拡張対応）
# ════════════════════════════════════════════════════════

class CampaignProcessor:
    REQUIRED_COLS = [
        "キャンペーン名", "ターゲティング", "ターゲティングマッチタイプ",
        "クリック数", "商品購入数", "売上",
    ]

    def __init__(self, df: pd.DataFrame) -> None:
        for col in ["クリック数", "商品購入数", "売上"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df["kw_norm"] = df["ターゲティング"].apply(normalize_text)
        df["theme"] = df["キャンペーン名"].apply(extract_campaign_theme)
        self._df = df

    @property
    def registered_keywords(self) -> set:
        return set(self._df["kw_norm"])

    @property
    def themes(self) -> list:
        return [t for t in self._df["theme"].unique() if t]


class SearchTermProcessor:
    REQUIRED_COLS = [
        "キャンペーン名", "検索用語", "インプレッション",
        "クリック数", "商品購入数", "売上",
    ]

    def __init__(
        self,
        df: pd.DataFrame,
        brand_excludes: list,
        stop_words: list,
        stats: ExclusionStats,
    ) -> None:
        for col in ["インプレッション", "クリック数", "商品購入数", "売上"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df["kw_norm"] = df["検索用語"].apply(normalize_text)
        df["campaign_theme"] = df["キャンペーン名"].apply(extract_campaign_theme)

        mask_asin = df["kw_norm"].apply(is_asin)
        stats.asin += int(mask_asin.sum())
        df = df[~mask_asin].copy()

        mask_brand = df["kw_norm"].apply(lambda k: any(b in k for b in brand_excludes))
        stats.brand += int(mask_brand.sum())
        df = df[~mask_brand].copy()

        mask_stop = df["kw_norm"].apply(lambda k: any(s in k for s in stop_words))
        stats.stop_word += int(mask_stop.sum())
        df = df[~mask_stop].copy()

        self._df = df
        self._stats = stats

    def build_candidates(self, registered_keywords: set) -> list:
        agg = (
            self._df.groupby("kw_norm")
            .agg(
                campaign_theme=("campaign_theme", "first"),
                keyword=("検索用語", "first"),
                is_auto=("キャンペーン名", lambda x: x.str.contains("オート").any()),
                clicks=("クリック数", "sum"),
                orders=("商品購入数", "sum"),
                sales=("売上", "sum"),
            )
            .reset_index()
        )

        candidates = []
        for _, row in agg.iterrows():
            kw_norm = row["kw_norm"]

            if kw_norm in registered_keywords:
                self._stats.exact_match += 1
                continue

            if is_already_covered(kw_norm, registered_keywords):
                self._stats.partial_match += 1
                continue

            is_auto = row["is_auto"]
            orders = row["orders"]
            clicks = row["clicks"]
            sales = row["sales"]
            theme = row["campaign_theme"]

            if is_auto and orders >= 1:
                score = 1000 + (orders * 300) + (clicks * 10)
                candidates.append(KWCandidate(
                    tier="Tier1", keyword=row["keyword"], campaign_theme=theme,
                    score=score, reason="Auto売上あり", source="Auto",
                    clicks=clicks, orders=orders, sales=sales,
                ))
            elif is_auto and clicks >= 5 and orders == 0:
                score = 500 + (clicks * 10)
                candidates.append(KWCandidate(
                    tier="Tier2", keyword=row["keyword"], campaign_theme=theme,
                    score=score, reason="Auto高クリック", source="Auto",
                    clicks=clicks, orders=orders, sales=sales,
                ))

        return candidates


class RakkoProcessor:
    def __init__(
        self,
        df: pd.DataFrame,
        brand_excludes: list,
        stop_words: list,
        stats: ExclusionStats,
    ) -> None:
        df["kw_norm"] = df["キーワード"].apply(normalize_text)
        if "月間検索数" in df.columns:
            df["月間検索数"] = pd.to_numeric(df["月間検索数"], errors="coerce")
        self._df = df
        self._brand_excludes = brand_excludes
        self._stop_words = stop_words
        self._stats = stats

    def _assign_theme(self, kw_norm: str, themes: list) -> str:
        matched = [t for t in themes if normalize_text(t) in kw_norm]
        return max(matched, key=len) if matched else ""

    def build_candidates(self, registered_keywords: set, themes: list) -> list:
        candidates = []
        for _, row in self._df.iterrows():
            kw_norm = row["kw_norm"]
            kw_raw = str(row["キーワード"])

            if is_asin(kw_norm):
                self._stats.asin += 1
                continue
            if any(b in kw_norm for b in self._brand_excludes):
                self._stats.brand += 1
                continue
            if any(s in kw_norm for s in self._stop_words):
                self._stats.stop_word += 1
                continue

            if kw_norm in registered_keywords:
                self._stats.exact_match += 1
                continue

            if is_already_covered(kw_norm, registered_keywords):
                self._stats.partial_match += 1
                continue

            sv = row.get("月間検索数", None)
            if isinstance(sv, float) and sv != sv:
                sv = None
            if sv is not None and sv < 50:
                continue

            score = float(sv) if sv is not None else 0.0
            theme = self._assign_theme(kw_norm, themes)

            candidates.append(KWCandidate(
                tier="Tier3", keyword=kw_raw, campaign_theme=theme,
                score=score, reason="ラッコ需要あり", source="Rakko",
                search_volume=sv,
            ))

        return candidates


class KeywordScorer:
    """
    全プロセッサーから候補KWを収集し、重複除外・ソートして最終リストを返す。
    将来: DataDiveProcessor を add_candidates するだけで拡張可能。
    """

    def __init__(self) -> None:
        self._all_candidates: list = []

    def add_candidates(self, candidates: list) -> None:
        self._all_candidates.extend(candidates)

    def finalize(self) -> pd.DataFrame:
        if not self._all_candidates:
            return pd.DataFrame(columns=[
                "Tier", "Keyword", "CampaignTheme", "Score",
                "Reason", "Source", "Clicks", "Orders", "Sales", "SearchVolume",
            ])

        rows = [
            {
                "Tier": c.tier,
                "Keyword": c.keyword,
                "CampaignTheme": c.campaign_theme,
                "Score": c.score,
                "Reason": c.reason,
                "Source": c.source,
                "Clicks": c.clicks,
                "Orders": c.orders,
                "Sales": c.sales,
                "SearchVolume": c.search_volume,
                "_dedup": normalize_text(c.keyword),
            }
            for c in self._all_candidates
        ]

        df = pd.DataFrame(rows)
        df = (
            df.sort_values("Score", ascending=False)
            .drop_duplicates(subset="_dedup", keep="first")
            .drop(columns="_dedup")
            .reset_index(drop=True)
        )
        return df


# ════════════════════════════════════════════════════════
# Streamlit UI
# ════════════════════════════════════════════════════════

st.set_page_config(
    page_title="ANIH 勝てるKW発掘ツール",
    page_icon="🐾",
    layout="wide",
)

with st.sidebar:
    st.header("フィルター設定")

    brand_text = st.text_area(
        "ブランド除外（改行区切り）",
        value=DEFAULT_BRAND_EXCLUDES,
        height=120,
    )
    brand_excludes = [
        normalize_text(b) for b in brand_text.strip().splitlines() if b.strip()
    ]

    stop_text = st.text_area(
        "除外語（改行区切り）",
        value=DEFAULT_STOP_WORDS,
        height=160,
    )
    stop_words = [
        normalize_text(s) for s in stop_text.strip().splitlines() if s.strip()
    ]

    st.markdown("---")
    st.caption("v1.2 | v2: DataDive対応予定")

st.title("ANIH 勝てるKW発掘ツール v1.2")
st.markdown(
    "ANIHが勝てる可能性が高いKWを抽出します。"
    " 登録済みKW（2語以上）に部分一致で包含される語句は自動除外。"
)

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("① 検索用語レポート")
    search_file = st.file_uploader(
        "Amazon 検索用語CSV", type="csv", key="search"
    )
with col2:
    st.subheader("② キャンペーンレポート")
    campaign_file = st.file_uploader(
        "Amazon キャンペーンCSV", type="csv", key="campaign"
    )
with col3:
    st.subheader("③ ラッコキーワードCSV")
    rakko_file = st.file_uploader(
        "ラッコキーワードCSV", type="csv", key="rakko"
    )

st.markdown("---")
run_btn = st.button("勝てるKW抽出", type="primary", use_container_width=True)

if run_btn:
    if not search_file:
        st.error("検索用語レポートがアップロードされていません")
        st.stop()
    if not campaign_file:
        st.error("キャンペーンレポートがアップロードされていません")
        st.stop()

    with st.spinner("処理中..."):
        raw_search = read_csv_auto(search_file)
        raw_campaign = read_csv_auto(campaign_file)
        raw_rakko = (
            read_csv_auto(rakko_file)
            if rakko_file
            else pd.DataFrame(columns=["キーワード", "月間検索数"])
        )

        missing_s = check_required_columns(raw_search, SearchTermProcessor.REQUIRED_COLS)
        missing_c = check_required_columns(raw_campaign, CampaignProcessor.REQUIRED_COLS)
        if missing_s:
            st.error(f"検索用語レポートに不足列: {missing_s}")
            st.stop()
        if missing_c:
            st.error(f"キャンペーンレポートに不足列: {missing_c}")
            st.stop()

        stats = ExclusionStats()

        camp_proc = CampaignProcessor(raw_campaign.copy())
        search_proc = SearchTermProcessor(
            raw_search.copy(), brand_excludes, stop_words, stats
        )
        rakko_proc = RakkoProcessor(
            raw_rakko.copy(), brand_excludes, stop_words, stats
        )

        registered_kws = camp_proc.registered_keywords
        themes = camp_proc.themes

        scorer = KeywordScorer()
        scorer.add_candidates(search_proc.build_candidates(registered_kws))
        scorer.add_candidates(rakko_proc.build_candidates(registered_kws, themes))

        df_result = scorer.finalize()

    st.markdown("---")

    tier_counts = df_result["Tier"].value_counts().to_dict() if not df_result.empty else {}

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("追加候補 合計", f"{len(df_result):,} 件")
    c2.metric("Tier1 (Auto購入あり)", f"{tier_counts.get('Tier1', 0):,} 件")
    c3.metric("Tier2 (Auto高クリック)", f"{tier_counts.get('Tier2', 0):,} 件")
    c4.metric("Tier3 (ラッコ需要)", f"{tier_counts.get('Tier3', 0):,} 件")
    c5.metric("登録済みKW数", f"{len(registered_kws):,} 件")

    if df_result.empty:
        st.warning("条件に合うキーワードが見つかりませんでした。ファイルやフィルター設定を確認してください。")
    else:
        st.subheader(f"追加推奨KW（上位100件 / 全{len(df_result):,}件）")

        display_df = df_result.head(100).copy()
        display_df["Score"] = display_df["Score"].round(1)
        display_df["Sales"] = display_df["Sales"].apply(
            lambda x: f"¥{x:,.0f}" if pd.notna(x) and x is not None else "-"
        )
        display_df["SearchVolume"] = display_df["SearchVolume"].apply(
            lambda x: f"{int(x):,}" if pd.notna(x) and x is not None else "-"
        )
        display_df["Clicks"] = display_df["Clicks"].apply(
            lambda x: int(x) if pd.notna(x) and x is not None else "-"
        )
        display_df["Orders"] = display_df["Orders"].apply(
            lambda x: int(x) if pd.notna(x) and x is not None else "-"
        )

        def color_tier(val: str) -> str:
            palette = {
                "Tier1": "background-color:#d4edda;color:#155724",
                "Tier2": "background-color:#fff3cd;color:#856404",
                "Tier3": "background-color:#d1ecf1;color:#0c5460",
            }
            return palette.get(val, "")

        st.dataframe(
            display_df.style.applymap(color_tier, subset=["Tier"]),
            use_container_width=True,
            height=520,
        )

        csv_bytes = df_result.to_csv(
            index=False, encoding="utf-8-sig"
        ).encode("utf-8-sig")

        st.download_button(
            label="追加KW.csv をダウンロード（UTF-8 BOM）",
            data=csv_bytes,
            file_name="追加KW.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # デバッグ（v1.2: 除外件数追加）
    with st.expander("デバッグ情報"):
        st.markdown("**除外件数内訳**")
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("ASIN除外", f"{stats.asin:,} 件")
        d2.metric("ブランド除外", f"{stats.brand:,} 件")
        d3.metric("除外語フィルター", f"{stats.stop_word:,} 件")
        d4.metric("完全一致除外", f"{stats.exact_match:,} 件")
        d5.metric("部分一致除外", f"{stats.partial_match:,} 件")
        st.markdown("**その他**")
        st.write(f"登録済みKW: {len(registered_kws):,} 件")
        st.write(f"テーマ一覧: {themes}")
        st.write(f"ブランド除外語: {brand_excludes}")
        st.write(f"除外語: {stop_words}")
        st.write(f"ラッコCSV行数: {len(raw_rakko):,} 行")
