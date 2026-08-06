"""
Model registry (PHASE 4).

Every transaction row persists the ``model_version`` that categorised it. The
registry is the other half of that pair: given a version string recorded in
March, it returns the artifact, the metrics it was accepted on, and the
training metadata. Without it, ``model_version = 'cat-v3'`` is a string with no
referent and the question "why was this categorised that way in March?" is
unanswerable.

Versions are content-addressed (``cat-<date>-<hash8>``) so two runs with
different data or hyper-parameters can never collide on one name.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ModelCard:
    """Everything needed to reproduce and audit a model release."""

    name: str
    version: str
    task: str
    created_at: str
    base_model: str = ""
    labels: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    training_metadata: Dict[str, Any] = field(default_factory=dict)
    artifact_uri: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_version(name: str, fingerprint: Dict[str, Any]) -> str:
    """Deterministic version id from the training configuration."""
    blob = json.dumps(fingerprint, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:8]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{name}-{stamp}-{digest}"


class ModelRegistry:
    """Filesystem-backed registry, mirrored into ``model_registry`` in Postgres."""

    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or settings.model_registry_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths -----------------------------------------------------------
    def version_dir(self, name: str, version: str) -> Path:
        return self.root / name / version

    def card_path(self, name: str, version: str) -> Path:
        return self.version_dir(name, version) / "model_card.json"

    def _pointer_path(self, name: str) -> Path:
        return self.root / name / "ACTIVE"

    # -- write -----------------------------------------------------------
    def save(
        self,
        card: ModelCard,
        artifact_dir: Optional[Path] = None,
        activate: bool = False,
    ) -> Path:
        """Persist a model card and (optionally) its artifact directory."""
        target = self.version_dir(card.name, card.version)
        target.mkdir(parents=True, exist_ok=True)

        if artifact_dir is not None and Path(artifact_dir) != target / "artifact":
            dest = target / "artifact"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(artifact_dir, dest)
            card.artifact_uri = str(dest)

        self.card_path(card.name, card.version).write_text(
            json.dumps(card.to_dict(), indent=2), encoding="utf-8"
        )
        logger.info(f"Registered model {card.name}:{card.version}")

        if activate:
            self.activate(card.name, card.version)
        return target

    def activate(self, name: str, version: str) -> None:
        """Point the ``ACTIVE`` marker at a version."""
        if not self.card_path(name, version).exists():
            raise FileNotFoundError(f"No registered model {name}:{version}")
        self._pointer_path(name).parent.mkdir(parents=True, exist_ok=True)
        self._pointer_path(name).write_text(version, encoding="utf-8")
        logger.info(f"Model {name}:{version} is now ACTIVE")

    # -- read ------------------------------------------------------------
    def active_version(self, name: str) -> Optional[str]:
        pointer = self._pointer_path(name)
        if not pointer.exists():
            return None
        version = pointer.read_text(encoding="utf-8").strip()
        return version or None

    def load_card(self, name: str, version: Optional[str] = None) -> Optional[ModelCard]:
        version = version or self.active_version(name)
        if not version:
            return None
        path = self.card_path(name, version)
        if not path.exists():
            return None
        return ModelCard(**json.loads(path.read_text(encoding="utf-8")))

    def list_versions(self, name: str) -> List[str]:
        base = self.root / name
        if not base.exists():
            return []
        return sorted(
            d.name for d in base.iterdir() if d.is_dir() and (d / "model_card.json").exists()
        )

    def artifact_path(self, name: str, version: Optional[str] = None) -> Optional[Path]:
        version = version or self.active_version(name)
        if not version:
            return None
        path = self.version_dir(name, version) / "artifact"
        return path if path.exists() else None

    # -- database mirror -------------------------------------------------
    async def sync_to_database(self, card: ModelCard, activate: bool = False) -> None:
        """
        Mirror the card into Postgres.

        The filesystem is the source of truth for weights; the table exists so
        a transaction's ``model_version`` can be joined to its metrics without
        reading the disk.
        """
        from app.db.models import ModelRegistryEntry
        from app.db.session import system_session

        try:
            async with system_session() as session:
                existing = (
                    await session.execute(
                        select(ModelRegistryEntry).where(
                            ModelRegistryEntry.name == card.name,
                            ModelRegistryEntry.version == card.version,
                        )
                    )
                ).scalar_one_or_none()

                if existing is None:
                    session.add(
                        ModelRegistryEntry(
                            id=uuid.uuid4(),
                            name=card.name,
                            version=card.version,
                            task=card.task,
                            artifact_uri=card.artifact_uri,
                            metrics=card.metrics,
                            training_metadata=card.training_metadata,
                            is_active=activate,
                        )
                    )
                else:
                    existing.metrics = card.metrics
                    existing.training_metadata = card.training_metadata
                    existing.artifact_uri = card.artifact_uri
                    existing.is_active = activate or existing.is_active

                if activate:
                    await session.execute(
                        update(ModelRegistryEntry)
                        .where(
                            ModelRegistryEntry.name == card.name,
                            ModelRegistryEntry.version != card.version,
                        )
                        .values(is_active=False)
                    )
        except Exception as exc:
            # The registry must stay usable without a database (training runs
            # offline, in CI, on a laptop).
            logger.warning(f"Could not mirror model card to database: {exc}")


_registry: Optional[ModelRegistry] = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
