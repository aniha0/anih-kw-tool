# Streamlit Community Cloud デプロイ手順書

ブラウザのURLだけでどこからでも使える状態にするための手順です。
所要時間：約20〜30分（初回のみ）

---

## 事前準備

以下のアカウントが必要です（すべて無料）。

| サービス | URL | 用途 |
|----------|-----|------|
| GitHub | https://github.com | コードの置き場所 |
| Streamlit Community Cloud | https://share.streamlit.io | アプリの公開場所 |

---

## ステップ1：GitHubにリポジトリを作成する

1. https://github.com にアクセスしてログイン
2. 右上の **「+」→「New repository」** をクリック
3. 以下を入力する

   | 項目 | 入力値 |
   |------|--------|
   | Repository name | `anih-kw-tool`（任意） |
   | 公開設定 | **Public**（Streamlit無料プランの要件） |
   | Initialize with README | チェックを**外す** |

4. **「Create repository」** をクリック

---

## ステップ2：ファイルをGitHubにアップロードする

### 方法A：ブラウザから直接アップロード（Git不要・推奨）

1. 作成したリポジトリのページを開く
2. **「uploading an existing file」** リンクをクリック
3. 以下の4ファイルを**まとめてドラッグ＆ドロップ**する

   ```
   app.py
   requirements.txt
   README.md
   .gitignore
   ```

4. ページ下部の **「Commit changes」** をクリック

> ⚠️ `.gitignore` はファイル名が「.」から始まるためFinderで非表示になる場合があります。
> その場合は `Cmd + Shift + .` で隠しファイルを表示してください。

### 方法B：Git CLIを使う場合

```bash
git init
git add app.py requirements.txt README.md .gitignore
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/あなたのユーザー名/anih-kw-tool.git
git push -u origin main
```

---

## ステップ3：Streamlit Community Cloudでデプロイする

1. https://share.streamlit.io にアクセス
2. **「Sign in with GitHub」** でGitHubアカウントでログイン
3. **「New app」** をクリック
4. 以下を入力する

   | 項目 | 入力値 |
   |------|--------|
   | Repository | `あなたのユーザー名/anih-kw-tool` |
   | Branch | `main` |
   | Main file path | `app.py` |
   | App URL（任意） | 好きな名前（例：`anih-kw-tool`） |

5. **「Deploy!」** をクリック

デプロイには2〜3分かかります。完了すると以下のようなURLでアクセスできます。

```
https://あなたが決めた名前.streamlit.app
```

---

## ステップ4：動作確認

1. 表示されたURLをブラウザで開く
2. 検索用語CSV・キャンペーンCSV・ラッコCSVをアップロード
3. 「勝てるKW抽出」をクリックして結果が出ればデプロイ成功

---

## アプリを更新したいとき

コードを変更したら、GitHubにファイルをアップロードし直すだけで
Streamlit Cloudが自動で再デプロイします。

1. GitHubのリポジトリを開く
2. `app.py` をクリック → 鉛筆アイコン（編集）をクリック
3. 内容を変更して **「Commit changes」**
4. 数分後に自動で反映される

---

## よくあるトラブル

| 症状 | 原因 | 対処 |
|------|------|------|
| 「ModuleNotFoundError」 | requirements.txtの記載漏れ | requirements.txtにモジュールを追加してcommit |
| デプロイが止まる | requirements.txtがない | ファイルがリポジトリにあるか確認 |
| アプリが開かない | URLが間違っている | Streamlit Cloudのダッシュボードで正しいURLを確認 |
| CSVが読めない | 文字コードの問題 | UTF-8（BOM付き）で保存されているか確認 |

---

## セキュリティについて

- アップロードされたCSVはサーバーに**保存されません**（メモリ上で処理）
- セッションを閉じると自動的に消去されます
- Amazonの広告データ（売上・費用等）を含むため、URLの共有範囲に注意してください

---

## フォルダ構成（最終形）

```
anih-kw-tool/          ← GitHubリポジトリのルート
├── app.py             ← メインアプリ（Streamlit）
├── requirements.txt   ← 必要なライブラリ一覧
├── README.md          ← アプリの説明
└── .gitignore         ← Gitに含めないファイルの設定
```
