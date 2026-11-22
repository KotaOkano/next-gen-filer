# NextGenFiler

Windows標準のエクスプローラーに代わる、高速・多機能なデスクトップファイラーアプリです。
Python と PySide6 で構築されており、ローカルファイルの操作だけでなく、SFTP や共有フォルダ（SMB）への接続・ファイル暗号化機能などを備えています。

---

## 主な機能
* **快適なファイル操作**: コピー、移動、リネーム、削除などの基本操作に加え、新しいフォルダ / テキストファイルの作成、戻る / 進む (履歴) に対応
* **大きなファイルに強いコピー**: チャンク転送で進捗表示と途中キャンセルに対応し、中断しても中途半端なファイルが残りません
* **ネットワーク接続**: SFTP（ホスト鍵の確認・秘密鍵認証対応）および SMB（Windows共有フォルダ）への接続サポート
* **セキュリティ機能**: ファイルの暗号化・復号ツール搭載
* **プレビュー機能**: 画像・テキストに加え、PDF や HTML コンテンツの閲覧サポート (Quick Look)

---

## 使い方（一般ユーザー・すぐ使いたい方向け）

Pythonのインストール不要で、以下の手順ですぐに利用できます。

1. **ZIPのダウンロード**：[最新リリースページ](https://github.com/KotaOkano/next-gen-filer/releases/latest)のAssetsから `NextGenFiler_配布用.zip` を取得（※緑色の「Code」ボタンのZIPは非推奨）
2. **解凍**：ダウンロードしたZIPを任意の場所へ展開
3. **起動**：`NextGenFiler.bat` をダブルクリック（初回は数分の自動セットアップあり）
4. **ショートカット作成**（任意）：`NextGenFiler.bat` からデスクトップ等へショートカットを作成

---

## 開発者向けセットアップ（GitHub からクローンする場合）

簡単な方法：クローンしたフォルダで `NextGenFiler.bat` をダブルクリックすると、システムの Python で仮想環境 (.venv) を自動作成して起動します（Python 3.12 以上が必要）。

手動で行う場合は以下の通りです。

1. リポジトリをクローンします。
   ```bash
   git clone https://github.com/KotaOkano/next-gen-filer.git
   cd next-gen-filer
   ```
2. 仮想環境を作成し、依存ライブラリをインストールします。
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. アプリを起動します。
   ```bash
   python main.py
   ```

---

## 使用技術・サードパーティ製ライブラリ

本ソフトウェアは以下のオープンソースライブラリを使用しています。

* **PySide6** (Qt for Python) - LGPLv3
* **Paramiko** - LGPLv2.1
* **smbprotocol** - MIT License
* **cryptography** - Apache-2.0 / BSD-3-Clause
* **pywin32** - PSF License

---

## ライセンス・免責事項

* 本ソフトウェアは **MIT License** のもとで公開されています。詳細は [LICENSE](LICENSE) ファイルをご覧ください。
* 本ソフトウェアの使用によって生じたデータの損失やトラブル等について、開発者は一切の責任を負いかねます。重要なファイルの操作時はバックアップを取ることを推奨します。
