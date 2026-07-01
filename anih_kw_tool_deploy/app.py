# ANIHA Amazon広告分析ツール — 開発仕様書

> **対象読者:** Claude / ChatGPT / Gemini および人間の開発者・運用者  
> **目的:** ANIHA の設計・実装・運用ルールを一元管理し、引継ぎ・継続開発の基準とする  
> **最終更新:** Ver1 確定時点  
> **ステータス:** Ver1 完成・実運用開始

---

## 1. Ver1 完成宣言

ANIHA Amazon広告分析ツール Ver1 は正式に完成した。

| 項目 | 内容 |
|------|------|
| 完成ファイル | `app_v1_final.py` |
| 総行数 | 3612行 |
| 構文検証 | `python3 -m py_compile` SYNTAX OK |
| 実装完了機能 | CPC調整（7日分析）/ キーワード追加（30日分析） |
| 開発フェーズ | Phase4（実運用・データ蓄積）へ移行 |

Ver1 はこの時点で凍結する。以後の変更は本仕様書に定めるルールに従う。

---

## 2. 現在のシステム構成

### 2-1. フレームワーク

```
Streamlit アプリケーション（シングルファイル構成）
Python 3.x / pandas / streamlit
```

### 2-2. ファイル構成

```
app_v1_final.py          # メインアプリ（本番ファイル）
analysis_data/
  cpc_change_history.json    # CPC変更履歴（ダウンロード時に自動保存）
  cpc_kw_analysis.json       # CPC分析レポート蓄積
  kw_add_analysis.json       # キーワード追加分析レポート蓄積
  cpc_product_analysis.json  # 商品CPC分析レポート
```

### 2-3. 画面構成（サイドバー）

サイドバーは `_nav_btn()` / `_VALID_PAGES` / `_PAGE_FUNCS` で管理。  
変更禁止。

### 2-4. 主要 Session State 変数

| 変数名 | 内容 |
|--------|------|
| `dw` | キーワード追加用 DataFrame |
| `dc_cpc` | キーワード CPC 調整用 DataFrame |
| `dc_cpc_product` | 商品 CPC 調整用 DataFrame |
| `dc_cpc_video` | 動画 CPC 調整用 DataFrame |
| `df_pt_add_m` | 手動商品ターゲ追加用 DataFrame |
| `df_pt_add_v` | 動画商品ターゲ追加用 DataFrame |

### 2-5. 主要ユーティリティ関数

| 関数名 | 役割 |
|--------|------|
| `fcol(df, candidates)` | 列名候補リストから実在する列名を返す |
| `rcsv(file)` | CSV 読み込み（エンコーディング自動判定） |
| `norm(s)` | キーワード正規化（全角→半角、大文字→小文字、空白除去） |
| `tonum(series)` | ¥ / カンマ付き文字列を numeric に変換 |
| `get_theme(name)` | キャンペーン名からテーマを抽出 |
| `official(theme)` | テーマを正式名称に統一 |
| `assign_cpc_rank(row)` | CPC ランク判定（戻り値: rank, action, delta） |
| `build_cpc_df(df)` | CPC 調整列を付与した DataFrame を生成 |

---

## 3. Ver1 凍結ルール

以下の項目は Ver1 において **変更禁止** とする。不具合修正のみ許可。

| カテゴリ | 凍結内容 |
|----------|----------|
| 分析ロジック | Before/After 比較方法、集計単位 |
| CSV 読込 | `rcsv()` / `_anls_parse_csv()` の処理内容 |
| CSV 出力 | 列名・列順・エンコーディング（UTF-8 BOM） |
| 履歴保存方式 | JSON 形式・保存タイミング・保存先 |
| 一致キー | Campaign / Ad Group / Keyword の 3 キー構成 |
| merge 処理 | `how="inner"`、`suffixes=("_b","_a")` |
| UI 構成 | サイドバー / 5 カード / 一覧 / 詳細 / タブ構成 |
| Session State | キー名・更新タイミング |
| analysis_reports | JSON スキーマ |
| 改善判定ロジック | ROAS ±3% 閾値による 3 分類 |

