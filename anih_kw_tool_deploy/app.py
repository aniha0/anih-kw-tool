Confirmed correct, single intended diff. Now let's run full AST diff and real-data checks.実装完了しました。確認結果です。

**①事前確認**
- 対象関数：なし（`with st.sidebar:`ブロック内、モジュールレベルのサイドバー構築コード。関数の中身は一切変更していません）
- 変更予定行：サイドバーのCPC調整セクション内、`_nav_btn`呼び出し2行のみ
- 表示変更のみで完結：ルーティングキー（`"📹 動画CPC調整"` `"📹 SB動画CPC調整"`）はそのまま維持し、表示ラベル（第1引数）と2行の記述順だけを変更しました。`page_cpc_video()`・`page_cpc_sbvideo()`の中身、`_PAGE_FUNCS`・`_VALID_PAGES`・`_CPC_PAGES`のキー文字列は無変更です。

**②実装後確認**
① app.py出力 → 実施
② md5 → `d23f023c546a6674661d10fe5d26012e`
③ py_compile → OK
④ AST diff → 関数レベル変更0件（`ADDED: []` `REMOVED: []` `CHANGED: []`）。top-level構造比較でも変更は`with st.sidebar:`ブロック1箇所のみで、他222個のトップレベル要素は完全一致
⑤ 変更関数一覧 → なし（関数外のサイドバー構築コードのみ）
⑥ 変更行数 → 2行（`_nav_btn("動画",...)`→`_nav_btn("動画KW",...)`、`_nav_btn("SB動画",...)`→`_nav_btn("動画商品",...)`、順序も入れ替え）
⑦ DataFrame生成ロジック変更 → 0件（`_mask_v`・`_sb_video_kw_mask`・`_build_pt_cpc_df`等は無変更を確認済み）

なお`_PAGE_FUNCS`辞書（7051〜7052行目）・`_VALID_PAGES`/`_CPC_PAGES`集合（386・396行目）に元のキー文字列がそのまま残っていることも実際にgrepで再確認しています。

引き続き保存フォルダの`app.py`という名前が使えない状態のため、`app_latest3.py`として保存しています。お手数ですが同様にリネームをお願いします。
