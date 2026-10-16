# M1実装報告

## 状態

- 実装状態：完了
- 検証日：2026-10-17
- 対象：画像整合性コア
- AI生成UI：未着手
- ComfyUI接続：未着手

## 実装した機能

### SQLite

- Project、Asset、ImageVersion、Mask、CompositeResult
- 外部契約へSQLite ROWIDを露出しないUUID
- foreign keyとWAL
- ImageVersion、Mask、CompositeResultの不変化
- Asset内容メタデータの更新禁止
- Projectの先頭版更新

### 不変Asset

- SHA-256によるcontent-addressed保存
- staging書き込みと原子的な確定移動
- 同一内容の重複排除
- 読み込み時のSHA-256再検証
- 保存領域外を参照するURIの拒否

### 画像正規化

- EXIF Orientation適用
- ICC profileからsRGBへの変換
- 8bit RGB/RGBA PNG
- 元ファイルとCanonical Imageの分離
- 832×1216だけを編集可能として登録
- 基準外サイズは元ファイルだけを不変保存
- palette PNGの透明情報をRGBAへ保持

### 厳密保持合成

- 元画像、AI生出力、二値マスクの寸法確認
- 空、全面、非二値マスクの拒否
- 0〜64pxの内向きフェザー
- マスク外を元画像からbyte単位で維持
- RGBA alphaを画像全体で元画像から維持
- PNG保存後の再読込検証

### 画素検証

- マスク外変更画素数
- マスク外最大差
- マスク外平均絶対差
- alpha変更画素数
- いずれかが0以外ならCompositeResultを登録しない

## 自動テスト結果

```text
Ran 17 tests
OK
```

確認項目：

- SQLite再起動後の永続性
- Asset内容メタデータの更新拒否
- SHA-256改ざん検知
- RGBハードマスク
- 内向きフェザー
- RGBA alpha全域保持
- 非二値マスク拒否
- マスク外1画素変更の検出
- 20個のランダムマスクでマスク外0画素
- 832×1216の一連のimport、mask、composite、保存
- 基準外画像の元ファイル保持
- 警告をエラー扱いにした全テスト
- Python構文コンパイル

## 実行方法

PillowとNumPyを利用できるPython環境で、リポジトリルートから実行する。

```bash
PYTHONPATH=services/generation-agent/src \
python3 -m unittest discover -s services/generation-agent/tests -v
```

## M1で意図的に未実装の機能

- Web UI
- AI生成UI
- ComfyUI接続
- Job queue
- Candidate採用UI
- クラウドストレージ
- Supabase
- 三面図とBlender資料

次の実装では、本コアを変更せず上位のApplication APIから利用する。