---

## 4. Ver1.1 開発方針

### 4-1. 目的

**分析精度の改善のみ。** 新規画面・設計変更・アーキテクチャ変更は行わない。

### 4-2. 開始条件

実データによる運用結果を蓄積してから開始する。  
推測による閾値変更は禁止。

### 4-3. 調整対象（Ver1.1 で変更してよいもの）

- ROAS 判定閾値（現在: ±3%）
- CVR 判定閾値
- CTR 判定閾値
- CPC 評価基準
- 改善 / 悪化 / 変化なし の判定ロジック
- 改善率の計算方法
- 一覧表示の並び順

### 4-4. 完成条件

実運用で改善判定・悪化判定が運用者の感覚と十分に一致した時点で Ver1.1 完成とする。

---

## 5. CPC 分析ロジック

### 5-1. 分析対象の定義

**「CPC 調整 実行用 CSV をダウンロードしたキーワード」のみ。**

推奨キーワード全件を分析対象にしない。  
`cpc_delta != 0` の全件を分析対象にしない。

### 5-2. 処理フロー

```
① CPC 調整実行（build_cpc_df によりランク・delta 付与）
↓
② 変更対象（cpc_delta != 0）のみ df_disp に絞り込み
↓
③ 「CPC 調整 実行用.csv」ダウンロードボタン押下
↓
④ on_click で _anls_save_cpc_change_history(df_disp[disp_cols].copy()) 実行
↓
⑤ analysis_data/cpc_change_history.json へ追記
↓
⑥ Amazon へ CSV アップロード・CPC 変更実施
↓
⑦ 7 日後レポート CSV をアップロード
↓
⑧ 履歴の最終エントリを読み込み
↓
⑨ 履歴キーに一致した before_df 行のみ抽出
↓
⑩ 7 日後 CSV と inner join（一致キーで結合）
↓
⑪ 改善 / 悪化 / 変化なし 判定
↓
⑫ analysis_data/cpc_kw_analysis.json へ保存（任意）
```

### 5-3. 履歴保存仕様

```python
# page_cpc() 内
_dl_csv_adj = df_disp[disp_cols].rename(columns=_rn).to_csv(...)
st.download_button(...,
    on_click=_anls_save_cpc_change_history,
    args=(df_disp[disp_cols].copy(),))
```

保存関数:

```python
def _anls_save_cpc_change_history(df_disp):
    save_cols = [c for c in ["campaign_name","ad_group","keyword",
                              "avg_cpc","rec_cpc","cpc_delta",
                              "cpc_rank","cpc_action",
                              "sales","cost","ROAS","orders"] if c in df_disp.columns]
    record = {
        "exported_at": datetime.now().isoformat(),
        "entries": df_disp[save_cols].to_dict(orient="records"),
    }
    existing = _anls_load("cpc_change_history.json")
    existing.append(record)
    _anls_save("cpc_change_history.json", existing)
```

**重要:** `cpc_delta != 0` の再フィルタは行わない。渡された DataFrame の全行をそのまま保存する。

### 5-4. Before データ

`dc_cpc`（CPC 調整タブ読み込み時に生成された DataFrame）。  
`campaign_name` / `ad_group` / `keyword` / `avg_cpc` / `rec_cpc` / `cpc_delta` / `ROAS` 等を含む。  
`match_type` 列は存在しない。

### 5-5. After データ生成（`_anls_build_cpc_after`）

```python
def _anls_build_cpc_after(df, cc, sc, oc_, od, clk, kwt_col) -> pd.DataFrame:
    # cost > 0 の行のみ対象
    # _kn_key = norm(campaign_name) + "|" + norm(ad_group) + "|" + norm(keyword)
    # agg: sales / cost / orders / clicks
    # 計算: ROAS / CVR / avg_cpc
    # 戻り値: _kn_key + 数値指標のみ（campaign_theme 列は含まない）
```

### 5-6. cpc_delta の定義

