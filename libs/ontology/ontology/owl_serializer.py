"""OWL serializer — import/export helpers for KnowledgeBundle transfer."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from ontology.engine import OntologyEngine


def export_bundle(engine: OntologyEngine, robot_id: str, environment_id: str,
                  robot_type: str = "generic") -> dict:
    """Export engine state as a serializable KnowledgeBundle dict."""
    owl_bytes = engine.export_bytes("turtle")
    return {
        "source_robot_id": robot_id,
        "environment_id": environment_id,
        "robot_type_source": robot_type,
        "ontology_owl_b64": base64.b64encode(owl_bytes).decode(),
        "triple_count": len(engine),
        "snapshot_hash": engine.snapshot_hash(),
        "created_at": time.time(),
    }


def import_bundle(bundle: dict) -> OntologyEngine:
    """Import a KnowledgeBundle dict into a new OntologyEngine."""
    eng = OntologyEngine()
    owl_b64 = bundle.get("ontology_owl_b64", "")
    if owl_b64:
        owl_bytes = base64.b64decode(owl_b64)
        eng.load_text(owl_bytes.decode(errors="ignore"))
    return eng


def sign_bundle(bundle: dict, private_pem: bytes) -> str:
    """Sign bundle dict with Ed25519. Returns hex signature."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        canonical = json.dumps(bundle, sort_keys=True).encode()
        priv = load_pem_private_key(private_pem, password=None)
        sig = priv.sign(canonical)
        return sig.hex()
    except ImportError:
        return ""


def verify_bundle(bundle: dict, signature_hex: str, public_pem: bytes) -> bool:
    """Verify bundle signature. Returns True if valid."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.exceptions import InvalidSignature
        canonical = json.dumps(bundle, sort_keys=True).encode()
        pub = load_pem_public_key(public_pem)
        sig = bytes.fromhex(signature_hex)
        pub.verify(sig, canonical)
        return True
    except Exception:
        return False
