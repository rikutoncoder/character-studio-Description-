# Character Studio

Character Studioは、アニメ調キャラクターの素体生成、衣装編集、デザイン固定、三面図、Blender向け制作資料の出力を目的とするWebアプリです。

Phase 1の詳細設計は承認済みで、M1の画像整合性コアを実装しています。

## Documentation

- [Phase 1詳細設計](./docs/phase1/README.md)
- [M1実装報告](./docs/phase1/12-m1-implementation-report.md)

## Current status

- Phase 0技術検証：完了
- Phase 1詳細設計：承認済み
- Phase 1実装：M1画像整合性コア完了

Phase 1では、確定済みデザインを壊さない仕組みを最優先し、不変Asset、SQLite保存、画像正規化、厳密保持合成、画素検証から実装します。

AI生成UIとComfyUI連携はまだ実装していません。