`cpc_delta` はツールが推奨する CPC 変更幅（+5 / 0 / -5 / -10 等）。  
ユーザーが実際に変更した金額ではない。

---

## 6. キーワード追加分析ロジック

### 6-1. 分析対象

`dw`（キーワード追加タブで生成した DataFrame）の全行。  
CPC 分析と異なり、履歴保存は不要。  
30 日後の比較で改善 / 悪化 / 変化なしを判定する。

### 6-2. After データ生成（`_anls_build_kw_after`）

```python
def _anls_build_kw_after(df, kc, cc, sc, oc_, od, clk) -> pd.DataFrame:
    # cost > 0 の行のみ対象
    # _kn_key = norm(keyword)（検索語句列）
    # agg: sales / cost / orders / clicks
    # 計算: ROAS / CVR / avg_cpc
    # 戻り値: _kn_key + 数値指標のみ（campaign_theme 列は含まない）
```

### 6-3. 一致キー

`norm(keyword)` のみの 1 キー。

---

## 7. 7 日分析・30 日分析ルール

| 分析種別 | 期間 | 対象 | 履歴保存 |
|----------|------|------|----------|
| CPC 分析 | 7 日固定 | ダウンロードしたキーワードのみ | あり（ダウンロード時） |
| KW 追加分析 | 30 日固定 | dw 全件 | なし |

期間は変更不可（UI でユーザーが変更できない固定値）。

---

## 8. 分析対象の定義

### 8-1. CPC 分析

```
分析対象 = cpc_change_history.json の最終エントリに含まれるキーワード
         ∩ 7 日後 CSV に存在するキーワード
```

- 推奨キーワード全件（cpc_delta != 0）は分析対象外
- CSV をダウンロードしていない場合は分析不可（エラー表示）

### 8-2. KW 追加分析

```
分析対象 = dw（キーワード追加タブの抽出結果）の全行
         ∩ 30 日後 CSV に存在するキーワード
```

### 8-3. 件数表示（CPC 分析のみ）

```
変更履歴 N件  = 履歴保存件数（CSV ダウンロード時の行数）
CSV一致   M件  = inner join 成功件数 = 分析対象件数
不一致    K件  = N - M（CSV に存在しなかったキーワード）
分析対象  M件  = merged の行数
```

---

## 9. 履歴保存仕様

### 9-1. ファイル

`analysis_data/cpc_change_history.json`

### 9-2. スキーマ

```json
[
  {
    "exported_at": "2025-01-01T10:00:00.000000",
    "entries": [
      {
        "campaign_name": "キャンペーン名",
        "ad_group": "広告グループ名",
        "keyword": "キーワード",
        "avg_cpc": 100,
        "rec_cpc": 95,
        "cpc_delta": -5,
        "cpc_rank": "C",
        "cpc_action": "DOWN",
        "sales": 10000,
        "cost": 500,
        "ROAS": 20.0,
        "orders": 5
      }
    ]
  }
]
```

### 9-3. 保存タイミング

「CPC 調整 実行用.csv」ダウンロードボタン押下時（`on_click`）。  
「CPC 調整表.csv」ダウンロードボタンには保存処理を付与しない。

### 9-4. 読み込みタイミング

CPC 分析実行ボタン（🔍 分析実行）押下時。  
`_cpc_hist[-1]`（最終エントリ）のみを使用する。

### 9-5. analysis_reports スキーマ（cpc_kw_analysis.json / kw_add_analysis.json）

```json
[
  {
    "id": "20250101_100000",
    "saved_at": "2025-01-01",
    "type": "キーワードCPC調整",
    "period_days": 7,
    "n_before": 222,
    "n_matched": 18,
    "n_kaizen": 10,
    "n_akka": 5,
    "n_henko": 3,
    "rate": 55.6,
    "camps": ["液体", "涙やけ"]
  }
]
```

---

## 10. 一致キー仕様

### 10-1. CPC 分析キー

```
norm(campaign_name) | norm(ad_group) | norm(keyword)
```

