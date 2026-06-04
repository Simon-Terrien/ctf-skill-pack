"""
biobrain — Biologically-inspired modular cognitive runtime kernel
=================================================================

Usage:
    from biobrain.runtime.pipeline import BioBrain
    from biobrain.core.enums import InputSource

    brain = BioBrain(palace_path="~/.mempalace/palace")
    trace = brain.process("scan the target", source=InputSource.USER)
"""

__version__ = "0.7.0"
