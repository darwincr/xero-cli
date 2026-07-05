class XeroCliError(Exception):
    """Base exception for expected CLI failures."""


class AuthenticationError(XeroCliError):
    """Xero authentication failed or did not complete."""


class InteractiveAuthenticationRequired(AuthenticationError):
    """Xero requires a manual login, MFA, or verification step."""


class MfaRequired(AuthenticationError):
    """Xero requires a multi-factor authentication code."""


class ElementNotFoundError(XeroCliError):
    """A required page element was not found."""


class ValidationError(XeroCliError):
    """CLI input validation failed before opening Xero."""


class ScreenshotError(XeroCliError):
    """The browser page screenshot could not be captured."""
