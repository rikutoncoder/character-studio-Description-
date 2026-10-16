# データベース・JSONスキーマ設計

## 基本方針

- IDはアプリケーション側で生成するUUIDとする。
- 日時はUTCのISO 8601で扱う。
- 画像バイナリはSQLiteへ保存しない。
- Asset、確定版、生成試行は不変とし、更新ではなく追加する。
- SQLite固有のROWIDを外部契約へ露出しない。
- 可変設定を無制限なJSON列へ集約せず、検索・整合性に必要な項目は列として持つ。
- 将来PostgreSQLへ移行できる型の範囲を使う。

## エンティティ

### projects

- `id`: UUID、主キー
- `name`: string
- `character_id`: UUID
- `head_version_id`: UUID、nullable
- `created_at` / `updated_at`: timestamp

`head_version_id`の更新は候補採用トランザクション内で比較交換する。

### characters

- `id`: UUID
- `display_name`: string
- `metadata_json`: 将来の身体・部位情報を格納する版付きJSON
- `created_at` / `updated_at`

### assets

- `id`: UUID
- `kind`: `ORIGINAL_UPLOAD | CANONICAL_IMAGE | MASK | GUIDE | RAW_OUTPUT | STRICT_OUTPUT | PREVIEW`
- `storage_uri`: string
- `sha256`: 64桁hex
- `byte_size`: integer
- `mime_type`: Phase 1正本は`image/png`
- `width` / `height`: integer
- `channels`: `RGB | RGBA | L`
- `color_space`: 正本は`sRGB`
- `alpha_policy`: RGBA正本は`PRESERVE_SOURCE`
- `source_asset_id`: UUID、nullable
- `retention_class`: `PERMANENT | TEMPORARY_30_DAYS`
- `expires_at`: timestamp、nullable
- `protected_at`: timestamp、nullable
- `created_at`

Assetレコードの内容は更新しない。プレビューは元Assetと別Assetにする。

採用済み画像、元画像、採用結果を再現するために必要なMask・Guide・設定参照は`PERMANENT`とする。失敗出力と不採用Candidateの専用Assetは`TEMPORARY_30_DAYS`とし、保護された場合は期限削除の対象外とする。

### image_versions

- `id`: UUID
- `project_id`: UUID
- `parent_version_id`: UUID、nullable
- `restored_from_version_id`: UUID、nullable
- `canonical_asset_id`: UUID
- `adopted_candidate_id`: UUID、nullable
- `ordinal`: project内表示順
- `created_at`

Phase 1のUIは一本の先頭版を表示するが、親参照を保持して将来の分岐表現を可能にする。

### layers

- `id`: UUID
- `workspace_id`: UUID
- `kind`: `BASE | MASK | COLOR_GUIDE | SILHOUETTE_GUIDE | REFERENCE`
- `asset_id`: UUID、nullable
- `name`: string
- `z_index`: integer
- `visible`: boolean
- `opacity`: 0〜1
- `blend_mode`: Phase 1は許可値を限定
- `transform_json`: 画像座標系のアフィン変換
- `created_at` / `updated_at`

確定版画像のLayer構成と、編集中WorkspaceのLayer構成を混同しない。

### masks

- `id`: UUID
- `source_version_id`: UUID
- `asset_id`: UUID、二値L PNG
- `feather_inward_px`: integer
- `editable_pixel_count`: integer
- `bounding_box_json`: integer pixel bounds
- `created_at`

### guides

- `id`: UUID
- `source_version_id`: UUID
- `asset_id`: UUID
- `kind`: `COLOR | SILHOUETTE`
- `mask_id`: UUID
- `parameters_json`: 版付きJSON
- `created_at`

### edit_operations

- `id`: UUID
- `project_id`: UUID
- `source_version_id`: UUID
- `operation_type`: `RECOLOR | STRUCTURE_ADD`
- `mask_id`: UUID
- `guide_id`: UUID、nullable
- `prompt` / `negative_prompt`: string
- `preset_snapshot_json`: 生成開始時点の完全な設定
- `created_at`

### generation_jobs

- `id`: UUID
- `edit_operation_id`: UUID
- `source_version_id`: UUID
- `status`: job status enum
- `idempotency_key`: string、unique scopeを定義
- `requested_by_session_id`: UUID
- `created_at` / `updated_at`

### workflow_revisions

- `id`: 安定したrevision ID
- `operation_type`: `RECOLOR | STRUCTURE_ADD`
- `repository_path`: Git管理下の相対パス
- `file_sha256`: Workflow JSONのSHA-256
- `parameter_schema_version`: integer
- `created_at`

Workflow JSON自体はGitを正本とし、DBには実行時の照合情報を保存する。公開済みrevisionの内容は変更せず、変更時は新しいrevisionを追加する。

### model_files

- `id`: UUID
- `display_name`: string
- `local_path_ref`: Generation Agent内部だけで解決する参照
- `file_sha256`: 64桁hex
- `byte_size`: integer
- `modified_at_observed`: timestamp
- `verified_at`: timestamp
- `status`: `VERIFIED | CHANGED | MISSING | INVALID`

