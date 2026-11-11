class DomainError(Exception):
    def __init__(self, message: str, code: str = "validation_error", status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class ConfigurationError(DomainError):
    def __init__(self, message: str = "Required internal runtime configuration is missing"):
        super().__init__(message, "configuration_error", 503)
