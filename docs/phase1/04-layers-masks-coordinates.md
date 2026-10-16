# レイヤー・マスク・座標系の詳細設計

## 座標空間

### Image Space

正本となる座標空間。原点は画像左上、X軸は右、Y軸は下方向とする。

- 有効ピクセル：`0 <= x < width`、`0 <= y < height`
- 矩形：左上を含み右下を含まない半開区間
- ピクセル中心：`(x + 0.5, y + 0.5)`
- 保存マスクは整数ピクセルグリッドへラスタライズする。

### Stage Space

React Konvaの編集ステージ座標。パン、ズーム、回転を含み得るが、永続データの基準にはしない。

### Client Space

ブラウザのCSSピクセル座標。Pointer EventはClient SpaceからStage Space、Image Spaceの順に逆変換する。

### Device Pixel Space

端末の物理ピクセル。Device Pixel Ratioは表示品質だけに使い、マスク寸法やブラシ半径の正本へ混入させない。

## 変換規則

画面入力は次の順に変換する。

```text
Pointer client coordinates
  → canvas bounding rectangle correction
  → inverse stage pan/zoom
  → inverse layer transform
  → image coordinates
```

ブラシ半径は画像ピクセル単位で保存する。ズーム倍率が変化しても同じ編集操作が正本上で同じ半径になるように表示半径を調整する。

## レイヤー

### Base Layer

- 確定版の正本画像を参照する。
- 編集Workspace内でも内容を上書きしない。
- 新しい確定版への切替時は別Asset参照へ更新する。

### Mask Layer

- 正本は同寸法の二値L PNG。
- `0`は保護、`255`は編集許可。
- UI上の半透明色は表示表現であり、正本値ではない。
- アンチエイリアス表示から中間階調を正本へ混入させない。

### Color Guide Layer

- マスク内の仮着色を表すRGB/RGBA PNG。
- 元画像の陰影保持方法など、生成入力作成パラメータをGuideに保存する。
- マスク外は利用しない。

### Silhouette Guide Layer

- 構造追加の輪郭、袖、前身頃などの領域を表す。
- 元画像の身体や手足を保護するマスクと一対で扱う。

### Reference Layer

- 将来の参照画像拘束用。Phase 1では必須実装にしない。

## Workspaceと確定版

Workspaceは未確定の編集状態である。レイヤー、選択ツール、表示倍率、Undo/Redoコマンドを持つが、確定版そのものではない。

ジョブ作成時にはWorkspaceから必要なマスクとガイドを不変Assetへスナップショットし、以後のブラシ編集で実行中ジョブの入力が変化しないようにする。

## Undo/Redo

- ブラシストローク、消去、レイヤー表示、ガイド変形などをCommandとして扱う。
- 画面表示だけのパン・ズームは、画像編集Undo/Redoへ含めない。
- ジョブ作成、候補採用、版復元はWorkspace Undoで取り消さない。
- 大きなマスク全体を毎操作コピーせず、タイルまたは差分パッチを履歴として保持する方式を候補とする。
- ページ再読み込みを超えたUndo履歴の保証範囲は未決定とする。

## マスク正本化

1. ブラシ操作は高精度の作業バッファへ描画する。
2. 保存時にImage Spaceの整数グリッドへラスタライズする。
3. しきい値処理で値を0または255に限定する。
4. 正本画像と同寸法であることを検証する。
5. `editable_pixel_count`とbounding boxを計算する。
6. 可逆PNGとしてAsset保存する。

空マスク、全面マスク、画像外だけのストロークは生成要求時に拒否する。全面マスクを許可する特別なユースケースが必要になった場合は別途承認する。

## 内向きフェザー

フェザーは二値マスクの内部距離を利用して生成する。外部の係数は必ず0にする。

```text
if M(x,y) == 0:
    F(x,y) = 0
else:
    F(x,y) = clamp(distance_to_mask_edge(x,y) / feather_px, 0, 1)
```

`feather_px=0`はハードマスクを表す。Phase 1の既定値は3px候補とするが、2〜4pxの範囲で操作別プリセットに保存する。

## 画像取り込み

- EXIF Orientationを適用する。
- sRGBへ変換する。
- 8bit RGBまたはRGBAへ正規化する。
- RGBAの場合、alpha channelは以後の編集・生成でも元画像から全域保持する。
- 可逆PNG正本を作成する。
- 元アップロードを別Assetとして保持する。
- 基準解像度以外の入力を許可する場合も、暗黙リサイズはしない。

Phase 1の編集・生成解像度は832×1216に限定する。別サイズの元ファイルはOriginal Upload Assetとして保存できるが、自動リサイズ、切り抜き、余白追加を行わず、編集対象外として案内する。
