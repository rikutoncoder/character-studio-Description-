# M2 画像編集Workspace 実装記録

## 範囲と依存

M1の不変Asset、ImageVersion、Mask、SHA-256検証、画像正規化、厳密保持合成は変更しない。M2は生成前の編集状態を`workspaces`テーブルに別管理する。ComfyUI接続、Job、Candidate採用は含めない。

- `services/generation-agent/src/character_studio/workspace.py`: Workspaceの保存・検証・revision比較交換
- `services/generation-agent/src/character_studio/internal_api.py`: `127.0.0.1`専用、共有シークレット必須の内部HTTPアダプタ
- `apps/web`: Next.js Application API経由の編集画面。ブラウザからPythonへ直結しない

## SQLite v2 migrationと保存契約

`schema_migrations`のv2で`workspaces`を追加する。`project_id`と`base_version_id`を固定し、`revision`を1から開始する。各保存では新しい内容アドレス付きPNG Assetを登録し、`UPDATE ... WHERE id=? AND revision=?`の結果が1件の場合だけWorkspace参照を進める。競合はHTTP 409で返す。M1の`assets`内容、`image_versions`、`masks`、project headを更新しない。

JSONの`schemaVersion`は1、寸法は832×1216。`mask`は8-bit Lの生画素をbase64で転送し、サーバーで0/255と寸法を検証した後、正本L PNGに保存する。`colorGuide`と`silhouetteGuide`もRGBA生画素をbase64転送してPNGに保存する。マスク外alphaは0を強制する。`featherInwardPx`はPNGへ焼き込まず0〜64の独立値、初期値3。レイヤーは4種類固定で表示・不透明度を保存する。空・全面マスクはWorkspaceでは許可し、生成用の不変Mask作成時はM1が拒否する。

## ブラウザ側の編集ルール

React Konvaは表示だけに使用する。Pointer EventsのCSS座標から画像座標へ変換し、`raster.ts`が整数画素グリッドへ線分距離でラスタライズする。DPRや現在のズーム値はラスタ処理に入力しない。一本指は描画、二本目が入ると未確定ストロークを破棄してピンチ操作に移る。Pencil筆圧は使わない。

編集単位の差分PatchでUndo/Redoする。マスクの消去時、同じ画素の2ガイドも同じPatchで消し、Undoでまとめて復元する。履歴上限は100操作または差分32MiBの先着。再読み込み後は履歴を復元せず、保存済み状態を起点にする。

ストロークやレイヤー変更から750msで自動保存し、手動保存もできる。未保存の編集はIndexedDBに復旧用スナップショットを置き、再開時には確認を求める。409では編集内容を破棄せず、最新版の読み直しまたは別Workspaceへの保存を選べる。自動マージ・自動上書きはしない。

## 起動・検証

1. `services/generation-agent`をPythonのimportパスに設定し、`CHARACTER_STUDIO_INTERNAL_TOKEN`（24字以上）と`CHARACTER_STUDIO_DATA_DIR`を設定して`python -m character_studio.internal_api`を起動する。内部APIは常に`127.0.0.1:8765`へbindする。
2. `apps/web`で同じトークンを`.env.local`に設定し、`pnpm install`、`pnpm dev`を実行する。既定のNext.jsサーバーもlocalhostだけへbindする。
3. `pnpm test`、`pnpm build`、`pnpm test:e2e`を実行する。Pythonは`python -m unittest discover -s tests -v`でM1を含めて回帰試験する。

LANへ提供する場合は、承認済みAPI設計のHTTPS/端末認証を先に満たす必要がある。M2のローカル開発サーバーをそのままLANへ公開しない。実機iPhone Safariでの手触り、二本指、IndexedDBの復旧、画面回転は実機検証が必要であり、エミュレーションのみで合格扱いにしない。

## 残る検証

- 実機iPhone Safari、およびSafari/Chrome/Edgeの現行・直前メジャー版での操作
- HTTPSと端末認証を備えたLAN提供（承認済みPhase 1の別スコープ）
- 大きなストロークでの描画と保存時間、IndexedDB容量の実測

## 2026-10-17 検証結果

- Python unittest: 22件合格（既存M1の17件を含む）
- Vitest: 6件合格
- Playwright: デスクトップChromium 4件、スマホ相当Chromium 4件、iPhone相当WebKit 4件、計12件合格
- Next.js production build・TypeScript検査: 合格
- Playwright WebKit 26.0: 72.6MiBのブラウザ実行ファイルをユーザー承認後に導入し、4件合格
- 実機iPhone Safari: 未実施。合格判定には追加確認が必要
