# ディレクトリ構成案

## リポジトリ構成

```text
apps/
└── web/
    ├── src/app/                  # Next.js routing、pages、route handlers
    ├── src/features/             # project、editor、jobs、history、devices
    ├── src/components/           # 汎用UI
    ├── src/server/application/   # ユースケース
    ├── src/server/infrastructure/# SQLite、filesystem、agent adapters
    └── tests/

services/
└── generation-agent/
    ├── src/application/          # job execution use cases
    ├── src/domain/               # agent固有状態と検証規則
    ├── src/adapters/comfyui/     # ComfyUI APIとworkflow binding
    ├── src/imaging/              # strict composite、pixel validation
    ├── workflows/
    │   ├── manifest.json         # revision、用途、対応パラメータ、hash
    │   └── {revision}/workflow.json
    └── tests/

packages/
├── domain/                       # 共有ドメイン型と不変条件
├── contracts/                    # OpenAPI、JSON Schema、event schema
├── canvas-core/                  # 座標変換、mask commands、geometry
└── test-fixtures/                # 小型の固定画像・mask・応答

docs/
├── phase0/
└── phase1/

runtime-data/                     # Git管理外
├── database/
├── assets/
├── staging/
├── previews/
├── jobs/
└── logs/
```

## Web内のfeature分割

```text
features/
├── projects/
├── assets/
├── editor/
│   ├── canvas/
│   ├── layers/
│   ├── masks/
│   ├── guides/
│   └── history/
├── generation/
├── comparison/
├── versions/
└── devices/
```

featureからSQLiteやファイルシステムへ直接アクセスしない。サーバーユースケースまたは契約化されたAPIを介する。

## Runtime Asset配置

Asset IDをUUID、内容識別をSHA-256とし、同一ディレクトリへの集中を避ける。

```text
runtime-data/assets/{sha256-prefix}/{sha256}.{extension}
runtime-data/staging/{operation-id}/{temporary-file}
runtime-data/jobs/{job-id}/{attempt-number}/
```

確定前のファイルは`staging`へ書き、形式、寸法、ハッシュを検証してから`assets`へ確定移動する。DBへは確定後のURIだけを登録する。

## 禁止する構成

- React componentからComfyUIを直接呼ぶ。
- Zustandを確定データの唯一の保存先にする。
- API route内へ版管理規則や画像合成ロジックを直接記述する。
- `public/`以下へユーザー画像を配置する。
- 確定Assetと一時ファイルを同じディレクトリで上書き運用する。
- ComfyUIワークフローJSONをコード内の巨大文字列として埋め込む。
- 公開済みWorkflow revisionのJSONを同じrevision名のまま変更する。
