# Phase 1実装タスク一覧と依存関係

## 実装開始条件

- 本設計資料の承認
- 未決定事項のうちBlockerに分類された項目の確定
- Phase 0 fixtureとして利用するAssetの選定

設計承認前にアプリケーションコードを作成しない。

## Workstream A — 基盤と契約

### A1. ドメインモデル

- Project、Asset、ImageVersion、Mask、Guide
- EditOperation、GenerationJob、JobAttempt、Candidate
- Device、Session、PairingCode
- 状態遷移と不変条件

### A2. API・JSON契約

- OpenAPI
- JSON Schema
- SSE event schema
- error code catalog

依存：A1 → A2

## Workstream B — ローカル永続化

### B1. SQLite Repository

- schema migration
- Repository adapter
- adoption compare-and-swap transaction
- idempotency records

### B2. Filesystem Storage

- staging
- PNG検証
- SHA-256
- content-addressed placement
- orphan recovery
- 30日retention、Candidate保護、cleanup preview

依存：A1 → B1、A1 → B2

## Workstream C — 画像処理

### C1. Import normalization

- EXIF orientation
- sRGB
- RGB/RGBA 8bit PNG
- RGBA alphaの全域保持
- original upload preservation
- 832×1216の編集可否判定と基準外サイズ案内

### C2. Strict Composite

- binary mask validation
- inward feather
- mask-inside composite
- mask-outside byte preservation

### C3. Pixel Validator

- outside changed pixels
- max/mean delta
- post-save verification
- diagnostics

依存：B2 → C1、C1 → C2、C2 → C3

C2・C3はComfyUI連携より前にfixtureで完成させる。

## Workstream D — 編集Canvas

### D1. Coordinate core

- Client / Stage / Image変換
- DPR分離
- zoom / pan

### D2. Mask editor

- brush / eraser
- binary rasterization
- Undo/Redo
- inward feather preview

### D3. Guide editor

- color guide
- silhouette guide
- mask clipping

依存：A1 → D1 → D2 → D3、B2 → D2

## Workstream E — Generation Agent

### E1. Queue and state machine

- serial lease
- attempt lifecycle
- cancellation
- timeout

### E2. ComfyUI adapter

- Git管理Workflow manifestとrevision loader
- Workflow JSON SHA-256照合
- モデル初回hash、変更検知、再検証
- health check
- workflow binding
- progress and history
- output retrieval

### E3. Composite pipeline

- Raw Asset registration
- C2/C3 integration
- Candidate creation

依存：A2 → E1、E1 → E2、C3 + E2 → E3

## Workstream F — 制作フローUI

### F1. Project / Asset

- project list
- image import
- current version

### F2. Generation

- preset settings
- queue/progress
- failure/retry/cancel

### F3. Comparison / Adoption

- original/raw/strict
- difference view
- adoption and conflict

### F4. Version History

- version list/detail
- restore as new version

依存：B1 + C1 → F1、D3 + E1 → F2、E3 → F3、F3 → F4

## Workstream G — スマホと認証

### G1. Local owner session

### G2. Pairing code and device session

### G3. LAN HTTPSと証明書案内

- Character Studio専用ローカル認証局
- Mac内の秘密鍵保護
- 安定したLAN内ホスト名とサーバー証明書
- スマホの初回信頼設定と指紋照合
- 証明書更新・再登録案内

### G4. Responsive editor

### G5. Disconnect/reconnect recovery

依存：A2 + B1 → G1 → G2、G2 → G3、D3 + F2 + G3 → G4、E1 + G4 → G5

## Workstream H — 検証

### H1. Unit / property tests

### H2. Contract / integration tests

### H3. Playwright PC / mobile E2E

### H4. Mac M4実ComfyUI smoke test

依存：各Workstreamと並行。H4はE3、F3、C3完了後。

## 推奨マイルストーン

### M1 — 不変Assetと画素保証

A1、B1、B2、C1、C2、C3を完了。ComfyUIなしで厳密保持0画素を証明する。

### M2 — 編集Workspace

D1、D2、D3を完了。PC・スマホviewportで同じマスク正本が作られることを確認する。

### M3 — 生成と比較

E1〜E3、F1〜F3を完了。fake Agentから実ComfyUIへ段階的に切り替える。

### M4 — 版・競合・復元

F4、複数端末競合、復元を完了する。

### M5 — スマホ実機・安定化

G1〜G5、H1〜H4を完了し、Phase 1完了基準を評価する。

## Phase 1完了条件

- PCと同一LAN内スマホ実機で制作フローを完了できる。
- 編集・生成入力を832×1216に限定し、別サイズを暗黙変換しない。
- ComfyUIはlocalhost限定である。
- 直列キュー、キャンセル、失敗、再実行が動作する。
- Raw Outputを直接採用できない。
- Strict Outputのマスク外変更が0画素である。
- 競合候補を自動採用しない。
- 版復元で履歴を削除しない。
- Phase 0相当の色変更と構造追加が成功する。
- 実測時間、メモリ、画質、保持違反、手動修正を記録できる。
- 失敗・不採用Assetを30日後に安全に整理でき、保護・採用・参照中Assetを削除しない。

## 後続Phaseへ送る項目

- インターネット経由のクラウド接続
- PostgreSQL / Supabase Storage adapter
- 複数ユーザーと共有
- 三面図生成
- Blender向け制作資料
- 高度なControlNet・参照拘束
- 自動マスク、部位認識
