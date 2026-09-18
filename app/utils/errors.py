class NovelRagError(Exception):
    """Base class for expected application errors."""


class TextProcessingError(NovelRagError):
    pass


class EpubConversionError(NovelRagError):
    pass


class ModelInputTooLargeError(NovelRagError):
    pass


class StorageError(NovelRagError):
    pass


class SourceValidationError(NovelRagError):
    pass