| レイヤー | キー生成コード |
|----------|--------------|
| 履歴キー | `_cpc_key(r)` — `r.get("campaign_name")` / `r.get("ad_group")` / `r.get("keyword")` |
| Before キー | `_cpc_key(r)` — `dc_cpc` の各行に適用 |
| After キー | `d[cc].apply(norm) + "\|" + d[_agn2].apply(norm) + "\|" + d[kc2].apply(norm)` |

**Match Type は使用しない。** `dc_cpc` に `match_type` 列が存在しないため。

### 10-2. KW 追加分析キー

```
norm(keyword)
```

検索語句列（`kc` で検出）を `norm()` で正規化した値。

### 10-3. `norm()` の定義

```python
def norm(s):
    # 全角→半角変換
    # 大文字→小文字変換
    # 前後空白除去
    # 連続空白を単一空白に統一
    return normalized_string
```

### 10-4. 禁止事項

- 部分一致（`contains`）禁止
- 前方一致（`startswith`）禁止
- Keyword 単独キー禁止（CPC 分析において）
- Campaign Theme 単独キー禁止

---

## 11. Session State 仕様

### 11-1. 分析結果の保存

```python
st.session_state[f"_anls_{csv_key}_result"] = {
    "merged":       merged,        # 分析済み DataFrame
    "camps":        camps,         # キャンペーン一覧
    "stats":        (n_total, n_kaizen, n_akka, n_henko, rate),
    "hist_fname":   hist_fname,    # analysis_reports ファイル名
    "label":        label,         # 分析ラベル
    "period_days":  period_days,   # 分析期間
    "n_before":     len(before_df),
    "n_history":    n_history,     # CPC 分析のみ（KW 追加は None）
}
```

### 11-2. 更新タイミング

「🔍 分析実行」ボタン押下時のみ更新する。

### 11-3. 更新しないタイミング

以下の操作では Session State を更新しない:

- キャンペーン切替 selectbox 変更
- expander の開閉
- タブの切替
- 「💾 分析結果を保存」ボタン（JSON ファイルへの書き込みのみ）

### 11-4. 再実行の禁止

分析結果は Session State に保持し、UI 操作のたびに再実行しない。  
一度実行した分析結果は画面遷移後も保持する。

---

## 12. UI 仕様

### 12-1. タブ構成

6 ページ関数それぞれに `st.tabs(["改善", "分析"])` を持つ:

| ページ関数 | 改善タブ内容 | 分析タブ内容 |
|-----------|------------|------------|
| `page_add_kw()` | KW 追加抽出 | 30 日分析 |
| `page_cpc()` | CPC 調整 | 7 日分析 |
| `page_cpc_product()` | 商品 CPC 調整 | 商品 CPC 分析 |
| `page_cpc_video()` | 動画 CPC 調整 | 動画 CPC 分析 |
| `page_pt_add_manual()` | 手動 PT 追加 | PT 分析 |
| `page_pt_add_video()` | 動画 PT 追加 | 動画 PT 分析 |

### 12-2. 分析結果 UI 構成

```
① サマリー 5 カード
   分析対象件数 / 🟢改善件数 / 🔴悪化件数 / 🟡変化なし件数 / 改善率

② 件数インフォ（CPC 分析のみ）
   変更履歴N件 | CSV一致M件 | 不一致K件 | 分析対象M件

③ キャンペーン絞り込み selectbox

④ キャンペーン別サマリーテーブル（expander）

⑤ 対象一覧
   ---
   🟢/🔴/🟡 キーワード名
   　改善/悪化/変化なし
   ▶ 詳細（expander）
     Before / After 各指標の比較

⑥ 分析結果を保存ボタン
⑦ 保存済み分析履歴（expander）
```

### 12-3. 一覧 UI ルール

- 一覧に数字を表示しない
- アイコン / 判定 / ▶詳細 の 3 行のみ
- Before/After の数値は ▶詳細 expander 内のみ表示

### 12-4. 5 カード定義

