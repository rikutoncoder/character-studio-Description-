# API契約案

## 方針

- ブラウザ向けはREST JSONを基本とする。
- 進捗通知はServer-Sent Eventsを使う。
- OpenAPIを契約の正本候補とし、TypeScriptとPythonで共有する。
- APIレスポンスにSQLiteのROWIDやローカル絶対パスを含めない。
- 破壊的更新より、不変Resourceの作成を優先する。
- すべてのエラーに安定した`code`を付ける。
- LAN向けブラウザAPIはHTTPSだけで提供する。
- HTTPでは認証Cookieを受け付けず、Assetや編集APIを返さない。

## 共通形式

```json
{
  "data": {},
  "meta": {
    "requestId": "uuid"
  }
}
```

エラー：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "The project head changed after generation started.",
    "details": {
      "expectedHeadVersionId": "uuid",
      "actualHeadVersionId": "uuid"
    }
  },
  "meta": {
    "requestId": "uuid"
  }
}
```

## Project / Version

```text
POST /api/projects
GET  /api/projects
GET  /api/projects/{projectId}
GET  /api/projects/{projectId}/versions
GET  /api/versions/{versionId}
POST /api/versions/{versionId}/restore
```

復元要求例：

```json
{
  "projectId": "uuid",
  "expectedHeadVersionId": "uuid",
  "idempotencyKey": "client-generated-uuid"
}
```

復元は新しいImageVersionを返す。

## Asset

```text
POST /api/assets/imports
GET  /api/assets/{assetId}/metadata
GET  /api/assets/{assetId}/content
```

importは元アップロードAssetと正規化Canonical Assetを返す。大容量ファイルの上限、許可形式、寸法をサーバーで検証する。

832×1216以外はOriginal Upload Assetとして保存できるが、編集可能なCanonical Assetを作成しない。暗黙のリサイズを行わず、`EDITABLE_DIMENSION_REQUIRED`と実寸法を返す。

RGBA正本には`alphaPolicy: "PRESERVE_SOURCE"`を付与する。Phase 1の生成・採用APIから別のalpha policyを指定できない。

## Mask / Guide

```text
POST /api/masks
GET  /api/masks/{maskId}
POST /api/guides
GET  /api/guides/{guideId}
```

Mask作成要求には`sourceVersionId`、二値PNG、`featherInwardPx`を含める。サーバーで寸法と二値性を再検証する。

## Generation Job

```text
POST /api/generation-jobs
GET  /api/generation-jobs/{jobId}
GET  /api/generation-jobs/{jobId}/events
POST /api/generation-jobs/{jobId}/cancel
POST /api/generation-jobs/{jobId}/retry
```

作成要求例：

```json
{
  "projectId": "uuid",
  "sourceVersionId": "uuid",
  "maskId": "uuid",
  "guideId": "uuid",
  "operationType": "RECOLOR",
  "prompt": "blue torso garment",
  "negativePrompt": "white torso panel",
  "preset": {
    "name": "RECOLOR_GUIDED",
    "steps": 20,
    "cfg": 7.0,
    "denoise": 0.30,
    "seed": "271828186"
  }
}
```

HTTP `Idempotency-Key`を必須とする。

## Candidate

```text
GET  /api/candidates/{candidateId}
POST /api/candidates/{candidateId}/adopt
POST /api/candidates/{candidateId}/reject
POST /api/candidates/{candidateId}/protect
DELETE /api/candidates/{candidateId}/protect
```

採用要求：

```json
{
  "expectedHeadVersionId": "uuid",
  "idempotencyKey": "client-generated-uuid"
}
```

採用前に、Candidateが検証合格、未採用、未却下、未STALEであり、Candidateの`sourceVersionId`、要求の`expectedHeadVersionId`、現在のProject先頭版IDがすべて一致することを確認する。競合時はHTTP 409と`VERSION_CONFLICT`を返す。

保護操作はCandidateを採用するものではなく、30日後の自動削除対象から外す。採用済みCandidateは保護操作の有無にかかわらず自動削除しない。

## Storage maintenance

```text
GET  /api/storage/summary
GET  /api/storage/expiring-assets
POST /api/storage/cleanup
```

cleanupは30日を経過した未保護・未採用・失敗Assetだけを対象とする。確定版や再現に必要なAssetを含む場合はサーバー側で拒否する。

## Device / Pairing

```text
POST   /api/pairing-codes
POST   /api/device-sessions/pair
GET    /api/devices
DELETE /api/devices/{deviceId}/sessions
```

ペアリングコード生成はMac上のオーナーセッションだけに許可する。コードは短時間・単回使用とし、レート制限する。

端末セッションはSecure、HttpOnly、SameSite属性を持つCookieでブラウザへ渡し、サーバー側にはトークン平文ではなく検証用hashだけを保存する。期限は最終利用から30日間で、利用時に更新する。端末失効後は既存Cookieが残っていても認証を拒否する。

## SSEイベント

```json
{
  "eventId": "uuid",
  "type": "job.status.changed",
  "jobId": "uuid",
  "occurredAt": "2026-10-16T00:00:00Z",
  "payload": {
    "previous": "QUEUED",
    "current": "LEASED"
  }
}
```

クライアントは未知のイベントtypeを無視できなければならない。SSEは状態の唯一の正本ではなく、再接続時はGET APIで現在状態を取得する。

## Generation Agent内部契約

ローカルPhase 1ではlocalhost限定の内部APIまたはプロセス間契約を使う。

```text
POST /internal/jobs/{jobId}/lease
POST /internal/attempts/{attemptId}/events
POST /internal/attempts/{attemptId}/artifacts
POST /internal/attempts/{attemptId}/complete
POST /internal/attempts/{attemptId}/fail
```

将来クラウド化する際は、Mac Agentが外向き通信でleaseする形へ変更する。外部からMac Agentへ到達する前提をAPI契約に入れない。

## 代表的エラーコード

- `AUTH_REQUIRED`
- `HTTPS_REQUIRED`
- `DEVICE_REVOKED`
- `PAIRING_CODE_INVALID`
- `ASSET_NOT_FOUND`
- `ASSET_HASH_MISMATCH`
- `IMAGE_FORMAT_UNSUPPORTED`
- `IMAGE_DIMENSION_MISMATCH`
- `EDITABLE_DIMENSION_REQUIRED`
- `MASK_EMPTY`
- `MASK_NOT_BINARY`
- `PRESET_INVALID`
- `WORKFLOW_REVISION_NOT_FOUND`
- `WORKFLOW_HASH_MISMATCH`
- `MODEL_FILE_MISSING`
- `MODEL_NOT_VERIFIED`
- `COMFYUI_UNAVAILABLE`
- `JOB_ALREADY_TERMINAL`
- `PIXEL_INTEGRITY_FAILED`
- `VERSION_CONFLICT`
- `IDEMPOTENCY_CONFLICT`