初回登録時に全ファイルのSHA-256を計算する。以後はファイルサイズや更新日時などの変更を検知した場合に再計算し、通常Jobでは検証済みハッシュを記録する。ローカル絶対パスはブラウザ向けAPIへ返さない。

### job_attempts

- `id`: UUID
- `job_id`: UUID
- `attempt_number`: integer
- `status`: attempt status enum
- `seed`: 64bit整数を文字列化して契約してもよい
- `model_name` / `model_hash`: string
- `workflow_revision`: string
- `workflow_file_sha256`: 64桁hex
- `sampler` / `scheduler`: string
- `steps`: integer
- `cfg` / `denoise`: number
- `started_at` / `finished_at`: nullable
- `duration_ms`: nullable
- `raw_output_asset_id`: nullable
- `strict_output_asset_id`: nullable
- `error_code` / `error_detail`: nullable
- `metrics_json`: 検証済みの版付きJSON

### candidates

- `id`: UUID
- `job_attempt_id`: UUID
- `source_version_id`: UUID
- `raw_asset_id`: UUID
- `strict_asset_id`: UUID
- `validation_status`: `PENDING | PASSED | FAILED`
- `review_status`: `REVIEW_PENDING | ADOPTED | REJECTED | STALE`
- `outside_changed_pixels`: integer
- `outside_max_delta`: integer
- `outside_mean_absolute_delta`: number
- `adopted_version_id`: UUID、nullable
- `created_at` / `reviewed_at`: nullable

### devices / sessions / pairing_codes

`devices`は端末名、公開識別子、登録日時、失効日時を持つ。`sessions`はハッシュ化したトークン参照、有効期限、最終利用日時を持つ。セッションは最終利用から30日間有効なrolling expirationとし、利用時に期限を更新する。Mac側で端末を失効した場合は、その端末の全セッションを無効化する。`pairing_codes`は短時間有効、単回使用、試行回数制限付きとし、平文コードを永続保存しない。

## JSON契約例

### GenerationPresetSnapshot

```json
{
  "schemaVersion": 1,
  "preset": "RECOLOR_GUIDED",
  "model": {
    "name": "Animagine XL 3.1",
    "sha256": "UNRESOLVED_UNTIL_RECORDED"
  },
  "sampling": {
    "sampler": "euler_ancestral",
    "scheduler": "normal",
    "steps": 20,
    "cfg": 7.0,
    "denoise": 0.30,
    "seed": "271828186"
  },
  "compositing": {
    "maskId": "uuid",
    "featherInwardPx": 3
  }
}
```

### VersionSummary

```json
{
  "schemaVersion": 1,
  "id": "uuid",
  "projectId": "uuid",
  "parentVersionId": "uuid-or-null",
  "restoredFromVersionId": "uuid-or-null",
  "canonicalAssetId": "uuid",
  "ordinal": 12,
  "createdAt": "2026-10-16T00:00:00Z"
}
```

## 整合性規則

- `Mask.source_version_id`の寸法は、その版の正本画像寸法と一致しなければならない。
- Phase 1でImageVersionの正本にできるCanonical Imageは832×1216だけとする。
- 別サイズのOriginal Upload Assetは保存可能だが、ImageVersion、Mask、Guide、Generation Jobの入力に使用できない。
- Candidateのsource、raw、strict、maskは同一寸法でなければならない。
- RGBA正本から作るStrict Outputのalpha channelは、マスク内外を問わず元画像と完全一致しなければならない。
- `validation_status=PASSED`は、マスク外変更0画素の場合だけ許可する。
- `review_status=ADOPTED`は`validation_status=PASSED`の場合だけ許可する。
- 採用時は`candidate.source_version_id`、要求の`expected_head_version_id`、`project.head_version_id`の3つが一致しなければならない。
- 一つのCandidateを複数回採用しない。
- JobAttemptのworkflow revisionとfile hashは、実行したGit管理JSONと一致しなければならない。
- JobAttemptのmodel hashは、実行時に検証済みだったModel Fileのhashと一致しなければならない。
- Projectの先頭版更新とImageVersion作成は同じDBトランザクションで行う。
- ファイルが存在し、ハッシュが一致することを確認するまでAssetを利用可能にしない。
- 永続Assetから参照されるAssetを期限削除してはならない。
- 期限削除はCandidateが未採用で、保護されておらず、30日を経過した場合だけ許可する。

## ファイルとDBの疑似トランザクション

1. `staging`へ一時ファイルを書き込む。
2. PNG形式、寸法、チャンネル、色空間、SHA-256を検証する。
3. 内容ハッシュに基づく確定パスへ原子的に移動する。
4. DBトランザクションでAssetと関連レコードを登録する。
5. DB登録に失敗した未参照ファイルは、即時削除せず回収対象として記録する。

DB先行でファイルを登録してはならない。