| カード | 色 | 内容 |
|--------|----|------|
| 分析対象 | 青 | `n_total` 件 |
| 🟢 改善 | 緑 | `n_kaizen` 件 |
| 🔴 悪化 | 赤 | `n_akka` 件 |
| 🟡 変化なし | 黄 | `n_henko` 件 |
| 改善率 | 紫 | `rate` % |

---

## 13. 変更禁止事項

### 13-1. コード上の変更禁止事項

| 禁止内容 | 理由 |
|----------|------|
| `rcsv()` / `fcol()` / `norm()` / `tonum()` の変更 | 全処理の基盤 |
| `assign_cpc_rank()` / `build_cpc_df()` の変更 | CPC ランク判定基盤 |
| `_anls_parse_csv()` の変更 | After CSV 列検出ロジック |
| `_anls_row_judge()` の変更 | Ver1.1 まで凍結 |
| `_anls_render_list()` の変更 | UI 凍結 |
| `_anls_detail_html()` の変更 | UI 凍結 |
| `_anls_summary_html()` の変更 | 5 カード凍結 |
| Session State キー名の変更 | 既存保存データとの互換性 |
| `cpc_change_history.json` スキーマの変更 | 既存履歴との互換性 |
| `analysis_reports` JSON スキーマの変更 | 蓄積データとの互換性 |
| サイドバー構成の変更 | 画面遷移凍結 |
| merge の `how="inner"` の変更 | 設計根拠あり |

### 13-2. 運用上の変更禁止事項

- 分析対象の定義変更（実際にエクスポートしたキーワード以外を対象にしない）
- 一致キーの構成変更（match_type を追加する場合は Ver2 で設計から行う）
- 履歴保存タイミングの変更（ダウンロード時以外に保存しない）

---

## 14. Claude 開発ルール

AI（Claude / ChatGPT / Gemini）が本プロジェクトを引き継ぐ際の必須ルール。

### 14-1. 作業前の必須確認

```
① app_v1_final.py を最初から最後まで読み込む
② 変更対象の関数を特定する
③ 参照元・参照先を grep で確認する
④ 影響範囲を列挙する
⑤ 変更内容を提案し、ユーザーの承認を得てから実装する
```

### 14-2. 禁止事項

- 推測による回答禁止
- コード確認なしの修正禁止
- 影響範囲の無確認修正禁止
- 複数箇所を同時変更する際の無連絡禁止
- 変更禁止箇所の変更禁止
- ダミーコード・省略コードの出力禁止

### 14-3. 必須報告事項

変更を行う場合、以下を必ず報告する:

1. 修正した関数名一覧
2. 変更内容（旧コード / 新コード）
3. 変更理由（コードを根拠に）
4. 影響範囲
5. 変更禁止事項に触れていないことの確認

### 14-4. ファイル出力ルール

- 既存ファイルへの上書きは EPERM エラーの可能性あり
- 新バージョンは必ず新ファイル名で出力する（例: `app_v1_1_final.py`）
- Python スクリプトを使用して read/replace/write を行う（Edit ツールはマルチバイト文字で失敗する場合あり）
- 出力後は必ず `python3 -m py_compile` で構文確認する

### 14-5. セッション引継ぎ時の参照順序

```
1. この DEVELOPMENT_RULES.md を読み込む
2. app_v1_final.py を読み込む
3. 最新の変更差分を確認する
4. 作業を開始する
```

---

## 15. 実運用フロー

### 15-1. CPC 分析フロー（毎週）

```
月曜日
  ↓ 週次 CSV レポートを ANIHA に読み込む
  ↓ CPC 調整タブで「CPC調整 実行用.csv」を確認
  ↓ 「📥 CPC調整_実行用.csv」をダウンロード（← 履歴自動保存）
  ↓ Amazon 広告コンソールへ CSV アップロード

翌週月曜日（7 日後）
  ↓ 7 日分レポートを CSV でダウンロード
  ↓ ANIHA の「分析タブ」へ CSV をアップロード
  ↓ 「🔍 分析実行」を押す
  ↓ 改善 / 悪化 / 変化なし を確認
  ↓ 「💾 分析結果を保存」（任意）
```

