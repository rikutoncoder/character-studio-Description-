"""Stable errors used by the M1 domain and adapters."""


class CharacterStudioError(Exception):
    """Base class for expected Character Studio failures."""

    code = "CHARACTER_STUDIO_ERROR"


class AssetIntegrityError(CharacterStudioError):
    code = "ASSET_HASH_MISMATCH"


class AssetNotFoundError(CharacterStudioError):
    code = "ASSET_NOT_FOUND"


class ImageFormatError(CharacterStudioError):
    code = "IMAGE_FORMAT_UNSUPPORTED"


class ImageDimensionError(CharacterStudioError):
    code = "IMAGE_DIMENSION_MISMATCH"


class MaskEmptyError(CharacterStudioError):
    code = "MASK_EMPTY"


class MaskFullError(CharacterStudioError):
    code = "MASK_FULL"


class MaskNotBinaryError(CharacterStudioError):
    code = "MASK_NOT_BINARY"


class PixelIntegrityError(CharacterStudioError):
    code = "PIXEL_INTEGRITY_FAILED"


class RecordNotFoundError(CharacterStudioError):
    code = "RECORD_NOT_FOUND"
