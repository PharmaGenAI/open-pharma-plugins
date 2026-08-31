"""Campaign-studio models — re-exports for convenient imports."""

from ._common import SourceReference
from .brief import CampaignBrief
from .claims import ApprovedClaim
from .copy import BannerCopy, ChannelCopy, CopyBlock, EmailCopy, PosterCopy
from .journey import AudienceJourney, JourneyStage
from .message import MessageArchitecture, MessageTier
from .mlr import MlrPackage, RenderedAsset
from .validation import (
    ClaimValidationReport,
    ClaimValidationResult,
    PolicyCheck,
)

__all__ = [
    "ApprovedClaim",
    "AudienceJourney",
    "BannerCopy",
    "CampaignBrief",
    "ChannelCopy",
    "ClaimValidationReport",
    "ClaimValidationResult",
    "CopyBlock",
    "EmailCopy",
    "JourneyStage",
    "MessageArchitecture",
    "MessageTier",
    "MlrPackage",
    "PolicyCheck",
    "PosterCopy",
    "RenderedAsset",
    "SourceReference",
]