### 15-2. KW 追加分析フロー（毎月）

```
月初
  ↓ 月次 CSV レポートを ANIHA に読み込む
  ↓ キーワード追加タブで追加候補を確認・ダウンロード
  ↓ Amazon 広告コンソールへ KW を追加

翌月（30 日後）
  ↓ 30 日分レポートを CSV でダウンロード
  ↓ ANIHA の「分析タブ」へ CSV をアップロード
  ↓ 「🔍 分析実行」を押す
  ↓ 改善 / 悪化 / 変化なし を確認
```

### 15-3. 判定精度の検証（毎回必須）

| 確認項目 | 確認内容 |
|----------|----------|
| 改善判定精度 | 「改善」と判定されたものは実際に改善しているか |
| 悪化判定精度 | 「悪化」と判定されたものは実際に悪化しているか |
| 変化なし適切性 | 「変化なし」の件数は多すぎないか・少なすぎないか |
| 感覚との一致 | 分析結果が運用者の直感と一致するか |

精度が低い場合は実データを添えて Ver1.1 の閾値調整を依頼する。

---

## 16. 今後のロードマップ

| フェーズ | 内容 | 状態 |
|----------|------|------|
| Phase 1 | 設計 | ✅ 完了 |
| Phase 2 | 実装 | ✅ 完了 |
| Phase 3 | Ver1 確定 | ✅ 完了 |
| Phase 4 | 実運用・データ蓄積 | 🟢 進行中 |
| Phase 5 | Ver1.1 判定精度改善 | ⏳ 実データ蓄積後 |
| Phase 6 | Ver2 設計開始 | ⏳ Ver1.1 完成後 |

---

## 17. バージョン管理ルール

### 17-1. バージョン定義

| バージョン | 定義 |
|-----------|------|
| Ver1.x | 判定ロジックの閾値調整のみ |
| Ver2.x | 設計変更・新機能追加・UI 全面変更を含む場合 |

### 17-2. ファイル命名規則

```
app_v1_final.py       # Ver1 本番（凍結）
app_v1_1_final.py     # Ver1.1 本番（閾値調整後）
app_v2_draft.py       # Ver2 開発中
app_v2_final.py       # Ver2 本番
```

### 17-3. バージョンアップの条件

| バージョン | 条件 |
|-----------|------|
| Ver1 → Ver1.1 | 実データ蓄積後、閾値調整のみで精度向上が見込める場合 |
| Ver1.1 → Ver2 | 設計変更・新分析機能・UI 大幅変更が必要な場合 |

### 17-4. リリース前チェックリスト

```
□ python3 -m py_compile でエラーなし
□ 変更禁止事項に触れていないことを確認
□ 変更した関数のみ全文出力・レビュー完了
□ 影響範囲の確認完了
□ 既存機能が壊れていないことを確認
```

---

## 18. Ver2 で予定している内容（未確定）

> **注意:** 以下は現時点での検討候補。実運用データの蓄積後に正式決定する。Ver1 開発中に実装してはならない。

| 候補機能 | 概要 |
|----------|------|
| Match Type 対応 | 一致キーに Match Type を追加（4 キー化） |
| 複数履歴の選択分析 | `cpc_change_history.json` の任意エントリを選択して分析 |
| 分析期間の可変化 | 7 日 / 14 日 / 30 日を選択可能に |
| KW 追加履歴保存 | KW 追加分析にも history 保存を追加 |
| 商品 CPC 分析の完全対応 | `_render_pt_cpc_page` への history 保存追加 |
| ダッシュボード画面 | 全分析結果のサマリーを 1 画面で表示 |
| CSV 自動判定 | After CSV のフォーマットを自動識別 |
| エラーハンドリング強化 | CSV 形式不一致時の詳細エラーメッセージ |

---

*この仕様書は ANIHA Ver1 確定時点の情報を記載している。変更が生じた場合は該当セクションを更新し、更新日・更新理由を冒頭に追記すること。*
