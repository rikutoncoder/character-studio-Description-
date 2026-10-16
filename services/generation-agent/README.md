# Generation Agent — M1 image integrity core

M1は、AI生成UIやComfyUI接続より先に「確定済みデザインを壊さない仕組み」を実装する。

## Implemented scope

- SQLiteによるProject、Asset、ImageVersion、Mask、CompositeResult管理
- SHA-256によるcontent-addressed不変Asset保存
- EXIF OrientationとICC profileを考慮した8bit sRGB PNG正規化
- 832×1216だけを編集可能なCanonical Imageとして登録
- 二値マスクと内向きフェザーによる厳密保持合成
- マスク外変更0画素の保存後検証
- RGBA alpha channelの全域保持
- 固定ケースとランダムマスクの自動テスト

## Run tests

リポジトリルートから、PillowとNumPyを含むPython環境で実行する。

```bash
PYTHONPATH=services/generation-agent/src \
python3 -m unittest discover -s services/generation-agent/tests -v
```

実行時Assetは`runtime-data/`以下へ保存し、Gitには追加しない。
