from enum import StrEnum


class RatingJobStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MANUAL_ACTION = "manual_action"
    CANCELLED = "cancelled"


class RatingAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MatchStrategy(StrEnum):
    CACHE = "cache"
    TMDB = "tmdb"
    SEARCH = "search"
    MANUAL = "manual"


class WriteStrategy(StrEnum):
    SESSION = "session"
    BROWSER = "browser"


class ErrorType(StrEnum):
    MATCH_NOT_FOUND = "match_not_found"
    SESSION_INVALID = "session_invalid"
    AUTH_FAILED = "auth_failed"
    CHALLENGE_DETECTED = "challenge_detected"
    WRITE_REJECTED = "write_rejected"
    VERIFICATION_FAILED = "verification_failed"
    TRANSIENT_NETWORK_ERROR = "transient_network_error"
    UNKNOWN = "unknown"
