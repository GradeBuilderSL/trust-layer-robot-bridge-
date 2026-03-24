"""Типы знаний для Knowledge Service."""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from datetime import datetime


class KnowledgeType(str, Enum):
    """Типы знаний, поддерживаемые системой."""
    SAFETY_RULE = "safety_rule"
    OPERATIONAL_POLICY = "operational_policy"
    TRUST_METRIC = "trust_metric"
    ROBOT_SKILL = "robot_skill"
    CONSTRAINT = "constraint"
    INCIDENT_REPORT = "incident_report"
    BEST_PRACTICE = "best_practice"
    COMPLIANCE_REQUIREMENT = "compliance_requirement"
    PROFESSION_RULE = "profession_rule"
    EXPERIENCE_PATTERN = "experience_pattern"


@dataclass
class KnowledgeMetadata:
    """Метаданные записи знания."""
    id: str
    type: KnowledgeType
    version: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: str = "system"
    tags: Dict[str, str] = None
    description: str = ""
    validity_period: Optional[Dict[str, datetime]] = None
    audit_ref: str = ""  # Ссылка на запись в decision_log

    def __post_init__(self):
        if self.tags is None:
            self.tags = {}
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = self.created_at


@dataclass
class KnowledgeRecord:
    """Полная запись знания с данными и метаданными."""
    metadata: KnowledgeMetadata
    data: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь для сериализации."""
        return {
            "metadata": {
                "id": self.metadata.id,
                "type": self.metadata.type,
                "version": self.metadata.version,
                "created_at": self.metadata.created_at.isoformat() if self.metadata.created_at else None,
                "updated_at": self.metadata.updated_at.isoformat() if self.metadata.updated_at else None,
                "created_by": self.metadata.created_by,
                "tags": self.metadata.tags,
                "description": self.metadata.description,
                "validity_period": {
                    k: v.isoformat() for k, v in self.metadata.validity_period.items()
                } if self.metadata.validity_period else None,
                "audit_ref": self.metadata.audit_ref
            },
            "data": self.data
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeRecord':
        """Создать из словаря."""
        meta_data = data["metadata"]
        validity_period = None
        if meta_data.get("validity_period"):
            validity_period = {
                k: datetime.fromisoformat(v) if isinstance(v, str) else v
                for k, v in meta_data["validity_period"].items()
            }
        
        metadata = KnowledgeMetadata(
            id=meta_data["id"],
            type=KnowledgeType(meta_data["type"]),
            version=meta_data.get("version", 1),
            created_at=datetime.fromisoformat(meta_data["created_at"]) if isinstance(meta_data.get("created_at"), str) else meta_data.get("created_at"),
            updated_at=datetime.fromisoformat(meta_data["updated_at"]) if isinstance(meta_data.get("updated_at"), str) else meta_data.get("updated_at"),
            created_by=meta_data.get("created_by", "system"),
            tags=meta_data.get("tags", {}),
            description=meta_data.get("description", ""),
            validity_period=validity_period,
            audit_ref=meta_data.get("audit_ref", "")
        )
        
        return cls(metadata=metadata, data=data["data"])